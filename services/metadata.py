"""Audited corrections, independent of approval and immutable ranking versions."""
import json

from models import Episode, Evidence, ReviewRecord, Series
from services.common import public_url, utcnow


def update_metadata(session, kind, target_id, data):
    model = {"series": Series, "episode": Episode}.get(kind)
    target = session.get(model, target_id) if model else None
    if target is None:
        raise ValueError("整理对象不存在")
    reason = data.get("reason", "").strip()
    if not reason:
        raise ValueError("请填写整理依据")
    evidence_url = public_url(data.get("evidence_url"), douyin=True)
    if kind == "series":
        values = {key: data[key].strip() for key in ("title", "title_note", "ai_type", "ai_basis")}
        if not values["title"]:
            raise ValueError("作品名称不能为空；正式剧名未知时可填写标题简称并注明")
        if target.review_status == "approved" and (not values["ai_type"] or not values["ai_basis"]):
            raise ValueError("已通过作品仍须保留 AI 类型与制作依据")
        if target.review_status == "approved" and any(text in values["ai_basis"] for text in ("没有剧情", "不是短剧", "无法确认AI", "无法确认 AI")):
            raise ValueError("依据与通过状态矛盾，请先将作品转为待核验")
    else:
        number = data.get("episode_number")
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 1):
            raise ValueError("集号须为正整数，未知或合集留空")
        if data.get("content_kind") not in ("unknown", "single", "compilation"):
            raise ValueError("请选择单集、合集或未标注")
        if data["content_kind"] == "compilation" and number is not None:
            raise ValueError("多集合辑不能填写单一集号")
        values = {"episode_number": number, "content_kind": data["content_kind"],
                  "content_note": data.get("content_note", "").strip()}
    changes = {key: {"before": getattr(target, key), "after": value}
               for key, value in values.items() if getattr(target, key) != value}
    if not changes:
        return {"message": "资料未变化", "changed": False}
    evidence = Evidence(source_url=evidence_url, note=reason, captured_at=utcnow())
    session.add(evidence)
    session.flush()
    for key, value in values.items():
        setattr(target, key, value)
    session.add(ReviewRecord(target_type=kind, target_id=target_id, status="metadata_updated", reason=reason,
                             checks=json.dumps({"changes": changes}, ensure_ascii=False),
                             evidence_id=evidence.id, reviewed_at=utcnow()))
    session.flush()
    return {"message": "资料已整理；核验结论保留，生成新版本后更新榜单", "changed": True}
