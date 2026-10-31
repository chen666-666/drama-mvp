"""Automatic collection tests: all video records below are synthetic."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import CollectionRun, Episode, MetricSnapshot, SearchHit, SearchJob, Series
from services.search import job_dict, prepare_job, prepare_resume, recover_jobs, run_job
from sources.douk import SourceUnavailable, normalize_video, save_session, session_status

CUTOFF = datetime(2026, 10, 30, 4)


def video(number="30001", days=1):
    return {"aweme_id": number, "desc": "合成测试正片", "create_time": int((CUTOFF-timedelta(days=days)).replace(tzinfo=timezone.utc).timestamp()),
            "author": {"uid": "synthetic-author", "sec_uid": "synthetic-sec", "nickname": "合成测试作者"},
            "statistics": {"digg_count": 100, "comment_count": 0, "collect_count": None, "share_count": 3}}


class FakeSource:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def search_page(self, keyword, sort, offset, count, cutoff):
        self.calls.append((keyword, sort, offset))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        with self.factory() as session:
            run = CollectionRun(name="合成搜索批次", mode="sample", cutoff_at=CUTOFF, window_hours=168,
                                keywords=json.dumps(["AI短剧"]), official_metadata="{}")
            session.add(run)
            session.commit()
            self.run_id = run.id

    def tearDown(self):
        self.engine.dispose()

    def job(self, pages=2):
        with self.factory() as session:
            result = prepare_job(session, session.get(CollectionRun, self.run_id), ["likes"], pages)
            session.commit()
            return result.id

    def test_normalization_does_not_infer_series_ai_or_unknown_metrics(self):
        raw = video()
        raw["statistics"].update(comment_count="1.2万", share_count=-1)
        row = normalize_video(raw, "AI短剧", "likes", "1", CUTOFF)
        self.assertEqual(row["series_key"], "pending-video:30001")
        self.assertIsNone(row["comments"])
        self.assertIsNone(row["shares"])
        self.assertNotIn("ai_type", row)
        self.assertEqual(row["author_id"], "synthetic-author")
        raw["mix_info"] = {"mix_id": "123", "mix_name": "合成合集"}
        self.assertEqual(normalize_video(raw, "AI短剧", "general", "1", CUTOFF)["series_key"], "mix:123")

    def test_login_pause_preserves_cursor_resume_deduplicates_and_filters_window(self):
        job_id = self.job()
        source = FakeSource([{"videos": [video(), video("30002", days=8)], "offset": 10, "has_more": True},
                             SourceUnavailable("needs_login", "请先登录")])
        run_job(job_id, self.factory, source, lambda _: None)
        with self.factory() as session:
            job = session.get(SearchJob, job_id)
            state = job_dict(job)
            self.assertEqual(state["status"], "needs_login")
            self.assertEqual(state["progress"]["offset"], 10)
            self.assertEqual(state["progress"]["imported"], 1)
            self.assertEqual(state["progress"]["skipped"], 1)
            self.assertEqual(session.scalar(select(func.count(Episode.id))), 1)
            prepare_resume(session, job)
            session.commit()
        resumed = FakeSource([{"videos": [video()], "offset": 20, "has_more": False}])
        run_job(job_id, self.factory, resumed, lambda _: None)
        self.assertEqual(resumed.calls, [("AI短剧", "likes", 10)])
        with self.factory() as session:
            state = job_dict(session.get(SearchJob, job_id))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["progress"]["pages_done"], 2)
            self.assertEqual(state["progress"]["requests"], 3)
            self.assertEqual(session.scalar(select(func.count(Episode.id))), 1)
            self.assertEqual(session.scalar(select(func.count(MetricSnapshot.id))), 2)
            self.assertEqual(session.scalar(select(func.count(SearchHit.id))), 2)
            self.assertEqual(session.scalar(select(Series.review_status)), "pending")
            self.assertTrue(all(m.method == "douk" for m in session.scalars(select(MetricSnapshot))))

    def test_confirmed_grouping_and_episode_number_survive_refresh(self):
        job_id = self.job(1)
        run_job(job_id, self.factory, FakeSource([{"videos": [video()], "has_more": False}]), lambda _: None)
        with self.factory() as session:
            episode = session.scalar(select(Episode))
            series = session.get(Series, episode.series_id)
            series.series_key, series.title, series.review_status = "confirmed", "核验后的剧名", "approved"
            episode.episode_number, episode.review_status = 3, "approved"
            session.commit()
        job_id = self.job(1)
        run_job(job_id, self.factory, FakeSource([{"videos": [video()], "has_more": False}]), lambda _: None)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Series.title)), "核验后的剧名")
            self.assertEqual(session.scalar(select(Episode.episode_number)), 3)
            self.assertEqual(session.scalar(select(Episode.review_status)), "approved")
            self.assertEqual(session.scalar(select(func.count(Series.id))), 1)

    def test_budgets_overlap_official_mode_and_restart_recovery(self):
        job_id = self.job(1)
        with self.factory() as session:
            run = session.get(CollectionRun, self.run_id)
            with self.assertRaises(ValueError): prepare_job(session, run, ["likes"], 1)
        recover_jobs(self.factory)
        with self.factory() as session:
            self.assertEqual(session.get(SearchJob, job_id).status, "interrupted")
            run = session.get(CollectionRun, self.run_id)
            run.keywords = json.dumps([str(n) for n in range(7)])
            with self.assertRaises(ValueError): prepare_job(session, run, ["likes", "latest", "general"], 1)
            run.mode = "official"
            with self.assertRaises(ValueError): prepare_job(session, run, ["likes"], 1)

    def test_failure_is_not_success_and_unknown_exception_not_stored(self):
        for exception in [SourceUnavailable("blocked", "需要验证"), RuntimeError("sensitive-cookie=hidden")]:
            job_id = self.job(1)
            run_job(job_id, self.factory, FakeSource([exception]), lambda _: None)
            with self.factory() as session:
                result = job_dict(session.get(SearchJob, job_id))
                self.assertIn(result["status"], ["blocked", "failed"])
                self.assertEqual(result["progress"]["pages_done"], 0)
                self.assertNotIn("sensitive-cookie", json.dumps(result))

    def test_local_session_never_returned_or_echoed(self):
        import main
        with tempfile.TemporaryDirectory() as directory, patch("sources.douk.SESSION_PATH", Path(directory)/"login.json"), \
             patch.object(main, "init_db"), patch.object(main, "recover_jobs"), TestClient(main.app) as client:
            cookie = "sessionid=synthetic-secret; other=value"
            result = client.post("/api/source/douk/session", json={"cookie": cookie})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["configured"])
            self.assertNotIn("synthetic-secret", result.text)
            for content in ({"cookie": {"synthetic-secret": 1}}, {"cookie": "synthetic-secret"}):
                result = client.post("/api/source/douk/session", json=content)
                self.assertEqual(result.status_code, 400)
                self.assertNotIn("synthetic-secret", result.text)
            self.assertEqual(client.post("/api/source/douk/session", json={"cookie": cookie},
                             headers={"origin": "https://unrelated.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
