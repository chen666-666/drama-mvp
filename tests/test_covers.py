"""A new ranking retains the original poster without inheriting media or writes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import func, select
from models import DownloadCatalog, RankingRun
from services import downloads
from services.site import library_detail
from tests import test_top3 as fixtures


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RankingTests()
        self.fixture.setUp()
        f = self.fixture
        f.import_rows([f.row(likes=str(n)) for n in (30, 20, 10)])
        f.approve_all()
        self.first = f.ranking()
        self.latest = f.ranking()
        self.session = f.session
        self.item = self.latest['items'][0]
        self.catalog = downloads.catalog_for(self.session, self.first['id'], self.item['series_id'])
        self.session.commit()
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = patch.object(downloads, 'DATA', self.data)
        self.patch.start()
        self.poster(self.catalog)

    def poster(self, catalog):
        path = self.data / f'thumbnails/catalog-{catalog.id}.jpg'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'synthetic poster')

    def detail(self):
        return library_detail(self.session, self.latest['id'], self.latest)

    def tearDown(self):
        self.patch.stop()
        self.fixture.tearDown()
        self.temp.cleanup()

    def test_new_ranking_reuses_same_series_poster_without_creating_catalog_or_media(self):
        count = self.session.scalar(select(func.count()).select_from(DownloadCatalog))
        detail = self.detail()
        self.assertEqual(detail['catalogs'][0]['cover_url'], f'/api/downloads/catalogs/{self.catalog.id}/cover')
        self.assertEqual(detail['catalogs'][0]['episodes'], [])
        self.assertTrue(all(row['cover_url'] is None for row in detail['catalogs'][1:]))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(DownloadCatalog)), count)
        self.assertFalse(self.session.new)
        self.assertFalse(self.session.dirty)

    def test_current_poster_takes_priority_and_missing_file_falls_back(self):
        current = downloads.catalog_for(self.session, self.latest['id'], self.item['series_id'])
        self.assertEqual(self.detail()['catalogs'][0]['cover_url'], f'/api/downloads/catalogs/{self.catalog.id}/cover')
        self.poster(current)
        self.assertEqual(self.detail()['catalogs'][0]['cover_url'], f'/api/downloads/catalogs/{current.id}/cover')
        (self.data / f'thumbnails/catalog-{current.id}.jpg').unlink()
        (self.data / f'thumbnails/catalog-{self.catalog.id}.jpg').unlink()
        self.assertIsNone(self.detail()['catalogs'][0]['cover_url'])

    def test_unselected_or_independent_catalog_is_not_a_poster_source(self):
        ranking = self.session.get(RankingRun, self.first['id'])
        snapshot = json.loads(ranking.snapshot)
        snapshot['items'][0]['selected'] = False
        ranking.snapshot = json.dumps(snapshot)
        self.session.flush()
        self.assertIsNone(self.detail()['catalogs'][0]['cover_url'])
        self.catalog.ranking_id = None
        self.catalog.published = False
        self.session.flush()
        self.assertIsNone(self.detail()['catalogs'][0]['cover_url'])
