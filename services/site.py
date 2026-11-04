"""Public, read-only website views. No task creation or private diagnostics."""
import os
import json
from functools import lru_cache
from sqlalchemy import select
from models import DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile, Series
from services import downloads


def daily_rankings(rankings):
    """One latest generation per Beijing cutoff day, across collection runs."""
    from services.common import parse_time, beijing
    days = {}
    for row in rankings:
        snapshot = json.loads(row.snapshot)
        day = beijing(parse_time(snapshot["cutoff_at"]))[:10]
        current = days.get(day)
        if current is None or (row.created_at, row.id) > (current.created_at, current.id):
            days[day] = row
    return [days[day] for day in sorted(days, reverse=True)]


def public_mode():
    return os.getenv("DRAMA_SITE_MODE", "local") == "public"


def public_snapshot(ranking):
    keys = ("id", "version", "run_name", "cutoff_at_beijing", "disclaimer", "shortfall", "scoring_mode", "title")
    item_keys = ("series_id", "series_title", "author_name", "ai_type", "ai_basis", "title_note", "title",
                 "position", "score", "selected", "original_url", "video_id")
    return {**{key: ranking.get(key) for key in keys},
            "items": [{key: item.get(key) for key in item_keys} for item in ranking["items"] if item["selected"]]}


def display_titles(session, snapshot):
    """Use current names on website views while preserving saved ranking history."""
    identifiers = {item["series_id"] for item in snapshot["items"]}
    current = {row.id: row for row in session.scalars(select(Series).where(Series.id.in_(identifiers)))}
    items = []
    for item in snapshot["items"]:
        series = current.get(item["series_id"])
        items.append({**item, "series_title": series.title, "title_note": series.title_note} if series else dict(item))
    return {**snapshot, "items": items}


def library_detail(session, ranking_id, snapshot):
    return {"ranking": public_snapshot(display_titles(session, {"id": ranking_id, **snapshot})),
            "catalogs": [public_catalog(session, ranking_id, item)
                         for item in snapshot["items"] if item["selected"]]}


def playable_path(session, media):
    if media.manual_status != "passed":
        raise ValueError("本站暂未收录此视频")
    job = session.get(DownloadJob, media.job_id)
    episode = session.get(DownloadEpisode, job.episode_id) if job else None
    catalog = session.get(DownloadCatalog, episode.catalog_id) if episode else None
    if not catalog or episode.status != "approved" or episode.access != "public" or job.status != "verified":
        raise ValueError("本站暂未收录此视频")
    downloads.catalog_item(session, catalog)
    if catalog.ranking_id is None and not catalog.published:
        raise ValueError("本站暂未收录此视频")
    path = downloads.inside(downloads.DATA, media.relative_path)
    stat = path.stat()
    if stat.st_size != media.size or checked_digest(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) != media.sha256:
        raise ValueError("本站暂未收录此视频")
    return path


@lru_cache(maxsize=256)
def checked_digest(path, size, modified, changed, inode):
    # Public media is mounted read-only. Any replacement or ordinary edit invalidates this cache.
    from services.media import digest
    from pathlib import Path
    return digest(Path(path))


def catalog_cover(session, ranking_id, series_id, catalog):
    """Keep an existing series poster when a new ranking has no thumbnail yet."""
    def available(row):
        return downloads.inside(downloads.DATA, f"thumbnails/catalog-{row.id}.jpg").is_file()

    if catalog and available(catalog):
        return f"/api/downloads/catalogs/{catalog.id}/cover"
    if ranking_id is not None:
        candidates = session.scalars(select(DownloadCatalog).where(
            DownloadCatalog.series_id == series_id, DownloadCatalog.ranking_id.is_not(None)
        ).order_by(DownloadCatalog.id.desc()))
        for candidate in candidates:
            if not available(candidate):
                continue
            try:
                downloads.selected_item(session, candidate.ranking_id, series_id)
            except ValueError:
                continue
            return f"/api/downloads/catalogs/{candidate.id}/cover"
    return None


def public_catalog(session, ranking_id, item, catalog=None):
    if catalog is None:
        catalog = session.scalar(select(DownloadCatalog).where(DownloadCatalog.ranking_id == ranking_id,
                                                              DownloadCatalog.series_id == item["series_id"]))
    episodes = []
    if catalog:
        rows = session.execute(select(DownloadEpisode, DownloadJob, MediaFile)
            .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
            .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id)
            .where(DownloadEpisode.catalog_id == catalog.id)
            .order_by(DownloadEpisode.episode_number, DownloadEpisode.id)).all()
        shared_sources = downloads.preload_reused_resources(session, [episode for episode, _, _ in rows])
        for episode, job, media in rows:
            if episode.reused_media_id:
                job, media = downloads.reused_resource(session, episode, catalog)
            if not media or episode.status != "approved" or episode.access != "public":
                continue
            try:
                playable_path(session, media)
            except (ValueError, OSError):
                continue
            episodes.append({"id": episode.id, "video_id": episode.video_id, "title": episode.title,
                "episode_number": episode.episode_number, "episode_range": episode.episode_range, "duration": media.duration,
                "url": f"/api/library/media/{media.id}", "download_url": f"/api/library/media/{media.id}/download",
                "representative": ranking_id is not None and episode.video_id == item["video_id"]})
    numbers = [row["episode_number"] for row in episodes if row["episode_number"]]
    return {"series_id": item["series_id"],
        "cover_url": catalog_cover(session, ranking_id, item["series_id"], catalog),
        "episodes": episodes, "summary": {"public": len(episodes), "verified": len(episodes),
            "full_series": "全剧完整性未确认" if not catalog or not catalog.checked or not catalog.total_episodes or
            len(episodes) != catalog.total_episodes or sorted(numbers) != list(range(1, catalog.total_episodes + 1))
            else "已核对目录范围内全部正片，人工抽检完成"}}


def manual_detail(session, catalog, *, public=True):
    if catalog.ranking_id is not None or (public and not catalog.published):
        raise ValueError("本站暂未收录此作品")
    item = downloads.catalog_item(session, catalog)
    public_keys = ("series_id", "series_title", "title_note", "author_name", "ai_type", "ai_basis",
                   "selected", "video_id", "title", "original_url")
    return {"ranking": {"id": None, "kind": "manual", "catalog_id": catalog.id,
            "title": "指定收录", "items": [{key: item[key] for key in public_keys} if public else item]},
            "catalogs": [public_catalog(session, None, item, catalog) if public else downloads.catalog_detail(session, catalog)]}


def manual_library(session):
    rows = []
    for catalog in session.scalars(select(DownloadCatalog).where(DownloadCatalog.ranking_id.is_(None),
                                                                DownloadCatalog.published.is_(True)).order_by(DownloadCatalog.id.desc())):
        try:
            detail = manual_detail(session, catalog)
        except ValueError:
            continue
        if detail["catalogs"][0]["episodes"]:
            rows.append({"id": catalog.id, "item": detail["ranking"]["items"][0], "catalog": detail["catalogs"][0]})
    return rows
