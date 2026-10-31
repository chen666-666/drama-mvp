"""Single durable worker. Run with python -m workers.download."""
import json
import os
import time
import threading
from contextlib import contextmanager

import httpx
from sqlalchemy import select, update

from database import SessionLocal, init_db
from models import DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile
from services.common import iso, utcnow
from services.downloads import DATA, inside, safe_name, selected_item
from services.media import thumbnail, validate
from sources.download import DouKDownloader
from sources.douk import SourceUnavailable


class Stopped(Exception):
    pass


@contextmanager
def exclusive_worker():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "download-worker.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def heartbeat():
    temporary = DATA / "worker-heartbeat.tmp"
    temporary.write_text(json.dumps({"pid": os.getpid(), "at": iso(utcnow())}), encoding="utf-8")
    temporary.replace(DATA / "worker-heartbeat.json")


def keep_alive(stop):
    while not stop.wait(5):
        heartbeat()


def recover(session):
    # Reuse only the exact job's temporary file and revalidate; partial transfers restart safely.
    for job in session.scalars(select(DownloadJob).where(DownloadJob.status.in_(["downloading", "validating"]))):
        job.status, job.message, job.updated_at = "paused", "工作进程重启，已保留任务与临时文件，请恢复后重新检查", utcnow()


def process_one(job_id, factory=SessionLocal, downloader=None):
    downloader = downloader or DouKDownloader()
    from services.media import media_tools
    if not media_tools()["ffprobe"]:
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job and job.status == "queued":
                job.status, job.message = "paused", "缺少 ffprobe 媒体工具，安装后可恢复"
        return
    with factory.begin() as session:
        claimed = session.execute(update(DownloadJob).where(DownloadJob.id == job_id, DownloadJob.status == "queued")
                                  .values(status="downloading", attempts=DownloadJob.attempts + 1, updated_at=utcnow()))
        if not claimed.rowcount:
            return
        job = session.get(DownloadJob, job_id)
        episode = session.get(DownloadEpisode, job.episode_id)
        if episode.status != "approved" or episode.access != "public" or job.attempts > job.max_attempts:
            job.status, job.message = "paused", "目录核验或尝试次数不符合下载条件"
            return
        catalog = session.get(DownloadCatalog, episode.catalog_id)
        catalog_id = catalog.id
        item = selected_item(session, catalog.ranking_id, catalog.series_id)
        video_id, source_duration = episode.video_id, episode.source_duration
        folder = f"videos/榜单{catalog.ranking_id}/{item['position']:02d}_{safe_name(item['series_title'])}_{safe_name(item['author_name'])}"
        label = f"EP{episode.episode_number:03d}" if episode.episode_number else "集号待确认"
        filename = f"{safe_name(item['series_title'])}_{label}_{video_id}"
    temporary = inside(DATA, f"tmp/job-{job_id}.part")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    last_progress = [0.0]

    def progress(size, force=False):
        current = time.monotonic()
        if not force and current - last_progress[0] < 1:
            return
        last_progress[0] = current
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job.status not in ("downloading", "validating"):
                raise Stopped()
            job.bytes_downloaded, job.updated_at = size, utcnow()

    try:
        resolved = downloader.resolve(video_id)
        if resolved.get("author_account_id") != item["author_account_id"]:
            raise ValueError("视频原作者与入选作品不一致，请重新核验剧集归属")
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job.status != "downloading":
                raise Stopped()
            job.tool = f"DouK {resolved.get('revision', '版本未知')}"
            job.message = "正在传输公开正片"
            episode = session.get(DownloadEpisode, job.episode_id)
            source_duration = resolved.get("duration") or source_duration
            episode.source_duration = source_duration
        downloader.transfer(resolved["url"], temporary, progress)
        progress(temporary.stat().st_size, True)
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job.status != "downloading":
                raise Stopped()
            job.status, job.message = "validating", "正在检查容器、轨道、时长与文件摘要"
        result = validate(temporary, source_duration, decode=os.getenv("DRAMA_FULL_DECODE", "0") == "1")
        relative = f"{folder}/{filename}.{result.pop('suffix')}"
        final = inside(DATA, relative)
        final.parent.mkdir(parents=True, exist_ok=True)
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job.status != "validating":
                raise Stopped()
            temporary.replace(final)
            media = session.scalar(select(MediaFile).where(MediaFile.job_id == job_id))
            if media is None:
                media = MediaFile(job_id=job_id, relative_path=relative, **result)
                session.add(media)
            else:
                for key, value in {"relative_path": relative, **result}.items():
                    setattr(media, key, value)
                media.manual_status, media.manual_note, media.checked_at = "pending", "", None
            job.status, job.message, job.updated_at = "verified", "自动校验通过，等待人工抽看", utcnow()
        if thumbnail(final, inside(DATA, f"thumbnails/catalog-{catalog_id}.jpg")):
            with factory.begin() as session:
                session.get(DownloadCatalog, catalog_id).cover_url = f"/api/downloads/catalogs/{catalog_id}/cover"
    except Stopped:
        return
    except Exception as exc:
        if isinstance(exc, SourceUnavailable):
            status, message = ("paused" if exc.status in ("needs_login", "blocked") else "failed"), str(exc)
        elif isinstance(exc, ValueError):
            status, message = "failed", str(exc)
        elif isinstance(exc, httpx.HTTPError):
            status, message = "failed", "网络传输失败，任务已保留；可有限重试"
        else:
            status, message = "failed", "下载或媒体检查未完成，请检查工具和磁盘后恢复"
        with factory.begin() as session:
            job = session.get(DownloadJob, job_id)
            if job.status not in ("downloading", "validating"):
                return
            if status == "paused":
                # Login/verification interrupts every queued job for this source.
                for queued in session.scalars(select(DownloadJob).where(DownloadJob.status == "queued")):
                    queued.status, queued.message, queued.updated_at = "paused", message, utcnow()
            if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) and job.attempts < job.max_attempts:
                status, message = "queued", "临时网络故障，准备有限重试"
            job.status, job.message, job.updated_at = status, message, utcnow()


def main():
    init_db()
    try:
        with exclusive_worker():
            stop = threading.Event()
            thread = threading.Thread(target=keep_alive, args=(stop,), daemon=True)
            heartbeat()
            thread.start()
            with SessionLocal.begin() as session:
                recover(session)
            try:
                while True:
                    with SessionLocal() as session:
                        job_id = session.scalar(select(DownloadJob.id).where(DownloadJob.status == "queued").order_by(DownloadJob.id).limit(1))
                    if job_id:
                        process_one(job_id)
                    time.sleep(2)
            finally:
                stop.set()
                thread.join(timeout=6)
    except (BlockingIOError, OSError):
        print("下载工作进程已运行，或目录无法写入。")


if __name__ == "__main__":
    main()
