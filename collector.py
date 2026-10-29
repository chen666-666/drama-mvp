"""AI enrichment and persistence for dramas collected from public sources."""

import json
import os
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI

from database import SessionLocal, init_db
from models import Drama


load_dotenv(Path(__file__).resolve().parent / ".env")


class AIProcessingError(RuntimeError):
    """Raised when an LLM response cannot be used as drama metadata."""


def get_ai_client() -> tuple[OpenAI, str]:
    """Build an OpenAI-compatible client from the local .env file."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    missing = [
        name
        for name, value in (("OPENAI_API_KEY", api_key), ("OPENAI_MODEL", model))
        if not value
    ]
    if missing:
        raise AIProcessingError(
            f"缺少配置：{', '.join(missing)}。请复制 .env.example 为 .env 后填写。"
        )

    return OpenAI(api_key=api_key, base_url=base_url), model


def extract_json(content: str) -> dict[str, Any]:
    """Accept JSON returned directly or inside a Markdown code fence."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        result = json.loads(cleaned.strip())
    except json.JSONDecodeError as exc:
        raise AIProcessingError(f"模型没有返回合法 JSON：{content!r}") from exc
    if not isinstance(result, dict):
        raise AIProcessingError("模型返回的 JSON 必须是对象。")
    return result


def process_with_ai(
    drama: dict[str, str],
    client: OpenAI | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """Request a score, summary, and tags for one public drama entry."""
    if client is None or model is None:
        client, model = get_ai_client()

    prompt = f"""请作为影视内容编辑分析下面的剧集，并且只返回一个 JSON 对象，不要 Markdown。

剧集标题：{drama['title']}
剧情简介：{drama['description']}

JSON 必须严格使用以下字段：
- ai_score：0 到 10 的数字，可保留一位小数。
- ai_summary：30 到 80 个中文字符的推荐摘要。
- tags：3 到 5 个简短的中文标签组成的数组。
"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是专业、客观的中文影视内容编辑。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content
    except Exception as exc:
        raise AIProcessingError(f"调用大模型 API 失败：{exc}") from exc

    if not content:
        raise AIProcessingError("模型没有返回任何内容。")

    result = extract_json(content)
    try:
        score = float(result["ai_score"])
        summary = str(result["ai_summary"]).strip()
        tags = [str(tag).strip() for tag in result["tags"] if str(tag).strip()]
    except (KeyError, TypeError, ValueError) as exc:
        raise AIProcessingError("模型返回内容缺少 ai_score、ai_summary 或 tags。") from exc

    if not 0 <= score <= 10 or not summary or not 3 <= len(tags) <= 5:
        raise AIProcessingError("模型结果不符合评分、摘要或标签数量要求。")

    return {
        **drama,
        "ai_score": score,
        "ai_summary": summary,
        "tags": json.dumps(tags, ensure_ascii=False),
    }


def collect_dramas(source_dramas: Iterable[dict[str, str]]) -> int:
    """AI-enrich source dramas, then atomically replace all stored records."""
    dramas = list(source_dramas)
    if not dramas:
        raise ValueError("没有可入库的剧集数据。")

    client, model = get_ai_client()
    processed_dramas = [
        process_with_ai(drama, client=client, model=model) for drama in dramas
    ]

    init_db()
    session = SessionLocal()
    try:
        session.query(Drama).delete()
        session.add_all(Drama(**drama) for drama in processed_dramas)
        session.commit()
        return len(processed_dramas)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print("请运行 spider.py 抓取 TVmaze 公开数据并执行入库。")
