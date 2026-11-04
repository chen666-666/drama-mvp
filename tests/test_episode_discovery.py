"""Synthetic discovery: real transactions and failure boundaries, no platform/media requests."""
import asyncio
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from models import (Base, Series, Episode, RankingRun, SearchHit, MetricSnapshot, DownloadEpisode,
                    DownloadCatalog, DownloadJob, DiscoveryTask, DiscoverySource, DiscoveryVideo,
                    DiscoveryObservation, DiscoverySubscription, DiscoveryMatch, ReviewWorkflow)
from services import episode_discovery as discovery, review_workflows as flows, downloads
from services.common import utcnow
from sources.episode_discovery import DiscoverySource as Adapter, validate_page, public_metadata, number_hint
from sources.douk import SourceUnavailable
from sources.bridge_scheduler import RequestGate
from workers.episode_discovery import process_turn, writing
from tests import test_top3


def video(vid='90001', title='合成测试剧名 第十二集', uid='701', sec='sec701', mix='601'):
    return {'aweme_id':vid,'desc':title,'author':{'uid':uid,'sec_uid':sec},'mix_info':{'mix_id':mix},
            'create_time':1,'statistics':{'digg_count':0},'play_addr':{'url_list':['https://secret/?signature=abc']}}


def page(videos=(), cursor=0, more=False):
    return {'videos':list(videos),'cursor':cursor,'has_more':more}


