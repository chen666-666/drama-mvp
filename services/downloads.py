"""Version-bound episode catalogs and durable download control."""
import json
import os
from pathlib import Path
import re

from sqlalchemy import select

from models import Author, DownloadCatalog, DownloadEpisode, DownloadJob, Episode, MediaFile, RankingRun, Series
from services.common import iso, public_url, utcnow
from services.episode_numbers import covered_numbers, parse_episode_number

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("DRAMA_DATA_DIR", str(ROOT / "data"))).resolve()
VIDEOS = DATA / "videos"
STATES = {"queued": "待处理", "downloading": "下载中", "validating": "待校验", "verified": "校验通过",
          "failed": "失败", "paused": "已暂停", "cancelled": "已取消"}


def selected_item(session, ranking_id, series_id):
    ranking = session.get(RankingRun, ranking_id)
    if not ranking:
        raise ValueError("榜单版本不存在")
    item = next((row for row in json.loads(ranking.snapshot)["items"] if row["selected"] and row["series_id"] == series_id), None)
    if not item:
        raise ValueError("只能管理该榜单版本实际入选的作品")
    return item


def catalog_for(session, ranking_id, series_id):
    item = selected_item(session, ranking_id, series_id)
    catalog = session.scalar(select(DownloadCatalog).where(DownloadCatalog.ranking_id == ranking_id, DownloadCatalog.series_id == series_id))
    if catalog:
        inherit_media(session, catalog)
        return catalog
    catalog = DownloadCatalog(ranking_id=ranking_id, series_id=series_id)
    session.add(catalog)
    session.flush()
    session.add(DownloadEpisode(catalog_id=catalog.id, video_id=item["video_id"], title=item["title"],
        original_url=item["original_url"], episode_number=item.get("episode_number"), episode_range=item.get("episode_range"), status="approved", access="public",
        evidence_url=item["original_url"], note="继承此榜单版本已核验的代表集；下载前再次检查可访问性"))
    # Carry every known episode, including older ones outside the ranking window.
    for episode in session.scalars(select(Episode).where(Episode.series_id == series_id)):
        representative = episode.video_id == item["video_id"]
        if representative:
            continue
        approved = representative or (episode.review_status == "approved" and episode.content_kind != "compilation")
        session.add(DownloadEpisode(catalog_id=catalog.id, video_id=episode.video_id, title=episode.title,
            original_url=episode.original_url, episode_number=episode.episode_number, episode_range=episode.episode_range,
            status="approved" if approved else "pending", access="public" if approved else "unknown",
            evidence_url=episode.original_url if approved else "",
            note="从榜单代表集或已核验正片继承；下载前再次检查可访问性" if approved else "来自已发现视频，需确认正片归属"))
    session.flush()
    inherit_media(session, catalog, inherit_scope=True)
    return catalog


def catalog_item(session, catalog):
    if catalog.ranking_id is not None:
        return selected_item(session, catalog.ranking_id, catalog.series_id)
    series = session.get(Series, catalog.series_id)
    if not series or series.review_status != "approved":
        raise ValueError("指定收录作品须先通过作品核验")
    author = session.get(Author, series.author_id)
    seed = session.scalar(select(Episode).where(Episode.series_id == series.id,
                          Episode.review_status == "approved", Episode.content_kind != "compilation").order_by(Episode.id))
    return {"series_id": series.id, "series_title": series.title, "title_note": series.title_note,
            "author_name": author.name, "author_account_id": author.account_id,
            "author_url": catalog.source_url or author.homepage_url, "collection_url": "",
            "ai_type": series.ai_type, "ai_basis": series.ai_basis, "selected": True,
            "video_id": seed.video_id if seed else "", "title": seed.title if seed else series.title,
            "original_url": seed.original_url if seed else (catalog.source_url or author.homepage_url)}


