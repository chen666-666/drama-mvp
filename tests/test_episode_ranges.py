"""Range persistence, API validation, immutable history and media eligibility."""
from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select

import main
from models import DownloadEpisode, Episode, RankingRun, ReviewRecord
from services import downloads
from services.episode_numbers import parse_episode_number
from services.export import export_zip
from services.migration import REVISION, upgrade_database
from tests import test_collections
from tests.auth_support import AdminClient


class EpisodeRangeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_collections.CollectionTests()
        self.fixture.setUp()
        self.session = self.fixture.session
        self.catalog = self.fixture.catalog
        self.episode = self.session.scalar(select(DownloadEpisode))

    def tearDown(self):
        self.fixture.tearDown()

    def payload(self, value):
        return dict(title="无集号标题的合成视频", episode_number=value, status="approved", access="public",
                    note="人工确认此视频包含指定集数", evidence_url=self.episode.original_url,
                    ownership=True, main_episode=True, not_duplicate=True)

    def test_range_api_roundtrip_batch_review_and_gap_hints(self):
        with AdminClient(main.app) as client:
            response = client.post(f"/api/downloads/episodes/{self.episode.id}/review", json=self.payload("1-12"))
            self.assertEqual(response.status_code, 200, response.text)
            self.session.expire_all()
            self.assertIsNone(self.episode.episode_number)
            self.assertEqual(self.episode.episode_range, "1-12")
            self.catalog.total_episodes = 13
            self.session.flush()
            detail = downloads.catalog_detail(self.session, self.catalog)
            self.assertEqual(detail["episodes"][0]["episode_range"], "1-12")
            self.assertEqual(detail["summary"]["missing_numbers"], [13])
            self.assertEqual(detail["summary"]["full_series"], "全剧完整性未确认")
            downloads.review_episodes(self.session, self.catalog, {
                **self.payload(None), "episode_ids": [self.episode.id], "public_access": True})
            self.assertEqual(self.episode.episode_range, "1-12")
            history = json.loads(self.episode.review_history)
            self.assertEqual(history[-1]["previous"]["episode_range"], "1-12")
            self.assertEqual(history[-1]["episode_range"], "1-12")

    def test_single_and_blank_clear_previous_range(self):
        for value, expected in [("1-12", (None, "1-12")), ("3", (3, None)), ("", (None, None))]:
            downloads.review_episode(self.session, self.episode, self.payload(value))
            self.assertEqual((self.episode.episode_number, self.episode.episode_range), expected)

    def test_invalid_range_does_not_modify_approval_or_history(self):
        with AdminClient(main.app) as client:
            for value in ("12-1", "0-12", "1-", "1-2-3", "1.5", "1,2", True, 1.5):
                with self.subTest(value=value):
                    response = client.post(f"/api/downloads/episodes/{self.episode.id}/review", json=self.payload(value))
                    self.assertEqual(response.status_code, 422, response.text)
            self.session.expire_all()
            self.assertEqual(self.episode.episode_number, 120)
            self.assertIsNone(self.episode.episode_range)

    def test_metadata_range_marks_compilation_and_keeps_old_snapshot(self):
        self.session.scalar(select(Episode)).published_at = self.fixture.f.run.cutoff_at
        before = self.fixture.f.ranking()
        saved = self.session.get(RankingRun, before["id"])
        original = saved.snapshot
        episode = self.session.scalar(select(Episode))
        with AdminClient(main.app) as client:
            response = client.post(f"/api/episodes/{episode.id}/metadata", json={
                "episode_number": "1-12", "content_kind": "single", "content_note": "一段视频含十二集",
                "reason": "逐项核对视频范围", "evidence_url": episode.original_url})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(episode.content_kind, "compilation")
            self.assertEqual(episode.review_status, "approved")
            self.assertEqual(episode.episode_range, "1-12")
            self.assertIsNone(episode.episode_number)
            after = self.fixture.f.ranking()
            self.assertEqual(saved.snapshot, original)
            self.assertEqual(after["items"][0]["episode_range"], "1-12")
            self.assertEqual(after["items"][0]["content_kind"], "compilation")
            new_catalog = downloads.catalog_for(self.session, after["id"], episode.series_id)
            self.assertEqual(downloads.catalog_detail(self.session, new_catalog)["episodes"][0]["episode_range"], "1-12")
            audit = list(self.session.scalars(select(ReviewRecord).where(ReviewRecord.status == "metadata_updated")))[-1]
            self.assertEqual(json.loads(audit.checks)["changes"]["episode_range"]["after"], "1-12")
            with zipfile.ZipFile(io.BytesIO(export_zip(after))) as archive:
                rows = list(csv.DictReader(io.StringIO(archive.read("02_候选与排名.csv").decode("utf-8-sig"))))
                self.assertEqual(rows[0]["episode_range"], "1-12")

    def test_public_and_admin_playback_return_reviewed_range(self):
        self.fixture.media()
        downloads.review_episode(self.session, self.episode, self.payload("1-12"))
        self.catalog.published = True
        self.session.commit()
        with AdminClient(main.app) as client:
            admin = client.get(f"/api/playback/collections/{self.catalog.id}")
            self.assertEqual(admin.json()["episodes"][0]["episode_range"], "1-12")
            client.cookies.clear()
            public = client.get(f"/api/library/collections/{self.catalog.id}")
            row = public.json()["catalogs"][0]["episodes"][0]
            self.assertEqual(row["episode_range"], "1-12")
            self.assertIsNone(row["episode_number"])
            self.assertNotIn("review_history", row)
            self.assertEqual(client.get(f"/api/downloads/collections/{self.catalog.id}").status_code, 401)

    def test_csv_range_preserves_single_video(self):
        result = self.fixture.f.import_rows([self.fixture.f.row(episode_number="1-12")])
        self.assertEqual(result["imported"], 1)
        row = self.session.scalar(select(Episode).order_by(Episode.id.desc()))
        self.assertIsNone(row.episode_number)
        self.assertEqual(row.episode_range, "1-12")
        self.assertEqual(row.content_kind, "compilation")

    def test_existing_workflow_and_quick_review_stamps_remain_valid_without_range(self):
        from services.review_workflows import episode_stamp, fingerprint
        from services.quick_reviews import eligibility
        old = [self.episode.catalog_id, self.episode.video_id, self.episode.title, self.episode.original_url,
               self.episode.episode_number, self.episode.status, self.episode.access, self.episode.review_history]
        self.assertEqual(episode_stamp(self.episode), fingerprint(old))
        old_quick = [*old, self.catalog.ranking_id, self.catalog.series_id, self.catalog.published]
        self.assertEqual(eligibility(self.episode, self.catalog), old_quick)
        self.episode.episode_number = None
        self.episode.episode_range = "1-12"
        first = episode_stamp(self.episode)
        quick = eligibility(self.episode, self.catalog)
        self.episode.episode_range = "1-13"
        self.assertNotEqual(episode_stamp(self.episode), first)
        self.assertNotEqual(eligibility(self.episode, self.catalog), quick)


