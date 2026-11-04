"""Explicit episode designations; a range describes one video, never extra files."""
import re
import unicodedata
from typing import Annotated

from pydantic import BeforeValidator


def parse_episode_number(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("集号请填写正整数或范围（如 1-12），未知请留空")
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.fullmatch(r"([0-9]{1,15})(?:\s*[-–—~～至到]\s*([0-9]{1,15}))?", text)
    if not match or int(match[1]) < 1:
        raise ValueError("集号请填写正整数或范围（如 1-12），未知请留空")
    first = int(match[1])
    if match[2] is None:
        return first, None
    last = int(match[2])
    if last < first:
        raise ValueError("集号范围的结束集号不能小于开始集号")
    return (first, None) if first == last else (None, f"{first}-{last}")


def validated_episode_number(value):
    number, span = parse_episode_number(value)
    return span or number


EpisodeNumberInput = Annotated[int | str | None, BeforeValidator(validated_episode_number)]


def covered_numbers(rows, limit=10000):
    """Bound expansion for gap hints; this does not assert media completeness."""
    result = set()
    for row in rows:
        if row.get("episode_range"):
            first, last = map(int, row["episode_range"].split("-"))
            result.update(range(first, min(last, limit) + 1))
        elif row.get("episode_number"):
            result.add(row["episode_number"])
    return result
