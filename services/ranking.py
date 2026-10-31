"""Deterministic public-interaction ranking and immutable, self-contained snapshots."""
import json
import re
from datetime import timedelta

from sqlalchemy import func, select

from models import Author, CollectionRun, Episode, Evidence, MetricSnapshot, RankingItem, RankingRun, ReviewRecord, SearchHit, SearchJob, Series
from services.common import METRICS, beijing, iso, parse_time, utcnow

SAMPLE_TITLE = "抖音 AI 短剧近 7 天公开互动候选 Top3"
DISCLAIMER = "基于本次检索样本，非抖音官方全量榜单。采样时累计互动不等于七天新增互动。"


def content_kind(episode):
    if not episode:
        return "unknown"
    if episode.content_kind != "unknown":
        return episode.content_kind
    if re.search(r"[0-9一二三四五六七八九十百]+\s*[-—–~～至到]\s*[0-9一二三四五六七八九十百]+\s*(?:集|[大全总]?合集)", episode.title) or re.search(r"大合集|多集合辑|全集纯享", episode.title):
        return "compilation"
    return "unknown"


def review_details(session, kind, target_id):
    rows = session.scalars(select(ReviewRecord).where(ReviewRecord.target_type == kind, ReviewRecord.target_id == target_id).order_by(ReviewRecord.id)).all()
    result = []
    for row in rows:
        evidence = session.get(Evidence, row.evidence_id)
        result.append({"id": row.id, "status": row.status, "reason": row.reason, "checks": json.loads(row.checks),
                       "reviewed_at": iso(row.reviewed_at), "reviewed_at_beijing": beijing(row.reviewed_at), "evidence_url": evidence.source_url,
                       "evidence_note": evidence.note, "captured_at": iso(evidence.captured_at)})
    return result


def candidates(session, run):
    hits = session.scalars(select(SearchHit).where(SearchHit.run_id == run.id).order_by(SearchHit.id)).all()
    grouped = {}
    for hit in hits:
        key = ("episode", hit.episode_id) if hit.episode_id else ("link", hit.source_url)
        grouped.setdefault(key, []).append(hit)
    result = []
    for key, source_hits in grouped.items():
        episode = session.get(Episode, key[1]) if key[0] == "episode" else None
        series = session.get(Series, episode.series_id) if episode and episode.series_id else None
        author = session.get(Author, series.author_id) if series else None
        metric = session.scalar(select(MetricSnapshot).where(MetricSnapshot.run_id == run.id, MetricSnapshot.episode_id == episode.id).order_by(MetricSnapshot.sampled_at.desc(), MetricSnapshot.id.desc())) if episode else None
        result.append({
            "episode_id": episode.id if episode else None, "video_id": episode.video_id if episode else None,
            "title": episode.title if episode else "链接待补全", "original_url": episode.original_url if episode else key[1],
            "episode_number": episode.episode_number if episode else None,
            "content_kind": content_kind(episode),
            "content_note": (episode.content_note or ("视频标题含多集范围或合集标记，按合集保留；可在整理视频中纠正" if episode.content_kind == "unknown" and content_kind(episode) == "compilation" else "")) if episode else "",
            "published_at": iso(episode.published_at) if episode else None,
            "published_at_beijing": beijing(episode.published_at) if episode else "未知",
            "episode_status": episode.review_status if episode else "pending",
            "series_id": series.id if series else None, "series_key": series.series_key if series else None,
            "series_title": series.title if series else "待补全", "series_status": series.review_status if series else "pending",
            "title_note": series.title_note if series else "",
            "ai_type": series.ai_type if series else "", "ai_basis": series.ai_basis if series else "",
            "collection_url": series.collection_url if series else "", "author_id": author.id if author else None,
            "author_account_id": author.account_id if author else "", "author_name": author.name if author else "待补全",
            "author_url": author.homepage_url if author else "",
            "metric_snapshot_id": metric.id if metric else None,
            "sampled_at": iso(metric.sampled_at) if metric else None,
            "sampled_at_beijing": beijing(metric.sampled_at) if metric else "未知",
            **{name: getattr(metric, name) if metric else None for name in METRICS},
            "missing_reason": metric.missing_reason if metric else "尚无互动采样",
            "method": metric.method if metric else None,
            "sources": [{"id": h.id, "source_url": h.source_url, "keyword": h.keyword, "sort_order": h.sort_order,
                         "position": h.position, "searched_at": iso(h.searched_at), "official_rank": h.official_rank} for h in source_hits],
            "series_reviews": review_details(session, "series", series.id) if series else [],
            "episode_reviews": review_details(session, "episode", episode.id) if episode else [],
        })
    return result


