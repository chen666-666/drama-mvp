"""Manual source adapter. Links create incomplete records; no unverified scraping."""
import csv
import io
import re
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from models import Author, Episode, MetricSnapshot, SearchHit, Series
from services.common import METRICS, METRIC_LABELS, integer, parse_time, public_url, utcnow

CSV_FIELDS = [
    "video_id", "original_url", "title", "series_key", "series_title", "author_id", "author_name",
    "author_url", "collection_url", "ai_type", "episode_number", "published_at", "sampled_at",
    "likes", "comments", "favorites", "shares", "missing_reason", "keyword", "sort_order",
    "position", "source_url", "searched_at", "official_rank",
]


def extract_video_id(url):
    parsed = urlparse(url)
    match = re.search(r"/(?:video|note)/(\d+)(?:/|$)", parsed.path)
    if match:
        return match[1]
    return (parse_qs(parsed.query).get("modal_id") or [None])[0]


def add_hit(session, run_id, episode_id, row, fallback_url):
    searched_at = parse_time(row.get("searched_at"), "检索时间", required=False) or utcnow()
    if searched_at > utcnow():
        raise ValueError("检索时间不能在未来")
    session.add(SearchHit(
        run_id=run_id, episode_id=episode_id,
        source_url=public_url(row.get("source_url") or fallback_url),
        keyword=str(row.get("keyword") or "").strip(), sort_order=str(row.get("sort_order") or "").strip(),
        position=str(row.get("position") or "").strip(), searched_at=searched_at,
        official_rank=integer(row.get("official_rank"), "官方名次", minimum=1),
    ))


def import_links(session, run_id, links, provenance):
    imported = 0
    errors = []
    for line, raw in enumerate(links.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            with session.begin_nested():
                found = re.search(r"https?://[^\s<>]+", raw)
                url = public_url(found.group(0).rstrip("，。；,;！!") if found else raw, douyin=True)
                video_id = extract_video_id(url)
                episode = None
                if video_id:
                    if not video_id.isdigit() or len(video_id) > 100:
                        raise ValueError("视频 ID 无效")
                    episode = session.scalar(select(Episode).where(Episode.video_id == video_id))
                    if episode is None:
                        episode = Episode(video_id=video_id, original_url=url)
                        session.add(episode)
                        session.flush()
                add_hit(session, run_id, episode.id if episode else None, provenance, url)
                session.flush()
                imported += 1
        except ValueError as exc:
            errors.append({"line": line, "message": str(exc)})
    return {"imported": imported, "errors": errors, "message": "链接已登记；短链接、作者及合集链接需补充正片 CSV，未自动采集详情。"}


def import_row(session, run_id, row):
    required = ("video_id", "original_url", "title", "series_key", "series_title", "author_id", "author_name", "published_at", "sampled_at")
    for field in required:
        if not str(row.get(field) or "").strip():
            raise ValueError(f"{field} 不能为空")
    url = public_url(row["original_url"], douyin=True)
    video_id = str(row["video_id"]).strip()
    if not video_id.isascii() or not video_id.isdigit() or len(video_id) > 100:
        raise ValueError("video_id 必须为抖音数字视频 ID")
    parsed_id = extract_video_id(url)
    if parsed_id and video_id != parsed_id:
        raise ValueError("video_id 与原链接中的视频 ID 不一致")
    published_at = parse_time(row["published_at"], "发布时间")
    sampled_at = parse_time(row["sampled_at"], "采样时间")
    if published_at > sampled_at or sampled_at > utcnow():
        raise ValueError("应满足 发布时间 ≤ 采样时间 ≤ 当前时间")
    metrics = {field: integer(row.get(field), METRIC_LABELS[field]) for field in METRICS}
    author_url = public_url(row.get("author_url"), douyin=True, required=False)
    collection_url = public_url(row.get("collection_url"), douyin=True, required=False)
    author_key = str(row["author_id"]).strip()
    series_key = str(row["series_key"]).strip()
    author = session.scalar(select(Author).where(Author.account_id == author_key, Author.platform == "douyin"))
    if author is None:
        author = Author(account_id=author_key, name=row["author_name"].strip(), homepage_url=author_url)
        session.add(author)
        session.flush()
    else:
        author.name = row["author_name"].strip()
        if author_url:
            author.homepage_url = author_url
    series = session.scalar(select(Series).where(Series.author_id == author.id, Series.series_key == series_key))
    if series is None:
        series = Series(author_id=author.id, series_key=series_key, title=row["series_title"].strip(),
                        collection_url=collection_url, ai_type=str(row.get("ai_type") or "").strip())
        session.add(series)
        session.flush()
    elif series.title != row["series_title"].strip():
        raise ValueError("同一作者的 series_key 已对应其他剧名，请核实作品归属后使用归并功能")
    else:
        ai_type = str(row.get("ai_type") or "").strip()
        if ai_type and series.ai_type != ai_type:
            series.ai_type, series.review_status = ai_type, "pending"
        if collection_url:
            series.collection_url = collection_url
    episode = session.scalar(select(Episode).where(Episode.video_id == video_id, Episode.platform == "douyin"))
    if episode is None:
        episode = Episode(video_id=video_id, original_url=url)
        session.add(episode)
    elif episode.series_id is not None and episode.series_id != series.id:
        raise ValueError("该视频已归属其他作品，请先人工归并或纠正归属")
    number = integer(row.get("episode_number"), "集号", minimum=1)
    changed = episode.series_id != series.id or episode.title != row["title"].strip() or episode.published_at != published_at or episode.episode_number != number or episode.original_url != url
    episode.series_id, episode.title, episode.original_url = series.id, row["title"].strip(), url
    episode.published_at, episode.episode_number = published_at, number
    if changed:
        episode.review_status = "pending"
    session.flush()
    missing = "、".join(METRIC_LABELS[field] for field, value in metrics.items() if value is None)
    session.add(MetricSnapshot(run_id=run_id, episode_id=episode.id, sampled_at=sampled_at, **metrics,
                              missing_reason=str(row.get("missing_reason") or (f"未取得 {missing}" if missing else "")).strip()))
    add_hit(session, run_id, episode.id, row, url)
    session.flush()


def import_csv(session, run_id, content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames or not {"video_id", "series_key", "author_id", "published_at", "sampled_at"}.issubset(reader.fieldnames):
        raise ValueError("CSV 表头不完整，请下载并使用模板；文件编码应为 UTF-8")
    imported, errors = 0, []
    try:
        for row in reader:
            line = reader.line_num
            if None in row:
                errors.append({"line": line, "message": "列数超出表头，请检查逗号或使用带引号的 CSV 单元格"})
                continue
            if all(not str(value or "").strip() for value in row.values()):
                continue
            try:
                with session.begin_nested():
                    import_row(session, run_id, row)
                imported += 1
            except ValueError as exc:
                errors.append({"line": line, "message": str(exc)})
    except csv.Error as exc:
        errors.append({"line": reader.line_num, "message": f"CSV 格式错误：{exc}"})
    return {"imported": imported, "errors": errors}
