"""Independent collections: synthetic sources, bytes and isolated migrations."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, func

import main
from models import Author, DownloadCatalog, DownloadEpisode, DownloadJob, Episode, MediaFile, RankingRun, Series
from services import downloads as svc
from services.media import digest
from services.migration import upgrade_database, REVISION
from services.site import manual_detail, manual_library, playable_path
from sources.douk import SourceUnavailable
from tests import test_top3 as fixtures
from tests.auth_support import AdminClient


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RankingTests()
        self.f.setUp()
        self.f.import_rows([self.f.row(author_id="12345", series_key="existing-key", series_title="合成鳄鱼缅北",
            episode_number="120", title="合成鳄鱼缅北第120集")])
        self.f.approve_all()
        self.session = self.f.session
        self.series = self.session.scalar(select(Series))
        self.series.title_note = "公开说明"
        self.session.get(Author, self.series.author_id).homepage_url = "https://www.douyin.com/user/synthetic-author"
        # Independent collecting must not demand a known publication time.
        self.session.scalar(select(Episode)).published_at = None
        self.catalog = svc.manual_catalog(self.session, self.series.id)
        self.session.commit()
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [patch.object(svc, "DATA", self.data), patch.object(main, "init_db"), patch.object(main, "recover_jobs")]
        for item in self.patches:
            item.start()

        def dependency():
            try:
                yield self.session
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
        main.app.dependency_overrides[main.database_session] = dependency

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for item in reversed(self.patches):
            item.stop()
        self.f.tearDown()
        self.temp.cleanup()

    def media(self, status="passed"):
        episode = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        svc.enqueue(self.session, self.catalog, [episode.id], trial=True)
        job = self.session.scalar(select(DownloadJob).where(DownloadJob.episode_id == episode.id))
        job.status = "verified"
        path = self.data / "videos/synthetic.mp4"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(bytes(range(256)) * 8)
        media = MediaFile(job_id=job.id, relative_path="videos/synthetic.mp4", size=path.stat().st_size, duration=1,
            format="mp4", sha256=digest(path), tracks="[]", validation_note="内部诊断", manual_note="私密抽检说明", manual_status=status)
        self.session.add(media)
        self.session.commit()
        return media

    def test_reuses_existing_series_catalog_and_unknown_time_without_rankings(self):
        self.assertEqual(svc.manual_catalog(self.session, self.series.id).id, self.catalog.id)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(DownloadCatalog)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(RankingRun)), 0)
        self.assertIsNone(self.session.scalar(select(Episode)).published_at)
        self.assertEqual(len(svc.catalog_detail(self.session, self.catalog)["episodes"]), 1)
        self.series.review_status = "pending"
        with self.assertRaisesRegex(ValueError, "核验"):
            svc.manual_catalog(self.session, self.series.id)

    def test_directory_filters_author_title_deduplicates_and_resumes(self):
        def video(identifier, title, author="12345"):
            return {"aweme_id": identifier, "desc": title, "author": {"uid": author}, "duration": 2000}
        payload = {"videos": [video("888", "合成鳄鱼缅北第119集"), video("889", "其他短剧第1集"),
            video("890", "合成鳄鱼缅北第118集", "other"), video("888", "合成鳄鱼缅北第119集"),
            video("891", "合成鳄鱼缅北第1-3集合集")], "cursor": 20, "has_more": True}
        with patch("sources.download.bridge_request", return_value=payload) as bridge:
            result = svc.discover(self.session, self.catalog)
            self.assertEqual(result["added"], 2)
            self.assertEqual(result["skipped"], 2)
            row = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id == "888"))
            self.assertEqual(row.episode_number, 119)
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.access, "unknown")
            self.assertNotIn(119, svc.catalog_detail(self.session, self.catalog)["summary"]["missing_numbers"])
            self.assertIsNone(self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id == "891")).episode_number)
            svc.discover(self.session, self.catalog)
            self.assertEqual(bridge.call_args.args[1]["cursor"], 20)
            svc.discover(self.session, self.catalog, keyword="")
            self.assertEqual(bridge.call_args.args[1]["cursor"], 0)
            self.assertEqual(self.session.scalar(select(func.count()).select_from(RankingRun)), 0)
        cursor = self.catalog.cursor
        with patch("sources.download.bridge_request", side_effect=SourceUnavailable("needs_login", "请登录")):
            with self.assertRaises(SourceUnavailable):
                svc.discover(self.session, self.catalog)
        self.assertEqual(self.catalog.cursor, cursor)

    def test_unreviewed_and_batch_gate_still_apply(self):
        svc.add_links(self.session, self.catalog, "https://www.douyin.com/video/555")
        pending = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id == "555"))
        with self.assertRaisesRegex(ValueError, "公开"):
            svc.enqueue(self.session, self.catalog, [pending.id], trial=True)
        seed = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        with self.assertRaisesRegex(ValueError, "试下载"):
            svc.enqueue(self.session, self.catalog, [seed.id], trial=False)

    def test_publication_gate_private_data_range_and_unpublish(self):
        media = self.media("pending")
        catalog_id, media_id = self.catalog.id, media.id
        with AdminClient(main.app) as client:
            url = f"/api/downloads/collections/{catalog_id}/publication"
            self.assertEqual(client.post(url, json={"published": True}).status_code, 400)
            media.manual_status = "passed"
            self.session.commit()
            self.assertEqual(client.post(url, json={"published": True}).status_code, 200)
            public = client.get(f"/api/library/collections/{catalog_id}")
            self.assertEqual(public.status_code, 200)
            self.assertNotIn("内部诊断", public.text)
            self.assertNotIn("私密抽检说明", public.text)
            self.assertNotIn("relative_path", public.text)
            self.assertEqual(public.json()["catalogs"][0]["episodes"][0]["episode_number"], 120)
            self.assertFalse(public.json()["catalogs"][0]["episodes"][0]["representative"])
            client.cookies.clear()
            self.assertEqual(client.get(f"/api/downloads/collections/{catalog_id}").status_code, 401)
            self.assertEqual(client.get(f"/api/playback/collections/{catalog_id}").status_code, 401)
            response = client.get(f"/api/library/media/{media_id}/download", headers={"Range": "bytes=0-15"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(len(response.content), 16)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(len(manual_library(self.session)), 1)
            self.catalog.published = False
            self.session.commit()
            self.assertEqual(client.get(f"/api/library/media/{media_id}").status_code, 404)
            self.assertEqual(client.get(f"/api/library/collections/{catalog_id}").status_code, 404)

    def test_digest_rejection_and_series_review_revoke_public_access(self):
        media = self.media()
        self.catalog.published = True
        self.session.commit()
        self.assertTrue(playable_path(self.session, media).is_file())
        media.manual_status = "rejected"
        with self.assertRaises(ValueError):
            playable_path(self.session, media)
        media.manual_status = "passed"
        self.series.review_status = "excluded"
        with self.assertRaises(ValueError):
            playable_path(self.session, media)
        self.series.review_status = "approved"
        (self.data / media.relative_path).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            playable_path(self.session, media)

    def test_missing_and_duplicate_numbers_prevent_complete_claim(self):
        self.media()
        self.catalog.total_episodes, self.catalog.checked = 1, True
        detail = svc.catalog_detail(self.session, self.catalog)
        self.assertEqual(detail["summary"]["full_series"], "全剧完整性未确认")
        self.assertEqual(detail["summary"]["missing_numbers"], [1])

    def test_public_mode_and_readonly_admin_files(self):
        self.media("pending")
        with AdminClient(main.app) as client:
            result = client.get(f"/api/playback/collections/{self.catalog.id}")
            self.assertTrue(result.json()["episodes"][0]["file_url"])
            self.assertNotIn("relative_path", result.text)
            with patch("services.site.public_mode", return_value=True):
                self.assertEqual(client.get(f"/api/downloads/collections/{self.catalog.id}").status_code, 403)
                self.assertEqual(client.get(f"/api/library/collections/{self.catalog.id}").status_code, 404)


class CollectionMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_catalog_children_and_foreign_keys(self):
        from tests import test_publishing
        f = test_publishing.PublishingTests()
        f.setUp()
        try:
            path = f.root / "old.db"
            engine = create_engine(f"sqlite:///{path.as_posix()}")
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "004_downloads")
            with f.fixture.engine.connect() as source, closing(sqlite3.connect(path)) as old:
                old.execute("PRAGMA foreign_keys=OFF")
                for table in f.fixture.engine.dialect.get_table_names(source):
                    if table == "alembic_version":
                        continue
                    columns = [row[1] for row in old.execute(f'PRAGMA table_info("{table}")')]
                    if not columns:  # Newer private tables do not exist in the 004 fixture.
                        continue
                    names = ','.join(columns)
                    rows = source.exec_driver_sql(f'SELECT {names} FROM "{table}"').fetchall()
                    old.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})', [tuple(row) for row in rows])
                old.commit()
                old_columns = {table: ','.join('"'+r[1]+'"' for r in old.execute(f'PRAGMA table_info("{table}")'))
                               for table in ("ranking_runs", "download_episodes", "download_jobs", "media_files")}
                before = {table: old.execute(f'SELECT {old_columns[table]} FROM {table} ORDER BY id').fetchall()
                          for table in ("ranking_runs", "download_episodes", "download_jobs", "media_files")}
            upgrade_database(engine)
            with closing(sqlite3.connect(path)) as upgraded:
                self.assertEqual(upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0], REVISION)
                self.assertEqual(upgraded.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(upgraded.execute("PRAGMA foreign_key_check").fetchall(), [])
                for table, rows in before.items():
                    self.assertEqual(upgraded.execute(f'SELECT {old_columns[table]} FROM {table} ORDER BY id').fetchall(), rows)
                self.assertEqual(upgraded.execute("SELECT published,source_url FROM download_catalogs").fetchone(), (0, ""))
            upgrade_database(engine)
            engine.dispose()
        finally:
            f.tearDown()


class CollectionBundleTests(unittest.TestCase):
    def test_independent_public_bundle_without_rankings(self):
        from services.publishing import build_bundle, verify_bundle
        f = CollectionTests()
        f.setUp()
        try:
            media = f.media()
            f.catalog.published = True
            f.session.commit()
            source = f.data / "source.db"
            with f.f.engine.connect() as connection, closing(sqlite3.connect(source)) as target:
                connection.connection.driver_connection.backup(target)
                target.execute("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)")
                target.execute("INSERT INTO alembic_version VALUES (?)", (REVISION,))
                target.commit()
            result = build_bundle(source, f.data, f.data / "publish", [], preview=False, release_id="synthetic-collection")
            directory = f.data / "publish/synthetic-collection"
            self.assertEqual(verify_bundle(directory)["video_count"], 1)
            self.assertEqual(result["ranking_ids"], [])
            with closing(sqlite3.connect(directory / "public.db")) as db:
                self.assertEqual(db.execute("SELECT source_url FROM download_catalogs").fetchone()[0], "")
                self.assertEqual(db.execute("SELECT manual_note FROM media_files").fetchone()[0], "")
                self.assertEqual(db.execute("SELECT count(*) FROM ranking_runs").fetchone()[0], 0)
        finally:
            f.tearDown()
