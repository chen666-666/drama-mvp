"""Durable orchestration. Human decisions stay in the existing audited services."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from database import SessionLocal
from models import (CollectionRun, DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile,
                    RankingRun, ReviewWorkflow, SearchJob)
from services import downloads, quick_reviews, ranking, search
from services.common import iso, beijing, utcnow
from services.review import review

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="review-workflow")
_dispatch_lock = threading.Lock()
_scheduled = set()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def save(flow, payload):
    flow.payload, flow.updated_at = encode(payload), utcnow()


def record(flow, action, count=0):
    payload = json.loads(flow.payload)
    payload.setdefault('events', []).append({'action': action, 'count': count, 'at': iso(utcnow())})
    payload['events'] = payload['events'][-1000:]
    save(flow, payload)


def draft(session, run):
    snapshot = ranking.make_ranking(session, run, persist=False)
    stable = {k: v for k, v in snapshot.items() if k not in
              ("version", "generated_at", "generated_at_beijing", "search_jobs", "collection_summary")}
    return {**snapshot, "token": fingerprint(stable)}


def create(session, run, operation_key, do_search=False):
    existing = session.scalar(select(ReviewWorkflow).where(ReviewWorkflow.operation_key == operation_key))
    if existing:
        if existing.run_id != run.id or json.loads(existing.payload)["search_requested"] != do_search:
            raise ValueError("操作标识已用于其他处理范围")
        return existing
    if session.scalar(select(ReviewWorkflow.id).where(ReviewWorkflow.run_id == run.id,
                       ReviewWorkflow.status.in_(["active", "paused", "interrupted"]))):
        raise ValueError("这个批次已有流程，请继续原任务")
    job = search.prepare_job(session, run, ["general", "likes", "latest"], 1) if do_search else None
    flow = ReviewWorkflow(operation_key=operation_key, run_id=run.id,
        search_job_id=job.id if job else None, status="active", stage="search" if job else "review",
        message="等待搜索" if job else "请核对证据卡，草案会随核验更新",
        payload=encode({"search_requested": do_search, "baseline": ranking.candidates(session, run),
                        "catalogs": {}, "operations": {}, "events": []}))
    session.add(flow)
    session.flush()
    return flow


def get(session, identifier):
    flow = session.get(ReviewWorkflow, identifier)
    if not flow:
        raise ValueError("任务不存在")
    return flow


def require_active(flow):
    if flow.status != "active":
        raise ValueError("请先恢复任务；取消后的流程不能继续写入")


def evidence_candidates(session, run):
    # Use this batch's whole sample; unknown likes still need investigation.
    return [row for row in ranking.candidates(session, run)
            if row["likes"] is None or row["likes"] >= 50_000]


def evidence_cards(session, flow):
    run = session.get(CollectionRun, flow.run_id)
    baseline = {row["episode_id"]: row for row in json.loads(flow.payload)["baseline"]}
    groups = {}
    for row in evidence_candidates(session, run):
        key = str(row["series_id"]) if row["series_id"] else "link:" + row["original_url"]
        card = groups.setdefault(key, {"series_id": row["series_id"], "title": row["series_title"],
            "author_name": row["author_name"], "account_id": row["author_account_id"],
            "author_url": row["author_url"], "collection_url": row["collection_url"],
            "ai_type": row["ai_type"], "ai_basis": row["ai_basis"], "series_status": row["series_status"],
            "series_reviews": row["series_reviews"], "episodes": [], "gaps": [], "changes": []})
        gaps = []
        if row["series_status"] != "approved":
            gaps.append("剧情、AI制作与原作者待核验")
        if not row["ai_basis"] or not row["series_reviews"]:
            gaps.append("待补AI制作依据及来源")
        if row["episode_status"] != "approved":
            gaps.append("正片、作品归属、公开及非重复待核验")
        if not row["published_at"] or not row["video_id"]:
            gaps.append("视频详情或发布时间待补")
        if any(row[name] is None for name in ("likes", "comments", "favorites", "shares")):
            gaps.append("互动指标不齐全，未知不补零")
        old = baseline.get(row["episode_id"])
        changes = [label for field, label in (("title", "视频标题"), ("series_title", "作品名称"),
            ("title_note", "名称说明"), ("author_account_id", "作者账号"), ("published_at", "发布时间"),
            ("original_url", "视频链接"), ("content_kind", "单集或合集形式"), ("series_id", "作品归属"),
            ("ai_basis", "AI依据"), ("metric_snapshot_id", "互动采样"), ("episode_status", "正片核验"),
            ("series_status", "作品核验")) if old and old.get(field) != row.get(field)]
        if not old:
            changes.append("本任务开始后新增")
        card["episodes"].append({**row, "gaps": gaps, "changes": changes, "token": fingerprint(row)})
        card["gaps"] = sorted(set(card["gaps"] + gaps))
        card["changes"] = sorted(set(card["changes"] + changes))
    return list(groups.values())


def batch_review(session, flow, data):
    require_active(flow)
    payload = json.loads(flow.payload)
    key = "review:" + data["operation_key"]
    signature = fingerprint(data)
    if key in payload["operations"]:
        if payload["operations"][key] != signature:
            raise ValueError("操作标识对应的核验内容已改变，请重新确认")
        return
    rows = {r["episode_id"]: r for r in evidence_candidates(session, session.get(CollectionRun, flow.run_id))}
    ids = data["episode_ids"]
    if len(ids) != len(set(ids)):
        raise ValueError("请勿重复选择条目")
    series_seen = set()
    for identifier in ids:
        row = rows.get(identifier)
        if not row or data["tokens"].get(str(identifier)) != fingerprint(row):
            raise ValueError("候选资料或采样已变化，请刷新证据卡后重新核对")
        target = row["series_id"] if data["target_type"] == "series" else identifier
        if not target:
            raise ValueError("请先补全作品归属")
        if target in series_seen:
            continue
        series_seen.add(target)
        review(session, {**data, "target_id": target}, require_evidence=False)
    payload["operations"][key] = signature
    save(flow, payload)
    record(flow, 'candidate_review', len(series_seen))


def finalize(session, flow, token, acknowledged):
    require_active(flow)
    if flow.ranking_id:
        return flow.ranking_id
    if flow.search_job_id and session.get(SearchJob, flow.search_job_id).status in search.ACTIVE:
        raise ValueError("请等待搜索与互动更新完成后再确认版本")
    run = session.get(CollectionRun, flow.run_id)
    current = draft(session, run)
    if token != current["token"]:
        raise ValueError("草案已变化，请刷新后重新确认")
    if not acknowledged:
        raise ValueError("请明确确认未决候选影响、样本局限和生成新版本")
    result = ranking.make_ranking(session, run)
    flow.ranking_id, flow.stage, flow.message = result["id"], "scope", "榜单已固定，请确认作品及允许准备的条目范围"
    record(flow, 'finalize_ranking', sum(r['selected'] for r in result['items']))
    return result["id"]


def episode_stamp(episode):
    return fingerprint([episode.catalog_id, episode.video_id, episode.title, episode.original_url,
        episode.episode_number, episode.status, episode.access, episode.review_history,
        *(["episode_range", episode.episode_range] if episode.episode_range else [])])


def supplement(session, flow, operation_key):
    """Explicit new preparation workflow, never reopen a completed human scope."""
    if flow.status != 'complete' or not flow.ranking_id:
        raise ValueError('仅已完成且已固定榜单的流程可以新建补充准备')
    existing = session.scalar(select(ReviewWorkflow).where(ReviewWorkflow.operation_key == operation_key))
    if existing:
        if json.loads(existing.payload).get('supplement_of') != flow.id:
            raise ValueError('操作标识已用于其他流程')
        return existing
    if session.scalar(select(ReviewWorkflow.id).where(ReviewWorkflow.ranking_id == flow.ranking_id,
            ReviewWorkflow.status.in_(['active','paused','interrupted']))):
        raise ValueError('此榜单已有未结束的准备流程，请先处理')
    result = ReviewWorkflow(operation_key=operation_key,run_id=flow.run_id,ranking_id=flow.ranking_id,
        status='active',stage='scope',message='补充准备：请先核验新候选，再明确选择本次范围',
        payload=encode({'search_requested':False,'baseline':[], 'catalogs':{}, 'operations':{},
                        'events':[], 'supplement_of':flow.id}))
    session.add(result);session.flush()
    return result


def confirm_scope(session, flow, selections):
    require_active(flow)
    if not flow.ranking_id:
        raise ValueError("请先明确生成正式榜单")
    selected = {r["series_id"] for r in json.loads(session.get(RankingRun, flow.ranking_id).snapshot)["items"] if r["selected"]}
    payload = json.loads(flow.payload)
    if not selections or len({s["catalog_id"] for s in selections}) != len(selections):
        raise ValueError("请选择目录，不要重复")
    desired = {str(s['catalog_id']): set(s['episode_ids']) for s in selections}
    old_scopes = dict(payload['catalogs'])
    for selection in selections:
        catalog = session.get(DownloadCatalog, selection["catalog_id"])
        if not catalog or catalog.ranking_id != flow.ranking_id or catalog.series_id not in selected:
            raise ValueError("目录不属于本次固定榜单的入选作品")
        ids = selection["episode_ids"]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("请明确选择允许准备的条目范围")
        stamps = {}
        for identifier in ids:
            episode = session.get(DownloadEpisode, identifier)
            if not episode or episode.catalog_id != catalog.id or episode.status != "approved" or episode.access != "public":
                raise ValueError("只能确认本目录已核验的公开正片")
            if selection["tokens"].get(str(identifier)) != episode_stamp(episode):
                raise ValueError("目录资料已变化，请重新核对范围")
            stamps[str(identifier)] = episode_stamp(episode)
        previous = payload["catalogs"].get(str(catalog.id), {})
        payload["catalogs"][str(catalog.id)] = {**previous, "episode_ids": ids, "stamps": stamps,
            "state": "waiting", "message": "范围已确认，等待准备", "deferred": previous.get("deferred", [])}
    for catalog_id, previous in old_scopes.items():
        removed = set(previous['episode_ids']) - desired.get(catalog_id, set())
        for job_id in previous.get('owned_jobs', []):
            job = session.get(DownloadJob, job_id)
            if job and job.episode_id in removed and job.status in ('queued', 'downloading', 'validating'):
                downloads.control_job(session, job, 'pause')
        if removed and previous.get('batch_id'):
            quick_reviews.pause(previous['batch_id'])
        if catalog_id not in desired:
            del payload['catalogs'][catalog_id]
        else:
            payload['catalogs'][catalog_id]['owned_jobs'] = [identifier for identifier in previous.get('owned_jobs', [])
                if session.get(DownloadJob, identifier) and session.get(DownloadJob, identifier).episode_id in desired[catalog_id]]
    flow.stage, flow.message = "prepare", "仅在已确认范围内准备；新增目录条目需再次确认"
    save(flow, payload)
    record(flow, 'confirm_scope', sum(len(s['episode_ids']) for s in selections))


def control(session, flow, action):
    if flow.status in ("cancelled", "complete"):
        raise ValueError("已结束的任务不能继续；已有文件和结论保留")
    payload = json.loads(flow.payload)
    if action in ("pause", "cancel"):
        flow.status = "paused" if action == "pause" else "cancelled"
        flow.message = "停止领取后续步骤；正在处理的媒体在安全边界结束。文件、结论及公开内容保留。"
        if flow.search_job_id:
            job = session.get(SearchJob, flow.search_job_id)
            if job.status in search.ACTIVE:
                job.status, job.message = "paused", "用户暂停了所属流程，已取回页面会保留"
        for info in payload["catalogs"].values():
            # Only jobs created by this workflow belong to its pause/resume authority.
            for job_id in info.get("owned_jobs", []):
                job = session.get(DownloadJob, job_id)
                if job and job.status in ("queued", "downloading", "validating"):
                    downloads.control_job(session, job, "pause")
            if info.get("batch_id"):
                quick_reviews.pause(info["batch_id"])
    elif action == "resume":
        flow.status, flow.message = "active", "正在复核已完成步骤；失败对象可单独重试"
        for info in payload["catalogs"].values():
            if info.get('batch_id'):
                try:
                    batch = quick_reviews.read(info['batch_id'])
                    if batch.get('cancel_requested') or batch['status'] == 'interrupted':
                        info.pop('batch_id', None)
                        info['state'] = 'waiting'
                except ValueError:
                    info.pop('batch_id', None)
            for job_id in info.get("owned_jobs", []):
                job = session.get(DownloadJob, job_id)
                if job and job.status == "paused":
                    downloads.control_job(session, job, "resume")
    else:
        raise ValueError("无效操作")
    save(flow, payload)
    record(flow, action)


def tick(session, flow):
    if flow.status != "active":
        return
    if flow.stage == "search":
        job = session.get(SearchJob, flow.search_job_id)
        flow.message = job.message
        if job.status == "completed":
            flow.stage = "review"
        return
    if flow.stage != "prepare":
        return
    payload = json.loads(flow.payload)
    for catalog_id, info in payload["catalogs"].items():
        try:
            catalog = session.get(DownloadCatalog, int(catalog_id))
            downloads.catalog_item(session, catalog)
            episodes = [session.get(DownloadEpisode, identifier) for identifier in info["episode_ids"]]
            if any(not e or episode_stamp(e) != info["stamps"][str(e.id)] for e in episodes):
                raise ValueError("已确认条目的资料或资格已变化，请重新确认范围")
            detail = downloads.catalog_detail(session, catalog)
            rows = [r for r in detail["episodes"] if r["id"] in info["episode_ids"]]
            trial = next((r for r in detail["episodes"] if r["job"] and r["job"]["trial"]), None)
            if not detail["summary"]["trial_passed"]:
                if trial and trial["id"] not in info["episode_ids"]:
                    raise ValueError("既有试下载不在确认范围内，请先完成原试下载或补充确认")
                target = trial or next((r for r in rows if not r["job"]), None)
                if target and not target["job"]:
                    downloads.enqueue(session, catalog, [target["id"]], trial=True)
            else:
                downloads.enqueue(session, catalog, info["episode_ids"])
            session.flush()
            reused_ids = {r["id"] for r in rows if r.get("reused", False)}
            jobs = list(session.scalars(select(DownloadJob).where(DownloadJob.episode_id.in_(set(info["episode_ids"]) - reused_ids))))
            # Record only jobs first observed as created by this tick, not existing external work.
            old_ids = {r["job"]["id"] for r in rows if r["job"]}
            info["owned_jobs"] = sorted(set(info.get("owned_jobs", [])) | {j.id for j in jobs if j.id not in old_ids})
            failed = [j for j in jobs if j.status in ("failed", "paused", "cancelled")]
            if failed:
                info.update(state="error", message="部分视频准备失败或暂停，请在详细页核对并重试；其他作品继续")
            else:
                info.update(state="waiting", message="等待视频准备或人工试下载抽检；试下载通过后才批量入队")
            pending = session.scalars(select(MediaFile).join(DownloadJob).where(
                DownloadJob.episode_id.in_(info["episode_ids"]), DownloadJob.status == "verified", MediaFile.manual_status == "pending")).all()
            if pending:
                if info.get('batch_id'):
                    try:
                        batch_status = quick_reviews.status(info['batch_id'])
                        if batch_status['status'] == 'interrupted':
                            info.update(state='blocked', message=batch_status['message'] + '；请点击重新准备或缩小范围，后台不会循环重试')
                            continue
                    except ValueError:
                        info.update(state='blocked', message='快审批次已过期，请重新准备；不会沿用过期观看记录')
                        continue
                batch = quick_reviews.create(session, catalog, skip_media_ids=info.get("deferred", []), episode_ids=info["episode_ids"])
                info.update(batch_id=batch["id"], state="review", message="有待人工快审的视频，准备最多十集；不会自动通过")
            reviewed = session.scalars(select(MediaFile).join(DownloadJob).where(
                DownloadJob.episode_id.in_(info["episode_ids"]), DownloadJob.status == "verified", MediaFile.manual_status.in_(["passed", "rejected"]))).all()
            reused_count = sum(r.get("reused", False) for r in rows)
            if len(reviewed) + reused_count == len(info["episode_ids"]):
                for media in reviewed:
                    if media.manual_status == 'passed':
                        downloads.delivery_path(session, media)
                info.update(state="complete", message="本次范围已处理；人工拒绝项不公开，全剧完整性另行核对")
        except (ValueError, OSError) as exc:
            info.update(state="error", message=str(exc) if isinstance(exc, ValueError) else "文件检查失败，请在详细页处理")
            info['retries'] = info.get('retries', 0) + 1
    save(flow, payload)


def run_once(identifier, factory=SessionLocal):
    try:
        with factory() as session:
            # Serialize scope changes, queue creation and finalization with all app writers.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            flow = get(session, identifier)
            tick(session, flow)
            session.commit()
    except Exception:
        with factory() as session:
            flow = session.get(ReviewWorkflow, identifier)
            if flow and flow.status == 'active':
                flow.status, flow.message = 'interrupted', '流程推进未完成，请核对后恢复；已有文件与结论保留'
                session.commit()
    finally:
        with _dispatch_lock:
            _scheduled.discard(identifier)


def dispatch(identifier):
    with _dispatch_lock:
        if identifier in _scheduled:
            return
        _scheduled.add(identifier)
    _pool.submit(run_once, identifier)


def recover(factory=SessionLocal):
    with factory() as session:
        for flow in session.scalars(select(ReviewWorkflow).where(ReviewWorkflow.status == "active")):
            flow.status, flow.message = "interrupted", "服务已重启，请人工恢复；已保存步骤和结论保留"
        session.commit()


def start_scheduler():
    stop = threading.Event()
    def loop():
        while not stop.wait(4):
            try:
                with SessionLocal() as session:
                    ids = list(session.scalars(select(ReviewWorkflow.id).where(
                        ReviewWorkflow.status == "active", ReviewWorkflow.stage.in_(["search", "prepare"]))))
                for identifier in ids:
                    dispatch(identifier)
            except Exception:
                # A transient DB busy condition is retried on the next bounded tick.
                continue
    threading.Thread(target=loop, daemon=True, name="workflow-scheduler").start()
    return stop


def view(session, flow):
    run = session.get(CollectionRun, flow.run_id)
    payload = json.loads(flow.payload)
    current = draft(session, run)
    catalogs = []
    if flow.ranking_id:
        for catalog in session.scalars(select(DownloadCatalog).where(DownloadCatalog.ranking_id == flow.ranking_id)):
            detail = downloads.catalog_detail(session, catalog, include_discovery=True)
            info = payload["catalogs"].get(str(catalog.id), {})
            detail["workflow"] = info
            for row in detail["episodes"]:
                row["scope_token"] = episode_stamp(session.get(DownloadEpisode, row["id"]))
            detail["unconfirmed_count"] = sum(r["id"] not in info.get("episode_ids", []) for r in detail["episodes"])
            catalogs.append(detail)
    fixed = json.loads(session.get(RankingRun, flow.ranking_id).snapshot) if flow.ranking_id else None
    compact = lambda snapshot: [(r["series_id"], r["video_id"], r["score"], r["position"]) for r in snapshot["items"]]
    return {"id": flow.id, "run_id": flow.run_id, "run_name": run.name, "cutoff_at": beijing(run.cutoff_at),
        "status": flow.status, "stage": flow.stage, "message": flow.message, "ranking_id": flow.ranking_id,
        "updated_at": iso(flow.updated_at), "search_job": search.job_dict(session.get(SearchJob, flow.search_job_id)) if flow.search_job_id else None,
        "cards": evidence_cards(session, flow), "draft": current, "catalogs": catalogs,
        "operation_events": payload.get('events', []),
        "fixed_ranking": fixed, "ranking_changed": bool(fixed and compact(fixed) != compact(current)),
        "all_scoped_complete": bool(payload["catalogs"]) and all(i.get("state") == "complete" for i in payload["catalogs"].values())}
