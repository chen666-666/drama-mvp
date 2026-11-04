"""Extend only a fresh synthetic collections database for continuous review UI tests."""
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
url=os.getenv('DRAMA_DATABASE_URL','')
path=Path(url.removeprefix('sqlite:///')).resolve()
if not url.startswith('sqlite:///') or path.parent!=ROOT/'data' or not path.name.startswith('test-collections-quick') or not path.is_file():
    raise SystemExit('Use a new test-collections-quick*.db fixture')
from database import SessionLocal
from models import DownloadEpisode,DownloadJob,MediaFile,DownloadCatalog
from sqlalchemy import select
with SessionLocal() as session:
    for media in session.scalars(select(MediaFile)):
        media.manual_status='pending'
    catalog=session.scalar(select(DownloadCatalog))
    episode=DownloadEpisode(catalog_id=catalog.id,video_id='880099',title='合成第99集文件缺失',episode_number=99,original_url='https://www.douyin.com/video/880099',status='approved',access='public')
    session.add(episode);session.flush()
    job=DownloadJob(episode_id=episode.id,status='verified');session.add(job);session.flush()
    session.add(MediaFile(job_id=job.id,relative_path='quick-reviews-ui/missing.mp4',size=2048,duration=3,format='mp4',tracks='[]',sha256='0'*64,validation_note='合成缺失测试'))
    session.commit()
print('Prepared four synthetic pending-media cases')
