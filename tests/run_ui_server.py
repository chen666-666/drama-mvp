"""Dedicated synthetic UI server. Never use with a production database or port."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv("DRAMA_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    raise SystemExit("An isolated SQLite URL is required")
path = Path(url.removeprefix("sqlite:///")).resolve()
if path.parent != (ROOT / "data").resolve() or not (path.name.startswith("test-") or path.name == "step2-ui.db"):
    raise SystemExit("Use data/test-*.db or data/step2-ui.db only")

import auth
os.environ.update(DRAMA_SITE_ORIGIN="http://127.0.0.1:8001", DRAMA_ADMIN_USER="synthetic-admin",
                  DRAMA_ADMIN_PASSWORD_HASH=auth.password_hash("synthetic-password"), DRAMA_SESSION_SECRET="synthetic-ui-secret-" * 4)
import uvicorn
port = int(os.getenv("TOP3_UI_PORT", "8001"))
if port not in (8001, 8002):
    raise SystemExit("Use isolated port 8001 or 8002")
os.environ["DRAMA_SITE_ORIGIN"] = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory() as directory:
    auth.PRIVATE = Path(directory)
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="warning")
