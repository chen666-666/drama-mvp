"""Publishing and public playback use synthetic data and isolated databases only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
import tests.test_top3 as fixtures
from models import DownloadJob, MediaFile, RankingRun
from services import downloads
from services.media import digest
from services.migration import REVISION
from services.publishing import build_bundle, verify_bundle


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.fixture = fixtures.RankingTests()
        self.fixture.setUp()
        f = self.fixture
        f.import_rows([f.row(likes="100"), f.row(likes="50"), f.row(likes="30"), f.row(likes="1")])
        f.approve_all()
        self.ranking = f.ranking()
        self.session = f.session
        self.patch = patch.object(downloads, "DATA", self.data)
        self.patch.start()
        self.catalog = downloads.catalog_for(self.session, self.ranking["id"], self.ranking["items"][0]["series_id"])
        episode = downloads.catalog_detail(self.session, self.catalog)["episodes"][0]
        downloads.enqueue(self.session, self.catalog, [episode["id"]], True)
        self.session.flush()
        job = self.session.scalar(select(DownloadJob))
        job.status = "verified"
        path = self.data / "videos/合成剧/测试.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(bytes(range(256)) * 16)
        self.video = path
        self.media = MediaFile(job_id=job.id, relative_path=path.relative_to(self.data).as_posix(), size=path.stat().st_size,
            duration=1.0, format="mp4", sha256=digest(path), tracks="[]", validation_note="合成校验", manual_status="passed", manual_note="内部备注不公开")
        self.session.add(self.media)
        self.session.commit()
        self.source = self.root / "source.db"
        self.save_source()

    def save_source(self):
        self.session.commit()
        with self.fixture.engine.connect() as conn:
            raw = conn.connection.driver_connection
            with closing(sqlite3.connect(self.source)) as target:
                raw.backup(target)
                target.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT PRIMARY KEY)")
                target.execute("DELETE FROM alembic_version")
                target.execute("INSERT INTO alembic_version VALUES (?)", (REVISION,))
                target.commit()

    def tearDown(self):
        self.patch.stop()
        self.fixture.tearDown()
        self.temp.cleanup()

    def build(self, **kwargs):
        return build_bundle(self.source, self.data, self.root / "publish", [self.ranking["id"]], **kwargs)

    def test_bundle_preserves_snapshot_ids_and_filters_private_records(self):
        before = digest(self.source)
        result = self.build(preview=False, release_id="synthetic-1")
        directory = self.root / "publish/synthetic-1"
        self.assertEqual(verify_bundle(directory), result)
        self.assertEqual(result["video_count"], 1)
        self.assertEqual(len(result["gaps"]), 2)
        self.assertEqual(digest(self.source), before)
        with closing(sqlite3.connect(directory / "public.db")) as db:
            snapshot = db.execute("SELECT snapshot FROM ranking_runs").fetchone()[0]
            self.assertEqual(snapshot, self.session.get(RankingRun, self.ranking["id"]).snapshot)
            self.assertEqual(db.execute("SELECT count(*) FROM search_jobs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT manual_note FROM media_files").fetchone()[0], "")
            self.assertEqual(db.execute("SELECT id FROM media_files").fetchone()[0], self.media.id)
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.build(preview=False, release_id="synthetic-1")

    def test_preview_excludes_missing_rejected_and_changed_files(self):
        result = self.build()
        self.assertEqual(result["video_count"], 1)
        self.assertFalse((self.root / "publish/source-instance.json").exists())
        self.media.manual_status = "pending"
        self.save_source()
        self.assertEqual(self.build()["video_count"], 0)
        self.media.manual_status = "passed"
        self.save_source()
        self.video.write_bytes(b"changed")
        self.assertEqual(self.build()["video_count"], 0)

    def test_tamper_paths_and_secret_text_fail_closed(self):
        from services.publishing import checked_text, relative_path
        for path in ("../x", "videos/../../x", "D:/video.mp4", "/videos/x", "videos//x"):
            with self.assertRaises(ValueError):
                relative_path(path)
        with self.assertRaises(ValueError):
            checked_text("Cookie: synthetic-session-value")
        self.build(preview=False, release_id="synthetic-1")
        (self.root / "publish/synthetic-1/data" / self.media.relative_path).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "摘要"):
            verify_bundle(self.root / "publish/synthetic-1")

    def test_previous_release_cannot_drop_old_rankings_or_change_snapshot(self):
        self.build(preview=False, release_id="synthetic-1")
        previous = self.root / "publish/synthetic-1/manifest.json"
        self.assertEqual(self.build(previous=previous)["video_count"], 1)
        row = self.session.get(RankingRun, self.ranking["id"])
        row.snapshot = row.snapshot + " "
        self.save_source()
        with self.assertRaisesRegex(ValueError, "快照"):
            self.build(previous=previous)

    def test_public_http_playback_ranges_readonly_and_no_admin_leaks(self):
        import main
        def dependency():
            yield self.session
        factory = sessionmaker(bind=self.fixture.engine, autoflush=False)
        main.app.dependency_overrides[main.database_session] = dependency
        try:
            with patch.dict(os.environ, {"DRAMA_SITE_MODE": "public"}), patch.object(main, "init_db"), patch.object(main, "SessionLocal", factory), TestClient(main.app) as client:
                home = client.get("/")
                self.assertEqual(home.status_code, 200)
                self.assertNotIn("下载管理", home.text)
                self.assertNotIn("发布到网站", home.text)
                watch = client.get("/watch?ranking_id=1&series_id=1")
                self.assertEqual(watch.status_code, 200)
                self.assertIn('id="watch-video"', watch.text)
                self.assertIn('data-manage="false"', watch.text)
                self.assertEqual(client.get("/downloads/watch").status_code, 403)
                detail = client.get(f"/api/library/rankings/{self.ranking['id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertNotIn("relative_path", detail.text)
                self.assertNotIn("manual_note", detail.text)
                self.assertNotIn("search_jobs", detail.text)
                url = detail.json()["catalogs"][0]["episodes"][0]["url"]
                response = client.get(url, headers={"Range": "bytes=2-9"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, self.video.read_bytes()[2:10])
                self.assertEqual(response.headers["content-range"], "bytes 2-9/4096")
                self.assertEqual(client.head(url).status_code, 200)
                download_url = detail.json()["catalogs"][0]["episodes"][0]["download_url"]
                attachment = client.get(download_url)
                self.assertEqual(attachment.status_code, 200)
                self.assertEqual(attachment.content, self.video.read_bytes())
                self.assertIn("attachment", attachment.headers["content-disposition"])
                self.assertEqual(attachment.headers["cache-control"], "no-store")
                self.assertEqual(client.head(download_url).status_code, 200)
                self.assertEqual(client.get(download_url, headers={"Range": "bytes=2-9"}).content, self.video.read_bytes()[2:10])
                self.assertIn('data-download=', home.text)
                self.assertEqual(client.get(url, headers={"Range":"bytes=99999-"}).status_code, 416)
                for route in main.app.routes:
                    for method in getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}:
                        self.assertEqual(client.request(method, route.path).status_code, 403, route.path)
                for path in ("/workbench", "/login", "/api/source/douk/status", "/api/downloads/status", "/docs", "/openapi.json", f"/api/downloads/media/{self.media.id}/file"):
                    self.assertEqual(client.get(path).status_code, 403, path)
                self.assertEqual(client.get(f"/rankings/{self.ranking['id']}/report").status_code, 200)
                original = self.video.read_bytes()
                self.video.write_bytes(b"x" * len(original))
                self.assertEqual(client.get(url).status_code, 404)
                self.video.write_bytes(original)
                self.media.manual_status = "rejected"
                self.session.flush()
                self.assertEqual(client.get(url).status_code, 404)
                self.assertEqual(client.get(f"/api/library/rankings/{self.ranking['id']}").json()["catalogs"][0]["episodes"], [])
        finally:
            main.app.dependency_overrides.clear()

    def test_server_preflight_activation_idempotency_and_failed_activation_recovery(self):
        from services.site_release import activate, rollback
        import shutil
        self.build(preview=False, release_id="synthetic-1")
        server = self.root / "server"
        incoming = server / "data/site/incoming/synthetic-1"
        shutil.copytree(self.root / "publish/synthetic-1", incoming)
        (server / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
        commands = []
        runner = lambda command, root: commands.append(command)
        checker = lambda url, manifest: [{"status":206}]
        self.assertEqual(activate(server, incoming)["status"], "preview")
        self.assertEqual(commands, [])
        manifest = verify_bundle(incoming)
        with patch("services.site_release.code_fingerprint", return_value=manifest["code_fingerprint"]):
            with self.assertRaisesRegex(ValueError, "首次激活"):
                activate(server, incoming, preview=False, runner=runner, checker=checker)
            result = activate(server, incoming, preview=False, inventory_confirmed=True, rights_confirmed=True,
                              domain="https://synthetic.example", runner=runner, checker=checker)
            self.assertEqual(result["status"], "active")
            self.assertEqual(activate(server, incoming)["status"], "already_active")
            with self.assertRaisesRegex(ValueError, "尚无"):
                rollback(server, runner=runner, checker=checker)
        previous = self.root / "publish/synthetic-1/manifest.json"
        self.build(preview=False, release_id="synthetic-2", previous=previous)
        second = server / "data/site/incoming/synthetic-2"
        shutil.copytree(self.root / "publish/synthetic-2", second)
        current_path = server / "data/site/current.json"
        before = current_path.read_bytes()
        with patch("services.site_release.code_fingerprint", return_value=manifest["code_fingerprint"]), patch("services.site_release.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "已恢复原服务"):
                activate(server, second, preview=False, rights_confirmed=True, domain="https://synthetic.example",
                         runner=runner, checker=lambda *_: (_ for _ in ()).throw(ValueError("controlled-failure")))
            self.assertEqual(current_path.read_bytes(), before)
            self.assertIn("synthetic-1", " ".join(commands[-1]))
            self.assertEqual(activate(server, second, preview=False, rights_confirmed=True, domain="https://synthetic.example",
                                     runner=runner, checker=checker)["status"], "active")
            self.assertEqual(rollback(server, preview=False, runner=runner, checker=checker)["release_id"], "synthetic-1")

    def test_anonymous_manage_playback_and_safety_checks(self):
        import main
        from models import DownloadEpisode
        from tests.auth_support import AdminClient

        def dependency():
            yield self.session

        main.app.dependency_overrides[main.database_session] = dependency
        url = f"/api/library/media/{self.media.id}"
        detail_url = f"/api/library/rankings/{self.ranking['id']}"
        job = self.session.get(DownloadJob, self.media.job_id)
        episode = self.session.get(DownloadEpisode, job.episode_id)
        try:
            with patch.dict(os.environ, {"DRAMA_SITE_MODE": "manage"}), patch.object(main, "init_db"), patch.object(main, "recover_jobs"), AdminClient(main.app), TestClient(main.app, base_url="https://synthetic.example") as client:
                self.assertEqual(len(client.cookies), 0)
                self.assertEqual(client.get("/watch").status_code, 200)
                detail = client.get(detail_url)
                catalog = detail.json()["catalogs"][0]
                self.assertEqual(len(catalog["episodes"]), 1)
                download_url = catalog["episodes"][0]["download_url"]
                attachment = client.get(download_url)
                self.assertEqual(attachment.status_code, 200)
                self.assertEqual(attachment.content, self.video.read_bytes())
                self.assertIn("attachment", attachment.headers["content-disposition"])
                self.assertEqual(attachment.headers["cache-control"], "no-store")
                partial = client.get(download_url, headers={"Range": "bytes=2-9"})
                self.assertEqual(partial.status_code, 206)
                self.assertEqual(partial.content, self.video.read_bytes()[2:10])
                self.assertEqual(client.head(download_url).status_code, 200)
                self.assertEqual(client.get(download_url, headers={"Range": "bytes=99999-"}).status_code, 416)
                self.assertEqual(client.get("/api/library/media/999999/download").status_code, 404)
                self.assertNotIn("内部备注", detail.text)
                self.assertNotIn("relative_path", detail.text)
                response = client.get(url, headers={"Range": "bytes=2-9"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, self.video.read_bytes()[2:10])
                self.assertEqual(client.head(url).status_code, 200)
                self.assertEqual(self.media.manual_status, "passed")
                # Listing and streaming apply the same eligibility rules.
                for row, field, invalid in ((self.media, "manual_status", "rejected"),
                                            (self.media, "manual_status", "pending"),
                                            (job, "status", "failed"),
                                            (episode, "status", "pending"),
                                            (episode, "access", "private"),
                                            (self.media, "relative_path", "../outside.mp4"),
                                            (self.media, "relative_path", "videos/missing.mp4"),
                                            (self.media, "size", 1),
                                            (self.media, "sha256", "0" * 64)):
                    previous = getattr(row, field)
                    setattr(row, field, invalid)
                    self.session.flush()
                    self.assertEqual(client.get(url).status_code, 404, field)
                    self.assertEqual(client.get(download_url).status_code, 404, field)
                    self.assertEqual(client.get(detail_url).json()["catalogs"][0]["episodes"], [], field)
                    setattr(row, field, previous)
                    self.session.flush()
                snapshot = self.session.get(RankingRun, self.ranking["id"])
                before = snapshot.snapshot
                value = json.loads(before)
                value["items"][0]["selected"] = False
                snapshot.snapshot = json.dumps(value)
                self.session.flush()
                self.assertEqual(client.get(url).status_code, 404)
                self.assertEqual(client.get(download_url).status_code, 404)
                snapshot.snapshot = before
                self.session.flush()
                original_bytes = self.video.read_bytes()
                self.video.write_bytes(b"X" * len(original_bytes))
                self.assertEqual(client.get(download_url).status_code, 404)
                self.video.write_bytes(original_bytes)
                for path in ("/downloads", "/downloads/watch", "/workbench"):
                    self.assertEqual(client.get(path, follow_redirects=False).status_code, 303, path)
                for path in (f"/api/downloads/media/{self.media.id}/file", f"/api/downloads/media/{self.media.id}/download", "/api/source/douk/status"):
                    self.assertEqual(client.get(path).status_code, 401, path)
                self.assertEqual(client.post("/api/runs", json={}).status_code, 401)
                # Independent public bundles retain the same manual review requirement.
                self.media.manual_status = "pending"
                self.session.flush()
                with patch.dict(os.environ, {"DRAMA_SITE_MODE": "public"}):
                    self.assertEqual(client.get(url).status_code, 404)
                    self.assertEqual(client.get(download_url).status_code, 404)
                    self.assertEqual(client.get(detail_url).json()["catalogs"][0]["episodes"], [])
        finally:
            main.app.dependency_overrides.clear()

    def test_real_public_startup_uses_readonly_database(self):
        import subprocess
        self.build(preview=False, release_id="readonly")
        public_db = self.root / "publish/readonly/public.db"
        before = digest(public_db)
        env = {**os.environ, "DRAMA_SITE_MODE": "public", "DRAMA_SITE_RELEASE": "readonly", "DRAMA_DATABASE_URL": f"sqlite:///{public_db.as_posix()}",
               "DRAMA_DATA_DIR": str(self.root / "publish/readonly/data")}
        code = """
