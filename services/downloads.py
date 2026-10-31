"""Version-bound episode catalogs and durable download control."""
import json
from pathlib import Path
import re

from sqlalchemy import select

from models import DownloadCatalog, DownloadEpisode, DownloadJob, Episode, MediaFile, RankingRun
from services.common import iso, public_url, utcnow

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
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
        return catalog
    catalog = DownloadCatalog(ranking_id=ranking_id, series_id=series_id)
    session.add(catalog)
    session.flush()
    session.add(DownloadEpisode(catalog_id=catalog.id, video_id=item["video_id"], title=item["title"],
        original_url=item["original_url"], episode_number=item.get("episode_number"), status="approved", access="public",
        evidence_url=item["original_url"], note="继承此榜单版本已核验的代表集；下载前再次检查可访问性"))
    # Carry every known episode, including older ones outside the ranking window.
    for episode in session.scalars(select(Episode).where(Episode.series_id == series_id)):
        representative = episode.video_id == item["video_id"]
        if representative:
            continue
        approved = representative or (episode.review_status == "approved" and episode.content_kind != "compilation")
        session.add(DownloadEpisode(catalog_id=catalog.id, video_id=episode.video_id, title=episode.title,
            original_url=episode.original_url, episode_number=episode.episode_number,
            status="approved" if approved else "pending", access="public" if approved else "unknown",
            evidence_url=episode.original_url if approved else "",
            note="从榜单代表集或已核验正片继承；下载前再次检查可访问性" if approved else "来自已发现视频，需确认正片归属"))
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


def catalog_detail(session, catalog):
    rows = []
    for episode in session.scalars(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id).order_by(DownloadEpisode.episode_number, DownloadEpisode.id)):
        job = session.scalar(select(DownloadJob).where(DownloadJob.episode_id == episode.id))
        media = session.scalar(select(MediaFile).where(MediaFile.job_id == job.id)) if job else None
        rows.append({key: getattr(episode, key) for key in ("id", "video_id", "title", "original_url", "episode_number", "source_duration", "status", "access", "note", "evidence_url")} |
                    {"review_history": json.loads(episode.review_history), "job": job_dict(job), "media": media_dict(media)})
    eligible = [row for row in rows if row["status"] == "approved" and row["access"] == "public"]
    valid = [row for row in eligible if row["job"] and row["job"]["status"] == "verified" and row["media"] and row["media"]["exists"] and row["media"]["manual_status"] != "rejected"]
    checked = [row for row in valid if row["media"]["manual_status"] == "passed"]
    complete = bool(eligible) and len(valid) == len(eligible)
    return {"id": catalog.id, "ranking_id": catalog.ranking_id, "series_id": catalog.series_id, "checked": catalog.checked,
            "has_more": catalog.has_more, "total_episodes": catalog.total_episodes, "note": catalog.note, "cover_url": catalog.cover_url,
            "updated_at": iso(catalog.updated_at), "episodes": rows,
            "summary": {"discovered": len(rows), "public": len(eligible), "queued": sum(bool(row["job"]) for row in rows),
                        "downloaded": sum(bool(row["media"] and row["media"]["exists"]) for row in rows), "verified": len(valid),
                        "manual_passed": len(checked), "trial_passed": any(row["job"]["trial"] for row in checked),
                        "pending": sum(row["status"] == "pending" for row in rows),
                        "completion": "已发现公开正片已下载" if complete else "公开正片尚未下载完",
                        "full_series": "已核对目录范围内全部正片，人工抽检完成" if catalog.checked and catalog.total_episodes and len(checked) == catalog.total_episodes and len(eligible) == catalog.total_episodes and len(rows) == len(eligible) else "全剧完整性未确认"}}


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
    previous = {key: getattr(episode, key) for key in ("status", "access", "note", "episode_number")}
    for key in ("title", "status", "access", "note", "episode_number"):
        setattr(episode, key, data[key])
    episode.evidence_url = evidence
    episode.review_history = json.dumps([*json.loads(episode.review_history), {"at": iso(utcnow()), "previous": previous,
        "status": episode.status, "note": episode.note, "evidence_url": evidence}], ensure_ascii=False)
    if job and job.status == "queued" and episode.status != "approved":
        job.status, job.message = "paused", "目录核验变更，请核对后恢复"
    catalog = session.get(DownloadCatalog, episode.catalog_id)
    catalog.checked, catalog.updated_at = False, utcnow()


