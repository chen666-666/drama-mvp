"""Seed synthetic Red Fruit cards in a new, explicitly isolated UI database."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv("DRAMA_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    raise SystemExit("Set an isolated SQLite URL first")
path = Path(url.removeprefix("sqlite:///")).resolve()
if path.parent != ROOT / "data" or not path.name.startswith("test-hongguo-") or path.exists():
    raise SystemExit("Use a new data/test-hongguo-*.db only")

from database import SessionLocal, init_db
from models import Drama

init_db()
from main import library_page
output = ROOT / "data/hongguo-ui"
output.mkdir(parents=True, exist_ok=True)
(output / "empty.html").write_text(library_page("hongguo"), encoding="utf-8")
rows = [
    ("白昼谜案", "漫剧", 8.0, [" 悬疑题材 ", "#悬疑", "探案"], "2026-10-20T12:00:00"),
    ("晨光归途", "真人", 9.0, ["穿越", "逆袭"], "2026-10-21T12:00:00"),
    ("暗夜回声", "漫剧", 10.0, ["悬疑", "探案"], "2026-10-22T12:00:00"),
    ("白昼谜案", "漫剧", 8.0, ["悬疑", "探案"], "2026-10-20T12:00:00"),
    ("云端来信", "真人", 7.0, ["温情"], "2026-10-23T12:00:00"),
    ("边城往事", "真人", 6.9, ["逆袭题材"], "2026-10-24T12:00:00"),
    ("零点故事", None, 0.0, [], "2026-10-25T12:00:00.123456"),
    ("引号\"与'标签 <script>bad()</script>", "特殊分类", 8.5,
     ["O'Reilly", '双"引号', "<img src=x onerror=bad()>", "　＃穿越题材　"], "2026-10-25T12:00:00.123455"),
]
with SessionLocal() as session:
    for title, category, score, tags, created in rows:
        session.add(Drama(title=title, category=category, ai_score=score,
            tags=json.dumps(tags, ensure_ascii=False), created_at=datetime.fromisoformat(created),
            description="合成剧情简介", ai_summary="合成页面验收资料，不代表真实作品。",
            cover_url="/static/synthetic-missing-cover.jpg", official_url="https://example.com/synthetic"))
    session.commit()
print(f"Seeded {len(rows)} synthetic cards in {path.name}")
