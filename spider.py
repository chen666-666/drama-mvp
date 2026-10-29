"""Aggregate public, server-rendered Red Fruit drama metadata responsibly.

Only publicly visible HTML metadata is collected: title, cover image URL,
summary, and public detail-page URL. This module never downloads media,
accesses stream URLs, uses cookies, sends custom headers, logs in, or tries
to bypass verification, rate limits, signatures, or other access controls.
"""

import logging
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from collector import collect_dramas


PUBLIC_LIST_PAGES = (
    ("红果热播榜", "https://hongguoduanju.com/rank/hot-drama"),
    ("真人剧热播榜", "https://hongguoduanju.com/rank/hot-real-drama"),
    ("AI剧热播榜", "https://hongguoduanju.com/rank/hot-ai-drama"),
    ("漫剧热播榜", "https://hongguoduanju.com/rank/hot-comic-drama"),
)
MAX_DRAMAS_PER_PAGE = 6
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 2

logger = logging.getLogger(__name__)


class RedFruitSpiderError(RuntimeError):
    """Raised when no public list page can be collected safely."""


def normalize_text(value: str) -> str:
    """Collapse page whitespace without altering the visible content."""
    return " ".join(value.split())


def has_description_class(value: object) -> bool:
    """Match the page description without depending on its generated suffix."""
    classes = value.split() if isinstance(value, str) else (value or [])
    return any("description" in str(class_name) for class_name in classes)


def has_verification_signal(soup: BeautifulSoup) -> bool:
    """Detect a visible access challenge and stop that page immediately."""
    visible_text = soup.get_text(" ", strip=True).lower()
    signals = ("captcha", "验证码", "人机验证", "安全验证", "请完成验证")
    return any(signal in visible_text for signal in signals)


def get_ranking_list(soup: BeautifulSoup):
    """Return a server-rendered ranking list, if the page exposes one."""
    return soup.find(
        "ol",
        attrs={"aria-label": lambda value: value and "热播榜" in value},
    )


def fetch_page_dramas(page_name: str, page_url: str) -> list[dict[str, str]]:
    """Fetch one public page once, then wait two seconds before any next page.

    A blocked, challenged, dynamic, or failed page is skipped. No retry is
    attempted, so the remaining public pages can still be processed politely.
    """
    try:
        # Intentionally use httpx defaults: no custom headers, cookies, or auth.
        response = httpx.get(page_url, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        logger.warning("%s 请求超时，已跳过且不会重试。", page_name)
        return []
    except httpx.HTTPError as exc:
        logger.warning("%s 请求失败（%s），已跳过且不会重试。", page_name, exc)
        return []
    finally:
        # This runs once after every page request, including request failures.
        time.sleep(REQUEST_DELAY_SECONDS)

    if response.status_code in {403, 429}:
        logger.warning("%s 返回 HTTP %s，已跳过且不会重试。", page_name, response.status_code)
        return []
    if response.is_error:
        logger.warning("%s 返回 HTTP %s，已跳过。", page_name, response.status_code)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    if has_verification_signal(soup):
        logger.warning("%s 出现验证码或安全验证信号，已跳过。", page_name)
        return []

    ranking = get_ranking_list(soup)
    if ranking is None:
        logger.warning("%s 未直出榜单内容，可能为动态加载；已跳过。", page_name)
        return []

    dramas: list[dict[str, str]] = []
    for article in ranking.find_all("article", limit=MAX_DRAMAS_PER_PAGE):
        title_node = article.find("h2", id=lambda value: value and value.startswith("rank-title-"))
        detail_link = article.find(
            "a",
            href=lambda value: value and value.startswith("/detail?series_id="),
        )
        cover = article.find("img", src=True)
        description_node = article.find("p", class_=has_description_class)

        title = normalize_text(title_node.get_text(" ", strip=True)) if title_node else ""
        official_url = urljoin(page_url, detail_link["href"]) if detail_link else ""
        cover_url = str(cover["src"]).strip() if cover else ""
        description = (
            normalize_text(description_node.get_text(" ", strip=True))
            if description_node
            else ""
        )

        if not all((title, official_url, cover_url, description)):
            logger.warning("%s 中有条目缺少公开元数据，已跳过。", page_name)
            continue

        dramas.append(
            {
                "title": title,
                "cover_url": cover_url,
                "description": description,
                "official_url": official_url,
            }
        )
    return dramas


def fetch_red_fruit_dramas() -> list[dict[str, str]]:
    """Aggregate and de-duplicate publicly rendered metadata from all pages."""
    dramas: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for page_name, page_url in PUBLIC_LIST_PAGES:
        for drama in fetch_page_dramas(page_name, page_url):
            normalized_title = drama["title"].casefold()
            if drama["official_url"] in seen_urls or normalized_title in seen_titles:
                logger.info("跳过重复短剧：%s", drama["title"])
                continue
            seen_urls.add(drama["official_url"])
            seen_titles.add(normalized_title)
            dramas.append(drama)

    if not dramas:
        raise RedFruitSpiderError("没有从任何公开直出榜单页获得可用数据，已停止。")
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