def manual_catalog(session, series_id, source_url=""):
    from urllib.parse import urlparse
    series = session.get(Series, series_id)
    if not series or series.review_status != "approved":
        raise ValueError("请选择已通过作品核验的已有短剧")
    author = session.get(Author, series.author_id)
    url = public_url(source_url or author.homepage_url, douyin=True)
    if not re.fullmatch(r"/user/[^/]+/?", urlparse(url).path):
        raise ValueError("请填写该原作者的抖音主页链接")
    catalog = session.scalar(select(DownloadCatalog).where(DownloadCatalog.ranking_id.is_(None),
                                                          DownloadCatalog.series_id == series_id))
    if catalog:
        if catalog.source_url != url:
            catalog.cursor, catalog.source, catalog.has_more = "0", "", True
        catalog.source_url = url
        return catalog
    catalog = DownloadCatalog(series_id=series_id, source_url=url)
    session.add(catalog)
    session.flush()
    for episode in session.scalars(select(Episode).where(Episode.series_id == series_id)):
        approved = episode.review_status == "approved" and episode.content_kind != "compilation"
        session.add(DownloadEpisode(catalog_id=catalog.id, video_id=episode.video_id, title=episode.title or episode.video_id,
            original_url=episode.original_url, episode_number=episode.episode_number, episode_range=episode.episode_range,
            status="approved" if approved else "pending", access="public" if approved else "unknown",
            evidence_url=episode.original_url if approved else "", note="从本作品已有资料收录，保留来源核验状态"))
    session.flush()
    return catalog


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:70]
    if not value or value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "作品_" + value
    return value


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("文件路径超出资料目录")
    return path


def media_dict(media):
    if not media:
        return None
    exists = inside(DATA, media.relative_path).is_file()
    return {"id": media.id, "relative_path": media.relative_path, "size": media.size, "duration": media.duration,
            "format": media.format, "sha256": media.sha256, "tracks": json.loads(media.tracks),
            "validation_note": media.validation_note, "manual_status": media.manual_status, "manual_note": media.manual_note,
            "checked_at": iso(media.checked_at), "created_at": iso(media.created_at), "exists": exists}


def job_dict(job):
    if not job:
        return None
    return {"id": job.id, "status": job.status, "status_label": STATES[job.status], "trial": job.trial,
            "attempts": job.attempts, "max_attempts": job.max_attempts, "message": job.message, "tool": job.tool,
            "bytes_downloaded": job.bytes_downloaded, "updated_at": iso(job.updated_at)}


def preload_reused_resources(session, episodes):
    """Hold shared source objects for this view, avoiding per-episode database reads."""
    identifiers = {row.reused_media_id for row in episodes if row.reused_media_id}
    if not identifiers:
        return []
    media = list(session.scalars(select(MediaFile).where(MediaFile.id.in_(identifiers))))
    jobs = list(session.scalars(select(DownloadJob).where(DownloadJob.id.in_({row.job_id for row in media}))))
    originals = list(session.scalars(select(DownloadEpisode).where(DownloadEpisode.id.in_({row.episode_id for row in jobs}))))
    catalogs = list(session.scalars(select(DownloadCatalog).where(DownloadCatalog.id.in_(
        {row.catalog_id for row in originals} | {row.catalog_id for row in episodes}))))
    rankings = list(session.scalars(select(RankingRun).where(RankingRun.id.in_({row.ranking_id for row in catalogs}))))
    return [*media, *jobs, *originals, *catalogs, *rankings]


def reused_resource(session, episode, catalog=None):
    """Resolve a direct reference, rechecking both ownership and the original review."""
    if not episode.reused_media_id or episode.status != "approved" or episode.access != "public":
        return None, None
    media = session.get(MediaFile, episode.reused_media_id)
    if not media or media.sha256 != episode.reused_sha256:
        return None, None
    job = session.get(DownloadJob, media.job_id)
    source = session.get(DownloadEpisode, job.episode_id) if job else None
    origin = session.get(DownloadCatalog, source.catalog_id) if source else None
    catalog = catalog or session.get(DownloadCatalog, episode.catalog_id)
    if (not origin or not catalog or origin.series_id != catalog.series_id or
            source.video_id != episode.video_id or source.id == episode.id):
        return None, None
    if origin.ranking_id is None or catalog.ranking_id is None or origin.ranking_id >= catalog.ranking_id:
        return None, None
    old_ranking = session.get(RankingRun, origin.ranking_id)
    new_ranking = session.get(RankingRun, catalog.ranking_id)
    if not old_ranking or not new_ranking or old_ranking.created_at > new_ranking.created_at:
        return None, None
    try:
        from services.site import playable_path
        catalog_item(session, catalog)
        playable_path(session, media)
    except (ValueError, OSError):
        return None, None
    return job, media


