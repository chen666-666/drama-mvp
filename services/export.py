"""First-step report, CSVs, search log, evidence references and step-two handoff."""
import csv
import io
import json
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from services.common import beijing, parse_time

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
TEMPLATES.filters["beijing"] = lambda value: beijing(parse_time(value)) if value else "未知"


def report_html(snapshot):
    return TEMPLATES.get_template("report.html").render(ranking=snapshot)


def csv_text(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        cleaned = {}
        for key in fields:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            cleaned[key] = "" if value is None else value
        writer.writerow(cleaned)
    return "\ufeff" + output.getvalue()


def export_zip(ranking):
    fields = ["ranking_id", "ranking_version", "rule_version", "scoring_mode", "position", "selected", "official_rank", "series_id", "series_key", "series_title", "author_id", "author_account_id", "author_name",
              "author_url", "collection_url", "title_note", "ai_type", "ai_basis", "episode_id", "video_id", "title", "episode_number", "content_kind", "content_note",
              "original_url", "published_at", "metric_snapshot_id", "sampled_at", "likes", "comments", "favorites", "shares",
              "score", "missing_reason", "exclusion_reasons", "series_reviews", "episode_reviews", "sources"]
    stream = io.BytesIO()
    context = {"ranking_id": ranking["id"], "ranking_version": ranking["version"], "rule_version": ranking["rule_version"], "scoring_mode": ranking["scoring_mode"]}
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("01_榜单报告.html", report_html(ranking))
        archive.writestr("02_候选与排名.csv", csv_text([{**context, **row} for row in ranking["items"]], fields))
        archive.writestr("03_全部候选.csv", csv_text([{**context, **row} for row in ranking["candidates"]], fields))
        lines = [ranking["title"], ranking["disclaimer"], f"榜单 ID：{ranking['id']} / 批次 {ranking['run_id']} / 版本 {ranking['version']}",
                 f"生成：{ranking['generated_at_beijing']}", f"截止：{ranking['cutoff_at_beijing']}",
                 f"窗口：{ranking['window_start']} 至 {ranking['cutoff_at']}（两端包含；官方榜以官方统计周期为准）",
                 f"计划关键词：{'、'.join(ranking['keywords'])}", f"规则：{ranking['rule_version']}；{ranking['explanation']}",
                 f"实际覆盖：{json.dumps(ranking['coverage'], ensure_ascii=False)}", "未记录的翻页量与全平台覆盖未知。", ranking["shortfall"]]
        for row in ranking["candidates"]:
            for source in row["sources"]:
                lines.append(json.dumps({"video_id": row["video_id"], **source}, ensure_ascii=False))
        archive.writestr("05_检索方法与采集时间.txt", "\n".join(lines))
        archive.writestr("自动搜索任务与分页记录.json", json.dumps(ranking.get("search_jobs", []), ensure_ascii=False, indent=2))
        archive.writestr("第一步验收与剩余核验.json", json.dumps({"ranking_id": ranking["id"], "version": ranking["version"],
                          "representative_policy": ranking.get("representative_policy", "旧版代表集规则，见原快照"),
                          "review_scope": ranking.get("review_scope", {})}, ensure_ascii=False, indent=2))
        evidence = [{"series_id": row["series_id"], "video_id": row["video_id"],
                     "series_reviews": row["series_reviews"], "episode_reviews": row["episode_reviews"]} for row in ranking["candidates"]]
        archive.writestr("06_来源证据/核验记录与来源链接.json", json.dumps({"official_metadata": ranking["official_metadata"], "records": evidence}, ensure_ascii=False, indent=2))
        archive.writestr("榜单快照.json", json.dumps(ranking, ensure_ascii=False, indent=2))
        archive.writestr("第二步入选名单.json", json.dumps({"ranking_id": ranking["id"], "run_id": ranking["run_id"], "version": ranking["version"],
            "items": [{key: row[key] for key in ("position", "series_id", "series_title", "author_id", "author_account_id", "author_url", "collection_url", "video_id", "original_url")} for row in ranking["items"] if row["selected"]]}, ensure_ascii=False, indent=2))
    return stream.getvalue()
