"""Bounded, resumable keyword searches. Review decisions remain independent."""
from datetime import timedelta
import json
import threading
import time

from sqlalchemy import select

from database import SessionLocal
from models import Author, CollectionRun, Episode, MetricSnapshot, SearchJob, Series
from services.common import iso, parse_time, utcnow
from sources.douk import DouKSource, SORTS, SourceUnavailable, normalize_video, UPSTREAM
from sources.manual import import_row

_dispatch_lock = threading.Lock()
ACTIVE = ("queued", "running")
RESUMABLE = ("needs_login", "blocked", "failed", "interrupted")


def job_dict(job):
    return {"id": job.id, "run_id": job.run_id, "status": job.status,
            "config": json.loads(job.config), "progress": json.loads(job.progress),
            "message": job.message, "created_at": iso(job.created_at), "updated_at": iso(job.updated_at)}


def recover_jobs(factory=SessionLocal):
    with factory() as session:
        for job in session.scalars(select(SearchJob).where(SearchJob.status.in_(ACTIVE))):
            job.status, job.message = "interrupted", "服务重启中断了采集；可从已保存的位置继续"
            job.updated_at = utcnow()
        session.commit()


def prepare_job(session, run, sorts, pages):
    if run.mode != "sample":
        raise ValueError("自动关键词搜索适用于样本榜；官方榜需核验官方来源")
    if session.scalar(select(SearchJob.id).where(SearchJob.status.in_(ACTIVE))):
        raise ValueError("已有搜索任务正在执行，请等待完成后再开始")
    keywords = json.loads(run.keywords)
    sorts = list(dict.fromkeys(sorts))
    if not sorts or any(sort not in SORTS for sort in sorts) or not 1 <= pages <= 3:
        raise ValueError("请选择有效排序，每种排序可查看 1—3 页")
    if any(len(word) > 100 for word in keywords) or len(keywords) * len(sorts) * pages * 10 > 180:
        raise ValueError("每次最多查看 180 条结果；请减少关键词、排序或页数（关键词限 100 字）")
    tasks = [{"keyword": word, "sort": sort} for word in keywords for sort in sorts]
    now = utcnow()
    job = SearchJob(run_id=run.id, status="queued", created_at=now, updated_at=now,
                    config=json.dumps({"source": UPSTREAM, "tasks": tasks, "pages": pages, "count": 10,
                                       "budget": len(tasks) * pages * 10}, ensure_ascii=False),
                    progress=json.dumps({"task": 0, "page": 0, "offset": 0, "requests": 0, "pages_done": 0,
                                         "imported": 0, "skipped": 0, "history": []}), message="等待搜索")
    session.add(job)
    session.flush()
    return job


def prepare_resume(session, job):
    if job.status not in RESUMABLE:
        raise ValueError("当前任务无需继续")
    if session.scalar(select(SearchJob.id).where(SearchJob.status.in_(ACTIVE))):
        raise ValueError("已有搜索任务正在执行")
    job.status, job.message, job.updated_at = "queued", "等待从上次位置继续", utcnow()
    session.flush()


def import_video(session, run, video, task, position, sampled):
    row = normalize_video(video, task["keyword"], task["sort"], position, sampled)
    published = parse_time(row["published_at"])
    if not run.cutoff_at - timedelta(hours=run.window_hours) <= published <= run.cutoff_at:
        return False, "发布时间不在批次窗口内"
    existing = session.scalar(select(Episode).where(Episode.platform == "douyin", Episode.video_id == row["video_id"]))
    if existing and existing.series_id:
        series = session.get(Series, existing.series_id)
        author = session.get(Author, series.author_id)
        if author.account_id != row["author_id"]:
            raise ValueError("已保存视频与搜索结果的作者 ID 冲突，需人工核对")
        # Retain confirmed grouping, episode numbering and reviewed metadata.
        row.update(series_key=series.series_key, series_title=series.title,
                   episode_number=existing.episode_number, title=existing.title or row["title"],
                   published_at=iso(existing.published_at) if existing.published_at else row["published_at"],
                   original_url=existing.original_url)
    import_row(session, run.id, row)
    episode = session.scalar(select(Episode).where(Episode.video_id == row["video_id"], Episode.platform == "douyin"))
    snapshot = session.scalar(select(MetricSnapshot).where(MetricSnapshot.run_id == run.id,
                              MetricSnapshot.episode_id == episode.id).order_by(MetricSnapshot.id.desc()))
    snapshot.method = "douk"
    return True, ""