def reusable_sources(session, ranking_id, series_id):
    """Only earlier rankings of this exact series may supply reviewed media."""
    ranking = session.get(RankingRun, ranking_id)
    rows = session.execute(select(DownloadEpisode, DownloadJob, MediaFile, DownloadCatalog)
        .join(DownloadCatalog, DownloadCatalog.id == DownloadEpisode.catalog_id)
        .join(RankingRun, RankingRun.id == DownloadCatalog.ranking_id)
        .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
        .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id)
        .where(DownloadCatalog.series_id == series_id, RankingRun.id < ranking_id,
               RankingRun.created_at <= ranking.created_at)
        .order_by(RankingRun.created_at.desc(), RankingRun.id.desc(), DownloadEpisode.id.desc()))
    sources, decided = [], set()
    for episode, job, media, catalog in rows:
        if episode.video_id in decided:
            continue
        # Empty default rows from a later, unprepared version do not hide older resources.
        if not job and not episode.reused_media_id and episode.review_history == "[]":
            continue
        decided.add(episode.video_id)
        if episode.reused_media_id:
            job, media = reused_resource(session, episode, catalog)
        if not media:
            continue
        try:
            from services.site import playable_path
            if episode.status != "approved" or episode.access != "public":
                continue
            playable_path(session, media)
        except (ValueError, OSError):
            continue
        sources.append((episode, media, catalog))
    return sources


def inherit_media(session, catalog, inherit_scope=False):
    """Write only from authorized preparation/startup; public reads never create rows."""
    if catalog.ranking_id is None:
        return 0
    existing = {row.video_id: row for row in session.scalars(select(DownloadEpisode).where(
        DownloadEpisode.catalog_id == catalog.id))}
    jobs = {row.episode_id: row for row in session.scalars(select(DownloadJob).join(DownloadEpisode).where(
        DownloadEpisode.catalog_id == catalog.id))}
    count = 0
    sources = reusable_sources(session, catalog.ranking_id, catalog.series_id)
    for source, media, origin in sources:
        target = existing.get(source.video_id)
        history = json.loads(target.review_history) if target else []
        if target:
            job = jobs.get(target.id)
            local_media = session.scalar(select(MediaFile).where(MediaFile.job_id == job.id)) if job else None
            if (target.reused_media_id or target.status == "excluded" or target.access not in ("unknown", "public") or
                    history and target.status != "approved" or
                    job and job.status in ("downloading", "validating") or
                    local_media and local_media.manual_status in ("passed", "rejected")):
                continue
            if job:
                job.status, job.message, job.updated_at = "cancelled", "已有同剧同视频的已审核资源，停止重复准备", utcnow()
        if not target:
            target = DownloadEpisode(catalog_id=catalog.id, video_id=source.video_id)
            session.add(target)
        if not history:
            for key in ("title", "original_url", "episode_number", "episode_range", "source_duration", "status", "access", "evidence_url"):
                setattr(target, key, getattr(source, key))
        target.reused_media_id, target.reused_sha256 = media.id, media.sha256
        target.note = f"沿用榜单 {origin.ranking_id} 已通过人工审核的资源，无需重复下载和审核"
        original_job = session.get(DownloadJob, media.job_id)
        original_episode = session.get(DownloadEpisode, original_job.episode_id)
        target.review_history = json.dumps([*history, {"at": iso(utcnow()), "action": "reuse",
            "source_catalog_id": origin.id, "source_episode_id": source.id,
            "source_media_id": media.id, "sha256": media.sha256,
            "source_review_history": json.loads(original_episode.review_history)}], ensure_ascii=False)
        count += 1
    # Preserve a checked scope only when every original row is carried without additions.
    if count and inherit_scope and sources:
        origin = sources[0][2]
        source_videos = set(session.scalars(select(DownloadEpisode.video_id).where(DownloadEpisode.catalog_id == origin.id)))
        session.flush()
        target_videos = set(session.scalars(select(DownloadEpisode.video_id).where(DownloadEpisode.catalog_id == catalog.id)))
        if origin.checked and source_videos == target_videos:
            catalog.checked, catalog.total_episodes, catalog.note = origin.checked, origin.total_episodes, origin.note
    session.flush()
    return count