def make_ranking(session, run):
    generated_at = utcnow()
    rows = candidates(session, run)
    start = run.cutoff_at - timedelta(hours=run.window_hours)
    metadata = json.loads(run.official_metadata)
    if run.mode == "official" and not metadata.get("verified"):
        raise ValueError("请先核验官方榜的发布主体、分类、更新时间、统计周期、指标及证据")
    eligible = []
    for row in rows:
        reasons = []
        if not row["series_id"] or not row["video_id"] or not row["published_at"]:
            reasons.append("详情未补全")
        if row["series_status"] != "approved":
            reasons.append("作品未通过原作者、剧情及 AI 制作核验")
        if row["episode_status"] != "approved":
            reasons.append("正片、归属、公开及非重复上传未核验通过")
        if row["series_status"] == "approved" and (not row["ai_basis"] or not row["series_reviews"]):
            reasons.append("缺少 AI 制作依据或核验记录")
        if row["episode_status"] == "approved" and not row["episode_reviews"]:
            reasons.append("缺少正片核验记录")
        if run.mode == "sample" and row["content_kind"] == "compilation":
            reasons.append("多集合集不参与单集代表集排名，保留其来源与互动记录")
        if run.mode == "sample" and row["published_at"]:
            published = parse_time(row["published_at"])
            if not start <= published <= run.cutoff_at:
                reasons.append("发布时间不在本批次窗口内")
        if run.mode == "official":
            ranks = {h["official_rank"] for h in row["sources"] if h["official_rank"] is not None}
            if len(ranks) > 1:
                raise ValueError(f"视频 {row['video_id']} 存在冲突的官方名次，请纠正后新建批次")
            row["official_rank"] = next(iter(ranks), None)
            if row["official_rank"] is None:
                reasons.append("未提供可核对的官方名次")
        row["exclusion_reasons"] = reasons
        row["score"] = None
        if not reasons:
            eligible.append(row)
    if run.mode == "official":
        scoring = "official"
        explanation = "保留经人工核验的官方名次，统计周期与指标以官方来源为准，不使用自定义互动公式重排。"
        for row in eligible:
            row["score"] = None
        sort_key = lambda row: (row["official_rank"], row["video_id"])
        series_ranks = {}
        rank_owners = {}
        for row in eligible:
            sid, rank = row["series_id"], row["official_rank"]
            if sid in series_ranks and series_ranks[sid] != rank:
                raise ValueError("同一作品的多集官方名次不一致，请核实")
            if rank in rank_owners and rank_owners[rank] != sid:
                raise ValueError("不同作品使用了相同官方名次，请核实")
            series_ranks[sid], rank_owners[rank] = rank, sid
    else:
        complete = bool(eligible) and all(all(row[name] is not None for name in METRICS) for row in eligible)
        scoring = "weighted" if complete else "likes"
        if complete:
            explanation = "单集分 = 点赞 + 3×评论 + 4×收藏 + 5×分享。同分依次比分享、收藏、发布时间（较新优先）、视频 ID（字典升序）。"
            for row in eligible:
                row["score"] = row["likes"] + 3 * row["comments"] + 4 * row["favorites"] + 5 * row["shares"]
            sort_key = lambda row: (-row["score"], -row["shares"], -row["favorites"], -_timestamp(row["published_at"]), row["video_id"])
        else:
            explanation = "合格比较范围存在缺失指标，统一按公开点赞数排名，未知值不补零。同分跳过分享、收藏，依次比发布时间（较新优先）、视频 ID（字典升序）。"
            if not eligible:
                explanation = "尚无通过核验且位于本批次窗口内的候选正片。暂无可比较的互动数据；请补全或核验后生成新版本。"
            for row in eligible:
                row["score"] = row["likes"]
                if row["likes"] is None:
                    row["exclusion_reasons"].append("公开点赞未知，无法比较")
            eligible = [row for row in eligible if row["likes"] is not None]
            sort_key = lambda row: (-row["score"], -_timestamp(row["published_at"]), row["video_id"])
    representatives = {}
    for row in sorted(eligible, key=sort_key):
        representatives.setdefault(row["series_id"], row)
    ordered = sorted(representatives.values(), key=sort_key)
    items = []
    for index, row in enumerate(ordered, 1):
        item = {**row, "position": row["official_rank"] if scoring == "official" else index,
                "selected": row["official_rank"] <= 3 if scoring == "official" else index <= 3}
        items.append(item)
    # All imported links, reviews and metric values are embedded; future edits never rewrite a published version.
    version = (session.scalar(select(func.max(RankingRun.version)).where(RankingRun.run_id == run.id)) or 0) + 1
    snapshot = {
        "run_id": run.id, "run_name": run.name, "version": version,
        "title": SAMPLE_TITLE if run.mode == "sample" else "抖音 AI 短剧官方榜核验结果 Top3",
        "mode": run.mode, "disclaimer": DISCLAIMER if run.mode == "sample" else "保留原官方名次；仅展示本次已核验的作品，来源覆盖见检索记录。",
        "cutoff_at": iso(run.cutoff_at), "cutoff_at_beijing": beijing(run.cutoff_at),
        "window_start": iso(start), "window_hours": run.window_hours, "timezone": "Asia/Shanghai",
        "keywords": json.loads(run.keywords), "rule_version": run.rule_version, "scoring_mode": scoring,
        "comparison_policy_version": "single-episode-v2",
        "explanation": explanation, "official_metadata": metadata,
        "generated_at": iso(generated_at), "generated_at_beijing": beijing(generated_at),
        "candidate_count": len(rows), "qualified_series_count": len(items),
        "shortfall": (f"仅有 {sum(item['selected'] for item in items)} 部入选作品，不足三部；"
                      "详见全部候选中的排除原因。" + ("官方榜保留原名次，不用后续名次补位。" if run.mode == "official" else ""))
                     if sum(item["selected"] for item in items) < 3 else "",
        "items": items, "candidates": rows,
        "representative_policy": "窗口内经核验的单集正片，多集合集不参与代表集比较；作品取最高分单集且只占一个名额",
        "review_scope": review_scope(rows, items, scoring, run),
        "search_jobs": [{"id": job.id, "status": job.status, "config": json.loads(job.config),
                         "progress": json.loads(job.progress), "message": job.message,
                         "created_at": iso(job.created_at), "updated_at": iso(job.updated_at)}
                        for job in session.scalars(select(SearchJob).where(SearchJob.run_id == run.id).order_by(SearchJob.id))],
        "coverage": {"search_hit_count": sum(len(row["sources"]) for row in rows),
                     "actual_keywords": sorted({h["keyword"] for row in rows for h in row["sources"] if h["keyword"]}),
                     "actual_sorts": sorted({h["sort_order"] for row in rows for h in row["sources"] if h["sort_order"]})},
    }
    completed = [job for job in snapshot["search_jobs"] if job["status"] == "completed"]
    if completed:
        latest = completed[-1]
        snapshot["collection_summary"] = {"job_id": latest["id"], "source": latest["config"]["source"],
            "pages_done": latest["progress"]["pages_done"], "requests": latest["progress"]["requests"],
            "imported": latest["progress"]["imported"], "skipped": latest["progress"]["skipped"],
            "budget": latest["config"]["budget"]}
    ranking = RankingRun(run_id=run.id, version=version, mode=run.mode, scoring_mode=scoring,
                         snapshot=json.dumps(snapshot, ensure_ascii=False), created_at=generated_at)
    session.add(ranking)
    session.flush()
    for row in items:
        session.add(RankingItem(ranking_id=ranking.id, series_id=row["series_id"], episode_id=row["episode_id"],
                               metric_snapshot_id=row["metric_snapshot_id"], position=row["position"], score=row["score"],
                               detail=json.dumps(row, ensure_ascii=False)))
    session.flush()
    return {"id": ranking.id, **snapshot}