class RangeParserAndMigrationTests(unittest.TestCase):
    def test_normalization_and_rejection(self):
        for value in ("1-12", " 01 - 012 ", "１－１２", "1至12", "1–12"):
            self.assertEqual(parse_episode_number(value), (None, "1-12"))
        self.assertEqual(parse_episode_number("3"), (3, None))
        self.assertEqual(parse_episode_number(""), (None, None))
        for value in ("12-1", "0-12", "1e3", "1.2", [], True, 1.5):
            with self.assertRaises(ValueError):
                parse_episode_number(value)

    def test_009_upgrade_preserves_all_existing_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old009.db"
            engine = create_engine(f"sqlite:///{path.as_posix()}")
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "009_episode_discovery")
                connection.exec_driver_sql("INSERT INTO dramas VALUES (1,'历史资料','','','','',5,'','历史分类','2026-11-01')")
            with closing(sqlite3.connect(path)) as db:
                tables = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")]
                previous = {table: ([r[1] for r in db.execute(f'PRAGMA table_info("{table}")')],
                                   db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()) for table in tables}
            upgrade_database(engine)
            upgrade_database(engine)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], REVISION)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                for table, (columns, rows) in previous.items():
                    names = ','.join(f'"{name}"' for name in columns)
                    self.assertEqual(db.execute(f'SELECT {names} FROM "{table}" ORDER BY rowid').fetchall(), rows)
                for table in ("episodes", "download_episodes"):
                    self.assertIn("episode_range", [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')])
            engine.dispose()
