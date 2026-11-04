"""Step-two tests use only synthetic sources and isolated databases."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.auth_support import AdminClient as TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import tests.test_top3 as fixtures
from models import DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile, RankingRun
from services import downloads as svc
from services.media import executable, validate
from services.package import build_package
from sources.download import allowed_media_url
from sources.douk import SourceUnavailable
from workers.download import process_one, recover


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RankingTests()
        self.fixture.setUp()
        f = self.fixture
        f.import_rows([f.row(author_id="same", series_key="same", series_title="合成下载测试剧", likes="900"),
                       f.row(author_id="same", series_key="same", series_title="合成下载测试剧", published_at="2026-10-01T12:00:00+08:00"),
                       f.row(likes="100"), f.row(likes="50"), f.row(likes="1")])
        f.approve_all()
        self.ranking = f.ranking()
        self.session = f.session
        self.catalog = svc.catalog_for(self.session, self.ranking["id"], self.ranking["items"][0]["series_id"])
        self.session.commit()
        self.factory = sessionmaker(bind=f.engine, autoflush=False)
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.patches = [patch.object(svc, "DATA", self.data), patch("workers.download.DATA", self.data), patch("services.package.DATA", self.data)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()
        self.temporary.cleanup()

    def detail(self):
        self.session.expire_all()
        return svc.catalog_detail(self.session, self.catalog)

    def trial(self):
        episode = self.detail()["episodes"][0]
        svc.enqueue(self.session, self.catalog, [episode["id"]], True)
        self.session.commit()
        return self.session.scalar(select(DownloadJob)).id

    def test_catalog_includes_old_episodes_but_never_reserve_series(self):
        self.assertEqual(len(self.detail()["episodes"]), 2)
        with self.assertRaisesRegex(ValueError, "实际入选"):
            svc.catalog_for(self.session, self.ranking["id"], self.ranking["items"][-1]["series_id"])
        self.assertEqual(self.detail()["summary"]["full_series"], "全剧完整性未确认")

    def test_manual_links_deduplicate_and_cannot_download_unreviewed(self):
        result = svc.add_links(self.session, self.catalog, "https://www.douyin.com/video/88888\nhttps://www.douyin.com/video/88888")
        self.assertEqual(result["added"], 1)
        row = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id == "88888"))
        with self.assertRaises(ValueError):
            svc.enqueue(self.session, self.catalog, [row.id], True)
        with self.assertRaises(ValueError):
            svc.add_links(self.session, self.catalog, "https://example.com/video/999")

    def test_batch_gate_idempotency_pause_cancel_resume_and_limit(self):
        rows = self.detail()["episodes"]
        with self.assertRaisesRegex(ValueError, "人工抽看"):
            svc.enqueue(self.session, self.catalog, [rows[0]["id"]], False)
        job_id = self.trial()
        self.assertEqual(svc.enqueue(self.session, self.catalog, [rows[0]["id"]], True)["queued"], 0)
        job = self.session.get(DownloadJob, job_id)
        for action, expected in [("pause", "paused"), ("resume", "queued"), ("cancel", "cancelled"), ("resume", "queued")]:
            svc.control_job(self.session, job, action)
            self.assertEqual(job.status, expected)
        job.attempts, job.status = 3, "failed"
        with self.assertRaisesRegex(ValueError, "三次"):
            svc.control_job(self.session, job, "resume")

    def test_recovery_keeps_job_and_does_not_mark_partial_success(self):
        job_id = self.trial()
        job = self.session.get(DownloadJob, job_id)
        job.status, job.attempts = "downloading", 1
        self.session.flush()
        recover(self.session)
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(self.detail()["summary"]["verified"], 0)

    def test_login_failure_pauses_entire_source_and_never_stores_credentials(self):
        job_id = self.trial()
        other = self.detail()["episodes"][1]
        self.session.add(DownloadJob(episode_id=other["id"]))
        self.session.commit()
        class LoginRequired:
            def resolve(self, _):
                raise SourceUnavailable("needs_login", "需要登录")
        with patch("services.media.media_tools", return_value={"ffprobe": True, "ffmpeg": True}):
            process_one(job_id, self.factory, LoginRequired())
        self.session.expire_all()
        self.assertTrue(all(job.status == "paused" for job in self.session.scalars(select(DownloadJob))))

    def test_invalid_media_is_never_successful_and_unknown_exception_is_redacted(self):
        job_id = self.trial()
        class Broken:
            def resolve(self, _):
                raise RuntimeError("secret-cookie-or-signed-url")
        with patch("services.media.media_tools", return_value={"ffprobe": True, "ffmpeg": True}):
            process_one(job_id, self.factory, Broken())
        self.session.expire_all()
        job = self.session.get(DownloadJob, job_id)
        self.assertEqual(job.status, "failed")
        self.assertNotIn("secret", job.message)
        self.assertIsNone(self.session.scalar(select(MediaFile)))

    def test_media_url_and_local_path_restrictions(self):
        allowed_media_url("https://v1.douyinvod.com/video.mp4")
        allowed_media_url("https://v3-dy-o.zjcdn.com/video.mp4")
        for url in ["http://v1.douyinvod.com/x", "https://localhost/x", "https://douyinvod.com.evil.test/x", "https://127.0.0.1/x", "https://u:p@douyinvod.com/x"]:
            with self.assertRaises(ValueError):
                allowed_media_url(url)
        for url in ["http://v3-dy-o.zjcdn.com/x", "https://zjcdn.com.evil.test/x", "https://u:p@v3-dy-o.zjcdn.com/x"]:
            with self.assertRaises(ValueError):
                allowed_media_url(url)
        with self.assertRaises(ValueError):
            svc.inside(self.data, "../outside")

    def test_directory_only_adds_correct_author_keeps_pending_and_pagination(self):
        payload = {"status": "ok", "cursor": 20, "has_more": True, "videos": [
            {"aweme_id": "808", "desc": "合成正片", "author": {"uid": "same"}, "duration": 5000},
            {"aweme_id": "809", "desc": "另一作者", "author": {"uid": "other"}}]}
        item = self.ranking["items"][0]
        row = self.session.get(RankingRun, self.ranking["id"])
        data = json.loads(row.snapshot)
        data["items"][0]["author_url"] = "https://www.douyin.com/user/test"
        row.snapshot = json.dumps(data)
        self.session.flush()
        with patch("sources.download.bridge_request", return_value=payload):
            self.assertEqual(svc.discover(self.session, self.catalog)["added"], 1)
            self.assertEqual(svc.discover(self.session, self.catalog)["added"], 0)
        episode = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.video_id == "808"))
        self.assertEqual(episode.status, "pending")
        self.assertEqual(self.catalog.cursor, "20")

    @unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "FFmpeg tools required")
    def test_real_media_worker_manual_gate_and_truthful_package(self):
        video = self.data / "synthetic.mp4"
        subprocess.run([executable("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True, capture_output=True)
        self.assertGreater(validate(video, 1, True)["duration"], 0)
        job_id = self.trial()
        class FixtureDownload:
            def resolve(self, _):
                return {"url": "synthetic-only", "revision": "synthetic", "author_account_id": "same", "duration": 1}
            def transfer(self, _, target, progress):
                shutil.copyfile(video, target)
                progress(target.stat().st_size)
        process_one(job_id, self.factory, FixtureDownload())
        self.session.expire_all()
        job = self.session.get(DownloadJob, job_id)
        self.assertEqual(job.status, "verified", job.message)
        self.assertFalse(self.detail()["summary"]["trial_passed"])
        media = self.session.scalar(select(MediaFile))
        with self.assertRaises(ValueError):
            svc.check_media(self.session, media, {"status": "passed", "note": "合成测试"})
        svc.check_media(self.session, media, {"status": "passed", "note": "合成测试抽检", "beginning": True, "middle": True, "ending": True, "sound": True})
        self.session.flush()
        self.assertTrue(self.detail()["summary"]["trial_passed"])
        export = build_package(self.session, self.session.get(RankingRun, self.ranking["id"]))
        with zipfile.ZipFile(svc.inside(self.data, export.relative_path)) as archive:
            names = archive.namelist()
            self.assertTrue(any(name.endswith("01_榜单报告.docx") for name in names))
            self.assertTrue(any(name.endswith(".mp4") for name in names))
            report = archive.read(next(name for name in names if name.endswith("04_剧集与下载记录.csv"))).decode("utf-8-sig")
            self.assertIn(media.sha256, report)
        path = svc.inside(self.data, media.relative_path)
        path.write_bytes(b"tampered")
        second = build_package(self.session, self.session.get(RankingRun, self.ranking["id"]))
        self.assertEqual(json.loads(second.summary)["files"], 0)
        self.assertIn("全剧完整性未确认", second.summary)

    def test_http_views_version_binding_and_local_mutations(self):
        import main
        def isolated():
            with self.factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        main.app.dependency_overrides[main.database_session] = isolated
        try:
            with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), patch.object(main, "SessionLocal", self.factory), TestClient(main.app) as client:
                for path in ["/", "/legacy", "/workbench", "/downloads"]:
                    self.assertEqual(client.get(path).status_code, 200)
                url = f"/api/downloads/rankings/{self.ranking['id']}"
                self.assertEqual(client.post(url + "/initialize", json={}).status_code, 200)
                self.assertEqual(len(client.get(url).json()["catalogs"]), 3)
                # A synthetic snapshot whose rank order differs from series IDs and array order.
                with self.factory.begin() as session:
                    ranking = session.get(RankingRun, self.ranking["id"])
                    snapshot = json.loads(ranking.snapshot)
                    selected = [item for item in snapshot["items"] if item["selected"]]
                    for position, item in enumerate(reversed(selected), 1):
                        item["position"] = position
                    ranking.snapshot = json.dumps(snapshot)
                expected = [item["series_id"] for item in sorted(selected, key=lambda item: item["position"])]
                self.assertEqual([catalog["series_id"] for catalog in client.get(url).json()["catalogs"]], expected)
                response = client.post(f"/api/downloads/catalogs/{self.catalog.id}/links", json={"links": "https://www.douyin.com/video/999"}, headers={"Origin": "https://untrusted.example"})
                self.assertEqual(response.status_code, 403)
                response = client.post(url + "/export", json={})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(client.get(response.json()["url"]).content)))
        finally:
            main.app.dependency_overrides.clear()

    def test_download_reuses_trial_active_and_repair_job(self):
        first = svc.download_one(self.session, self.catalog)
        second = svc.download_one(self.session, self.catalog)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertIsNone(first["file_url"])
        rows = self.detail()["episodes"]
        other = next(row for row in rows if not row["job"])
        with self.assertRaisesRegex(ValueError, "门槛"):
            svc.enqueue(self.session, self.catalog, [other["id"]], True)
        job = self.session.get(DownloadJob, first["job_id"])
        job.status = "paused"
        self.assertEqual(svc.download_one(self.session, self.catalog)["status"], "queued")
        job.status, job.attempts = "failed", 3
        with self.assertRaisesRegex(ValueError, "三次"):
            svc.download_one(self.session, self.catalog)

    def test_public_read_does_not_initialize_catalogs(self):
        import main
        from sqlalchemy import func
        def isolated():
            yield self.session
        main.app.dependency_overrides[main.database_session] = isolated
        try:
            before = self.session.scalar(select(func.count()).select_from(DownloadCatalog))
            with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), TestClient(main.app) as client:
                client.cookies.clear()
                result = client.get(f"/api/library/rankings/{self.ranking['id']}")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(len(result.json()["catalogs"]), 3)
                self.assertNotIn("relative_path", json.dumps(result.json()))
                self.assertNotIn("review_history", json.dumps(result.json()))
                self.assertEqual(self.session.scalar(select(func.count()).select_from(DownloadCatalog)), before)
        finally:
            main.app.dependency_overrides.clear()

    def test_attachment_range_integrity_and_access(self):
        import main
        from services.media import digest
        job_id = self.trial()
        job = self.session.get(DownloadJob, job_id)
        job.status = "verified"
        path = self.data / "synthetic-delivery.mp4"
        path.write_bytes(b"synthetic-video-content")
        media = MediaFile(job_id=job_id, relative_path=path.name, size=path.stat().st_size,
                          sha256=digest(path), duration=1, format="mp4", tracks='["video","audio"]', validation_note="synthetic")
        self.session.add(media)
        self.session.commit()
        def isolated():
            with self.factory() as session:
                yield session
        main.app.dependency_overrides[main.database_session] = isolated
        try:
            with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), TestClient(main.app) as client:
                url = f"/api/downloads/media/{media.id}/download"
                result = client.get(url)
                self.assertEqual(result.content, path.read_bytes())
                self.assertIn("attachment", result.headers["content-disposition"])
                self.assertEqual(result.headers["cache-control"], "no-store")
                result = client.get(url, headers={"Range": "bytes=0-3"})
                self.assertEqual(result.status_code, 206)
                self.assertEqual(result.content, b"synt")
                self.assertTrue(svc.download_one(self.session, self.catalog)["file_url"])
                media.manual_status = "rejected"
                self.session.commit()
                self.assertEqual(client.get(url).status_code, 400)
                media.manual_status = "pending"
                self.session.commit()
                path.write_bytes(b"X" * media.size)
                self.assertIn("摘要异常", client.get(url).text)
                self.assertEqual(svc.download_one(self.session, self.catalog)["status"], "queued")
                self.session.rollback()
                path.unlink()
                self.assertIn("文件缺失", client.get(url).text)
        finally:
            main.app.dependency_overrides.clear()

    def test_concurrent_clicks_return_one_job(self):
        import main
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        from sqlalchemy import create_engine
        target = self.data / "concurrent.db"
        from contextlib import closing
        with closing(sqlite3.connect(target)) as backup:
            with self.fixture.engine.connect() as source:
                source.connection.driver_connection.backup(backup)
        engine = create_engine("sqlite:///" + target.as_posix(), connect_args={"timeout": 15})
        factory = sessionmaker(bind=engine, autoflush=False)
        def isolated():
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        main.app.dependency_overrides[main.database_session] = isolated
        try:
            with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), TestClient(main.app) as client:
                url = f"/api/downloads/catalogs/{self.catalog.id}/download"
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda _: client.post(url, json={}), range(4)))
                self.assertTrue(all(row.status_code == 200 for row in results), [r.text for r in results])
                self.assertEqual(len({row.json()["job_id"] for row in results}), 1)
        finally:
            main.app.dependency_overrides.clear()
            engine.dispose()
