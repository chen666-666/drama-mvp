"""Cross-batch discovery omissions, fresh sampling, persistent recovery and history protection."""
from datetime import timedelta
from contextlib import closing
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from models import Author, CollectionRun, Episode, MetricSnapshot, RankingRun, ReviewRecord, RunCandidate, SearchHit, SearchJob, Series
from services import candidate_pool, search
from services.ranking import candidates, make_ranking
from services.review import review
from sources.douk import DouKSource, SourceUnavailable
from tests import test_search as search_fixtures

CUTOFF, FakeSource, video = search_fixtures.CUTOFF, search_fixtures.FakeSource, search_fixtures.video


class DetailSource(FakeSource):
    def __init__(self, responses=(), details=None):
        super().__init__(responses)
        self.details, self.detail_calls = details or {}, []

    def refresh_video(self, video_id):
        self.detail_calls.append(video_id)
        value = self.details[video_id]
        if isinstance(value, Exception):
            raise value
        return value


class CandidatePoolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = search_fixtures.SearchTests()
        self.fixture.setUp()
        self.factory, self.engine, self.run_id = self.fixture.factory, self.fixture.engine, self.fixture.run_id

    def tearDown(self):
        self.fixture.tearDown()

    def old_videos(self, numbers=('30001', '30002', '30003')):
        jid = self.fixture.job(1)
        search.run_job(jid, self.factory, FakeSource([{'videos': [video(n) for n in numbers], 'has_more': False}]), lambda _: None)
        with self.factory() as session:
            for s in session.scalars(select(Series)):
                review(session, {'target_type':'series','target_id':s.id,'status':'approved','reason':'合成原作者依据',
                    'evidence_url':'https://www.douyin.com/','ai_type':'AI漫剧','ai_basis':'合成声明',
                    'checks':{'story':True,'ai':True,'original_author':True}})
            for e in session.scalars(select(Episode)):
                review(session, {'target_type':'episode','target_id':e.id,'status':'approved','reason':'合成正片核验',
                    'evidence_url':e.original_url,'checks':{'main_episode':True,'ownership':True,'public':True,'not_duplicate':True}})
            session.commit()

    def new_run(self, cutoff=CUTOFF):
        with self.factory() as session:
            run = CollectionRun(name='新批次', mode='sample', cutoff_at=cutoff, window_hours=168,
                keywords=json.dumps(['AI短剧']), official_metadata='{}')
            session.add(run);session.flush()
            candidate_pool.seed(session, run)
            session.commit()
            return run.id

    def job(self, run_id, refresh_only=False):
        with self.factory() as session:
            job=search.prepare_job(session, session.get(CollectionRun,run_id), [] if refresh_only else ['likes'],1,refresh_only=refresh_only)
            session.commit()
            return job.id

    def rows(self, run_id):
        with self.factory() as session:
            return candidates(session, session.get(CollectionRun, run_id))

    def test_three_missing_search_results_still_rank_after_detail_refresh_and_history_unchanged(self):
        self.old_videos()
        with self.factory() as session:
            old=make_ranking(session,session.get(CollectionRun,self.run_id));session.commit()
            old_bytes=session.get(RankingRun,old['id']).snapshot
            review_count=session.scalar(select(func.count(ReviewRecord.id)))
        rid=self.new_run()
        rows=self.rows(rid)
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['likes'] is None and r['previous_sample']['likes']==50001 for r in rows))
        with self.factory() as session:
            self.assertTrue(make_ranking(session,session.get(CollectionRun,rid),persist=False)['generation_blockers'])
            with self.assertRaisesRegex(ValueError,'互动更新'):
                make_ranking(session,session.get(CollectionRun,rid))
        source=DetailSource([{'videos':[],'has_more':False}],{n:video(n) for n in ['30001','30002','30003']})
        jid=self.job(rid);search.run_job(jid,self.factory,source,lambda _:None)
        with self.factory() as session:
            ranked=make_ranking(session,session.get(CollectionRun,rid))
            self.assertEqual(len(ranked['items']),3)
            self.assertEqual(ranked['candidate_summary']['carried'],3)
            self.assertEqual(session.get(RankingRun,old['id']).snapshot,old_bytes)
            self.assertEqual(session.scalar(select(func.count(ReviewRecord.id))),review_count)
            self.assertEqual(session.scalar(select(func.count(SearchHit.id)).where(SearchHit.run_id==rid)),0)
            self.assertEqual(session.get(SearchJob,jid).status,'completed')
            self.assertEqual(json.loads(session.get(SearchJob,jid).progress)['requests'],1)
            self.assertEqual(len(source.detail_calls),3)

    def test_duplicate_search_hits_refresh_once_without_rechecking_confirmed_metadata(self):
        self.old_videos(('30001',));rid=self.new_run()
        with self.factory() as session:
            e=session.scalar(select(Episode));e.title='人工更正标题';e.episode_number=3;session.commit()
        source=DetailSource([{'videos':[video(),video()],'has_more':False}])
        search.run_job(self.job(rid),self.factory,source,lambda _:None)
        self.assertEqual(source.detail_calls,[])
        row=self.rows(rid)[0]
        self.assertEqual(row['title'],'人工更正标题');self.assertEqual(row['episode_number'],3)
        self.assertEqual(row['episode_status'],'approved');self.assertEqual(len(row['sources']),2)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(RunCandidate.id)).where(RunCandidate.run_id==rid)),1)

    def test_failure_is_visible_and_retry_resumes_details_without_keyword_search(self):
        self.old_videos(('30001','30002'));rid=self.new_run();jid=self.job(rid)
        source=DetailSource([{'videos':[],'has_more':False}],{'30001':RuntimeError('cookie=private'), '30002':video('30002')})
        search.run_job(jid,self.factory,source,lambda _:None)
        with self.factory() as session:
            job=session.get(SearchJob,jid)
            self.assertEqual(job.status,'failed')
            self.assertNotIn('private',json.dumps(candidates(session,session.get(CollectionRun,rid))))
            search.prepare_resume(session,job);session.commit()
        resumed=DetailSource(details={'30001':video()})
        search.run_job(jid,self.factory,resumed,lambda _:None)
        self.assertEqual(resumed.calls,[]);self.assertEqual(resumed.detail_calls,['30001'])
        self.assertTrue(all(r['membership']['refresh_status']=='refreshed' for r in self.rows(rid)))

    def test_login_and_restart_resume_preserve_detail_queue(self):
        self.old_videos(('30001','30002'));rid=self.new_run();jid=self.job(rid)
        source=DetailSource([{'videos':[],'has_more':False}],{'30001':video(), '30002':SourceUnavailable('needs_login','secret-cookie')})
        search.run_job(jid,self.factory,source,lambda _:None)
        with self.factory() as session:
            job=session.get(SearchJob,jid);self.assertEqual(job.status,'needs_login')
            self.assertNotIn('secret-cookie',job.message)
            search.prepare_resume(session,job);session.commit()
        search.recover_jobs(self.factory)
        with self.factory() as session:
            job=session.get(SearchJob,jid);self.assertEqual(job.status,'interrupted')
            search.prepare_resume(session,job);session.commit()
        resumed=DetailSource(details={'30002':video('30002')})
        search.run_job(jid,self.factory,resumed,lambda _:None)
        self.assertEqual(resumed.detail_calls,['30002']);self.assertEqual(resumed.calls,[])

    def test_window_boundaries_unknown_dates_and_official_isolation(self):
        self.old_videos(('30001','30002','30003'))
        with self.factory() as session:
            episodes=list(session.scalars(select(Episode).order_by(Episode.id)))
            episodes[0].published_at=CUTOFF-timedelta(hours=168)
            episodes[1].published_at=CUTOFF
            episodes[2].published_at=CUTOFF-timedelta(hours=168,microseconds=1)
            session.commit()
        rid=self.new_run();self.assertEqual({r['video_id'] for r in self.rows(rid)},{'30001','30002'})
        with self.factory() as session:
            run=session.get(CollectionRun,rid);run.mode='official';candidate_pool.seed(session,run,reset=True)
            self.assertTrue(all(m.refresh_status=='pending' for m in session.scalars(select(RunCandidate).where(RunCandidate.run_id==rid))))
            with self.assertRaisesRegex(ValueError,'官方榜'):
                search.prepare_job(session,run,[],1,refresh_only=True)
            episodes=list(session.scalars(select(Episode).order_by(Episode.id)))
            episodes[2].published_at=None;session.commit()
        later=self.new_run();self.assertEqual(len(self.rows(later)),3)

    def test_low_likes_unavailable_conflicts_and_excluded_never_silently_rank(self):
        self.old_videos(('30001','30002','30003','30004'));rid=self.new_run()
        low=video();low['statistics']['digg_count']=50000
        conflict=video('30003');conflict['author']['uid']='other-author'
        with self.factory() as session:
            e=session.scalar(select(Episode).where(Episode.video_id=='30004'));e.review_status='excluded';session.commit()
        source=DetailSource([{'videos':[],'has_more':False}],{'30001':low,'30002':SourceUnavailable('unavailable','private'), '30003':conflict})
        search.run_job(self.job(rid),self.factory,source,lambda _:None)
        rows={r['video_id']:r for r in self.rows(rid)}
        self.assertEqual(rows['30001']['membership']['refresh_status'],'below_threshold')
        self.assertEqual(rows['30002']['membership']['refresh_status'],'unavailable')
        self.assertEqual(rows['30003']['membership']['refresh_status'],'failed')
        self.assertEqual(rows['30004']['episode_status'],'excluded')
        self.assertNotIn('30004',source.detail_calls)
        with self.factory() as session:
            draft=make_ranking(session,session.get(CollectionRun,rid),persist=False)
            self.assertEqual(draft['items'],[]);self.assertTrue(draft['generation_blockers'])

    def test_explicit_repair_is_idempotent_and_resets_samples_without_rewriting_snapshots(self):
        self.old_videos(('30001',));rid=self.new_run()
        jid=self.job(rid,True);search.run_job(jid,self.factory,DetailSource(details={'30001':video()}),lambda _:None)
        with self.factory() as session:
            ranked=make_ranking(session,session.get(CollectionRun,rid));session.commit()
            before=session.get(RankingRun,ranked['id']).snapshot
        jid=self.job(rid,True)
        self.assertIsNone(self.rows(rid)[0]['metric_snapshot_id'])
        search.run_job(jid,self.factory,DetailSource(details={'30001':video()}),lambda _:None)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(RunCandidate.id)).where(RunCandidate.run_id==rid)),1)
            self.assertEqual(session.get(RankingRun,ranked['id']).snapshot,before)
            self.assertEqual(session.scalar(select(func.count(SearchHit.id)).where(SearchHit.run_id==rid)),0)

    def test_new_run_http_seeds_but_gets_do_not_write_and_repair_requires_auth(self):
        self.old_videos(('30001',))
        import main
        from tests.auth_support import AdminClient
        with self.factory() as session:
            def dependency():
                yield session
                session.commit()
            main.app.dependency_overrides[main.database_session]=dependency
            try:
                with patch.object(main,'init_db'),patch.object(main,'recover_jobs'),AdminClient(main.app) as client:
                    result=client.post('/api/runs',json={'name':'新批次','cutoff_at':CUTOFF.isoformat()+'Z','mode':'sample','keywords':['AI短剧']})
                    self.assertEqual(result.status_code,201);rid=result.json()['id']
                    count=session.scalar(select(func.count(RunCandidate.id)))
                    self.assertEqual(client.get(f'/api/runs/{rid}/candidates').json()[0]['video_id'],'30001')
                    self.assertEqual(client.get(f'/api/runs/{rid}/candidate-summary').json()['carried'],1)
                    self.assertEqual(session.scalar(select(func.count(RunCandidate.id))),count)
                    self.assertEqual(client.post(f'/api/runs/{rid}/rankings',json={}).status_code,400)
                    client.cookies.clear()
                    self.assertEqual(client.post(f'/api/runs/{rid}/search-jobs',json={'refresh_only':True,'sorts':[],'pages':1}).status_code,401)
            finally:
                main.app.dependency_overrides.clear()

    def test_detail_adapter_returns_controlled_errors_and_uses_separate_endpoint(self):
        import httpx
        response=unittest.mock.Mock();response.raise_for_status.return_value=None
        response.json.return_value={'status':'ok','video':video()}
        with patch('sources.douk._cookie',return_value='synthetic'),patch('sources.douk.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            self.assertEqual(DouKSource().refresh_video('30001')['aweme_id'],'30001')
            self.assertTrue(client.return_value.__enter__.return_value.post.call_args.args[0].endswith('/detail'))
            client.return_value.__enter__.return_value.post.side_effect=httpx.ConnectError('cookie=private')
            with self.assertRaisesRegex(SourceUnavailable,'视频详情更新失败'):
                DouKSource().refresh_video('30001')

    def test_pause_during_detail_fetch_keeps_queue_and_resume_does_not_skip_video(self):
        self.old_videos(('30001',));rid=self.new_run();jid=self.job(rid,True)
        factory=self.factory
        class PausingSource(DetailSource):
            def refresh_video(self,number):
                with factory() as session:
                    session.get(SearchJob,jid).status='paused';session.commit()
                return video(number)
        search.run_job(jid,self.factory,PausingSource(),lambda _:None)
        row=self.rows(rid)[0]
        self.assertIsNone(row['metric_snapshot_id']);self.assertEqual(row['membership']['refresh_status'],'pending')
        with self.factory() as session:
            search.prepare_resume(session,session.get(SearchJob,jid));session.commit()
        source=DetailSource(details={'30001':video()})
        search.run_job(jid,self.factory,source,lambda _:None)
        self.assertEqual(source.detail_calls,['30001']);self.assertEqual(self.rows(rid)[0]['membership']['refresh_status'],'refreshed')

    def test_interrupted_discovery_cannot_finalize_even_when_existing_metrics_are_ready(self):
        self.old_videos(('30001',));rid=self.new_run()
        refresh=self.job(rid,True)
        search.run_job(refresh,self.factory,DetailSource(details={'30001':video()}),lambda _:None)
        jid=self.job(rid)
        search.run_job(jid,self.factory,DetailSource([SourceUnavailable('needs_login','login')]),lambda _:None)
        with self.factory() as session:
            run=session.get(CollectionRun,rid)
            draft=make_ranking(session,run,persist=False)
            self.assertEqual(len(draft['items']),1)
            self.assertTrue(any('最近一次' in text for text in draft['generation_blockers']))
            with self.assertRaisesRegex(ValueError,'尚未完成'):
                make_ranking(session,run)

    def test_older_manual_import_cannot_replace_latest_whole_sample_or_threshold_result(self):
        from sources.manual import import_row
        from sources.douk import normalize_video
        self.old_videos(('30001',));rid=self.new_run()
        raw=video();raw['statistics']['digg_count']=50000
        search.run_job(self.job(rid,True),self.factory,DetailSource(details={'30001':raw}),lambda _:None)
        with self.factory() as session:
            older=normalize_video(video(),'AI短剧','likes','1',CUTOFF)
            older['likes']=999999
            import_row(session,rid,older);session.commit()
        row=self.rows(rid)[0]
        self.assertEqual(row['likes'],50000)
        self.assertEqual(row['membership']['refresh_status'],'below_threshold')


class CandidatePoolMigrationTests(unittest.TestCase):
    def test_upgrade_007_preserves_every_existing_table_and_adds_empty_pool(self):
        from alembic import command
        from alembic.config import Config
        from services.migration import ROOT, upgrade_database
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test-pool-migrate.db';engine=create_engine(f'sqlite:///{path.as_posix()}')
            config=Config(str(ROOT/'alembic.ini'))
            with engine.begin() as connection:
                config.attributes['connection']=connection
                command.upgrade(config,'007_reused_media')
            with engine.begin() as connection:
                connection.exec_driver_sql("INSERT INTO collection_runs (id,name,mode,cutoff_at,window_hours,keywords,rule_version,official_metadata,created_at) VALUES (1,'历史批次','sample','2026-10-30',168,'[]','public-interactions-v1','{}','2026-10-30')")
            def tables():
                with closing(sqlite3.connect(path)) as connection:
                    return {name:connection.execute('SELECT * FROM "'+name+'"').fetchall() for (name,) in connection.execute("select name from sqlite_master where type='table' and name not in ('alembic_version','run_candidates') and name not like 'discovery_%'")}
            before=tables();upgrade_database(engine)
            self.assertEqual(tables(),before)
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql('select count(*) from run_candidates').scalar(),0)
                self.assertEqual(connection.exec_driver_sql('pragma integrity_check').scalar(),'ok')
                self.assertEqual(connection.exec_driver_sql('pragma foreign_key_check').all(),[])
            engine.dispose()