def inherit_ranking_media(session, ranking_id, series_id=None):
    ranking = session.get(RankingRun, ranking_id)
    count = 0
    for item in json.loads(ranking.snapshot)["items"]:
        if series_id is not None and item["series_id"] != series_id:
            continue
        if item["selected"] and reusable_sources(session, ranking_id, item["series_id"]):
            catalog = catalog_for(session, ranking_id, item["series_id"])
            count += sum(bool(row.reused_media_id) for row in session.scalars(select(DownloadEpisode).where(
                DownloadEpisode.catalog_id == catalog.id)))
    return count


def backfill_reused_media(session, after_ranking_id=None, series_id=None):
    statement = select(RankingRun.id).order_by(RankingRun.id)
    if after_ranking_id is not None:
        statement = statement.where(RankingRun.id > after_ranking_id)
    for ranking_id in list(session.scalars(statement)):
        inherit_ranking_media(session, ranking_id, series_id)


def catalog_detail(session, catalog, *, include_discovery=False):
    from services.episode_discovery import view as discovery_view
    rows = []
    entries = session.execute(select(DownloadEpisode, DownloadJob, MediaFile)
        .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
        .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id)
        .where(DownloadEpisode.catalog_id == catalog.id)
        .order_by(DownloadEpisode.episode_number, DownloadEpisode.id)).all()
    shared_sources = preload_reused_resources(session, [episode for episode, _, _ in entries])
    for episode, job, media in entries:
        reused = False
        if episode.reused_media_id:
            job, media = reused_resource(session, episode, catalog)
            reused = bool(media)
        deliverable = (media and job.status == "verified" and episode.status == "approved" and
                       episode.access == "public" and media.manual_status != "rejected" and
                       inside(DATA, media.relative_path).is_file() and inside(DATA, media.relative_path).stat().st_size == media.size)
        rows.append({key: getattr(episode, key) for key in ("id", "video_id", "title", "original_url", "episode_number", "episode_range", "source_duration", "status", "access", "note", "evidence_url")} |
                    {"review_history": json.loads(episode.review_history), "job": job_dict(job), "media": media_dict(media),
                     "reused": reused, "reuse_unavailable": bool(episode.reused_media_id) and not reused,
                     "file_url": f"/api/downloads/media/{media.id}/download" if deliverable else None})
    eligible = [row for row in rows if row["status"] == "approved" and row["access"] == "public"]
    valid = [row for row in eligible if row["job"] and row["job"]["status"] == "verified" and row["media"] and row["media"]["exists"] and row["media"]["manual_status"] != "rejected"]
    checked = [row for row in valid if row["media"]["manual_status"] == "passed"]
    complete = bool(eligible) and len(valid) == len(eligible)
    numbers = [row["episode_number"] for row in rows if row["status"] != "excluded" and row["episode_number"]]
    coverage = covered_numbers([row for row in rows if row["status"] != "excluded"])
    eligible_numbers = [row["episode_number"] for row in eligible if row["episode_number"]]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    # Only claim completeness when known numbers cover the entire declared scope.
    numbered_complete = catalog.total_episodes and sorted(eligible_numbers) == list(range(1, catalog.total_episodes + 1))
    return {"id": catalog.id, "ranking_id": catalog.ranking_id, "series_id": catalog.series_id, "checked": catalog.checked,
            "published": catalog.published, "source_url": catalog.source_url, "source": catalog.source,
            "has_more": catalog.has_more, "total_episodes": catalog.total_episodes, "note": catalog.note, "cover_url": catalog.cover_url,
            "updated_at": iso(catalog.updated_at), "episodes": rows,
            **({"discovery": discovery_view(session, catalog)} if include_discovery else {}),
            "summary": {"discovered": len(rows), "public": len(eligible), "queued": sum(bool(row["job"]) for row in rows),
                        "downloaded": sum(bool(row["media"] and row["media"]["exists"]) for row in rows), "verified": len(valid),
                        "manual_passed": len(checked), "reused": sum(row["reused"] for row in checked),
                        "trial_passed": any(row["job"]["trial"] or row["reused"] for row in checked),
                        "pending": sum(row["status"] == "pending" for row in rows),
                        "completion": "已发现公开正片已下载" if complete else "公开正片尚未下载完",
                        "duplicate_numbers": duplicates,
                        "missing_numbers": [number for number in range(1, min(catalog.total_episodes or max(coverage, default=0), 10000) + 1) if number not in coverage],
                        "full_series": "已核对目录范围内全部正片，人工抽检完成" if catalog.checked and numbered_complete and len(checked) == catalog.total_episodes and len(rows) == len(eligible) else "全剧完整性未确认"}}


