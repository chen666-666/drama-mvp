"""Collect publicly rendered Red Fruit drama ranking metadata responsibly."""

import logging
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from collector import collect_dramas


# Keep several public rankings so the collection is not biased toward one list.
PUBLIC_LIST_PAGES = (
    ("热门短剧", "https://hongguoduanju.com/rank/hot-drama"),
    ("热门真人", "https://hongguoduanju.com/rank/hot-real-drama"),
    ("热门 AI 剧", "https://hongguoduanju.com/rank/hot-ai-drama"),
    ("热门漫剧", "https://hongguoduanju.com/rank/hot-comic-drama"),
)
MAX_DRAMAS = 50
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 2

logger = logging.getLogger(__name__)


class RedFruitSpiderError(RuntimeError):
    """Raised when no public list page can be collected safely."""


def normalize_text(value: str) -> str:
    """Collapse page whitespace without altering visible content."""
    return " ".join(value.split())


def has_description_class(value: object) -> bool:
    classes = value.split() if isinstance(value, str) else (value or [])
    return any("description" in str(class_name).lower() for class_name in classes)


def has_verification_signal(soup: BeautifulSoup) -> bool:
    visible_text = soup.get_text(" ", strip=True).lower()
    signals = ("captcha", "验证码", "人机验证", "安全验证", "请完成验证")
    return any(signal in visible_text for signal in signals)


def get_ranking_list(soup: BeautifulSoup) -> Tag | None:
    """Return a server-rendered ranking list, if the page exposes one."""
    return soup.find("ol", attrs={"aria-label": lambda value: value and "热播榜" in value})


def category_from_ranking_name(page_name: str) -> str:
    """Provide a stable fallback when a card exposes no visible category."""
    if "漫剧" in page_name:
        return "漫剧"
    if "真人" in page_name:
        return "真人"
    if "AI" in page_name.upper():
        return "AI剧"
    return "热门"


def extract_category(article: Tag, page_name: str) -> str:
    """Read a visible category/genre chip from a card, then fall back to its list."""
    for attribute in ("data-category", "data-genre", "data-type"):
        value = article.get(attribute)
        if isinstance(value, str) and normalize_text(value):
            return normalize_text(value)

    for node in article.find_all(["span", "p", "a", "div"]):
        classes = " ".join(node.get("class", [])).lower()
        if not any(key in classes for key in ("category", "genre", "type", "tag", "label")):
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        # A category chip should be short; avoid treating a synopsis as a category.
        if 1 <= len(text) <= 12:
            return text

    return category_from_ranking_name(page_name)


def fetch_page_dramas(page_name: str, page_url: str) -> list[dict[str, str]]:
    """Fetch one public ranking page once and keep the two-second pause."""
    try:
        # No login, cookies, custom headers, media downloads, or retry loops.
        response = httpx.get(page_url, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        logger.warning("%s 请求超时，已跳过且不会重试。", page_name)
        return []
    except httpx.HTTPError as exc:
        logger.warning("%s 请求失败（%s），已跳过且不会重试。", page_name, exc)
        return []
    finally:
        # Runs after every request attempt, including failed requests.
        time.sleep(REQUEST_DELAY_SECONDS)

    if response.status_code in {403, 429} or response.is_error:
        logger.warning("%s 返回 HTTP %s，已跳过。", page_name, response.status_code)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    if has_verification_signal(soup):
        logger.warning("%s 出现验证信号，已跳过。", page_name)
        return []

    ranking = get_ranking_list(soup)
    if ranking is None:
        logger.warning("%s 未提供服务端渲染榜单，已跳过。", page_name)
        return []

    dramas: list[dict[str, str]] = []
    for article in ranking.find_all("article"):
        title_node = article.find("h2", id=lambda value: value and value.startswith("rank-title-"))
        detail_link = article.find("a", href=lambda value: value and value.startswith("/detail?series_id="))
        cover = article.find("img", src=True)
        description_node = article.find("p", class_=has_description_class)

        title = normalize_text(title_node.get_text(" ", strip=True)) if title_node else ""
        official_url = urljoin(page_url, detail_link["href"]) if detail_link else ""
        cover_url = str(cover["src"]).strip() if cover else ""
        description = normalize_text(description_node.get_text(" ", strip=True)) if description_node else ""

        if not all((title, official_url, cover_url, description)):
            logger.warning("%s 中有条目缺少公开元数据，已跳过。", page_name)
            continue

        dramas.append({
            "title": title,
            "cover_url": cover_url,
            "description": description,
            "official_url": official_url,
            "category": extract_category(article, page_name),
        })
    return dramas


def fetch_red_fruit_dramas() -> list[dict[str, str]]:
    """Aggregate, de-duplicate, and cap metadata from all configured rankings."""
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
            if len(dramas) >= MAX_DRAMAS:
                return dramas

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
