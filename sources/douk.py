"""Public-data adapter for the separate, local GPL-3.0 DouK process."""
from datetime import datetime, timezone
from pathlib import Path
import os
import json
import re
from urllib.parse import quote

import httpx

from services.common import iso, utcnow
from sources.base import SourceCapabilities

ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = ROOT / "data/private/douyin-session.json"
BRIDGE = os.getenv("DOUK_BRIDGE_URL", "http://127.0.0.1:5555").rstrip("/")
SORTS = {"general": (0, "综合"), "likes": (1, "点赞"), "latest": (2, "最新")}
UPSTREAM = "https://github.com/JoeanAmier/TikTokDownloader"


DETAIL_FAILURE_NOTES = {
    "self_only": "抖音详情接口提示审核后仅作者本人可见，未提供公开详情与互动；请核对原视频后处理，不自动排除",
    "audit_not_pass": "抖音详情接口提示视频审核未通过，未提供当前详情与互动；请核对原视频后处理，不自动排除",
    "empty_detail": "抖音详情接口未返回视频详情，无法取得当前互动；可重试或补充真实采样",
    "identity_mismatch": "详情返回的视频编号不一致，本次采样未保存，请核对来源",
    "malformed_detail": "视频详情结构异常，本次采样未保存，请核对来源",
    "restricted": "来源提示视频已删除、私密或付费，无法确认公开访问；请核对原视频后处理",
}


class SourceUnavailable(RuntimeError):
    def __init__(self, status, message, reason_code=None):
        super().__init__(message)
        self.status = status
        self.reason_code = reason_code if isinstance(reason_code, str) and reason_code in DETAIL_FAILURE_NOTES else None


def save_session(cookie):
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie or "=" not in cookie or "\n" in cookie or "\r" in cookie:
        raise ValueError("请粘贴请求头中的 Cookie 值，不要粘贴整段请求或其他内容")
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SESSION_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"cookie": cookie, "saved_at": iso(utcnow())}), encoding="utf-8")
    temporary.replace(SESSION_PATH)


def session_status():
    if not SESSION_PATH.exists():
        return {"configured": False, "saved_at": None}
    data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    return {"configured": bool(data.get("cookie")), "saved_at": data.get("saved_at")}


def _cookie():
    if not SESSION_PATH.exists():
        return ""
    return json.loads(SESSION_PATH.read_text(encoding="utf-8")).get("cookie", "")


class DouKSource:
    capabilities = SourceCapabilities(keyword_search=True)

    def health(self):
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                response = client.get(BRIDGE + "/health")
                response.raise_for_status()
                return {"available": True, **response.json()}
        except (httpx.HTTPError, ValueError):
            return {"available": False, "message": "本机采集服务尚未启动，请运行 start-search.ps1"}

    def search_page(self, keyword, sort_order, offset=0, count=10, cutoff=None):
        if sort_order not in SORTS:
            raise ValueError("搜索排序无效")
        # Relative platform filter is useful only for a current batch; exact UTC filtering follows on import.
        recent = cutoff is None or (utcnow() - cutoff).total_seconds() < 3600
        body = {"keyword": keyword, "sort_type": SORTS[sort_order][0], "offset": offset,
                "count": count, "publish_time": 7 if recent else 0, "cookie": _cookie()}
        try:
            with httpx.Client(trust_env=False, timeout=45) as client:
                response = client.post(BRIDGE + "/search", json=body)
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            raise SourceUnavailable("failed", "本机采集服务不可用或请求超时；已保存的候选仍保留")
        if result.get("status") != "ok":
            status = result.get("status") if result.get("status") in ("needs_login", "blocked", "failed") else "failed"
            messages = {"needs_login": "抖音返回“请先登录，再继续搜索吧”。请配置本机登录状态后继续。",
                        "blocked": "抖音要求验证或限制了访问，请在官方页面处理后继续。",
                        "failed": "本次没有获得可解析的抖音搜索数据；请检查网络及工具状态。"}
            raise SourceUnavailable(status, messages[status])
        if not isinstance(result.get("videos"), list):
            raise SourceUnavailable("failed", "搜索响应缺少视频列表，尚未导入任何推测数据")
        return result

    def refresh_video(self, video_id):
        """Fetch statistics by known ID without relying on keyword search position."""
        messages = {"needs_login": "来源需要登录，请更新登录后恢复",
                    "blocked": "来源需要验证，请处理后恢复",
                    "unavailable": "视频已删除、私密、付费或无法确认公开访问",
                    "failed": "视频详情更新失败，请稍后重试"}
        try:
            with httpx.Client(trust_env=False, timeout=45) as client:
                response = client.post(BRIDGE + "/detail", json={"video_id": video_id, "cookie": _cookie()})
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            raise SourceUnavailable("failed", messages["failed"])
        if result.get("status") != "ok":
            status = result.get("status") if result.get("status") in messages else "failed"
            reason = result.get("reason")
            if isinstance(reason, str) and reason in DETAIL_FAILURE_NOTES:
                raise SourceUnavailable(status, DETAIL_FAILURE_NOTES[reason], reason)
            raise SourceUnavailable(status, messages[status])
        if not isinstance(result.get("video"), dict):
            raise SourceUnavailable("failed", DETAIL_FAILURE_NOTES["empty_detail"], "empty_detail")
        return result["video"]


def normalize_video(video, keyword, sort_order, position, sampled_at):
    """Never infer a verified series from a title or a keyword."""
    video_id = str(video.get("aweme_id") or "")
    author, statistics, mix = video.get("author") or {}, video.get("statistics") or {}, video.get("mix_info") or {}
    account = str(author.get("uid") or author.get("sec_uid") or "")
    title = str(video.get("desc") or "").strip()
    if not video_id.isascii() or not video_id.isdigit() or not account or not title:
        raise ValueError("搜索条目缺少视频 ID、稳定作者 ID 或标题")
    try:
        published = datetime.fromtimestamp(float(video["create_time"]), timezone.utc).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise ValueError("搜索条目缺少有效发布时间")
    row = {"video_id": video_id, "original_url": f"https://www.douyin.com/video/{video_id}", "title": title,
           "author_id": account, "author_name": str(author.get("nickname") or account),
           "author_url": f"https://www.douyin.com/user/{quote(str(author['sec_uid']), safe='')}" if author.get("sec_uid") else "",
           "series_key": f"mix:{mix['mix_id']}" if mix.get("mix_id") and mix.get("mix_name") else f"pending-video:{video_id}",
           "series_title": str(mix.get("mix_name") or f"待确认作品 · {title[:60]}"),
           "collection_url": f"https://www.douyin.com/collection/{mix['mix_id']}" if mix.get("mix_id") else "",
           "published_at": iso(published), "sampled_at": iso(sampled_at), "searched_at": iso(sampled_at),
           "keyword": keyword, "sort_order": SORTS[sort_order][1] if sort_order else "", "position": str(position),
           "source_url": f"https://www.douyin.com/search/{quote(keyword, safe='')}?type=video"}
    for name, source_name in (("likes", "digg_count"), ("comments", "comment_count"), ("favorites", "collect_count"), ("shares", "share_count")):
        value = statistics.get(source_name)
        row[name] = int(value) if isinstance(value, (int, str)) and not isinstance(value, bool) and re.fullmatch(r"[0-9]{1,15}", str(value)) else None
    return row