from fastapi.testclient import TestClient
from sqlalchemy import text
from database import engine
from main import app
with TestClient(app) as client:
    assert client.get('/').status_code == 200
    assert client.get('/api/library/rankings/1').status_code == 200
    assert client.post('/api/runs',json={}).status_code == 403
with engine.begin() as conn:
    try:
        conn.execute(text('DELETE FROM dramas'))
        raise AssertionError('public database was writable')
    except Exception as exc:
        assert 'readonly' in str(exc), str(exc)
"""
        result = subprocess.run([__import__("sys").executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest(public_db), before)

    def test_workstation_preview_build_and_transfer_configuration(self):
        import main
        import api_publishing
        from types import SimpleNamespace
        from tests.auth_support import AdminClient
        with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), patch.object(api_publishing, "engine", SimpleNamespace(url=SimpleNamespace(database=str(self.source)))), patch.object(api_publishing, "ROOT", self.root), patch("services.publish_transfer.CONFIG", self.root / "not-configured.json"), AdminClient(main.app) as client:
            self.assertEqual(client.get("/publish").status_code, 200)
            self.assertFalse(client.get("/api/publishing/config").json()["configured"])
            result = client.post("/api/publishing/preview", json={"ranking_ids":[self.ranking["id"]]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["video_count"], 1)
            result = client.post("/api/publishing/build", json={"ranking_ids":[self.ranking["id"]]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(client.get("/api/publishing/history").json()["bundles"]), 1)
            result = client.post(f"/api/publishing/bundles/{result.json()['release_id']}/transfer",json={})
            self.assertEqual(result.status_code, 400)
            self.assertIn("尚未配置" if os.name == "nt" else "仅在 Windows", result.text)


if __name__ == "__main__":
    unittest.main()
