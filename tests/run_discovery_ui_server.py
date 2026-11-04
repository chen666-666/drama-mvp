"""Isolated discovery UI fixture. All source requests are synthetic, no media downloads."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
url=os.getenv('DRAMA_DATABASE_URL','')
path=Path(url.removeprefix('sqlite:///')).resolve()
port=int(os.getenv('TOP3_UI_PORT','8002'))
if not url.startswith('sqlite:///') or path.parent!=ROOT/'data' or not path.name.startswith('test-discovery-ui-') or path.exists() or port not in (8001,8002):
    raise SystemExit('Use a new data/test-discovery-ui-*.db and isolated port 8001/8002')
os.environ.update(DRAMA_EPISODE_DISCOVERY='1',DRAMA_SITE_MODE='manage',
    DRAMA_SITE_ORIGIN=f'http://127.0.0.1:{port}',DRAMA_ADMIN_USER='synthetic-admin',
    DRAMA_SESSION_SECRET='synthetic-discovery-ui-'*4)
import auth
os.environ['DRAMA_ADMIN_PASSWORD_HASH']=auth.password_hash('synthetic-password')
from database import init_db, SessionLocal
from models import CollectionRun, ReviewWorkflow, DownloadCatalog, DownloadEpisode
from sqlalchemy import select
from services.common import KEYWORDS
from services import review_workflows as flows
from tests.test_top3 import RankingTests, CUTOFF
from tests.test_episode_discovery import FakeSource, page, video
from workers.episode_discovery import process_turn
init_db()
with SessionLocal() as session:
    f=RankingTests();f.session=session;f.counter=0
    f.run=CollectionRun(name='自动补集合成页面验收',mode='sample',cutoff_at=CUTOFF,window_hours=168,
                        keywords=json.dumps(KEYWORDS),official_metadata='{}')
    session.add(f.run);session.flush()
    f.import_rows([f.row(author_id='701',author_url='https://www.douyin.com/user/sec701',
        collection_url='https://www.douyin.com/collection/601',episode_number='8',likes='90000')])
    f.approve_all();ranking=f.ranking()
    catalog=session.scalar(select(DownloadCatalog));episode=session.scalar(select(DownloadEpisode))
    flow=ReviewWorkflow(operation_key='completed-ui-fixture',run_id=f.run.id,ranking_id=ranking['id'],
        status='complete',stage='prepare',message='合成已完成流程：新增候选需另建补充准备',
        payload=json.dumps({'search_requested':False,'baseline':[],'catalogs':{str(catalog.id):{
            'episode_ids':[episode.id],'stamps':{str(episode.id):flows.episode_stamp(episode)},'state':'complete'}},
            'events':[],'operations':{}}))
    session.add(flow);session.commit()

import main
from fastapi import Depends, Request
from pydantic import BaseModel
class Advance(BaseModel):
    turns: int=1
source=FakeSource({('mix','601','0'):page([video('90002'),video('90003',title='第4集',mix=''),
    video('90004',uid='different'),video('90005',title='合成测试剧名1至3合集')],1,True),
    ('mix','601','1'):page([video('90006',title='合成测试剧名第十三集')],2)})
@main.app.post('/api/test/discovery/advance')
def advance(body:Advance,request:Request):
    main.local_write(request)
    if not 1<=body.turns<=30:raise ValueError('Synthetic bounded turns only')
    for _ in range(body.turns):process_turn(SessionLocal,source,'synthetic-ui')
    return {'calls':len(source.calls)}

if __name__=='__main__':
    import uvicorn
    with tempfile.TemporaryDirectory() as directory:
        auth.PRIVATE=Path(directory)
        uvicorn.run(main.app,host='127.0.0.1',port=port,log_level='warning')
