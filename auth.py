"""Single administrator, persistent hashed sessions and same-origin CSRF protection."""
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlparse, quote

from dotenv import dotenv_values
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

PRIVATE = Path(__file__).resolve().parent / "data/private"
COOKIE = "drama_session"
LOGIN_COOKIE = "drama_login"
TTL = 8 * 3600


def config():
    values = {**dotenv_values(PRIVATE / "site-auth.env"), **os.environ}
    return {key: values.get("DRAMA_" + key, "") for key in
            ("SITE_ORIGIN", "ADMIN_USER", "ADMIN_PASSWORD_HASH", "SESSION_SECRET")}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${result}"


def password_matches(password, saved):
    try:
        kind, salt, value = saved.split("$")
        return kind == "scrypt" and hmac.compare_digest(password_hash(password, salt), saved)
    except (ValueError, TypeError):
        return False


def settings():
    cfg = config()
    origin = urlparse(cfg["SITE_ORIGIN"])
    local = origin.hostname in ("localhost", "127.0.0.1", "::1", "testserver")
    if (not all(cfg.values()) or len(cfg["SESSION_SECRET"]) < 32 or
            origin.scheme not in ("http", "https") or origin.scheme != "https" and not local or
            origin.path or origin.query or origin.fragment or origin.username):
        raise HTTPException(503, "网站登录尚未配置，请由管理员运行 scripts/init_admin.py")
    return cfg


@contextmanager
def store():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / "site-sessions.db"
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        if os.name != "nt":
            path.chmod(0o600)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS failures (peer TEXT, at REAL)")
        db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        db.execute("DELETE FROM failures WHERE at < ?", (time.time() - 900,))
        yield db


def sign(value, cfg):
    return hmac.new(cfg["SESSION_SECRET"].encode(), value.encode(), hashlib.sha256).hexdigest()


def require_admin(request):
    cfg = settings()
    token = request.cookies.get(COOKIE, "")
    with store() as db:
        valid = token and db.execute("SELECT 1 FROM sessions WHERE digest=? AND expires>?",
                                    (sign(token, cfg), time.time())).fetchone()
    if not valid:
        raise HTTPException(401, "请重新登录网站；抖音来源登录需在来源设置中单独配置")
    return cfg, token


def same_origin(request, cfg):
    origin = cfg["SITE_ORIGIN"]
    if request.headers.get("host", "").lower() != urlparse(origin).netloc.lower():
        raise HTTPException(403, "网站域名不匹配")
    if request.headers.get("origin") != origin:
        raise HTTPException(403, "请从当前网站页面操作")


def require_write(request):
    cfg, token = require_admin(request)
    same_origin(request, cfg)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sign("csrf:" + token, cfg)):
        raise HTTPException(403, "页面验证已失效，请刷新页面后重试")


def cookie_options(cfg):
    return dict(secure=cfg["SITE_ORIGIN"].startswith("https:"), httponly=True, samesite="lax", path="/")


def safe_next(value):
    from urllib.parse import urlsplit
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value and not any(ord(c) < 32 for c in value):
        path = urlsplit(value).path
        if path in ("/watch", "/downloads", "/downloads/watch", "/workbench", "/publish", "/control") or path.startswith("/control/"):
            return value
    return "/control"


