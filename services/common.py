from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

BEIJING = ZoneInfo("Asia/Shanghai")
KEYWORDS = ["AI短剧", "AI漫剧", "AIGC短剧", "AI真人短剧", "AI生成短剧", "AI动画短剧"]
METRICS = ("likes", "comments", "favorites", "shares")
METRIC_LABELS = {"likes": "点赞", "comments": "评论", "favorites": "收藏", "shares": "分享"}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_time(value, field="时间", required=True):
    if not value:
        if required:
            raise ValueError(f"{field}不能为空")
        return None
    try:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING)
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        raise ValueError(f"{field}格式错误，请使用 2026-10-31T12:00:00+08:00")


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def beijing(value):
    return value.replace(tzinfo=timezone.utc).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S 北京时间") if value else "未知"


def public_url(value, douyin=False, required=True):
    value = str(value or "").strip()
    if not value and not required:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请填写有效的公开 http/https 来源链接")
    if douyin and not (parsed.hostname == "douyin.com" or parsed.hostname.endswith(".douyin.com")):
        raise ValueError("候选原链接必须是抖音链接")
    return value


def integer(value, field, required=False, minimum=0):
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"{field}不能为空")
        return None
    value = str(value).strip()
    if not value.isascii() or not value.isdigit() or len(value) > 15:
        raise ValueError(f"{field}须为非负整数，未知请留空，不能填写 1.2万")
    number = int(value)
    if number < minimum:
        raise ValueError(f"{field}不能小于 {minimum}")
    return number
