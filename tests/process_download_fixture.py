"""Process a UI-test queue entry with synthetic media, never a platform download."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
url = os.environ.get("DRAMA_DATABASE_URL", "")
if "step2-ui.db" not in url:
    raise SystemExit("Use the isolated data/step2-ui.db database")

from database import SessionLocal
from services.media import executable
from workers.download import process_one


class SyntheticDownloader:
    def resolve(self, video_id):
        return {"url": "synthetic-local-fixture", "revision": "synthetic-ui-test", "duration": 1, "author_account_id": "step2-ui"}

    def transfer(self, url, target, progress):
        reference = Path(__file__).resolve().parents[1] / "data/test-media/synthetic.mp4"
        reference.parent.mkdir(parents=True, exist_ok=True)
        if not reference.exists():
            subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(reference)],
                           check=True, capture_output=True, timeout=20)
        shutil.copyfile(reference, target)
        progress(target.stat().st_size)


process_one(int(sys.argv[1]), SessionLocal, SyntheticDownloader())
