import json
from sqlalchemy import select

from models import Episode, Evidence, ReviewRecord, Series
from services.common import public_url, utcnow


def review(session, data):
    kind = data["target_type"]
    model = {"series": Series, "episode": Episode}.get(kind)
    if model is None or data["status"] not in ("pending", "approved", "excluded"):
        raise ValueError("核验对象或状态无效")
    target = session.get(model, data["target_id"])
    if target is None:
        raise ValueError("核验对象不存在")
    checks = data.get("checks", {})
    reason = data.get("reason", "").strip()
    evidence_url = public_url(data.get("evidence_url"))
    if not reason:
        raise ValueError("请填写核验理由和来源说明")
    if data["status"] == "approved":
        if kind == "series" and any(text in data.get("ai_basis", "") for text in ("没有剧情", "不是短剧", "无法确认AI", "无法确认 AI")):
            raise ValueError("AI 制作依据与通过结论矛盾，请先核实；无法确认时保持待核验")
        required = ("story", "ai", "original_author") if kind == "series" else ("main_episode", "ownership", "public", "not_duplicate")
        if not all(checks.get(key) is True for key in required):
            raise ValueError("通过前须逐项确认剧情、AI 与原作者，或正片、归属、公开及非重复上传")
        if kind == "series" and (not data.get("ai_type", "").strip() or not data.get("ai_basis", "").strip()):
            raise ValueError("请填写 AI 类型与 AI 制作依据")
        if kind == "episode" and (not target.series_id or not target.published_at or not target.title):
            raise ValueError("正片详情未补全，请先导入完整 CSV")
    if kind == "series":
        target.ai_type = data.get("ai_type", target.ai_type).strip()
        target.ai_basis = data.get("ai_basis", target.ai_basis).strip()
    target.review_status = data["status"]
    evidence = Evidence(source_url=evidence_url, note=reason, captured_at=utcnow())
    session.add(evidence)
    session.flush()
    session.add(ReviewRecord(target_type=kind, target_id=target.id, status=target.review_status,
                             reason=reason, checks=json.dumps(checks), evidence_id=evidence.id, reviewed_at=utcnow()))
    session.flush()
    return {"status": target.review_status}


def merge_series(session, source_id, target_id, reason, evidence_url):
    source, target = session.get(Series, source_id), session.get(Series, target_id)
    if not source or not target or source_id == target_id:
        raise ValueError("请选择两个不同且存在的作品")
    if source.author_id != target.author_id:
        raise ValueError("不同原作者的作品不能直接归并；同名不代表同剧")
    if not reason.strip():
        raise ValueError("请说明确认同剧的依据")
    evidence = Evidence(source_url=public_url(evidence_url), note=reason, captured_at=utcnow())
    session.add(evidence)
    session.flush()
    for episode in session.scalars(select(Episode).where(Episode.series_id == source_id)):
        episode.series_id = target_id
        episode.review_status = "pending"
    # Keep the old series key as an audit trail; future imports must use the target key.
    source.review_status, target.review_status = "excluded", "pending"
    session.add(ReviewRecord(target_type="series", target_id=source_id, status="merged", reason=reason,
                             checks=json.dumps({"target_series_id": target_id}), evidence_id=evidence.id, reviewed_at=utcnow()))
    session.flush()
    return {"target_series_id": target_id, "message": "已归并，请重新核验目标作品及其正片。旧榜单快照保持原结果。"}
