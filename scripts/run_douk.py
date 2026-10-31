"""Local DouK bridge for search, public directories and expiring media resolution."""
import asyncio
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "data/tools/douk"
sys.path.insert(0, str(TOOL))


async def main():
    from src.application import TikTokDownloader
    from src.interface.search import Search
    from src.interface.detail import Detail
    from src.interface.account import Account
    from src.interface.mix import Mix
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    from src.record import BaseLogger
    import uvicorn

    class SearchRequest(BaseModel):
        keyword: str = Field(min_length=1, max_length=100)
        sort_type: int = Field(default=0, ge=0, le=2)
        offset: int = Field(default=0, ge=0)
        count: int = Field(default=10, ge=5, le=20)
        publish_time: int = 7
        cookie: str = Field(default="", max_length=50000, repr=False)

    class MediaRequest(BaseModel):
        video_id: str = Field(pattern=r"^[0-9]{1,100}$")
        cookie: str = Field(default="", max_length=50000, repr=False)

    class DirectoryRequest(BaseModel):
        account: str = Field(default="", max_length=255)
        mix_id: str = Field(default="", pattern=r"^[0-9]*$")
        cursor: int = Field(default=0, ge=0)
        cookie: str = Field(default="", max_length=50000, repr=False)

    class Observed:
        code = None
        message = ""
        received = False
        more = False

        async def request_data(self, *args, **kwargs):
            value = await super().request_data(*args, **kwargs)
            if isinstance(value, dict):
                self.received = True
                self.code, self.message = value.get("status_code"), str(value.get("status_msg", ""))
                self.more = bool(value.get("has_more"))
            return value

    class PublicDetail(Observed, Detail): pass
    class PublicAccount(Observed, Account): pass
    class PublicMix(Observed, Mix): pass

    def response_status(operation, data):
        if operation.code == 2483 or "登录" in operation.message:
            return "needs_login"
        if operation.code not in (None, 0) or any(word in operation.message for word in ("验证", "频繁", "captcha")):
            return "blocked"
        return "ok" if operation.received and operation.code == 0 and isinstance(data, (dict, list)) else "unavailable"

    class ObservedSearch(Search):
        upstream_code = None
        upstream_message = ""
        received = False
        more = False
        valid_payload = False

        async def request_data(self, *args, **kwargs):
            data = await super().request_data(*args, **kwargs)
            if isinstance(data, dict):
                self.received = True
                self.upstream_code = data.get("status_code")
                self.upstream_message = str(data.get("status_msg", ""))[:200]
                self.more = bool(data.get("has_more"))
                self.valid_payload = self.upstream_code == 0 and isinstance(data.get("data"), list)
            return data

        def check_response(self, data, *args, **kwargs):
            if not self.valid_payload:
                self.finished = True
                return
            self.offset = data.get("cursor", self.offset)
            self.response.extend(data["data"])
            self.finished = not data.get("has_more")

    def public_video(item):
        if not isinstance(item, dict):
            return None
        video = item.get("aweme_info") or item
        if not video.get("aweme_id"):
            return None
        author, stats, mix = video.get("author") or {}, video.get("statistics") or {}, video.get("mix_info") or {}
        return {"aweme_id": video["aweme_id"], "desc": video.get("desc"), "create_time": video.get("create_time"),
                "author": {key: author.get(key) for key in ("uid", "sec_uid", "nickname")},
                "statistics": {key: stats.get(key) for key in ("digg_count", "comment_count", "collect_count", "share_count")},
                "mix_info": {key: mix.get(key) for key in ("mix_id", "mix_name")},
                "duration": (video.get("video") or {}).get("duration"),
                "cover": ((video.get("video") or {}).get("cover") or {}).get("url_list", [""])[0]}

    async with TikTokDownloader() as tool:
        # Disable upstream file logging, which may otherwise contain request headers.
        tool.config["Logger"] = 0
        tool.check_config()
        class QuietLogger(BaseLogger):
            def info(self, *args, **kwargs): pass
            def warning(self, *args, **kwargs): pass
            def error(self, *args, **kwargs): pass
            def debug(self, *args, **kwargs): pass
        tool.logger = QuietLogger
        settings = tool.settings.read()
        settings.update(download=False, tiktok_platform=False, max_retry=0, timeout=15, storage_format="")
        # Use upstream library objects; no media, automatic updates or background retry loop.
        from src.config import Parameter
        tool.parameter = Parameter(tool.settings, tool.cookie, logger=tool.logger,
                                   console=tool.console, recorder=tool.recorder, **settings)
        tool.parameter.set_headers_cookie()
        app = FastAPI(title="Local DouK search bridge", docs_url=None, redoc_url=None)

        @app.exception_handler(RequestValidationError)
        async def invalid_input(request, exc):
            return JSONResponse(status_code=400, content={"detail": "Invalid search parameters"})

        @app.get("/health")
        def health():
            return {"status": "ok", "tool": "DouK", "revision": (TOOL / "UPSTREAM_REVISION.txt").read_text().splitlines()[1]}

        @app.post("/search")
        async def search(body: SearchRequest):
            operation = ObservedSearch(tool.parameter, channel=1, keyword=body.keyword, offset=body.offset,
                                       count=body.count, pages=1, sort_type=body.sort_type, publish_time=body.publish_time,
                                       cookie=body.cookie)
            data = await operation.run(single_page=True)
            code, message = operation.upstream_code, operation.upstream_message
            if code == 2483 or "登录" in message:
                status, note = "needs_login", "抖音要求登录后才能搜索，请配置本机登录状态后继续"
            elif code not in (None, 0) or any(word in message for word in ("验证", "频繁", "captcha")):
                status, note = "blocked", "抖音要求访问验证或暂时限制访问，请在官方页面处理后继续"
            elif not operation.received or not operation.valid_payload:
                status, note = "failed", "未获得可解析的抖音响应，请检查网络或稍后再试"
            else:
                status, note = "ok", "搜索请求已完成"
            return {"status": status, "message": note, "upstream_code": code,
                    "videos": [value for item in data if (value := public_video(item))],
                    "offset": operation.offset, "has_more": operation.more}

        @app.post("/directory")
        async def directory(body: DirectoryRequest):
            if not body.mix_id and not body.account:
                return {"status": "unavailable"}
            if body.mix_id:
                operation = PublicMix(tool.parameter, cookie=body.cookie, mix_id=body.mix_id, cursor=body.cursor, count=20)
            else:
                operation = PublicAccount(tool.parameter, cookie=body.cookie, sec_user_id=body.account, cursor=body.cursor, count=20, pages=1)
            data = await operation.run(single_page=True)
            return {"status": response_status(operation, data), "videos": [v for row in data if (v := public_video(row))],
                    "cursor": operation.cursor, "has_more": operation.more}

        @app.post("/media")
        async def media(body: MediaRequest):
            operation = PublicDetail(tool.parameter, cookie=body.cookie, detail_id=body.video_id)
            data = await operation.run()
            status = response_status(operation, data)
            if status != "ok" or not isinstance(data, dict):
                return {"status": status}
            # Paid, private, deleted or image posts are never treated as downloadable public video.
            if data.get("is_delete") or (data.get("status") or {}).get("is_private") or (data.get("series_paid_info") or {}).get("item_price", 0):
                return {"status": "unavailable"}
            video = data.get("video") or {}
            urls = (video.get("play_addr") or {}).get("url_list") or []
            if not urls:
                return {"status": "unavailable"}
            return {"status": "ok", "url": urls[0], "duration": video.get("duration", 0) / 1000 or None,
                    "author_account_id": str((data.get("author") or {}).get("uid") or ""),
                    "revision": (TOOL / "UPSTREAM_REVISION.txt").read_text().splitlines()[1]}

        try:
            await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.getenv("DOUK_PORT", "5555")), log_level="warning")).serve()
        finally:
            # Library mode does not start the CLI's parameter-refresh thread.
            await tool.parameter.close_client()
            tool.parameter = None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
