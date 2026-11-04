"""Synthetic checks for first-render content, browser caching and bounded queries."""
import gzip
import json
import re
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

import main
from models import DownloadEpisode
from services import downloads
from services.web_assets import static_url
from tests import test_top3 as fixtures


class WebPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RankingTests()
        self.fixture.setUp()
        f = self.fixture
        f.import_rows([f.row(series_title="合成作品 <script>bad()</script>", likes=str(i)) for i in (50, 40, 30, 10)])
        f.approve_all()
        self.first = f.ranking()
        self.latest = f.ranking()
        f.session.commit()
        self.temp = tempfile.TemporaryDirectory()
        factory = sessionmaker(bind=f.engine, autoflush=False)
        self.patches = [patch.object(main, "init_db"), patch.object(main, "recover_jobs"),
                        patch.object(main, "SessionLocal", factory),
                        patch.object(downloads, "DATA", Path(self.temp.name))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()
        self.temp.cleanup()

    def test_home_contains_selected_cards_without_javascript_or_followup_api(self):
        with TestClient(main.app) as client:
            response = client.get("/", params={"ranking_id": self.first["id"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text.count('class="drama-card"'), 3)
            self.assertIn(f'/watch?ranking_id={self.latest["id"]}&amp;series_id=', response.text)
            self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', response.text)
            self.assertNotIn('<script>bad()</script>', response.text)
            self.assertIn('data-ranking-ready="true"', response.text)
            self.assertIn('no-cache', response.headers['cache-control'])
            latest = client.get("/").text
            self.assertIn(f'/watch?ranking_id={self.latest["id"]}&amp;series_id=', latest)
            unknown = client.get("/?ranking_id=99999").text
            self.assertIn(f'/watch?ranking_id={self.latest["id"]}&amp;series_id=', unknown)

    def test_public_text_compression_and_static_cache_version(self):
        with TestClient(main.app) as client:
            with client.stream("GET", "/", headers={"Accept-Encoding": "gzip"}) as response:
                compressed = b"".join(response.iter_raw())
                self.assertEqual(response.headers['content-encoding'], 'gzip')
                html = gzip.decompress(compressed)
                self.assertLess(len(compressed), len(html) // 2)
            url = static_url("library.css")
            response = client.get(url)
            self.assertIn("immutable", response.headers['cache-control'])
            response = client.get(url, headers={"If-None-Match": response.headers['etag']})
            self.assertEqual(response.status_code, 304)
            self.assertIn("immutable", response.headers['cache-control'])
            self.assertEqual(client.get("/static/library.css?v=old").headers['cache-control'], "no-cache")
            partial = client.get(url, headers={"Range": "bytes=0-19"})
            self.assertEqual(partial.status_code, 206)
            self.assertNotIn('content-encoding', partial.headers)
            self.assertEqual(len(partial.content), 20)

    def test_asset_url_changes_when_file_contents_change(self):
        with tempfile.TemporaryDirectory() as folder, patch("services.web_assets.STATIC", Path(folder)):
            asset = Path(folder) / "test.css"
            asset.write_text("body{color:red}", encoding="utf-8")
            before = static_url("test.css")
            asset.write_text("body{color:blue}", encoding="utf-8")
            self.assertNotEqual(before, static_url("test.css"))

    def test_public_player_embeds_only_public_fields_for_first_render(self):
        with TestClient(main.app) as client:
            series_id = self.first['items'][0]['series_id']
            response = client.get(f"/watch?ranking_id={self.first['id']}&series_id={series_id}")
            self.assertEqual(response.status_code, 200)
            match = re.search(r'<script id="watch-bootstrap" type="application/json">(.*?)</script>', response.text, re.S)
            self.assertIsNotNone(match)
            detail = json.loads(match[1])
            self.assertEqual(detail['ranking']['id'], self.first['id'])
            self.assertEqual(len(detail['catalogs']), 3)
            self.assertNotIn('review_history', match[1])
            self.assertNotIn('relative_path', match[1])
            self.assertNotIn('<script>bad()</script>', response.text)
            invalid = client.get('/watch?ranking_id=invalid&series_id=1')
            self.assertEqual(invalid.status_code, 200)
            self.assertNotIn('id="watch-bootstrap"', invalid.text)

    def test_catalog_query_count_stays_constant_with_many_episodes(self):
        f = self.fixture
        catalog = downloads.catalog_for(f.session, self.first["id"], self.first['items'][0]['series_id'])
        f.session.add_all(DownloadEpisode(catalog_id=catalog.id, video_id=str(990000 + i),
            title="合成正片", original_url=f"https://www.douyin.com/video/{990000+i}") for i in range(100))
        f.session.flush()
        queries = []
        def counted(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)
        event.listen(f.engine, "before_cursor_execute", counted)
        try:
            detail = downloads.catalog_detail(f.session, catalog)
        finally:
            event.remove(f.engine, "before_cursor_execute", counted)
        self.assertEqual(len(detail['episodes']), 101)
        self.assertEqual(len(queries), 1)