def review_scope(rows, items, scoring, run):
    """Conservative assessment of unresolved records, never substitute zeros for unknowns."""
    counts = {"approved": 0, "pending": 0, "excluded": 0, "compilations": 0}
    pending = []
    start = run.cutoff_at - timedelta(hours=run.window_hours)
    for row in rows:
        if row["series_status"] == "excluded" or row["episode_status"] == "excluded":
            counts["excluded"] += 1
        elif row.get("content_kind") == "compilation" and run.mode == "sample":
            counts["compilations"] += 1
        elif row["series_status"] == "approved" and row["episode_status"] == "approved":
            counts["approved"] += 1
        else:
            counts["pending"] += 1
            if run.mode == "sample" and (not row.get("published_at") or start <= parse_time(row["published_at"]) <= run.cutoff_at):
                pending.append(row)
    if scoring == "official":
        return {**counts, "assessment": "官方榜依核验来源保留名次，未核验记录不参与排名", "affecting_count": None, "unknown_count": None}
    selected = [row for row in items if row["selected"]]
    threshold = selected[-1]["score"] if len(selected) == 3 else None
    competitors, unknown = [], 0
    for row in pending:
        metrics = METRICS if scoring == "weighted" else ("likes",)
        if not row.get("published_at") or any(row[name] is None for name in metrics):
            unknown += 1
            continue
        score = (row["likes"] + 3*row["comments"] + 4*row["favorites"] + 5*row["shares"]) if scoring == "weighted" else row["likes"]
        if threshold is None or score >= threshold:
            competitors.append({"video_id": row["video_id"], "title": row["title"], "score": score})
    if len(selected) < 3:
        assessment = "合格作品不足三部，仍需补全或核验候选"
    elif unknown:
        assessment = "未核验候选存在缺失指标，尚不能判断其是否影响前三或统一排名口径"
    elif competitors:
        assessment = "存在分数达到或高于第三名的未核验候选，应优先复核"
    else:
        assessment = "在现有采样、归属与排名规则下，窗口内未核验记录的分数均低于第三名；不代表已核验全部候选或全平台覆盖"
    return {**counts, "assessment": assessment, "third_score": threshold, "affecting_count": len(competitors),
            "unknown_count": unknown, "potential_competitors": sorted(competitors, key=lambda row: -row["score"])}


def _timestamp(value):
    from datetime import datetime
    return datetime.fromisoformat(value).timestamp()