def run_job(job_id, factory=SessionLocal, source=None, pause=time.sleep):
    source = source or DouKSource()
    with _dispatch_lock:
        try:
            with factory() as session:
                job = session.get(SearchJob, job_id)
                if not job or job.status != "queued":
                    return
                config, progress = json.loads(job.config), json.loads(job.progress)
                run_id = job.run_id
                job.status, job.message, job.updated_at = "running", "正在连接抖音搜索", utcnow()
                session.commit()
            while progress["task"] < len(config["tasks"]):
                task = config["tasks"][progress["task"]]
                with factory() as session:
                    run = session.get(CollectionRun, run_id)
                    cutoff = run.cutoff_at
                started = utcnow()
                progress["requests"] += 1
                entry = {**task, "page": progress["page"] + 1, "offset": progress["offset"], "started_at": iso(started)}
                try:
                    result = source.search_page(task["keyword"], task["sort"], progress["offset"], config["count"], cutoff)
                except SourceUnavailable as exc:
                    entry.update(status=exc.status, finished_at=iso(utcnow()))
                    progress["history"].append(entry)
                    with factory() as session:
                        current = session.get(SearchJob, job_id)
                        current.status, current.message = exc.status, str(exc)
                        current.progress, current.updated_at = json.dumps(progress, ensure_ascii=False), utcnow()
                        session.commit()
                    return
                sampled = utcnow()
                imported, skipped, reasons = 0, 0, []
                with factory() as session:
                    run = session.get(CollectionRun, run_id)
                    for index, video in enumerate(result["videos"][:config["count"]], 1):
                        try:
                            with session.begin_nested():
                                ok, reason = import_video(session, run, video, task,
                                    f"第 {progress['page'] + 1} 页 / 第 {index} 条", sampled)
                            imported += int(ok)
                            skipped += int(not ok)
                            if reason:
                                reasons.append({"position": index, "reason": reason})
                        except ValueError as exc:
                            skipped += 1
                            reasons.append({"position": index, "reason": str(exc)})
                    progress["imported"] += imported
                    progress["skipped"] += skipped
                    progress["pages_done"] += 1
                    entry.update(status="ok", finished_at=iso(sampled), returned=len(result["videos"]),
                                 considered=min(len(result["videos"]), config["count"]),
                                 imported=imported, skipped=skipped, reasons=reasons)
                    progress["history"].append(entry)
                    previous_offset = progress["offset"]
                    next_offset = result.get("offset", 0)
                    more = result.get("has_more") and isinstance(next_offset, int) and next_offset > previous_offset
                    if progress["page"] + 1 < config["pages"] and more:
                        progress["page"] += 1
                        progress["offset"] = next_offset
                    else:
                        progress.update(task=progress["task"] + 1, page=0, offset=0)
                    current = session.get(SearchJob, job_id)
                    done = progress["task"] == len(config["tasks"])
                    current.status = "completed" if done else "running"
                    current.message = ("搜索完成，请核验 AI 制作与作品归属后生成 Top3" if done else
                                       f"已采集 {progress['imported']} 条窗口内候选，正在继续搜索")
                    current.progress, current.updated_at = json.dumps(progress, ensure_ascii=False), utcnow()
                    session.commit()
                if not done:
                    pause(2)
        except Exception:
            # Do not store upstream exceptions: some contain request headers or credentials.
            with factory() as session:
                current = session.get(SearchJob, job_id)
                if current and current.status in ACTIVE:
                    current.status, current.message = "failed", "采集出现异常；已保存的数据保留，可继续或检查本机服务"
                    current.updated_at = utcnow()
                    session.commit()


def dispatch(job_id):
    threading.Thread(target=run_job, args=(job_id,), daemon=True, name=f"douyin-search-{job_id}").start()
