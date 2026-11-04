"""Complete orchestration fixture: synthetic platform and local media, no external calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv('DRAMA_DATABASE_URL', '')
path = Path(url.removeprefix('sqlite:///')).resolve()
if not url.startswith('sqlite:///') or path.parent != ROOT / 'data' or not path.name.startswith('test-workflow-ui-') or path.exists():
    raise SystemExit('Use a new data/test-workflow-ui-*.db only')
port = int(os.getenv('TOP3_UI_PORT', '8002'))
if port not in (8001, 8002):
    raise SystemExit('Isolated port required')
folder = ROOT / 'data/workflow-ui'
folder.mkdir(parents=True, exist_ok=True)
os.environ['DRAMA_DATA_DIR'] = str(folder)
import auth
os.environ.update(DRAMA_SITE_ORIGIN=f'http://127.0.0.1:{port}', DRAMA_ADMIN_USER='synthetic-admin',
    DRAMA_ADMIN_PASSWORD_HASH=auth.password_hash('synthetic-password'), DRAMA_SESSION_SECRET='synthetic-workflow-secret-'*4)
from database import SessionLocal, init_db
from services.common import KEYWORDS, utcnow
from services import search
from services.media import executable
from models import CollectionRun, DownloadEpisode, DownloadCatalog, DownloadJob
from sources.douk import SourceUnavailable
from sqlalchemy import select
from workers.download import process_one
import uvicorn

video = folder / 'synthetic.mp4'
subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:d=1.2',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-shortest', str(video)], check=True)
init_db()
with SessionLocal() as session:
    cutoff = utcnow() - timedelta(minutes=1)
    run = CollectionRun(name='合成全流程验收', mode='sample', cutoff_at=cutoff, window_hours=168,
        keywords=json.dumps(KEYWORDS), official_metadata='{}')
    session.add(run); session.commit()


class SyntheticSource:
    attempts = 0
    def search_page(self, keyword, sort, offset, count, cutoff):
        # First request asks for a simulated login; resume uses the saved cursor.
        SyntheticSource.attempts += 1
        if SyntheticSource.attempts == 1:
            raise SourceUnavailable('needs_login', '合成来源登录中断：点击恢复继续，无需真实登录')
        rows = []
        for index in range(1, 4):
            rows.append({'aweme_id': str(990000+index), 'desc': f'合成作品{index}第1集',
                'create_time': int((cutoff-timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp()),
                'author': {'uid': str(700+index), 'sec_uid': f'synthetic{index}', 'nickname': f'合成作者{index}'},
                'mix_info': {'mix_id': str(600+index), 'mix_name': f'合成作品{index}'},
                'statistics': {'digg_count': 90000-index*10000, 'comment_count': 10, 'collect_count': 3, 'share_count': 2}})
        return {'videos': rows, 'has_more': False, 'offset': 0}


def dispatch(identifier):
    threading.Thread(target=search.run_job, args=(identifier,), kwargs={'source': SyntheticSource(), 'pause': lambda _: None}, daemon=True).start()


def directory(route, payload):
    assert route == '/directory'
    index = int(payload['mix_id']) - 600
    return {'videos': [{'aweme_id': str(991000+index*100+number), 'desc': f'合成作品{index}第{number}集',
                        'author': {'uid': str(700+index)}, 'duration': 1200} for number in range(2,14)], 'has_more': False, 'cursor': 1}


class SyntheticDownloader:
    def resolve(self, video_id):
        index = (int(video_id)-991000)//100 if int(video_id) > 991000 else int(video_id) % 1000
        return {'author_account_id': str(700+index), 'url': 'synthetic', 'duration': 1.2, 'revision': 'synthetic'}
    def transfer(self, _, destination, progress):
        shutil.copyfile(video, destination); progress(destination.stat().st_size)


def worker():
    while True:
        with SessionLocal() as session:
            ids = list(session.scalars(select(DownloadJob.id).where(DownloadJob.status == 'queued')))
        for identifier in ids:
            process_one(identifier, SessionLocal, SyntheticDownloader())
        threading.Event().wait(.5)


with tempfile.TemporaryDirectory() as private, patch.object(auth, 'PRIVATE', Path(private)), \
     patch.object(search, 'dispatch', dispatch), patch('sources.download.bridge_request', side_effect=directory):
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run('main:app', host='127.0.0.1', port=port, log_level='warning')