def add_links(session, catalog, links):
    from sources.manual import extract_video_id
    count = 0
    for line in links.splitlines():
        if not line.strip():
            continue
        match = re.search(r'https?://[^\s<>"，。]+', line)
        url = public_url(match.group(0) if match else line.strip(), douyin=True)
        # The import parser also accepts profile/short links. Downloads need canonical video IDs.
        video_id = extract_video_id(url)
        if not video_id or not re.fullmatch(r"[0-9]{1,100}", video_id):
            raise ValueError("剧集目录请填写 https://www.douyin.com/video/视频ID 格式的正片链接")
        if not session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id, DownloadEpisode.video_id == video_id)):
            session.add(DownloadEpisode(catalog_id=catalog.id, video_id=video_id, original_url=f"https://www.douyin.com/video/{video_id}", title="待核对正片 · " + video_id))
            session.flush()
            count += 1
    catalog.checked, catalog.updated_at = False, utcnow()
    return {"added": count}


def review_episode(session, episode, data):
    if data["status"] not in ("approved", "pending", "excluded") or data["access"] not in ("public", "unknown", "private", "paid", "unavailable"):
        raise ValueError("核验状态或访问范围无效")
    if not data["note"].strip():
        raise ValueError("请填写归属依据或缺集、访问原因")
    evidence = public_url(data["evidence_url"], douyin=True)
    if data["status"] == "approved" and (data["access"] != "public" or not all(data.get(key) for key in ("ownership", "main_episode", "not_duplicate"))):
        raise ValueError("通过前须确认原作者同剧正片、公开可访问且非重复上传")
    job = session.scalar(select(DownloadJob).where(DownloadJob.episode_id == episode.id))
    if job and job.status in ("downloading", "validating"):
        raise ValueError("请先暂停当前任务，再更改目录核验")
    number, span = parse_episode_number(data.get("episode_range") or data.get("episode_number"))
    previous = {key: getattr(episode, key) for key in ("status", "access", "note", "episode_number", "episode_range")}
    for key in ("title", "status", "access", "note"):
        setattr(episode, key, data[key])
    episode.episode_number, episode.episode_range = number, span
    episode.evidence_url = evidence
    episode.review_history = json.dumps([*json.loads(episode.review_history), {"at": iso(utcnow()), "previous": previous,
        "status": episode.status, "episode_number": number, "episode_range": span,
        "note": episode.note, "evidence_url": evidence}], ensure_ascii=False)
    if job and job.status == "queued" and episode.status != "approved":
        job.status, job.message = "paused", "目录核验变更，请核对后恢复"
    catalog = session.get(DownloadCatalog, episode.catalog_id)
    catalog.checked, catalog.updated_at = False, utcnow()


