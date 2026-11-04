"""Versioned browser assets and compression for public text responses only."""
from functools import lru_cache
import hashlib
from pathlib import Path

from starlette.datastructures import QueryParams
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parents[1] / "static"


@lru_cache(maxsize=128)
def _version(path, size, modified, changed):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def static_url(name):
    path = (STATIC / name).resolve()
    if not path.is_relative_to(STATIC.resolve()):
        raise ValueError("静态资源路径无效")
    stat = path.stat()
    version = _version(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return f"/static/{name}?v={version}"


class BrowserStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Only content-versioned URLs are safe to retain across deployments.
        query = QueryParams(scope.get("query_string", b"").decode("latin-1"))
        if response.status_code in (200, 304) and query.get("v") == static_url(path).split("?v=")[1]:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


class PublicTextCompression(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        compress = (path in ("/", "/legacy", "/watch", "/api/downloads/rankings") or
                    path.startswith("/api/library/rankings/") or
                    path.startswith("/static/") and path.endswith((".css", ".js")))
        ranged = any(name.lower() == b"range" for name, value in scope.get("headers", []))
        if scope["type"] == "http" and compress and not ranged and scope.get("method") != "HEAD":
            await super().__call__(scope, receive, send)
        else:
            # Media/Range responses, exports and authentication never pass through GZip.
            await self.app(scope, receive, send)
