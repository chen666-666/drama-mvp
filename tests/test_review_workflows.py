"""Isolated ranking/search/queue/recovery and privilege boundaries for orchestration."""
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
from models import (DownloadCatalog, DownloadEpisode, DownloadJob, Episode, MetricSnapshot,
                    RankingRun, ReviewRecord, ReviewWorkflow, SearchJob, Series)
from services import downloads, review_workflows as flows
from services.ranking import make_ranking
from tests import test_top3 as fixtures


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RankingTests(); self.f.setUp()
        self.session = self.f.session
        self.f.import_rows([self.f.row(likes='90000'), self.f.row(likes='80000'), self.f.row(likes='70000')])
        self.f.approve_all()
        self.flow = flows.create(self.session, self.f.run, 'test-operation-001')
        self.session.commit()

    def tearDown(self):
        self.f.tearDown()

    def finalize(self):
        return flows.finalize(self.session, self.flow, flows.draft(self.session, self.f.run)['token'], True)

    def initialize(self):
        rid = self.finalize()
        item = json.loads(self.session.get(RankingRun, rid).snapshot)['items'][0]
        catalog = downloads.catalog_for(self.session, rid, item['series_id'])
        self.session.commit()
        return catalog

    def scope(self, catalog):
        episodes = list(self.session.scalars(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id)))
        flows.confirm_scope(self.session, self.flow, [{'catalog_id': catalog.id,
            'episode_ids': [e.id for e in episodes], 'tokens': {str(e.id): flows.episode_stamp(e) for e in episodes}}])
        return episodes

    def test_live_draft_has_same_rules_and_does_not_write_versions(self):
        before = self.session.scalar(select(func.count()).select_from(RankingRun))
        a = flows.draft(self.session, self.f.run)
        self.assertEqual(a['token'], flows.draft(self.session, self.f.run)['token'])
        self.assertEqual(self.session.scalar(select(func.count()).select_from(RankingRun)), before)
        b = make_ranking(self.session, self.f.run)
        for field in ('items', 'scoring_mode', 'review_scope', 'candidates'):
            self.assertEqual(a[field], b[field])

    def test_operation_reuse_rejects_changed_range_and_new_duplicate(self):
        self.assertEqual(flows.create(self.session, self.f.run, 'test-operation-001').id, self.flow.id)
        with self.assertRaises(ValueError): flows.create(self.session, self.f.run, 'test-operation-001', True)
        with self.assertRaises(ValueError): flows.create(self.session, self.f.run, 'another-operation')

    def test_finalize_explicit_stale_safe_and_idempotent_snapshot_preserved(self):
        old = flows.draft(self.session, self.f.run)['token']
        metric = self.session.scalar(select(MetricSnapshot))
        metric.likes += 1; self.session.flush()
        with self.assertRaisesRegex(ValueError, '草案已变化'): flows.finalize(self.session, self.flow, old, True)
        with self.assertRaises(ValueError): flows.finalize(self.session, self.flow, flows.draft(self.session, self.f.run)['token'], False)
        rid = self.finalize(); snapshot = self.session.get(RankingRun, rid).snapshot
        self.assertEqual(self.finalize(), rid)
        metric.likes += 2; self.session.flush()
        self.assertEqual(self.session.get(RankingRun, rid).snapshot, snapshot)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(RankingRun)), 1)

    def test_cards_keep_authors_unknowns_and_change_hints(self):
        cards = flows.evidence_cards(self.session, self.flow)
        self.assertEqual(len(cards), 3)
        self.assertEqual(len({c['account_id'] for c in cards}), 3)
        metric = self.session.scalar(select(MetricSnapshot)); metric.likes = None; self.session.flush()
        updated = flows.evidence_cards(self.session, self.flow)
        self.assertTrue(any('互动指标不齐全' in gap for c in updated for gap in c['gaps']))
        self.assertIsNone(next(r for c in updated for r in c['episodes'] if r['episode_id'] == metric.episode_id)['likes'])

    def test_evidence_likes_threshold_uses_likes_and_keeps_boundary_unknowns(self):
        metrics = list(self.session.scalars(select(MetricSnapshot).order_by(MetricSnapshot.id)))
        for metric, likes in zip(metrics, (49999, 50000, None)):
            metric.likes = likes
            metric.shares = 999999  # A large weighted score cannot bypass the likes filter.
        self.session.flush()
        snapshot = flows.draft(self.session, self.f.run)
        reviews_before = self.session.scalar(select(func.count()).select_from(ReviewRecord))
        for series_status, episode_status in (('pending', 'pending'), ('approved', 'approved'),
                                               ('excluded', 'excluded')):
            with self.subTest(series_status=series_status):
                for episode in self.session.scalars(select(Episode)):
                    episode.review_status = episode_status
                    self.session.get(Series, episode.series_id).review_status = series_status
                self.session.flush()
                cards = flows.evidence_cards(self.session, self.flow)
                self.assertEqual([r['likes'] for c in cards for r in c['episodes']], [50000, None])
        for episode in self.session.scalars(select(Episode)):
            episode.review_status = 'approved'
            self.session.get(Series, episode.series_id).review_status = 'approved'
        self.session.flush()
        self.assertEqual(flows.draft(self.session, self.f.run)['token'], snapshot['token'])
        self.assertEqual(len(flows.ranking.candidates(self.session, self.f.run)), 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ReviewRecord)), reviews_before)

    def test_low_likes_do_not_add_gaps_to_other_episodes_of_same_series(self):
        episodes = list(self.session.scalars(select(Episode).order_by(Episode.id)))
        episodes[0].series_id = episodes[1].series_id
        episodes[0].review_status = 'pending'
        metric = self.session.scalar(select(MetricSnapshot).where(MetricSnapshot.episode_id == episodes[0].id))
        metric.likes = 0
        metric.comments = None
        self.session.flush()
        cards = flows.evidence_cards(self.session, self.flow)
        self.assertEqual(len(cards), 2)
        card = next(c for c in cards if c['series_id'] == episodes[1].series_id)
        self.assertEqual([r['episode_id'] for r in card['episodes']], [episodes[1].id])
        self.assertEqual(card['gaps'], [])
        for metric in self.session.scalars(select(MetricSnapshot)):
            metric.likes = 49999
        self.session.flush()
        self.assertEqual(flows.evidence_cards(self.session, self.flow), [])

    def test_evidence_uses_latest_batch_sample_and_rejects_hidden_review(self):
        row = flows.evidence_cards(self.session, self.flow)[0]['episodes'][0]
        self.f.import_rows([self.f.row(video_id=row['video_id'], original_url=row['original_url'],
            author_id=row['author_account_id'], series_key=row['series_key'], likes='49999')])
        self.session.flush()
        cards = flows.evidence_cards(self.session, self.flow)
        self.assertNotIn(row['episode_id'], [r['episode_id'] for c in cards for r in c['episodes']])
        current = next(r for r in flows.ranking.candidates(self.session, self.f.run)
                       if r['episode_id'] == row['episode_id'])
        # Even a matching current token must not allow reviewing a hidden row.
        data = dict(operation_key='hidden-low-likes', episode_ids=[row['episode_id']],
            tokens={str(row['episode_id']): flows.fingerprint(current)}, target_type='episode',
            status='excluded', reason='不应保存', evidence_url='', checks={})
        before = self.session.scalar(select(func.count()).select_from(ReviewRecord))
        with self.assertRaisesRegex(ValueError, '刷新证据卡'):
            flows.batch_review(self.session, self.flow, data)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ReviewRecord)), before)

    def test_selected_review_records_are_audited_once_and_stale_rejected(self):
        row = flows.evidence_cards(self.session, self.flow)[0]['episodes'][0]
        data = {'operation_key': 'review-operation-1', 'episode_ids': [row['episode_id']],
                'tokens': {str(row['episode_id']): row['token']}, 'target_type': 'episode', 'status': 'pending',
                'reason': '合成待补归属依据', 'evidence_url': row['original_url'], 'checks': {}}
        before = self.session.scalar(select(func.count()).select_from(ReviewRecord))
        flows.batch_review(self.session, self.flow, data)
        flows.batch_review(self.session, self.flow, data)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ReviewRecord)), before+1)
        self.assertEqual(self.session.get(Episode, row['episode_id']).review_status, 'pending')
        data['operation_key'] = 'review-operation-2'
        with self.assertRaisesRegex(ValueError, '变化'): flows.batch_review(self.session, self.flow, data)

    def test_selected_reviews_allow_optional_links_and_preserve_audit(self):
        from api_workflows import Review
        from models import Evidence
        from services.ranking import review_details

        for kind in ('series', 'episode'):
            for status in ('pending', 'approved', 'excluded'):
                for value in (None, '', '  ', 'https://www.douyin.com/'):
                    with self.subTest(kind=kind, status=status, evidence_url=value):
                        card = flows.evidence_cards(self.session, self.flow)[0]
                        row = card['episodes'][0]
                        data = dict(operation_key=f'optional-{kind}-{status}-{value}',
                            episode_ids=[row['episode_id']], tokens={str(row['episode_id']): row['token']},
                            target_type=kind, status=status, reason='合成逐项核验说明',
                            ai_type='AI漫剧', ai_basis='合成作者制作声明',
                            checks={key: True for key in ('story', 'ai', 'original_author',
                                'main_episode', 'ownership', 'public', 'not_duplicate')})
                        if value is not None:
                            data['evidence_url'] = value
                        data = Review(**data).model_dump()
                        flows.batch_review(self.session, self.flow, data)
                        target_id = row['series_id'] if kind == 'series' else row['episode_id']
                        record = self.session.scalar(select(ReviewRecord).where(
                            ReviewRecord.target_type == kind, ReviewRecord.target_id == target_id
                        ).order_by(ReviewRecord.id.desc()))
                        self.assertEqual(record.status, status)
                        self.assertEqual(record.reason, data['reason'])
                        evidence = self.session.get(Evidence, record.evidence_id)
                        self.assertEqual(evidence.source_url, (value or '').strip())
                        self.assertEqual(evidence.note, data['reason'])
                        self.assertEqual(review_details(self.session, kind, target_id)[-1]['evidence_url'],
                            evidence.source_url)

    def test_optional_link_keeps_validation_of_filled_links_and_required_facts(self):
        row = flows.evidence_cards(self.session, self.flow)[0]['episodes'][0]
        data = dict(operation_key='invalid-optional-link', episode_ids=[row['episode_id']],
            tokens={str(row['episode_id']): row['token']}, target_type='episode',
            status='pending', reason='合成核验说明', checks={})
        before = self.session.scalar(select(func.count()).select_from(ReviewRecord))
        for value in ('javascript:alert(1)', 'not-a-url'):
            with self.assertRaisesRegex(ValueError, 'http/https'):
                flows.batch_review(self.session, self.flow, {**data, 'evidence_url': value})
        with self.assertRaisesRegex(ValueError, '核验理由'):
            flows.batch_review(self.session, self.flow, {**data, 'reason': ' '})
        with self.assertRaisesRegex(ValueError, '逐项确认'):
            flows.batch_review(self.session, self.flow, {**data, 'status': 'approved'})
        with self.assertRaisesRegex(ValueError, 'AI 类型'):
            flows.batch_review(self.session, self.flow, {**data, 'target_type': 'series',
                'status': 'approved', 'checks': dict(story=True, ai=True, original_author=True)})
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ReviewRecord)), before)

    def test_scope_fixed_version_trial_gate_no_growth_and_resume_no_duplicates(self):
        catalog = self.initialize(); episodes = self.scope(catalog)
        flows.tick(self.session, self.flow); self.session.commit()
        jobs = list(self.session.scalars(select(DownloadJob)))
        self.assertEqual(len(jobs), 1); self.assertTrue(jobs[0].trial)
        other = DownloadEpisode(catalog_id=catalog.id, video_id='999888', title='新发现待确认',
                                original_url='https://www.douyin.com/video/999888', status='approved', access='public')
        self.session.add(other); self.session.commit()
        flows.tick(self.session, self.flow); self.session.commit()
        self.assertEqual(len(list(self.session.scalars(select(DownloadJob)))), 1)
        flows.control(self.session, self.flow, 'pause'); self.session.commit()
        self.assertEqual(jobs[0].status, 'paused')
        flows.tick(self.session, self.flow)
        flows.control(self.session, self.flow, 'resume'); self.session.commit()
        flows.tick(self.session, self.flow); self.session.commit()
        self.assertEqual(jobs[0].status, 'queued')
        self.assertEqual(len(list(self.session.scalars(select(DownloadJob)))), 1)
        self.assertEqual(flows.view(self.session, self.flow)['catalogs'][0]['unconfirmed_count'], 1)

    def test_stale_scope_and_one_catalog_failure_do_not_stop_other_series(self):
        rid = self.finalize()
        catalogs = [downloads.catalog_for(self.session, rid, row['series_id']) for row in
                    json.loads(self.session.get(RankingRun, rid).snapshot)['items']]
        selections = []
        for catalog in catalogs:
            episode = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalog.id))
            selections.append({'catalog_id': catalog.id, 'episode_ids': [episode.id], 'tokens': {str(episode.id): flows.episode_stamp(episode)}})
        flows.confirm_scope(self.session, self.flow, selections)
        episode = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == catalogs[0].id))
        episode.access = 'private'; self.session.commit()
        flows.tick(self.session, self.flow); self.session.commit()
        payload = json.loads(self.flow.payload)
        self.assertEqual(payload['catalogs'][str(catalogs[0].id)]['state'], 'error')
        self.assertEqual(len(list(self.session.scalars(select(DownloadJob)))), 2)

    def test_pause_cancel_recovery_preserve_existing_results(self):
        self.initialize(); self.session.commit()
        rid = self.flow.ranking_id
        from sqlalchemy.orm import sessionmaker
        flows.recover(sessionmaker(bind=self.f.engine))
        self.session.expire_all()
        self.assertEqual(self.flow.status, 'interrupted')
        flows.control(self.session, self.flow, 'resume')
        flows.control(self.session, self.flow, 'cancel'); self.session.commit()
        self.assertEqual(self.flow.status, 'cancelled')
        self.assertIsNotNone(self.session.get(RankingRun, rid))
        with self.assertRaises(ValueError): flows.control(self.session, self.flow, 'resume')

    def test_new_api_requires_auth_csrf_and_public_mode_denies(self):
        import main
        from tests.auth_support import AdminClient
        def dependency():
            try:
                yield self.session
                self.session.commit()
            except Exception:
                self.session.rollback(); raise
        main.app.dependency_overrides[main.database_session] = dependency
        try:
            with patch.object(main, 'init_db'), patch.object(main, 'recover_jobs'), AdminClient(main.app) as client:
                self.assertEqual(client.get('/control/tasks').status_code, 200)
                self.assertEqual(client.get(f'/api/control/tasks/{self.flow.id}').status_code, 200)
                self.assertEqual(client.post(f'/api/control/tasks/{self.flow.id}/control', json={'action': 'pause'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
                with patch('services.site.public_mode', return_value=True):
                    self.assertEqual(client.get('/api/control/tasks').status_code, 403)
                client.cookies.clear()
                self.assertEqual(client.get('/api/control/tasks').status_code, 401)
                self.assertEqual(client.get('/control/tasks', follow_redirects=False).status_code, 303)
        finally:
            main.app.dependency_overrides.clear()
