"""Run the separately installed DouK API locally without downloading media."""
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
                "mix_info": {key: mix.get(key) for key in ("mix_id", "mix_name")}}

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

        try:
            await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.getenv("DOUK_PORT", "5555")), log_level="warning")).serve()
        finally:
            # Library mode does not start the CLI's parameter-refresh thread.
            await tool.parameter.close_client()
            tool.parameter = None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
