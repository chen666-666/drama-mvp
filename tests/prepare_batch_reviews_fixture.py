"""Extend only a fresh collections UI fixture with synthetic batch-review cases."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
url=os.getenv('DRAMA_DATABASE_URL','')
path=Path(url.removeprefix('sqlite:///')).resolve()
if not url.startswith('sqlite:///') or path.parent!=ROOT/'data' or not path.name.startswith('test-collections-batch') or not path.is_file():
    raise SystemExit('Use an existing new test-collections-batch*.db fixture')
from database import SessionLocal
from models import DownloadCatalog,DownloadEpisode
from sqlalchemy import select
with SessionLocal() as session:
    catalog=session.scalar(select(DownloadCatalog))
    for vid,title,number in [('810003','合成鳄鱼缅北第3集',3),('810004','合成鳄鱼缅北第4集',4),
        ('810005','合成鳄鱼缅北第五集',None),('810006','合成鳄鱼缅北第6集预告',6),
        ('810007','合成鳄鱼缅北第7集',7),('810008','合成鳄鱼缅北第7集重发',7)]:
        session.add(DownloadEpisode(catalog_id=catalog.id,video_id=vid,title=title,episode_number=number,
            original_url='https://www.douyin.com/video/'+vid))
    session.commit()
print('Added six synthetic review cases')
