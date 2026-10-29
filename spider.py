"""Fetch a small, polite sample of publicly available TVmaze show data."""

import logging
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from collector import collect_dramas


TVMAZE_SHOWS_URL = "https://api.tvmaze.com/shows?page=0"
REQUEST_HEADERS = {
    "User-Agent": "drama-mvp-demo/1.0 (public-data demonstration; contact: local-demo)",
    "Accept": "application/json",
}
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 1

logger = logging.getLogger(__name__)


class TVMazeSpiderError(RuntimeError):
    """Raised when the public TVmaze endpoint cannot be used safely."""


class HTMLTextExtractor(HTMLParser):
    """Convert a small HTML summary fragment to plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def strip_html(html: str) -> str:
    """Remove HTML markup from a TVmaze summary."""
    parser = HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def fetch_tvmaze_dramas(limit: int = 6) -> list[dict[str, str]]:
    """Fetch up to ``limit`` shows from TVmaze's public first API page.

    This function makes one request only. It never attempts authentication,
    CAPTCHA handling, proxy rotation, retries, or rate-limit bypassing.
    """
    if not 1 <= limit <= 10:
        raise ValueError("演示抓取数量必须在 1 到 10 条之间。")

    try:
        with httpx.Client(
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = client.get(TVMAZE_SHOWS_URL)
    except httpx.HTTPError as exc:
        raise TVMazeSpiderError(f"请求 TVmaze 公开 API 失败：{exc}") from exc
    finally:
        # Keep at least one second between this request and any later request.
        time.sleep(REQUEST_DELAY_SECONDS)

    if response.status_code in {403, 429}:
        logger.error("TVmaze 返回 HTTP %s；已停止，不会重试。", response.status_code)
        raise TVMazeSpiderError(
            f"TVmaze 拒绝或限流请求（HTTP {response.status_code}），已停止。"
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise TVMazeSpiderError(
            f"TVmaze 返回异常状态码：HTTP {response.status_code}。"
        ) from exc

    try:
        shows: list[dict[str, Any]] = response.json()
    except ValueError as exc:
        raise TVMazeSpiderError("TVmaze 返回的内容不是有效 JSON。") from exc

    dramas: list[dict[str, str]] = []
    for show in shows[:limit]:
        image = show.get("image") or {}
        title = str(show.get("name") or "").strip()
        official_url = str(show.get("url") or "").strip()
        cover_url = str(image.get("medium") or "").strip()
        description = strip_html(str(show.get("summary") or ""))

        if not all((title, official_url, cover_url, description)):
            raise TVMazeSpiderError("TVmaze 返回的前几条数据缺少标题、链接、封面或简介。")

        dramas.append(
            {
                "title": title,
                "official_url": official_url,
                "cover_url": cover_url,
                "description": description,
            }
        )

    if len(dramas) != limit:
        raise TVMazeSpiderError("TVmaze 返回的剧集数量不足。")
    return dramas


def run() -> int:
    """Fetch six public TVmaze shows, enrich them with AI, and store them."""
    dramas = fetch_tvmaze_dramas(limit=6)
    count = collect_dramas(dramas)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    count = run()
    print(f"已抓取并写入 {count} 部 TVmaze 公开剧集到 drama.db")
