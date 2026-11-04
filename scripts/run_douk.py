"""Local DouK bridge for search, public directories and expiring media resolution."""
import asyncio
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "data/tools/douk"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOL))


def detail_error(data, video_id, filter_reason=""):
    """Never treat an empty or restricted detail payload as a successful video."""
    if filter_reason == "status_audit_self_see":
        return {"status": "unavailable", "reason": "self_only"}
    if filter_reason == "status_audit_not_pass":
        return {"status": "unavailable", "reason": "audit_not_pass"}
    if not isinstance(data, dict) or not data:
        return {"status": "failed", "reason": "empty_detail"}
    if str(data.get("aweme_id") or "") != video_id:
        return {"status": "failed", "reason": "identity_mismatch"}
    status, paid = data.get("status") or {}, data.get("series_paid_info") or {}
    if not isinstance(status, dict) or not isinstance(paid, dict):
        return {"status": "failed", "reason": "malformed_detail"}
    if data.get("is_delete") or status.get("is_private") or paid.get("item_price", 0):
        return {"status": "unavailable", "reason": "restricted"}
    return None


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
    from sources.bridge_scheduler import RequestGate
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
        detail_filter = ""
        code = None
        message = ""
        received = False
        more = False
        valid_page = False

        async def request_data(self, *args, **kwargs):
            value = await super().request_data(*args, **kwargs)
            if isinstance(value, dict):
                self.received = True
                self.code, self.message = value.get("status_code"), str(value.get("status_msg", ""))
                self.more = bool(value.get("has_more"))
                restriction = value.get("filter_detail")
                self.detail_filter = restriction.get("filter_reason", "") if isinstance(restriction, dict) else ""
                cursor_key = 'max_cursor' if isinstance(self, Account) else 'cursor'
                self.valid_page = (self.code == 0 and isinstance(value.get('aweme_list'), list)
                    and type(value.get('has_more')) in (bool, int) and value['has_more'] in (0,1)
                    and type(value.get(cursor_key)) is int and value[cursor_key] >= 0)
                if self.valid_page:
                    self.page_cursor = value[cursor_key]
            return value

        def check_response(self, value, *args, **kwargs):
            if not self.valid_page:
                self.finished = True
                return
            self.cursor = self.page_cursor
            self.response.extend(value['aweme_list'])
            self.finished = not self.more

    class PublicDetail(Observed, Detail):
        def check_response(self, *args, **kwargs):
            return Detail.check_response(self, *args, **kwargs)
    class PublicAccount(Observed, Account): pass
    class PublicMix(Observed, Mix): pass

    def response_status(operation, data):
        if operation.code == 2483 or "登录" in operation.message:
            return "needs_login"
        if operation.code not in (None, 0) or any(word in operation.message for word in ("验证", "频繁", "captcha")):
            return "blocked"
        if not operation.received:
            return 'transient'
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
                self.valid_payload = (self.upstream_code == 0 and isinstance(data.get("data"), list)
                    and type(data.get('has_more')) in (bool,int) and data['has_more'] in (0,1)
                    and type(data.get('cursor')) is int and data['cursor']>=0)
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
        gate = RequestGate(state_path=TOOL / 'source-gate.json')

        @app.middleware('http')
        async def schedule_upstream(request, call_next):
            if request.url.path not in ('/search','/directory','/detail','/media'):
                return await call_next(request)
            # Serialize the full upstream request, including request parsing; inspect status safely.
            from starlette.responses import Response
            async def execute():
                response = await call_next(request)
                content = b''.join([chunk async for chunk in response.body_iterator])
                import json
                try:
                    result = json.loads(content)
                except ValueError:
                    result = {'status':'failed'}
                execute.response = Response(content,status_code=response.status_code,headers=dict(response.headers),media_type='application/json')
                return result
            result = await gate.run(execute)
            return getattr(execute,'response',None) or JSONResponse(result)

        @app.post('/resume')
        async def resume(body: MediaRequest):
            # Bridge is loopback/internal-only. Public web callers go through authenticated APIs.
            await gate.resume()
            return {'status':'ok'}

        @app.exception_handler(RequestValidationError)
        async def invalid_input(request, exc):
            return JSONResponse(status_code=400, content={"detail": "Invalid search parameters"})

        @app.middleware("http")
        async def redact_errors(request, call_next):
            try:
                return await call_next(request)
            except Exception:
                # Never print upstream exceptions, request headers or signed media URLs.
                return JSONResponse(status_code=503, content={"status": "failed", "message": "解析服务暂不可用，请检查来源状态后重试"})

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
            if status=='ok' and (any(not public_video(item) for item in data)
                or operation.more and (operation.offset==body.offset or not data)):
                return {'status':'parse_error'}
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
            if response_status(operation,data)=='ok' and not operation.valid_page:
                return {'status':'parse_error'}
            if operation.valid_page and (any(not public_video(row) for row in data)
                or operation.more and (str(operation.cursor)==str(body.cursor) or not data)):
                return {'status':'parse_error'}
            return {"status": response_status(operation, data), "videos": [v for row in data if (v := public_video(row))],
                    "cursor": operation.cursor, "has_more": operation.more}

        @app.post("/detail")
        async def detail(body: MediaRequest):
            operation = PublicDetail(tool.parameter, cookie=body.cookie, detail_id=body.video_id)
            data = await operation.run()
            status = response_status(operation, data)
            if status != "ok":
                return {"status": status}
            failure = detail_error(data, body.video_id, operation.detail_filter)
            if failure:
                return failure
            if data.get("is_delete") or (data.get("status") or {}).get("is_private") or (data.get("series_paid_info") or {}).get("item_price", 0):
                return {"status": "unavailable"}
            safe = public_video(data)
            return {"status": "ok", "video": safe} if safe else {"status": "failed"}

        @app.post("/media")
        async def media(body: MediaRequest):
            operation = PublicDetail(tool.parameter, cookie=body.cookie, detail_id=body.video_id)
            data = await operation.run()
            status = response_status(operation, data)
            if status != "ok":
                return {"status": status}
            failure = detail_error(data, body.video_id, operation.detail_filter)
            if failure:
                return failure
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
            await uvicorn.Server(uvicorn.Config(app, host=os.getenv("DOUK_HOST", "127.0.0.1"), port=int(os.getenv("DOUK_PORT", "5555")), log_level="warning")).serve()
        finally:
            # Library mode does not start the CLI's parameter-refresh thread.
            await tool.parameter.close_client()
            tool.parameter = None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