def discover(session, catalog):
    from sources.download import bridge_request
    from urllib.parse import urlparse
    item = selected_item(session, catalog.ranking_id, catalog.series_id)
    mix_match = re.search(r"/(?:collection|mix)/([0-9]+)", item.get("collection_url") or "")
    account = urlparse(item.get("author_url") or "").path.rstrip("/").split("/")[-1]
    source = "mix:" + mix_match[1] if mix_match else "author:" + account
    if not mix_match and not account:
        raise ValueError("榜单未记录作者主页或合集，请手动添加正片链接")
    result = bridge_request("/directory", {"mix_id": mix_match[1] if mix_match else "", "account": account,
                            "cursor": int(catalog.cursor) if catalog.source == source else 0})
    added = 0
    for video in result.get("videos", []):
        video_id = str(video.get("aweme_id") or "")
        if not re.fullmatch(r"[0-9]{1,100}", video_id) or str((video.get("author") or {}).get("uid") or "") != item["author_account_id"]:
            continue
        if session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id, DownloadEpisode.video_id == video_id)):
            continue
        session.add(DownloadEpisode(catalog_id=catalog.id, video_id=video_id, title=video.get("desc") or video_id,
            original_url=f"https://www.douyin.com/video/{video_id}", source_duration=(video.get("duration") or 0) / 1000 or None,
            note="从原作者目录发现，待确认是否属于本剧正片；未按七天窗口截断"))
        session.flush()
        added += 1
        if not catalog.cover_url and str(video.get("cover", "")).startswith("https://"):
            catalog.cover_url = video["cover"]
    catalog.source, catalog.cursor = source, str(result.get("cursor", 0))
    catalog.has_more, catalog.checked, catalog.updated_at = bool(result.get("has_more")), False, utcnow()
    return {"added": added, "has_more": catalog.has_more, "message": "已读取一页原作者目录，请逐集确认归属与正片；可继续读取下一页" if catalog.has_more else "已读到目录末页，请核对同剧正片与缺集"}


def enqueue(session, catalog, episode_ids, trial=False):
    detail = catalog_detail(session, catalog)
    if trial and len(episode_ids) != 1:
        raise ValueError("试下载请选择一集")
    if not trial and not detail["summary"]["trial_passed"]:
        raise ValueError("请先试下载一集，通过自动校验并人工抽看后再批量下载")
    episodes = {row["id"]: row for row in detail["episodes"]}
    count = 0
    for episode_id in dict.fromkeys(episode_ids):
        row = episodes.get(episode_id)
        if not row or row["status"] != "approved" or row["access"] != "public":
            raise ValueError("只能下载本作品目录中已确认公开的正片")
        if row["job"]:
            continue
        session.add(DownloadJob(episode_id=episode_id, trial=trial))
        count += 1
    session.flush()
    return {"queued": count, "message": f"已加入 {count} 集；已有任务不会重复创建"}


def control_job(session, job, action):
    if action not in ("pause", "resume", "cancel"):
        raise ValueError("任务操作无效")
    if job.status == "verified":
        media = session.scalar(select(MediaFile).where(MediaFile.job_id == job.id))
        repair = not media or not inside(DATA, media.relative_path).is_file() or media.manual_status == "rejected"
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


def check_media(session, media, data):
    if data["status"] not in ("passed", "rejected") or not data["note"].strip():
        raise ValueError("请填写抽看结论与说明")
    if data["status"] == "passed" and not all(data.get(key) for key in ("beginning", "middle", "ending", "sound")):
        raise ValueError("请抽看开头、中段、结尾，并确认声音正常")
    if not inside(DATA, media.relative_path).is_file():
        raise ValueError("本地文件已缺失，请恢复文件后再验收")
    media.manual_status, media.manual_note, media.checked_at = data["status"], data["note"], utcnow()


def worker_status():
    from services.media import media_tools
    try:
        heartbeat = json.loads((DATA / "worker-heartbeat.json").read_text(encoding="utf-8"))
        from services.common import parse_time
        alive = (utcnow() - parse_time(heartbeat["at"])).total_seconds() < 25
    except (OSError, ValueError, KeyError):
        alive = False
    return {"running": alive, "media_tools": media_tools(), "video_directory": str(VIDEOS)}
