"""Isolated cross-batch UI fixture. All platform responses are synthetic."""
from datetime import timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.getenv('DRAMA_DATABASE_URL', '')
path = Path(url.removeprefix('sqlite:///')).resolve()
if not url.startswith('sqlite:///') or path.parent != ROOT / 'data' or not path.name.startswith('test-candidate-pool-ui-flow-') or path.exists():
    raise SystemExit('Use a new data/test-candidate-pool-ui-flow-*.db only')
port = int(os.getenv('TOP3_UI_PORT', '8001'))
if port not in (8001, 8002):
    raise SystemExit('Isolated port required')

import auth
os.environ.update(DRAMA_SITE_ORIGIN=f'http://127.0.0.1:{port}', DRAMA_ADMIN_USER='synthetic-admin',
    DRAMA_ADMIN_PASSWORD_HASH=auth.password_hash('synthetic-password'), DRAMA_SESSION_SECRET='synthetic-candidate-pool-'*4)
from database import SessionLocal, init_db
from models import CollectionRun, Episode, Series
from services import candidate_pool, search
from services.common import iso, utcnow
from services.ranking import make_ranking
from services.review import review
from sources.douk import SourceUnavailable
from sources.manual import import_row
from sqlalchemy import select
import uvicorn

cutoff = utcnow() - timedelta(minutes=2)
published = (cutoff-timedelta(days=1)).replace(microsecond=0)


def video(number):
    index = int(number)-889000
    return {'aweme_id':str(number), 'desc':f'合成作品{index}第1集',
        'create_time':int(published.replace(tzinfo=timezone.utc).timestamp()),
        'author':{'uid':str(777000+index),'nickname':f'合成作者{index}'},
        'statistics':{'digg_count':120000-index*10000,'comment_count':2,'collect_count':3,'share_count':4}}


init_db()
with SessionLocal() as session:
    old = CollectionRun(name='合成旧批次',mode='sample',cutoff_at=cutoff-timedelta(hours=1),window_hours=168,
        keywords=json.dumps(['AI短剧']),official_metadata='{}')
    session.add(old);session.flush()
    for index in range(1,4):
        number=str(889000+index)
        import_row(session,old.id,{'video_id':number,'original_url':f'https://www.douyin.com/video/{number}',
            'title':f'合成作品{index}第1集','series_key':number,'series_title':f'合成旧作品{index}',
            'author_id':str(777000+index),'author_name':f'合成作者{index}','ai_type':'AI动画',
            'published_at':iso(published),'sampled_at':iso(cutoff-timedelta(hours=1)),
            'likes':90000-index*1000,'comments':1,'favorites':1,'shares':1})
    for series in session.scalars(select(Series)):
        review(session,{'target_type':'series','target_id':series.id,'status':'approved','reason':'合成原作者声明',
            'evidence_url':'https://www.douyin.com/','ai_type':'AI动画','ai_basis':'合成制作依据',
            'checks':{'story':True,'ai':True,'original_author':True}})
    for episode in session.scalars(select(Episode)):
        review(session,{'target_type':'episode','target_id':episode.id,'status':'approved','reason':'合成正片核验',
            'evidence_url':episode.original_url,'checks':{'main_episode':True,'ownership':True,'public':True,'not_duplicate':True}})
    make_ranking(session,old)
    new = CollectionRun(name='合成新批次',mode='sample',cutoff_at=cutoff,window_hours=168,
        keywords=json.dumps(['AI短剧']),official_metadata='{}')
    session.add(new);session.flush();candidate_pool.seed(session,new);session.commit()


class SyntheticSource:
    failed_once = False
    def search_page(self,*args):
        return {'videos':[video('889004')],'has_more':False,'offset':0}

    def refresh_video(self,number):
        if number=='889002' and not SyntheticSource.failed_once:
            SyntheticSource.failed_once=True
            raise SourceUnavailable('failed','合成详情更新失败')
        return video(number)


def dispatch(identifier):
    threading.Thread(target=search.run_job,args=(identifier,),kwargs={
        'source':SyntheticSource(),'pause':lambda _:None},daemon=True).start()


with tempfile.TemporaryDirectory() as directory, patch.object(auth,'PRIVATE',Path(directory)),patch.object(search,'dispatch',dispatch):
    import main
    with patch.object(main,'dispatch',dispatch):
        uvicorn.run(main.app,host='127.0.0.1',port=port,log_level='warning')
