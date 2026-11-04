"""Build a fresh local two-area UI fixture; never open a production database."""
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
    raise SystemExit('Use an isolated SQLite database')
destination = Path(url.removeprefix('sqlite:///')).resolve()
if destination.parent != ROOT / 'data' or not destination.name.startswith('test-two-areas') or destination.exists():
    raise SystemExit('Use a new data/test-two-areas*.db file')

from tests.test_top3 import RankingTests
from models import DownloadJob, MediaFile
from services import downloads
from services.media import executable, digest, validate
from services.migration import REVISION

fixture = RankingTests()
fixture.setUp()
try:
    fixture.import_rows([fixture.row(author_id='author-a', series_key='series-a', series_title='合成星际旅人', title=f'合成星际旅人第{i}集', likes=str(1000-i), episode_number=str(i)) for i in (1,2,3)] +
        [fixture.row(series_title='合成山海奇遇', likes='500'), fixture.row(series_title='合成纸上江湖', likes='100')])
    fixture.approve_all()
    first, latest = fixture.ranking(), fixture.ranking()
    fixture.import_rows([fixture.row(series_title='合成待核验作品', likes='1')])
    video = ROOT / 'data/ui-refactor/synthetic.mp4'
    video.parent.mkdir(exist_ok=True, parents=True)
    subprocess.run([executable('ffmpeg'), '-v','error','-y','-f','lavfi','-i','color=c=navy:s=640x360:d=6',
        '-f','lavfi','-i','sine=frequency=440:duration=6','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-shortest',str(video)], check=True)
    info=validate(video,6)
    for ranking in (first, latest):
        for i,item in enumerate(row for row in ranking['items'] if row['selected']):
            catalog=downloads.catalog_for(fixture.session, ranking['id'], item['series_id'])
            rows=downloads.catalog_detail(fixture.session,catalog)['episodes']
            if i==0:
                for index,row in enumerate(rows):
                    job=DownloadJob(episode_id=row['id'],status='verified',trial=index==0,attempts=1)
                    fixture.session.add(job);fixture.session.flush()
                    fixture.session.add(MediaFile(job_id=job.id,relative_path=video.relative_to(ROOT/'data').as_posix(),size=video.stat().st_size,
                        duration=info['duration'],format='mp4',sha256=digest(video),tracks='[]',validation_note='合成视频自动校验',
                        manual_status='passed' if index<2 else 'pending',manual_note='合成测试'))
            elif i==1:
                fixture.session.add(DownloadJob(episode_id=rows[0]['id'],status='failed',trial=True,attempts=1,message='合成网络错误'))
    fixture.session.commit()
    with fixture.engine.connect() as connection, sqlite3.connect(destination) as target:
        connection.connection.driver_connection.backup(target)
        target.execute('CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)')
        target.execute('INSERT INTO alembic_version VALUES (?)',(REVISION,))
    manifest={'ranking_id':latest['id'],'previous_ranking_id':first['id'],'series_id':latest['items'][0]['series_id'],'run_id':fixture.run.id}
    (ROOT/'data/ui-refactor/fixture.json').write_text(json.dumps(manifest),encoding='utf-8')
    print(json.dumps(manifest))
finally:
    fixture.tearDown()