def review_episodes(session, catalog, data):
    """Apply one explicit directory decision atomically, retaining each source and number."""
    catalog_item(session, catalog)
    identifiers = list(dict.fromkeys(data["episode_ids"]))
    if not identifiers or len(identifiers) > 1000 or any(identifier <= 0 for identifier in identifiers):
        raise ValueError("请选择本目录内的 1—1000 条视频")
    if data["status"] not in ("approved", "excluded"):
        raise ValueError("批量核验仅支持确认正片或排除")
    if data["status"] == "approved" and not data.get("public_access"):
        raise ValueError("请确认所选每条视频均公开可访问")
    episodes = list(session.scalars(select(DownloadEpisode).where(
        DownloadEpisode.catalog_id == catalog.id, DownloadEpisode.id.in_(identifiers))))
    if len(episodes) != len(identifiers):
        raise ValueError("所选视频不存在或不属于当前目录，请刷新后重选")
    # A rejected row must leave the entire selection unchanged, including its audit records.
    with session.begin_nested():
        for episode in episodes:
            review_episode(session, episode, {**data, "title": episode.title,
                "episode_number": episode.episode_number, "episode_range": episode.episode_range,
                "access": "public" if data["status"] == "approved" else episode.access,
                "evidence_url": episode.evidence_url or episode.original_url})
    label = "确认为公开正片" if data["status"] == "approved" else "排除"
    return {"updated": len(episodes), "message": f"已批量{label} {len(episodes)} 条，原链接与集号已保留。"}


def discover(session, catalog, keyword=None, restart=False):
    from sources.download import bridge_request
    from urllib.parse import urlparse
    item = catalog_item(session, catalog)
    mix_match = re.search(r"/(?:collection|mix)/([0-9]+)", item.get("collection_url") or "")
    account = urlparse(item.get("author_url") or "").path.rstrip("/").split("/")[-1]
    source = "mix:" + mix_match[1] if mix_match else "author:" + account
    if not mix_match and not account:
        raise ValueError("榜单未记录作者主页或合集，请手动添加正片链接")
    if catalog.ranking_id is None:
        keyword = item["series_title"] if keyword is None else keyword.strip()
        source += ":filter:" + keyword
    else:
        keyword = ""
    result = bridge_request("/directory", {"mix_id": mix_match[1] if mix_match else "", "account": account,
                            "cursor": int(catalog.cursor) if catalog.source == source and not restart else 0})
    added, skipped = 0, 0
    normalize = lambda value: re.sub(r"[\W_]", "", value).casefold()
    for video in result.get("videos", []):
        video_id = str(video.get("aweme_id") or "")
        if not re.fullmatch(r"[0-9]{1,100}", video_id) or str((video.get("author") or {}).get("uid") or "") != item["author_account_id"]:
            skipped += 1
            continue
        title = str(video.get("desc") or video_id)
        if keyword and normalize(keyword) not in normalize(title):
            skipped += 1
            continue
        if session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id, DownloadEpisode.video_id == video_id)):
            continue
        matches = re.findall(r"第\s*([0-9]{1,5})\s*[集话]", title)
        number = int(matches[0]) if len(matches) == 1 and int(matches[0]) > 0 and not re.search(r"[0-9]\s*[-—~～至]\s*[0-9]", title) else None
        session.add(DownloadEpisode(catalog_id=catalog.id, video_id=video_id, title=title, episode_number=number,
            original_url=f"https://www.douyin.com/video/{video_id}", source_duration=(video.get("duration") or 0) / 1000 or None,
            note="从原作者目录发现，待确认是否属于本剧正片；未按七天窗口截断"))
        session.flush()
        added += 1
        if not catalog.cover_url and str(video.get("cover", "")).startswith("https://"):
            catalog.cover_url = video["cover"]
    catalog.source, catalog.cursor = source, str(result.get("cursor", 0))
    catalog.has_more, catalog.checked, catalog.updated_at = bool(result.get("has_more")), False, utcnow()
    return {"added": added, "skipped": skipped, "has_more": catalog.has_more, "message":
            f"本页跳过 {skipped} 条作者或标题不匹配的视频。" + ("已读取一页原作者目录，请逐集确认归属与正片；可继续读取下一页" if catalog.has_more else "已读到目录末页，请核对同剧正片与缺集")}


