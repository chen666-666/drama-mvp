"""Batch directory decisions preserve catalog boundaries and individual evidence."""
import json
import unittest

from sqlalchemy import select

import main
from models import DownloadCatalog, DownloadEpisode, DownloadJob, Series
from services import downloads as svc
from tests import test_collections as fixtures
from tests.auth_support import AdminClient


class BatchReviewTests(unittest.TestCase):
    setUp = fixtures.CollectionTests.setUp
    tearDown = fixtures.CollectionTests.tearDown

    def rows(self):
        svc.add_links(self.session, self.catalog, 'https://www.douyin.com/video/555\nhttps://www.douyin.com/video/556')
        rows = list(self.session.scalars(select(DownloadEpisode).where(DownloadEpisode.video_id.in_(['555', '556'])).order_by(DownloadEpisode.id)))
        rows[0].title, rows[0].episode_number = '合成第119集', 119
        rows[1].title = '合成中文集号待确认'
        self.session.commit()
        return rows

    def decision(self, rows, **changes):
        return {'episode_ids': [row.id for row in rows], 'status': 'approved', 'note': '已人工核对本批每条视频',
                'ownership': True, 'main_episode': True, 'not_duplicate': True, 'public_access': True, **changes}

    def test_batch_preserves_numbers_sources_and_writes_separate_history(self):
        rows = self.rows()
        rows[0].evidence_url = 'https://www.douyin.com/video/557'
        self.session.commit()
        result = svc.review_episodes(self.session, self.catalog, self.decision(rows, episode_ids=[rows[0].id, rows[1].id, rows[0].id]))
        self.assertEqual(result['updated'], 2)
        self.assertEqual(rows[0].episode_number, 119)
        self.assertIsNone(rows[1].episode_number)
        for row, url in zip(rows, ['https://www.douyin.com/video/557', 'https://www.douyin.com/video/556']):
            self.assertEqual((row.status, row.access, row.evidence_url), ('approved', 'public', url))
            history = json.loads(row.review_history)
            self.assertEqual(history[-1]['previous']['status'], 'pending')
            self.assertEqual(history[-1]['evidence_url'], url)
        self.assertFalse(self.catalog.published)
        self.assertIsNone(self.session.scalar(select(DownloadJob)))

    def test_each_attestation_and_note_required(self):
        rows = self.rows()
        for key in ['ownership', 'main_episode', 'not_duplicate', 'public_access']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                svc.review_episodes(self.session, self.catalog, self.decision(rows, **{key: False}))
        with self.assertRaises(ValueError):
            svc.review_episodes(self.session, self.catalog, self.decision(rows, note='  '))
        self.assertTrue(all(row.status == 'pending' and row.review_history == '[]' for row in rows))

    def test_foreign_missing_and_invalid_ids_reject_entire_selection(self):
        rows = self.rows()
        other_series = Series(author_id=self.series.author_id, series_key='other-series', title='另一部剧')
        self.session.add(other_series)
        self.session.flush()
        other = DownloadCatalog(series_id=other_series.id, ranking_id=None)
        self.session.add(other)
        self.session.flush()
        foreign = DownloadEpisode(catalog_id=other.id, video_id='777', original_url='https://www.douyin.com/video/777', title='其他目录正片')
        self.session.add(foreign)
        self.session.commit()
        for identifiers in [[], [rows[0].id, foreign.id], [rows[0].id, 999999], [-1], list(range(1, 1002))]:
            with self.subTest(ids=len(identifiers)), self.assertRaises(ValueError):
                svc.review_episodes(self.session, self.catalog, self.decision(rows, episode_ids=identifiers))
        with self.assertRaises(ValueError):
            svc.review_episodes(self.session, self.catalog, self.decision(rows, status='pending'))
        self.assertTrue(all(row.status == 'pending' for row in rows))

    def test_active_download_rolls_back_all_rows_histories_and_queue_changes(self):
        rows = self.rows()
        for row in rows:
            row.status, row.access = 'approved', 'public'
        jobs = [DownloadJob(episode_id=rows[0].id, status='queued'), DownloadJob(episode_id=rows[1].id, status='downloading')]
        self.session.add_all(jobs)
        self.session.commit()
        with self.assertRaisesRegex(ValueError, '暂停'):
            svc.review_episodes(self.session, self.catalog, self.decision(rows, status='excluded'))
        self.assertTrue(all(row.status == 'approved' and row.review_history == '[]' for row in rows))
        self.assertEqual(jobs[0].status, 'queued')
        jobs[1].status = 'paused'
        self.session.commit()
        svc.review_episodes(self.session, self.catalog, self.decision(rows, status='excluded'))
        self.assertTrue(all(row.status == 'excluded' for row in rows))
        self.assertEqual(jobs[0].status, 'paused')

    def test_api_auth_scope_validation_and_success(self):
        rows = self.rows()
        path = f'/api/downloads/catalogs/{self.catalog.id}/episodes/review'
        with AdminClient(main.app) as client:
            data = self.decision(rows)
            self.assertEqual(client.post(path, json=data, headers={'Origin':'https://evil.example'}).status_code, 403)
            self.assertEqual(client.post(path, json=data, headers={'X-CSRF-Token':'invalid'}).status_code, 403)
            self.assertEqual(client.post(path, json={**data, 'episode_ids':[]}).status_code, 422)
            self.assertEqual(client.post(path, json={**data, 'episode_ids':[rows[0].id, 999999]}).status_code, 400)
            result = client.post(path, json=data)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['updated'], 2)
            client.cookies.clear()
            self.assertEqual(client.post(path, json=data).status_code, 401)
