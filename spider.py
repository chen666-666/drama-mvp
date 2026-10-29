"""Collect public, server-rendered metadata from the Red Fruit drama ranking.

This module only reads publicly visible HTML metadata. It does not download
videos, access stream URLs, send custom headers, use cookies, log in, or try
to bypass a CAPTCHA, signature check, rate limit, or other access control.
"""

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from collector import collect_dramas


TARGET_URL = "https://hongguoduanju.com/rank/hot-drama"
MAX_DRAMAS = 6
REQUEST_TIMEOUT_SECONDS = 10

logger = logging.getLogger(__name__)


class RedFruitSpiderError(RuntimeError):
    """Raised when public page metadata cannot be collected safely."""


def normalize_text(value: str) -> str:
    """Collapse page whitespace without altering the visible content."""
    return " ".join(value.split())


def has_description_class(value: object) -> bool:
    """Match the page's descriptive paragraph without depending on its hash."""
    classes = value.split() if isinstance(value, str) else (value or [])
    return any("description" in str(class_name) for class_name in classes)


def has_verification_signal(soup: BeautifulSoup) -> bool:
    """Detect a visible access challenge and stop rather than bypass it."""
    visible_text = soup.get_text(" ", strip=True).lower()
    signals = ("captcha", "验证码", "人机验证", "安全验证", "请完成验证")
    return any(signal in visible_text for signal in signals)


def fetch_red_fruit_dramas(limit: int = MAX_DRAMAS) -> list[dict[str, str]]:
    """Fetch up to six server-rendered ranking entries from the public page.

    The request uses httpx defaults only: no custom User-Agent, headers,
    cookies, authentication, proxy, retry, or redirect-following behavior.
    """
    if not 1 <= limit <= MAX_DRAMAS:
        raise ValueError(f"抓取数量必须在 1 到 {MAX_DRAMAS} 条之间。")

    try:
        response = httpx.get(TARGET_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        logger.error("红果公开页请求超时，已停止：%s", exc)
        raise RedFruitSpiderError("红果公开页请求超时，已停止且不会重试。") from exc
    except httpx.HTTPError as exc:
        logger.error("红果公开页请求失败，已停止：%s", exc)
        raise RedFruitSpiderError(f"红果公开页请求失败：{exc}") from exc

    if response.status_code in {403, 429}:
        logger.error("红果公开页返回 HTTP %s，已停止且不会重试。", response.status_code)
        raise RedFruitSpiderError(
            f"红果公开页拒绝或限流请求（HTTP {response.status_code}），已停止。"
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RedFruitSpiderError(
            f"红果公开页返回异常状态码（HTTP {response.status_code}），已停止。"
        ) from exc

    soup = BeautifulSoup(response.text, "html.parser")
    if has_verification_signal(soup):
        raise RedFruitSpiderError("页面出现验证码或安全验证信号，已停止且不会绕过。")

    ranking = soup.find("ol", attrs={"aria-label": "红果热播榜"})
    if ranking is None:
        raise RedFruitSpiderError(
            "页面未直出红果热播榜内容，可能改为动态加载；已停止，不会尝试绕过。"
        )

    dramas: list[dict[str, str]] = []
    for article in ranking.find_all("article", limit=limit):
        title_node = article.find("h2", id=lambda value: value and value.startswith("rank-title-"))
        detail_link = article.find(
            "a",
            href=lambda value: value and value.startswith("/detail?series_id="),
        )
        cover = article.find("img", src=True)
        description_node = article.find("p", class_=has_description_class)

        title = normalize_text(title_node.get_text(" ", strip=True)) if title_node else ""
        official_url = urljoin(TARGET_URL, detail_link["href"]) if detail_link else ""
        cover_url = str(cover["src"]).strip() if cover else ""
        description = (
            normalize_text(description_node.get_text(" ", strip=True))
            if description_node
            else ""
        )

        if not all((title, official_url, cover_url, description)):
            logger.warning("跳过缺少标题、封面、简介或详情页链接的公开榜单条目。")
            continue

        dramas.append(
            {
                "title": title,
                "cover_url": cover_url,
                "description": description,
                "official_url": official_url,
            }
        )

    if not dramas:
        raise RedFruitSpiderError("未从公开直出 HTML 中解析到可用剧集数据，已停止。")
    return dramas


def run() -> int:
    """Collect public metadata, then delegate AI enrichment and storage."""
    return collect_dramas(fetch_red_fruit_dramas())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        count = run()
    except RedFruitSpiderError as exc:
        logger.error("采集已停止：%s", exc)
    else:
        print(f"已写入 {count} 部红果短剧的公开页面元数据到 drama.db")
