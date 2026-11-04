"""Carry known sample candidates into a batch, with honest provenance and fresh metrics."""
from datetime import timedelta
from sqlalchemy import or_, select
from models import CollectionRun, Episode, MetricSnapshot, RunCandidate, SearchHit
from services.common import iso, utcnow


def sample_state(metric):
    if metric.method == "douk-detail":
        if metric.likes is None:
            return "failed", "详情未返回公开点赞数，请重试或补充本批次采样"
        if metric.likes <= 50_000:
            return "below_threshold", "本次公开点赞数未超过 50000，保留候选但不参与自动比较"
    return "refreshed", "已取得本批次互动采样"


def seed(session, run, *, reset=False):
    """Write only at explicit batch creation/search/repair boundaries. No public GET writes."""
    if run.mode != "sample":
        return []
    existing = {r.episode_id: r for r in session.scalars(select(RunCandidate).where(RunCandidate.run_id == run.id))}
    hits = session.scalars(select(SearchHit).join(CollectionRun, CollectionRun.id == SearchHit.run_id)
        .join(Episode, Episode.id == SearchHit.episode_id)
        .where(CollectionRun.mode == "sample", Episode.platform == "douyin",
            or_(Episode.published_at.is_(None), Episode.published_at.between(
                run.cutoff_at - timedelta(hours=run.window_hours), run.cutoff_at)))
        .order_by(SearchHit.id)).all()
    seen = set()
    for hit in hits:
        if hit.episode_id in seen:
            continue
        seen.add(hit.episode_id)
        member = existing.get(hit.episode_id)
        if member is None:
            member = RunCandidate(run_id=run.id, episode_id=hit.episode_id, source_hit_id=hit.id)
            session.add(member)
            existing[hit.episode_id] = member
            metric = session.scalar(select(MetricSnapshot).where(MetricSnapshot.run_id == run.id,
                MetricSnapshot.episode_id == hit.episode_id).order_by(MetricSnapshot.sampled_at.desc(), MetricSnapshot.id.desc()))
            member.metric_snapshot_id = metric.id if metric else None
            member.refresh_status, member.refresh_note = sample_state(metric) if metric else ("pending", "等待更新本批次互动")
        if reset:
            member.metric_snapshot_id = None
            member.refresh_status, member.refresh_note = "pending", "等待重新获取本批次互动"
            member.updated_at = utcnow()
    session.flush()
    return list(existing.values())


def record_sample(session, metric):
    member = session.scalar(select(RunCandidate).where(RunCandidate.run_id == metric.run_id,
        RunCandidate.episode_id == metric.episode_id))
    if member:
        # The established comparison policy always takes the latest whole sample.
        latest = session.scalar(select(MetricSnapshot).where(MetricSnapshot.run_id == metric.run_id,
            MetricSnapshot.episode_id == metric.episode_id).order_by(MetricSnapshot.sampled_at.desc(), MetricSnapshot.id.desc()))
        metric = latest or metric
        previous = session.get(MetricSnapshot, member.metric_snapshot_id) if member.metric_snapshot_id else None
        if previous and (previous.sampled_at, previous.id) > (metric.sampled_at, metric.id):
            return
        member.metric_snapshot_id = metric.id
        member.refresh_status, member.refresh_note = sample_state(metric)
        member.updated_at = utcnow()


def membership_view(member, source):
    return {"kind": "existing", "source_run_id": source.run_id, "source_hit_id": source.id,
            "refresh_status": member.refresh_status, "refresh_note": member.refresh_note,
            "updated_at": iso(member.updated_at), "source_url": source.source_url,
            "original_searched_at": iso(source.searched_at)}


def summary(rows):
    carried = [r for r in rows if r.get("membership", {}).get("source_run_id") not in (None, r.get("run_id"))]
    states = [r.get("membership", {}).get("refresh_status", "refreshed" if r.get("metric_snapshot_id") else "pending") for r in rows]
    return {"total": len(rows), "carried": len(carried), "new": len(rows) - len(carried),
            **{state: states.count(state) for state in ("refreshed", "pending", "failed", "excluded", "unavailable", "below_threshold")}}
