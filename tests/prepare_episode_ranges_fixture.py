"""Fresh synthetic episode-range UI data, never a production database."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv("DRAMA_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    raise SystemExit("Use a new data/test-episode-ranges*.db")
destination = Path(url.removeprefix("sqlite:///")).resolve()
if destination.parent != ROOT / "data" or not destination.name.startswith("test-episode-ranges") or destination.exists():
    raise SystemExit("Use a new data/test-episode-ranges*.db")

from tests import test_top3 as fixtures
from models import Author, Series, DownloadJob, MediaFile
from services import downloads
from services.media import executable, validate
from services.migration import REVISION
from sqlalchemy import select

f = fixtures.RankingTests()
f.setUp()
try:
    f.import_rows([f.row(author_id="12345", series_key="existing", series_title="合成鳄鱼缅北", title=f"合成鳄鱼缅北第{i}集", episode_number=str(i)) for i in (1,2,120)])
    f.approve_all()
    series = f.session.scalar(select(Series))
    f.session.get(Author, series.author_id).homepage_url = "https://www.douyin.com/user/synthetic-author"
    catalog = downloads.manual_catalog(f.session, series.id)
    folder = ROOT / "data/episode-ranges/ui"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "synthetic.mp4"
    subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(video)], check=True)
    info = validate(video, 3)
    for index, row in enumerate(downloads.catalog_detail(f.session, catalog)["episodes"]):
        job = DownloadJob(episode_id=row["id"], status="verified", trial=index==0, attempts=1)
        f.session.add(job)
        f.session.flush()
        f.session.add(MediaFile(job_id=job.id, relative_path=video.relative_to(ROOT / "data").as_posix(),
            **{key: value for key, value in info.items() if key != "suffix"}, manual_status="pending" if index==2 else "passed"))
    catalog.published = True
    f.session.commit()
    with f.engine.connect() as connection, closing(sqlite3.connect(destination)) as target:
        connection.connection.driver_connection.backup(target)
        target.execute("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)")
        target.execute("INSERT INTO alembic_version VALUES (?)", (REVISION,))
        target.commit()
    manifest = {"catalog_id": catalog.id, "series_id": series.id, "run_id": f.run.id}
    (folder / "fixture.json").write_text(json.dumps(manifest), encoding="utf-8")
    print(json.dumps(manifest))
finally:
    f.tearDown()