def enqueue(session, catalog, episode_ids, trial=False):
    catalog_item(session, catalog)
    detail = catalog_detail(session, catalog)
    if trial and len(episode_ids) != 1:
        raise ValueError("试下载请选择一集")
    if not trial and not detail["summary"]["trial_passed"]:
        raise ValueError("请先试下载一集，通过自动校验并人工抽看后再批量下载")
    if trial and not detail["summary"]["trial_passed"]:
        previous = [row for row in detail["episodes"] if row["job"] and row["job"]["trial"]]
        if previous and any(row["id"] not in episode_ids for row in previous):
            raise ValueError("请先完成现有试下载与人工抽检，不能另建试下载绕过批量门槛")
    episodes = {row["id"]: row for row in detail["episodes"]}
    count = 0
    for episode_id in dict.fromkeys(episode_ids):
        row = episodes.get(episode_id)
        if not row or row["status"] != "approved" or row["access"] != "public":
            raise ValueError("只能下载本作品目录中已确认公开的正片")
        if row["job"]:
            continue
        target = session.get(DownloadEpisode, episode_id)
        target.reused_media_id, target.reused_sha256 = None, ""
        previous_job = session.scalar(select(DownloadJob).where(DownloadJob.episode_id == episode_id))
        if previous_job:
            control_job(session, previous_job, "resume")
        else:
            session.add(DownloadJob(episode_id=episode_id, trial=trial))
        count += 1
    session.flush()
    return {"queued": count, "message": f"已加入 {count} 集；已有任务不会重复创建"}


def control_job(session, job, action):
    episode = session.get(DownloadEpisode, job.episode_id)
    catalog = session.get(DownloadCatalog, episode.catalog_id)
    catalog_item(session, catalog)
    if action not in ("pause", "resume", "cancel"):
        raise ValueError("任务操作无效")
    if action == "resume" and episode.reused_media_id:
        if reused_resource(session, episode, catalog)[1]:
            raise ValueError("此视频已沿用审核通过的资源，无需重复准备")
        episode.reused_media_id, episode.reused_sha256 = None, ""
    if job.status == "verified":
        media = session.scalar(select(MediaFile).where(MediaFile.job_id == job.id))
        repair = not media or not inside(DATA, media.relative_path).is_file() or media.manual_status == "rejected"
        if not repair:
            from services.media import digest
            path = inside(DATA, media.relative_path)
            repair = path.stat().st_size != media.size or digest(path) != media.sha256
        if action != "resume" or not repair:
            raise ValueError("已通过校验的任务无需修改；文件缺失或抽检拒绝时可重新下载")
        job.status = "failed"
    if action == "resume":
        if job.status not in ("paused", "failed", "cancelled"):
            raise ValueError("此任务当前无法恢复")
        if job.attempts >= job.max_attempts:
            raise ValueError("已达到三次尝试上限，请检查来源后新建榜单版本重试")
        episode = session.get(DownloadEpisode, job.episode_id)
        if episode.status != "approved" or episode.access != "public":
            raise ValueError("请先核验为公开正片")
        job.status, job.message = "queued", "已恢复，等待工作进程"
    else:
        job.status = "paused" if action == "pause" else "cancelled"
        job.message = "已暂停，可恢复" if action == "pause" else "用户取消"
    job.updated_at = utcnow()


