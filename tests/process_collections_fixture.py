"""Process queued collection fixture jobs using a local synthetic video only."""
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv("DRAMA_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    raise SystemExit("Use data/test-collections*.db")
path = Path(url.removeprefix("sqlite:///")).resolve()
if path.parent != ROOT / "data" or not path.name.startswith("test-collections"):
    raise SystemExit("Use data/test-collections*.db")

from database import SessionLocal
from models import DownloadJob
from workers.download import process_one
from sqlalchemy import select


class SyntheticDownloader:
    def resolve(self, _):
        return {"author_account_id": "12345", "url": "synthetic", "duration": 3, "revision": "synthetic"}

    def transfer(self, _, destination, progress):
        shutil.copyfile(ROOT / "data/collections-ui/synthetic.mp4", destination)
        progress(destination.stat().st_size)


with SessionLocal() as session:
    jobs = list(session.scalars(select(DownloadJob.id).where(DownloadJob.status == "queued")))
for job in jobs:
    process_one(job, SessionLocal, SyntheticDownloader())
print(f"Processed {len(jobs)} synthetic jobs")