class FakeSource:
    def __init__(self, replies=None):
        self.calls=[];self.replies=replies or {}
    def fetch(self,kind,key,cursor):
        self.calls.append((kind,key,cursor))
        result=self.replies.get((kind,key,cursor))
        if isinstance(result,Exception):
            raise result
        if result is not None:
            return result
        if kind=='detail':
            return page([video(key)])
        if kind=='mix':
            return page([video('90002')],1)
        return page([],1)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.feature=patch.dict(os.environ,{'DRAMA_EPISODE_DISCOVERY':'0','DRAMA_SITE_MODE':'manage'})
        self.feature.start()
        self.f=test_top3.RankingTests();self.f.setUp();self.s=self.f.session
        self.f.import_rows([self.f.row(author_id='701',author_url='https://www.douyin.com/user/sec701',
            collection_url='https://www.douyin.com/collection/601',episode_number='8',likes='90000')])
        self.f.approve_all();self.ranking=self.f.ranking();self.sid=self.ranking['items'][0]['series_id']
        self.s.commit();os.environ['DRAMA_EPISODE_DISCOVERY']='1'
        self.factory=sessionmaker(bind=self.f.engine,autoflush=False)
    def tearDown(self):
        self.f.tearDown();self.feature.stop()
    def register(self):
        task=discovery.register(self.s,self.ranking['id'],self.sid);self.s.commit()
        self.tid=task.id
        return task
    def task(self):
        self.s.expire_all();return self.s.get(DiscoveryTask,self.tid)
    def run_all(self, source=None, limit=30):
        source=source or FakeSource()
        for _ in range(limit):
            if not process_turn(self.factory,source,'test-worker',lambda _:None):
                break
        return source
    def test_disabled_generation_and_draft_are_read_only_for_discovery(self):
        os.environ['DRAMA_EPISODE_DISCOVERY']='0'
        self.f.ranking()
        os.environ['DRAMA_EPISODE_DISCOVERY']='1'
        from services.ranking import make_ranking
        make_ranking(self.s,self.f.run,persist=False)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoveryTask)),0)
    def test_formal_generation_registers_only_selected_and_is_idempotent(self):
        result=self.f.ranking();task=self.s.scalar(select(DiscoveryTask))
        self.assertIsNotNone(task)
        discovery.register_ranking(self.s,self.s.get(RankingRun,result['id']))
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoverySubscription)),1)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoveryTask)),1)
    def test_ranking_and_registration_roll_back_together(self):
        before=self.s.scalar(select(func.count()).select_from(RankingRun));self.s.rollback()
        with patch.object(discovery,'sync',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):self.f.ranking()
        self.s.rollback()
        self.assertEqual(self.s.scalar(select(func.count()).select_from(RankingRun)),before)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoveryTask)),0)
    def test_multiple_rankings_share_task_but_old_ranking_not_subscribed(self):
        result=self.f.ranking();result2=self.f.ranking()
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoveryTask)),1)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoverySubscription)),2)
        self.assertFalse(self.s.scalar(select(DiscoverySubscription).where(DiscoverySubscription.ranking_id==self.ranking['id'])))
        task=self.s.scalar(select(DiscoveryTask));self.assertTrue(json.loads(task.progress)['pending_update'])
    def test_old_low_like_videos_are_pending_without_ranking_imports(self):
        self.register()
        before=(self.s.scalar(select(func.count()).select_from(SearchHit)),self.s.scalar(select(func.count()).select_from(MetricSnapshot)))
        snap=self.s.get(RankingRun,self.ranking['id']).snapshot
        self.run_all()
        self.assertEqual(self.task().status,'completed')
        row=self.s.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id=='90002'))
        self.assertEqual((row.status,row.access,row.episode_number),('pending','unknown',12))
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DownloadJob)),0)
        self.assertEqual((self.s.scalar(select(func.count()).select_from(SearchHit)),self.s.scalar(select(func.count()).select_from(MetricSnapshot))),before)
        self.assertEqual(self.s.get(RankingRun,self.ranking['id']).snapshot,snap)
        self.assertIsNone(self.s.scalar(select(Episode).where(Episode.video_id=='90002')))
    def test_multi_page_and_unknown_total_checks_author_even_after_mix_end(self):
        self.register()
        source=FakeSource({('mix','601','0'):page([video('90002')],1,True),
                           ('mix','601','1'):page([video('90003')],2,True),
                           ('mix','601','2'):page([video('90004')],3,False)})
        self.run_all(source)
        self.assertIn(('mix','601','2'),source.calls)
        self.assertTrue(any(c[0]=='author' for c in source.calls))
        self.assertEqual(self.task().status,'completed')
    def test_same_author_generic_other_author_promotional_and_compilation_are_separate(self):
        self.register()
        rows=[video('90002',title='第十二集',mix=''),video('90003',uid='999'),
              video('90004',title='合成测试剧名预告'),video('90005',title='合成测试剧名1至12集合集'),
              video('90006',title='合成测试剧名 第3集',mix='')]
        self.run_all(FakeSource({('mix','601','0'):page(rows,1)}))
        matches={self.s.get(DiscoveryVideo,m.video_id).video_id:m.kind for m in self.s.scalars(select(DiscoveryMatch))}
        self.assertEqual([matches[str(i)] for i in range(90002,90007)],['clue','conflict','promotional','compilation','matched'])
        self.assertIsNone(self.s.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id=='90002')))
    def test_existing_human_decisions_are_not_overwritten_on_rediscovery(self):
        task=self.register();self.run_all()
        row=self.s.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id=='90002'))
        row.status='excluded';row.title='人工标题';row.access='private';row.review_history='[{"note":"人工排除"}]';self.s.commit()
        discovery.control(self.s,self.task(),'update');self.s.commit();self.run_all()
        self.s.refresh(row)
        self.assertEqual((row.status,row.title,row.access,row.review_history),('excluded','人工标题','private','[{"note":"人工排除"}]'))
    def test_invalid_pages_and_cycles_never_advance_cursor(self):
        self.register();process_turn(self.factory,FakeSource(),'a')
        source=FakeSource({('mix','601','0'):page([video()],0,True)})
        process_turn(self.factory,source,'a')
        self.assertEqual(self.task().status,'failed')
        row=self.s.scalar(select(DiscoverySource).where(DiscoverySource.kind=='mix',DiscoverySource.mode=='head'))
        self.assertEqual(row.cursor,'0');self.assertFalse(row.ended)
    def test_page_commit_failure_retains_cursor_and_no_partial_candidates(self):
        self.register();process_turn(self.factory,FakeSource(),'a')
        with patch.object(discovery,'sync',side_effect=RuntimeError('commit boundary')):
            process_turn(self.factory,FakeSource(),'a')
        self.assertEqual(self.task().status,'failed')
        self.assertIsNone(self.s.scalar(select(DiscoveryVideo).where(DiscoveryVideo.video_id=='90002')))
        row=self.s.scalar(select(DiscoverySource).where(DiscoverySource.kind=='mix',DiscoverySource.mode=='head'))
        self.assertEqual(row.cursor,'0')
    def test_pause_while_request_in_flight_prevents_page_and_next_request(self):
        self.register();test=self
        class Pausing(FakeSource):
            def fetch(self,*args):
                with test.factory.begin() as session:
                    discovery.control(session,session.get(DiscoveryTask,test.tid),'pause')
                return super().fetch(*args)
        source=Pausing();process_turn(self.factory,source,'a')
        self.assertEqual(self.task().status,'paused');self.assertFalse(process_turn(self.factory,source,'a'))
        self.assertEqual(len(source.calls),1)
    def test_valid_lease_not_recovered_but_expired_lease_interrupted(self):
        self.register()
        with writing(self.factory) as session:
            self.assertEqual(discovery.claim(session,'owner'),self.tid);session.commit()
        task=self.task();discovery.recover(self.s);self.assertEqual(task.status,'running')
        task.lease_until=utcnow()-timedelta(seconds=1);self.s.commit()
        discovery.recover(self.s);self.s.commit();self.assertEqual(self.task().status,'interrupted')
        self.assertFalse(process_turn(self.factory,FakeSource(),'other'))
    def test_resume_continues_committed_cursor_not_page_zero(self):
        self.register();source=FakeSource({('mix','601','0'):page([video()],7,True),
                ('mix','601','7'):SourceUnavailable('blocked','sensitive')})
        self.run_all(source);self.assertEqual(self.task().status,'blocked')
        discovery.control(self.s,self.task(),'continue');self.s.commit()
        source2=self.run_all();self.assertIn(('mix','601','7'),source2.calls)
        self.assertNotIn(('detail','1001','0'),source2.calls)
    def test_network_retries_consume_budget_and_stop_at_three(self):
        self.register();source=FakeSource({('mix','601','0'):SourceUnavailable('transient','secret cookie=abc')})
        self.run_all(source)
        self.assertEqual(sum(c[0]=='mix' for c in source.calls),3);self.assertEqual(self.task().status,'failed')
        self.assertEqual(json.loads(self.task().progress)['counts']['mix'],3)
        self.assertNotIn('secret',self.task().message)
    def test_detail_budget_is_hard_two_attempts(self):
        self.register();source=FakeSource({('detail','1001','0'):SourceUnavailable('transient','network')})
        self.run_all(source)
        self.assertEqual(len(source.calls),2);self.assertEqual(self.task().status,'budget_exhausted')
    def test_total_request_budget_stops_without_automatic_new_round(self):
        task=self.register();p=json.loads(task.progress);p['requests']=50;task.progress=discovery.encode(p);self.s.commit()
        source=self.run_all();self.assertFalse(source.calls);self.assertEqual(self.task().status,'budget_exhausted')
    def test_time_budget_includes_wait_and_persists_next_page(self):
        task=self.register();task.started_at=utcnow()-timedelta(seconds=601);self.s.commit()
        source=self.run_all();self.assertFalse(source.calls);self.assertEqual(self.task().status,'budget_exhausted')
    def test_identity_uid_sec_mapping_and_conflict(self):
        self.register();source=FakeSource({('detail','1001','0'):page([video('1001',sec='other')])})
        self.run_all(source);self.assertEqual(self.task().status,'failed')
        self.assertEqual(len(source.calls),1)
    def test_context_change_blocks_sync_and_requires_explicit_recheck(self):
        self.register();self.run_all();series=self.s.get(Series,self.sid);series.title='更正后的作品';self.s.commit()
        with self.assertRaisesRegex(ValueError,'变化'):discovery.control(self.s,self.task(),'continue')
        discovery.control(self.s,self.task(),'recheck',aliases=['旧名']);self.s.commit()
        self.assertEqual(json.loads(self.task().config)['names'],['更正后的作品','旧名'])
    def test_subscription_pause_only_affects_its_directory(self):
        self.register();second=self.f.ranking();self.s.commit()
        sub=self.s.scalar(select(DiscoverySubscription).where(DiscoverySubscription.ranking_id==self.ranking['id']))
        sub.active=False;self.s.commit();self.run_all()
        original=self.s.get(DownloadCatalog,sub.catalog_id)
        self.assertIsNone(self.s.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id==original.id,DownloadEpisode.video_id=='90002')))
        self.assertIsNotNone(self.s.scalar(select(DownloadEpisode).join(DownloadCatalog).where(DownloadCatalog.ranking_id==second['id'],DownloadEpisode.video_id=='90002')))
    def test_feature_switch_off_prevents_claiming_and_public_mode_overrides_on(self):
        self.register();os.environ['DRAMA_EPISODE_DISCOVERY']='0'
        self.assertFalse(process_turn(self.factory,FakeSource(),'a'))
        os.environ.update(DRAMA_EPISODE_DISCOVERY='1',DRAMA_SITE_MODE='public')
        self.assertFalse(process_turn(self.factory,FakeSource(),'a'))
    def test_completed_preparation_gets_explicit_supplement_not_reopened(self):
        flow=ReviewWorkflow(operation_key='completed-flow',run_id=self.f.run.id,ranking_id=self.ranking['id'],
            status='complete',stage='prepare',payload=json.dumps({'catalogs':{},'events':[]}))
        self.s.add(flow);self.s.flush()
        new=flows.supplement(self.s,flow,'new-supplement')
        self.assertEqual((flow.status,new.status,new.stage),('complete','active','scope'))
        self.assertEqual(flows.supplement(self.s,flow,'new-supplement').id,new.id)
        self.assertEqual(json.loads(new.payload)['catalogs'],{})
    def test_async_addition_does_not_change_scope_stamp_or_create_download(self):
        self.register();catalog=self.s.scalar(select(DownloadCatalog));row=self.s.scalar(select(DownloadEpisode))
        stamp=flows.episode_stamp(row)
        self.run_all()
        self.assertEqual(flows.episode_stamp(self.s.get(DownloadEpisode,row.id)),stamp)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DownloadJob)),0)
    def test_cursor_cycle_across_committed_pages_is_not_end_of_directory(self):
        self.register()
        source=FakeSource({('mix','601','0'):page([video('90002')],1,True),
                           ('mix','601','1'):page([video('90003')],2,True),
                           ('mix','601','2'):page([video('90004')],1,True)})
        self.run_all(source)
        self.assertEqual(self.task().status,'failed')
        history=self.s.scalar(select(DiscoverySource).where(DiscoverySource.kind=='mix',DiscoverySource.mode=='history'))
        self.assertEqual(history.cursor,'2');self.assertFalse(history.ended)
    def test_incremental_head_reads_multiple_pages_and_keeps_history_cursor(self):
        self.register()
        source=FakeSource({('mix','601','0'):page([video('90002')],1,True),
                           ('mix','601','1'):page([video('90003')],2,True),
                           ('mix','601','2'):page([video('90004')],3,True),
                           ('mix','601','3'):page([],4,False)})
        self.run_all(source)
        history=self.s.scalar(select(DiscoverySource).where(DiscoverySource.kind=='mix',DiscoverySource.mode=='history'))
        self.assertEqual(history.cursor,'4')
        discovery.control(self.s,self.task(),'update');self.s.commit()
        fresh=FakeSource({('mix','601','0'):page([video('91000')],10,True),
                          ('mix','601','10'):page([video('91001')],11,True),
                          ('mix','601','11'):page([video('90002')],12,True),
                          ('mix','601','12'):page([video('90003')],13,True)})
        self.run_all(fresh)
        self.assertTrue(all(('mix','601',c) in fresh.calls for c in ('0','10','11','12')))
        self.s.refresh(history);self.assertEqual(history.cursor,'4')
        self.assertNotIn(('mix','601','13'),fresh.calls)
    def test_two_competing_workers_only_one_claims_same_series(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        self.register();self.s.commit()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test-claims.db'
            connection=self.f.engine.raw_connection()
            with closing(sqlite3.connect(path)) as target:connection.driver_connection.backup(target)
            connection.close()
            engine=create_engine(f'sqlite:///{path.as_posix()}',connect_args={'check_same_thread':False})
            factory=sessionmaker(bind=engine,autoflush=False)
            barrier=threading.Barrier(2)
            def claim(owner):
                barrier.wait()
                with writing(factory) as s:
                    value=discovery.claim(s,owner);s.commit();return value
            try:
                with ThreadPoolExecutor(2) as pool:results=list(pool.map(claim,['one','two']))
                self.assertEqual(sorted(v for v in results if v is not None),[self.tid])
            finally:engine.dispose()
    def test_management_get_is_read_only_and_anonymous_cannot_access(self):
        import main
        from tests.auth_support import AdminClient
        self.register()
        def isolated():
            with self.factory() as session:
                yield session;session.commit()
        main.app.dependency_overrides[main.database_session]=isolated
        try:
            with patch.object(main,'init_db'),patch.object(main,'recover_jobs'),AdminClient(main.app) as client:
                before=self.s.scalar(select(func.count()).select_from(DiscoveryVideo))
                for _ in range(2):
                    self.assertEqual(client.get('/api/control/discovery/catalogs/1').status_code,200)
                self.assertEqual(self.s.scalar(select(func.count()).select_from(DiscoveryVideo)),before)
                client.cookies.clear()
                self.assertEqual(client.get('/api/control/discovery/catalogs/1').status_code,401)
                self.assertEqual(client.post('/api/control/discovery/catalogs/1',json={'action':'start'}).status_code,401)
        finally:main.app.dependency_overrides.clear()


class AdapterAndGateTests(unittest.TestCase):
    def test_search_explicitly_unrestricted_and_independent_of_importer(self):
        with patch('sources.episode_discovery.bridge_request',return_value={'videos':[],'offset':1,'has_more':False}) as call:
            Adapter().fetch('search','已确认剧名',0)
        self.assertEqual(call.call_args.args[1]['publish_time'],0)
    def test_missing_wrong_type_empty_more_and_stuck_cursor_fail(self):
        invalid=[{},page([],1,True),page([video()],0,True),{'videos':[],'cursor':0,'has_more':0},
                 {'videos':[],'cursor':'0','has_more':False}]
        for result in invalid:
            with self.subTest(result=result),self.assertRaises(SourceUnavailable):validate_page(result,0)
        self.assertFalse(validate_page(page([],0,False),0)['has_more'])
    def test_public_metadata_whitelist_and_conservative_number_hints(self):
        meta=public_metadata(video(title='第十二集 cookie=secret https://x/?signature=abc'))
        self.assertEqual(meta['number'],12)
        self.assertNotIn('secret',json.dumps(meta));self.assertNotIn('play_addr',meta)
        for title in ['第1至12集','第八集上集','第十二集与第十三集','2026年新剧']:
            self.assertIsNone(number_hint(title))
        self.assertEqual(number_hint('第一百二十三集'),123)
    def test_global_fifo_spacing_and_account_gate(self):
        async def check():
            now=[0.0];starts=[]
            async def wait(seconds):now[0]+=seconds
            gate=RequestGate(clock=lambda:now[0],sleep=wait)
            async def call(kind):
                starts.append((kind,now[0]));return {'status':'needs_login' if kind=='directory' else 'ok'}
            results=await asyncio.gather(*[gate.run(lambda k=k:call(k)) for k in ['search','detail','directory','media']])
            self.assertEqual([x[0] for x in starts],['search','detail','directory'])
            self.assertEqual([x[1] for x in starts],[0,2,4]);self.assertEqual(results[-1]['status'],'needs_login')
            await gate.resume();await gate.run(lambda:call('media'));self.assertEqual(starts[-1],('media',6))
        asyncio.run(check())
    def test_login_gate_survives_bridge_restart_until_explicit_resume(self):
        async def check(path):
            gate=RequestGate(interval=0,state_path=path)
            await gate.run(lambda:asyncio.sleep(0,result={'status':'blocked'}))
            restarted=RequestGate(interval=0,state_path=path)
            called=[]
            async def call():called.append(True);return {'status':'ok'}
            self.assertEqual((await restarted.run(call))['status'],'blocked')
            self.assertFalse(called)
            await restarted.resume();await restarted.run(call);self.assertEqual(called,[True])
        with tempfile.TemporaryDirectory() as tmp:asyncio.run(check(Path(tmp)/'source-gate.json'))


class DiscoveryMigrationTests(unittest.TestCase):
    def test_migration_keeps_nonempty_snapshots_and_human_reviews(self):
        from services.migration import upgrade_database
        f=test_top3.RankingTests();f.setUp()
        try:
            with patch.dict(os.environ,{'DRAMA_EPISODE_DISCOVERY':'0'}):
                f.import_rows([f.row(likes='90000')]);f.approve_all();f.ranking();f.session.commit()
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'test-populated-008.db'
                raw=f.engine.raw_connection()
                try:
                    with closing(sqlite3.connect(path)) as db:
                        raw.driver_connection.backup(db)
                        for name in ('discovery_subscriptions','discovery_matches','discovery_observations','discovery_videos','discovery_sources','discovery_tasks'):
                            db.execute('drop table '+name)
                        for name in ('episodes', 'download_episodes'):
                            db.execute('alter table '+name+' drop column episode_range')
                        db.execute('create table alembic_version (version_num text primary key)')
                        db.execute("insert into alembic_version values ('008_candidate_pool')");db.commit()
                        names=[n for n, in db.execute("select name from sqlite_master where type='table' and name!='alembic_version'")]
                        columns={name: ','.join('"'+row[1]+'"' for row in db.execute('pragma table_info('+name+')')) for name in names}
                        before={name:db.execute('select '+columns[name]+' from '+name+' order by rowid').fetchall() for name in names}
                finally:raw.close()
                engine=create_engine(f'sqlite:///{path.as_posix()}')
                try:
                    upgrade_database(engine)
                    with closing(sqlite3.connect(path)) as db:
                        for name,rows in before.items():
                            self.assertEqual(db.execute('select '+columns[name]+' from '+name+' order by rowid').fetchall(),rows)
                        self.assertTrue(db.execute('select snapshot from ranking_runs').fetchone()[0])
                        self.assertEqual(db.execute('select count(*) from review_records').fetchone()[0],2)
                finally:engine.dispose()
        finally:f.tearDown()
    def test_008_upgrade_preserves_all_existing_rows(self):
        from alembic import command
        from alembic.config import Config
        from services.migration import ROOT, upgrade_database
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test-discovery.db';engine=create_engine(f'sqlite:///{path.as_posix()}')
            try:
                cfg=Config(str(ROOT/'alembic.ini'))
                with engine.begin() as connection:
                    cfg.attributes['connection']=connection;command.upgrade(cfg,'008_candidate_pool')
                with closing(sqlite3.connect(path)) as db:
                    before={name:db.execute('select * from "'+name+'"').fetchall() for name, in db.execute("select name from sqlite_master where type='table' and name!='alembic_version'")}
                upgrade_database(engine)
                with closing(sqlite3.connect(path)) as db:
                    for name,rows in before.items():self.assertEqual(db.execute('select * from "'+name+'"').fetchall(),rows)
                    self.assertEqual(db.execute('pragma integrity_check').fetchone()[0],'ok')
                    self.assertEqual(db.execute('pragma foreign_key_check').fetchall(),[])
                    self.assertEqual(db.execute('select count(*) from discovery_tasks').fetchone()[0],0)
            finally:engine.dispose()


class DiscoveryPublicExportTests(unittest.TestCase):
    def test_private_discovery_facts_do_not_enter_public_copy(self):
        from tests.test_publishing import PublishingTests
        feature=patch.dict(os.environ,{'DRAMA_EPISODE_DISCOVERY':'0','DRAMA_SITE_MODE':'manage'})
        feature.start();fixture=PublishingTests();fixture.setUp()
        try:
            os.environ['DRAMA_EPISODE_DISCOVERY']='1'
            s=fixture.session
            discovery.register(s,fixture.ranking['id'],fixture.catalog.series_id)
            s.add(DiscoveryVideo(video_id='777777',metadata_json=json.dumps({'title':'内部待辨认线索'})))
            fixture.save_source()
            fixture.build(preview=False,release_id='discovery-private-facts')
            with closing(sqlite3.connect(fixture.root/'publish/discovery-private-facts/public.db')) as db:
                for name in ('discovery_tasks','discovery_sources','discovery_subscriptions','discovery_videos','discovery_matches','discovery_observations'):
                    self.assertEqual(db.execute('select count(*) from '+name).fetchone()[0],0)
            self.assertEqual(s.scalar(select(func.count()).select_from(DiscoveryTask)),1)
        finally:fixture.tearDown();feature.stop()
