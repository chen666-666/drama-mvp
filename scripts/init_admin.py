"""Interactive private configuration. No password or secret is printed."""
import getpass
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth import PRIVATE, password_hash


def main():
    origin = input("网站地址（本机可用 http://127.0.0.1:8000）：").strip().rstrip("/")
    parsed = urlparse(origin)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))) or parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.netloc:
        raise SystemExit("请输入完整 HTTPS 网站地址，本机开发允许 HTTP 回环地址")
    user = input("管理员账号：").strip()
    password = getpass.getpass("管理员密码（至少 12 位）：")
    if not user or len(password) < 12 or password != getpass.getpass("再次输入密码："):
        raise SystemExit("账号或密码不符合要求，未写入配置")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    target = PRIVATE / "site-auth.env"
    values = {"DRAMA_SITE_ORIGIN": origin, "DRAMA_ADMIN_USER": user,
              "DRAMA_ADMIN_PASSWORD_HASH": password_hash(password), "DRAMA_SESSION_SECRET": secrets.token_hex(32)}
    # Exclusive creation protects any existing credentials; use dotenv's quoting rules.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(key + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'\n")
    print("网站登录配置已保存至 data/private/site-auth.env；请重启网页服务。")


if __name__ == "__main__":
    main()
