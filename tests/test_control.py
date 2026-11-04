"""Control boundaries and playback download views on isolated synthetic media."""
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select
import main
from models import DownloadCatalog, DownloadJob
from tests.auth_support import AdminClient
from tests import test_publishing


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_publishing.PublishingTests()
        self.fixture.setUp()
        self.session = self.fixture.session
        self.patches = [patch.object(main, 'init_db'), patch.object(main, 'recover_jobs')]
        for item in self.patches:
            item.start()
        def dependency():
            yield self.session
        main.app.dependency_overrides[main.database_session] = dependency
        self.url = f'/api/playback/rankings/{self.fixture.ranking["id"]}/series/{self.fixture.catalog.series_id}'

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()

    def test_control_pages_and_playback_metadata_require_admin(self):
        with AdminClient(main.app) as client:
            for path in ['/control', '/control/videos', '/control/reviews', '/control/collection', '/control/system']:
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn('no-store', response.headers['cache-control'])
            client.cookies.clear()
            for path in ['/control', '/control/videos?ranking_id=1&series_id=2', '/control/reviews', '/control/system']:
                response = client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303, path)
                self.assertIn('/login?next=', response.headers['location'])
            for path in ['/api/control/overview', self.url]:
                self.assertEqual(client.get(path).status_code, 401, path)

    def test_playback_view_is_minimal_and_does_not_initialize_directories(self):
        with AdminClient(main.app) as client:
            before = list(self.session.scalars(select(DownloadCatalog.id)))
            result = client.get(self.url).json()
            self.assertEqual(set(result), {'series_title', 'episodes'})
            self.assertTrue(result['episodes'][0]['file_url'].endswith('/download'))
            self.assertEqual(set(result['episodes'][0]), {'id', 'title', 'episode_number', 'episode_range', 'file_url', 'representative'})
            self.assertNotIn('内部备注', json.dumps(result, ensure_ascii=False))
            other = self.fixture.ranking['items'][1]['series_id']
            response = client.get(f'/api/playback/rankings/{self.fixture.ranking["id"]}/series/{other}')
            self.assertEqual(response.json()['episodes'], [])
            self.assertEqual(before, list(self.session.scalars(select(DownloadCatalog.id))))
            reserve = self.fixture.ranking['items'][-1]['series_id']
            self.assertEqual(client.get(f'/api/playback/rankings/{self.fixture.ranking["id"]}/series/{reserve}').status_code, 404)

    def test_pending_download_allowed_but_rejection_or_tamper_not_delivered(self):
        with AdminClient(main.app) as client:
            media = self.fixture.media
            media.manual_status = 'pending'; self.session.flush()
            self.assertTrue(client.get(self.url).json()['episodes'][0]['file_url'])
            media.manual_status = 'rejected'; self.session.flush()
            self.assertIsNone(client.get(self.url).json()['episodes'][0]['file_url'])
            media.manual_status = 'passed'; self.session.flush()
            self.fixture.video.write_bytes(b'changed bytes')
            self.assertIsNone(client.get(self.url).json()['episodes'][0]['file_url'])

    def test_overview_counts_and_object_links_are_read_only(self):
        with AdminClient(main.app) as client:
            self.fixture.media.manual_status = 'pending'; self.session.flush()
            groups = {row['key']: row for row in client.get('/api/control/overview').json()['groups']}
            self.assertEqual(groups['inspections']['count'], 1)
            self.assertIn('view=inspection', groups['inspections']['items'][0]['url'])
            self.assertIn(f'series_id={self.fixture.catalog.series_id}', groups['inspections']['items'][0]['url'])
            self.fixture.media.manual_status = 'passed'
            self.session.scalar(select(DownloadJob)).status = 'failed'; self.session.flush()
            groups = {row['key']: row for row in client.get('/api/control/overview').json()['groups']}
            self.assertEqual(groups['failures']['count'], 1)
            self.assertEqual(groups['inspections']['count'], 0)

    def test_public_deployment_rejects_control_and_private_playback_data(self):
        with AdminClient(main.app) as client, patch('services.site.public_mode', return_value=True):
            for path in ['/control', '/control/videos', '/api/control/overview', self.url]:
                self.assertEqual(client.get(path).status_code, 403, path)

    def test_login_returns_only_to_valid_internal_areas(self):
        from auth import safe_next
        for path in ['/control/videos?ranking_id=1&series_id=2', '/watch?ranking_id=1&series_id=2&episode_id=3', '/downloads/watch?ranking_id=1']:
            self.assertEqual(safe_next(path), path)
        for path in ['//evil.example', 'https://evil.example/control', 'https://evil.example/watch?ranking_id=1', '/watch-evil', '/control\\evil', '/watch\nmalicious']:
            self.assertEqual(safe_next(path), '/control')