def delivery_path(session, media):
    """Recheck authorization and actual bytes before streaming either playback or attachment."""
    from services.media import digest
    job = session.get(DownloadJob, media.job_id)
    episode = session.get(DownloadEpisode, job.episode_id)
    catalog = session.get(DownloadCatalog, episode.catalog_id)
    catalog_item(session, catalog)
    if episode.status != "approved" or episode.access != "public" or job.status != "verified" or media.manual_status == "rejected":
        raise ValueError("仅交付已确认公开、自动校验通过且未被人工拒绝的正片")
    path = inside(DATA, media.relative_path)
    if not path.is_file():
        raise ValueError("服务器视频文件缺失，请恢复或重新获取")
    if path.stat().st_size != media.size or digest(path) != media.sha256:
        raise ValueError("视频大小或摘要异常，已停止交付，请重新获取并校验")
    return path


def download_one(session, catalog):
    """Called in a serialized transaction: repeated clicks reuse one persistent job."""
    catalog_item(session, catalog)
    detail = catalog_detail(session, catalog)
    eligible = [row for row in detail["episodes"] if row["status"] == "approved" and row["access"] == "public"]
    if not eligible:
        raise ValueError("暂无已确认公开的正片，请先核对正片与访问范围")
    previous_trial = next((row for row in detail["episodes"] if row["job"] and row["job"]["trial"]), None)
    if previous_trial and not detail["summary"]["trial_passed"]:
        if previous_trial not in eligible:
            raise ValueError("请先重新核验试下载正片，不能跳过现有试下载门槛")
        first = previous_trial
    else:
        first = (next((row for row in eligible if row["file_url"]), None) or
                 next((row for row in eligible if row["job"] and row["job"]["status"] in ("queued", "downloading", "validating")), None) or
                 next((row for row in eligible if not row["job"]), None) or eligible[0])
    if not first["job"]:
        enqueue(session, catalog, [first["id"]], trial=not detail["summary"]["trial_passed"])
    job = (session.get(DownloadJob, first["job"]["id"]) if first["job"] else
           session.scalar(select(DownloadJob).where(DownloadJob.episode_id == first["id"])))
    media = session.scalar(select(MediaFile).where(MediaFile.job_id == job.id))
    file_url = None
    if job.status == "verified":
        try:
            if not media:
                raise ValueError("缺少媒体记录")
            delivery_path(session, media)
            file_url = f"/api/downloads/media/{media.id}/download"
        except ValueError:
            control_job(session, job, "resume")
    elif job.status in ("failed", "paused", "cancelled"):
        control_job(session, job, "resume")
    return {"job_id": job.id, "status": job.status, "media_id": media.id if media else None, "file_url": file_url,
            "message": "文件已准备好，可保存到电脑并完成人工抽检" if file_url else f"{STATES[job.status]}，无需重复提交；任务在服务器继续执行"}


def check_media(session, media, data):
    if data["status"] not in ("passed", "rejected") or not data["note"].strip():
        raise ValueError("请填写抽看结论与说明")
    if data["status"] == "passed" and not all(data.get(key) for key in ("beginning", "middle", "ending", "sound")):
        raise ValueError("请抽看开头、中段、结尾，并确认声音正常")
    if not inside(DATA, media.relative_path).is_file():
        raise ValueError("本地文件已缺失，请恢复文件后再验收")
    media.manual_status, media.manual_note, media.checked_at = data["status"], data["note"], utcnow()
    session.flush()
    if data["status"] == "passed":
        job = session.get(DownloadJob, media.job_id)
        episode = session.get(DownloadEpisode, job.episode_id)
        catalog = session.get(DownloadCatalog, episode.catalog_id)
        if catalog.ranking_id is not None:
            backfill_reused_media(session, catalog.ranking_id, catalog.series_id)


def worker_status():
    from services.media import media_tools
    try:
        heartbeat = json.loads((DATA / "worker-heartbeat.json").read_text(encoding="utf-8"))
        from services.common import parse_time
        alive = (utcnow() - parse_time(heartbeat["at"])).total_seconds() < 25
    except (OSError, ValueError, KeyError):
        alive = False
    return {"running": alive, "media_tools": media_tools(), "video_directory": str(VIDEOS)}
