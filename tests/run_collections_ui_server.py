"""Only isolated collection UI tests: replace the external directory source."""
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv("DRAMA_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    raise SystemExit("Use data/test-collections*.db")
path = Path(url.removeprefix("sqlite:///")).resolve()
if path.parent != ROOT / "data" or not path.name.startswith("test-collections"):
    raise SystemExit("Use data/test-collections*.db")


def directory(endpoint, body):
    if endpoint != "/directory":
        raise RuntimeError("No real platform calls in UI tests")
    if body.get("cursor"):
        return {"videos": [], "cursor": 0, "has_more": False}
    return {"videos": [{"aweme_id": "800001", "desc": "合成鳄鱼缅北第119集", "author": {"uid": "12345"}, "duration": 3000},
                       {"aweme_id": "800002", "desc": "其他短剧第1集", "author": {"uid": "12345"}, "duration": 3000},
                       {"aweme_id": "1003", "desc": "合成鳄鱼缅北第120集", "author": {"uid": "12345"}, "duration": 3000}],
            "cursor": 20, "has_more": True}


with patch("sources.download.bridge_request", side_effect=directory):
    runpy.run_path(str(ROOT / "tests/run_ui_server.py"), run_name="__main__")
