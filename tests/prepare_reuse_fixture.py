"""Fresh, isolated browser fixture with real synthetic video and three rankings."""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.environ.get('DRAMA_DATABASE_URL', '')
if not url.startswith('sqlite:///'):
    raise SystemExit('Use an isolated SQLite URL')
destination = Path(url.removeprefix('sqlite:///')).resolve()
if destination.parent != ROOT / 'data' or not destination.name.startswith('test-reuse-ui-') or destination.exists():
    raise SystemExit('Use a new data/test-reuse-ui-*.db')

from models import DownloadJob, MediaFile
from services import downloads
from services.media import digest, executable, validate
from services.migration import REVISION
from tests.test_top3 import RankingTests

f = RankingTests()
f.setUp()
try:
    f.import_rows([f.row(author_id='same', series_key='same', series_title='沿用资源合成剧',
        title=f'沿用资源第{i}集', episode_number=str(i), likes=str(1000-i)) for i in (1, 2)] +
        [f.row(series_title='新入选合成剧', likes='50'), f.row(series_title='另一部合成剧', likes='30')])
    f.approve_all()
    f.run.cutoff_at, f.run.name = datetime(2026, 11, 1, 4), '11月1日'
    first = f.ranking()
    catalog = downloads.catalog_for(f.session, first['id'], first['items'][0]['series_id'])
    path = ROOT / 'data/videos' / destination.stem / 'synthetic.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=navy:s=320x240:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-movflags', '+faststart', '-shortest', str(path)], check=True, capture_output=True)
    info = validate(path, 2)
    for i, row in enumerate(downloads.catalog_detail(f.session, catalog)['episodes']):
        job = DownloadJob(episode_id=row['id'], status='verified', trial=i == 0, attempts=1)
        f.session.add(job)
        f.session.flush()
        f.session.add(MediaFile(job_id=job.id, relative_path=path.relative_to(ROOT/'data').as_posix(),
            size=path.stat().st_size, duration=info['duration'], format='mp4', sha256=digest(path),
            tracks='[]', validation_note='合成视频自动校验', manual_status='passed', manual_note='合成声音与片段已确认'))
    f.session.flush()
    second = f.ranking()
    f.run.cutoff_at, f.run.name = datetime(2026, 11, 2, 4), '11月2日'
    latest = f.ranking()
    f.session.commit()
    with f.engine.connect() as connection, sqlite3.connect(destination) as target:
        connection.connection.driver_connection.backup(target)
        target.execute('CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)')
        target.execute('INSERT INTO alembic_version VALUES (?)', (REVISION,))
    out = ROOT/'data/resource-reuse-ui'
    out.mkdir(exist_ok=True)
    manifest = {'first': first['id'], 'second': second['id'], 'latest': latest['id'],
        'series_id': catalog.series_id, 'sha256': digest(path), 'database': str(destination)}
    (out/'fixture.json').write_text(json.dumps(manifest), encoding='utf-8')
    print(json.dumps(manifest))
finally:
    f.tearDown()