def install(app):
    @app.middleware("http")
    async def access(request: Request, call_next):
        path = request.url.path
        from services.site import public_mode
        if public_mode():
            import re
            allowed = request.method in ("GET", "HEAD") and (
                path in ("/", "/legacy", "/watch", "/downloads", "/health", "/api/downloads/rankings", "/api/library/rankings") or
                path.startswith("/static/") or re.fullmatch(
                    r"/api/library/(?:rankings|collections)/[0-9]+|/api/library/media/[0-9]+(?:/download)?|/api/downloads/catalogs/[0-9]+/cover|/rankings/[0-9]+/report", path))
            if not allowed:
                return JSONResponse({"detail": "此网站仅供浏览与播放，请在本机工作台管理资料"}, status_code=403)
            return await call_next(request)
        mutation = request.method not in ("GET", "HEAD", "OPTIONS")
        control_page = path == "/control" or path.startswith("/control/")
        private = (path.startswith("/api/downloads/") and path != "/api/downloads/rankings" and
                   not path.endswith("/cover")) or path.startswith(("/api/source/", "/api/runs", "/api/search-jobs", "/api/control/", "/api/playback/"))
        try:
            if path not in ("/api/auth/login", "/api/auth/logout"):
                if mutation:
                    require_write(request)
                elif private or control_page or path in ("/downloads", "/downloads/watch", "/workbench", "/publish"):
                    require_admin(request)
        except HTTPException as exc:
            if exc.status_code == 401 and (control_page or path in ("/downloads", "/downloads/watch", "/workbench", "/publish")):
                result = RedirectResponse("/login?next=" + quote(str(request.url.path) + ("?" + request.url.query if request.url.query else ""), safe=""), status_code=303)
            else:
                result = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            result.headers["Cache-Control"] = "no-store"
            return result
        result = await call_next(request)
        if private or control_page or path.startswith(("/api/auth/", "/api/publishing/")) or path in ("/login", "/downloads", "/downloads/watch", "/workbench", "/publish"):
            result.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/api/auth/session")
    def session(request: Request):
        cfg = settings()
        try:
            _, token = require_admin(request)
            return {"authenticated": True, "csrf_token": sign("csrf:" + token, cfg), "user": cfg["ADMIN_USER"]}
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
        nonce = f"{int(time.time())}:{secrets.token_urlsafe(24)}"
        nonce += ":" + sign("login:" + nonce, cfg)
        result = JSONResponse({"authenticated": False, "csrf_token": sign("csrf:" + nonce, cfg)})
        result.set_cookie(LOGIN_COOKIE, nonce, max_age=900, **cookie_options(cfg))
        return result

    @app.post("/api/auth/login")
    async def login(request: Request):
        cfg = settings()
        same_origin(request, cfg)
        nonce = request.cookies.get(LOGIN_COOKIE, "")
        try:
            stamp, random, signature = nonce.split(":")
            valid = 0 <= time.time() - int(stamp) <= 900 and hmac.compare_digest(signature, sign("login:" + stamp + ":" + random, cfg))
        except ValueError:
            valid = False
        if not valid or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sign("csrf:" + nonce, cfg)):
            raise HTTPException(403, "登录页面已失效，请刷新后重试")
        peer = sign("peer:" + (request.client.host if request.client else "unknown"), cfg)
        # Count before checking credentials, using one transaction across processes.
        with store() as db:
            count = db.execute("SELECT COUNT(*) FROM failures WHERE peer=?", (peer,)).fetchone()[0]
            if count >= 5:
                raise HTTPException(429, "登录尝试过多，请十五分钟后再试")
            db.execute("INSERT INTO failures VALUES (?, ?)", (peer, time.time()))
        if len(await request.body()) > 4096:
            raise HTTPException(400, "登录信息无效")
        try:
            body = await request.json()
            user, password = body.get("username", ""), body.get("password", "")
            valid = isinstance(user, str) and isinstance(password, str) and password_matches(password, cfg["ADMIN_PASSWORD_HASH"]) and hmac.compare_digest(user.encode(), cfg["ADMIN_USER"].encode())
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            raise HTTPException(401, "账号或密码不正确")
        token = secrets.token_urlsafe(32)
        with store() as db:
            db.execute("DELETE FROM failures WHERE peer=?", (peer,))
            previous = request.cookies.get(COOKIE, "")
            db.execute("DELETE FROM sessions WHERE digest=?", (sign(previous, cfg),))
            db.execute("INSERT INTO sessions VALUES (?, ?)", (sign(token, cfg), time.time() + TTL))
        result = JSONResponse({"authenticated": True})
        result.set_cookie(COOKIE, token, max_age=TTL, **cookie_options(cfg))
        result.delete_cookie(LOGIN_COOKIE, **cookie_options(cfg))
        return result

    @app.post("/api/auth/logout")
    def logout(request: Request):
        require_write(request)
        cfg, token = require_admin(request)
        with store() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (sign(token, cfg),))
        result = JSONResponse({"message": "已退出网站登录"})
        result.delete_cookie(COOKIE, **cookie_options(cfg))
        return result
