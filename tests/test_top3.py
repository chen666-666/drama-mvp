"""Boundary and end-to-end tests use synthetic data in isolated databases only."""
import csv
from contextlib import closing
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import Author, CollectionRun, Drama, Episode, MetricSnapshot, RankingRun, SearchHit, Series
from services.common import KEYWORDS, parse_time
from services.export import export_zip
from services.migration import upgrade_database
from services.ranking import make_ranking
from services.review import merge_series, review
from sources.manual import CSV_FIELDS, import_csv, import_links

CUTOFF = datetime(2026, 10, 30, 4)


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, autoflush=False)
        self.run = CollectionRun(name="合成测试批次", mode="sample", cutoff_at=CUTOFF, window_hours=168,
                                 keywords=json.dumps(KEYWORDS), official_metadata="{}")
        self.session.add(self.run)
        self.session.flush()
        self.counter = 0

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def row(self, **changes):
        self.counter += 1
        row = {"video_id": str(1000 + self.counter), "original_url": f"https://www.douyin.com/video/{1000+self.counter}",
               "title": "合成测试正片", "series_key": f"series-{self.counter}", "series_title": "合成测试剧名",
               "author_id": f"author-{self.counter}", "author_name": "合成测试作者", "ai_type": "AI漫剧",
               "published_at": "2026-10-29T12:00:00+08:00", "sampled_at": "2026-10-30T12:00:00+08:00",
               "likes": "10", "comments": "1", "favorites": "1", "shares": "1", "keyword": "AI短剧", "sort_order": "点赞", "position": "前10条"}
        row.update(changes)
        return row

    def import_rows(self, rows):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return import_csv(self.session, self.run.id, output.getvalue())

    def approve_all(self):
        for series in self.session.scalars(select(Series)):
            review(self.session, {"target_type": "series", "target_id": series.id, "status": "approved", "reason": "合成测试依据",
                                 "evidence_url": "https://www.douyin.com/", "ai_type": "AI漫剧", "ai_basis": "合成测试声明",
                                 "checks": {"story": True, "ai": True, "original_author": True}})
        for episode in self.session.scalars(select(Episode)):
            review(self.session, {"target_type": "episode", "target_id": episode.id, "status": "approved", "reason": "合成测试正片核验",
                                 "evidence_url": episode.original_url,
                                 "checks": {"main_episode": True, "ownership": True, "public": True, "not_duplicate": True}})

    def ranking(self):
        return make_ranking(self.session, self.run)

    def test_weighted_top3_and_reserves(self):
        self.import_rows([self.row(likes=str(value)) for value in [5, 100, 30, 20]])
        self.approve_all()
        ranking = self.ranking()
        self.assertEqual(ranking["scoring_mode"], "weighted")
        self.assertEqual([row["score"] for row in ranking["items"]], [112, 42, 32, 17])
        self.assertEqual(sum(row["selected"] for row in ranking["items"]), 3)

    def test_same_author_same_series_uses_best_episode_once(self):
        self.import_rows([self.row(author_id="same", series_key="same", likes="50"), self.row(author_id="same", series_key="same", likes="80")])
        self.approve_all()
        result = self.ranking()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["likes"], 80)

    def test_same_title_different_authors_stays_separate(self):
        self.import_rows([self.row(series_key="same"), self.row(series_key="same")])
        self.approve_all()
        self.assertEqual(len(self.ranking()["items"]), 2)

    def test_window_both_boundaries_included(self):
        values = [CUTOFF - timedelta(hours=168), CUTOFF, CUTOFF-timedelta(hours=168, microseconds=1), CUTOFF+timedelta(microseconds=1)]
        self.import_rows([self.row(published_at=value.isoformat()+"Z", sampled_at="2026-10-30T13:00:00Z") for value in values])
        self.approve_all()
        self.assertEqual(len(self.ranking()["items"]), 2)

    def test_one_missing_metric_downgrades_entire_comparison(self):
        self.import_rows([self.row(likes="20", shares="100"), self.row(likes="30", comments="")])
        self.approve_all()
        result = self.ranking()
        self.assertEqual(result["scoring_mode"], "likes")
        self.assertEqual([item["score"] for item in result["items"]], [30, 20])

    def test_missing_likes_excluded_and_never_filled_with_zero(self):
        self.import_rows([self.row(likes=""), self.row(likes="0", comments="0", shares="0", favorites="0")])
        self.approve_all()
        result = self.ranking()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["score"], 0)
        self.assertIsNone(result["candidates"][0]["likes"])
        self.assertIn("公开点赞未知，无法比较", result["candidates"][0]["exclusion_reasons"])

    def test_nonrepresentative_episode_also_triggers_uniform_downgrade(self):
        self.import_rows([self.row(author_id="same", series_key="same", likes="100"), self.row(author_id="same", series_key="same", likes="1", shares="")])
        self.approve_all()
        self.assertEqual(self.ranking()["scoring_mode"], "likes")

    def test_outside_window_missing_metric_does_not_downgrade(self):
        self.import_rows([self.row(), self.row(published_at="2026-01-01T00:00:00Z", shares="")])
        self.approve_all()
        self.assertEqual(self.ranking()["scoring_mode"], "weighted")

    def test_weighted_tie_uses_shares_then_favorites(self):
        self.import_rows([self.row(likes="10", comments="0", favorites="0", shares="0"),
                          self.row(likes="5", comments="0", favorites="0", shares="1")])
        self.approve_all()
        self.assertEqual(self.ranking()["items"][0]["shares"], 1)

    def test_weighted_tie_uses_favorites_before_publish_time(self):
        self.import_rows([self.row(likes="10", comments="0", favorites="0", shares="0"),
                          self.row(likes="6", comments="0", favorites="1", shares="0", published_at="2026-10-28T00:00:00Z")])
        self.approve_all()
        self.assertEqual(self.ranking()["items"][0]["favorites"], 1)

    def test_likes_tie_skips_secondary_metrics_and_uses_publish_then_id(self):
        self.import_rows([self.row(likes="10", comments="", shares="999", published_at="2026-10-28T00:00:00Z"),
                          self.row(likes="10", comments="", shares="0"), self.row(likes="10", comments="", shares="0")])
        self.approve_all()
        self.assertEqual([item["video_id"] for item in self.ranking()["items"]], ["1002", "1003", "1001"])

    def test_pending_and_excluded_never_rank(self):
        self.import_rows([self.row(likes="99999")])
        self.assertEqual(self.ranking()["items"], [])
        self.approve_all()
        episode = self.session.scalar(select(Episode))
        review(self.session, {"target_type": "episode", "target_id": episode.id, "status": "excluded", "reason": "合成测试预告",
                             "evidence_url": episode.original_url, "checks": {}})
        self.assertEqual(self.ranking()["items"], [])

    def test_approval_requires_evidence_checks_and_ai_basis(self):
        self.import_rows([self.row()])
        series = self.session.scalar(select(Series))
        with self.assertRaises(ValueError):
            review(self.session, {"target_type": "series", "target_id": series.id, "status": "approved", "reason": "证据", "evidence_url": "https://www.douyin.com/", "checks": {}})

    def test_csv_partial_success_and_no_orphans_from_failed_row(self):
        result = self.import_rows([self.row(), self.row(likes="1.2万"), self.row(source_url="javascript:alert(1)")])
        self.assertEqual(result["imported"], 1)
        self.assertEqual([item["line"] for item in result["errors"]], [3, 4])
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Author)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Episode)), 1)

    def test_repeat_import_adds_metric_history_not_duplicate_episode(self):
        row = self.row()
        self.import_rows([row, {**row, "likes": "40"}])
        self.approve_all()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Episode)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MetricSnapshot)), 2)
        self.assertEqual(self.ranking()["items"][0]["likes"], 40)

    def test_id_and_url_mismatch_rejected(self):
        result = self.import_rows([self.row(original_url="https://www.douyin.com/video/9999")])
        self.assertEqual(result["imported"], 0)

    def test_link_placeholders_and_no_automatic_metadata(self):
        result = import_links(self.session, self.run.id,
                              "分享 https://www.douyin.com/video/12345\nhttps://v.douyin.com/example/\nhttps://www.douyin.com/user/example", {})
        self.assertEqual(result["imported"], 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Episode)), 1)
        self.assertEqual(self.ranking()["items"], [])

    def test_unqualified_count_explains_shortfall(self):
        result = self.ranking()
        self.assertIn("0 部", result["shortfall"])

    def test_published_snapshot_stays_identical_after_new_sample_and_review(self):
        row = self.row()
        self.import_rows([row]); self.approve_all()
        first = self.ranking()
        original = self.session.get(RankingRun, first["id"]).snapshot
        self.import_rows([{**row, "likes": "500"}])
        second = self.ranking()
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["items"][0]["likes"], 500)
        self.assertEqual(self.session.get(RankingRun, first["id"]).snapshot, original)
        self.assertEqual(first["items"][0]["likes"], 10)

    def test_manual_merge_requires_same_author_and_resets_review(self):
        self.import_rows([self.row(author_id="same"), self.row(author_id="same"), self.row()]); self.approve_all()
        series = self.session.scalars(select(Series).order_by(Series.id)).all()
        with self.assertRaises(ValueError):
            merge_series(self.session, series[0].id, series[2].id, "测试", "https://www.douyin.com/")
        merge_series(self.session, series[0].id, series[1].id, "测试同剧", "https://www.douyin.com/")
        self.assertEqual(series[1].review_status, "pending")

    def test_official_ranks_preserved_and_no_rank_four_filling(self):
        self.run.mode = "official"
        self.run.official_metadata = json.dumps({"verified": True, "publisher": "测试", "category": "AI", "period_start": "2026-10-23T04:00:00Z", "period_end": "2026-10-30T04:00:00Z", "updated_at": "2026-10-30T04:00:00Z", "metric": "官方热度", "source_url": "https://www.douyin.com/", "evidence_note": "合成测试"})
        self.import_rows([self.row(likes="1", official_rank="1"), self.row(likes="99999", official_rank="4")]); self.approve_all()
        result = self.ranking()
        self.assertEqual([item["position"] for item in result["items"]], [1, 4])
        self.assertEqual(sum(item["selected"] for item in result["items"]), 1)
        self.assertEqual(result["scoring_mode"], "official")

    def test_unverified_official_source_blocked(self):
        self.run.mode = "official"
        with self.assertRaises(ValueError):
            self.ranking()

    def test_export_contains_report_csv_evidence_and_handoff(self):
        self.import_rows([self.row(series_title="<script>alert(1)</script>")]); self.approve_all()
        ranking = self.ranking()
        with zipfile.ZipFile(io.BytesIO(export_zip(ranking))) as archive:
            self.assertIn("01_榜单报告.html", archive.namelist())
            self.assertIn("第二步入选名单.json", archive.namelist())
            report = archive.read("01_榜单报告.html").decode()
            self.assertNotIn("<script>alert(1)</script>", report)
            self.assertIn("&lt;script&gt;", report)
            handoff = json.loads(archive.read("第二步入选名单.json"))
            self.assertEqual(handoff["ranking_id"], ranking["id"])
            self.assertEqual(len(handoff["items"]), 1)

    def test_beijing_to_utc_and_offsets(self):
        self.assertEqual(parse_time("2026-10-30T12:00:00"), CUTOFF)
        self.assertEqual(parse_time("2026-10-30T12:00:00+08:00"), CUTOFF)

    def test_multi_episode_compilation_excluded_and_single_short_story_allowed(self):
        self.import_rows([self.row(title="自制短剧1-6集纯享版", likes="99999"),
                          self.row(title="独立三分钟故事", likes="50")])
        self.approve_all()
        ranking = self.ranking()
        self.assertEqual(len(ranking["items"]), 1)
        self.assertEqual(ranking["items"][0]["title"], "独立三分钟故事")
        self.assertTrue(any("多集合集" in reason for reason in ranking["candidates"][0]["exclusion_reasons"]))
        self.assertEqual(ranking["review_scope"]["compilations"], 1)

    def test_remaining_review_can_affect_top3_and_unknown_metrics_are_disclosed(self):
        self.import_rows([self.row(likes=str(value)) for value in [100, 80, 60]])
        self.approve_all()
        self.import_rows([self.row(likes="150"), self.row(likes="1", shares="")])
        scope = self.ranking()["review_scope"]
        self.assertEqual(scope["affecting_count"], 1)
        self.assertEqual(scope["unknown_count"], 1)
        self.assertEqual(scope["pending"], 2)

    def test_metadata_correction_audited_and_previous_snapshot_unchanged(self):
        from services.metadata import update_metadata
        self.import_rows([self.row()]); self.approve_all()
        before = self.ranking()
        row = before["items"][0]
        update_metadata(self.session, "series", row["series_id"], {
            "title": "整理后的剧名", "title_note": "据视频文案整理", "ai_type": "AI漫剧", "ai_basis": "合成测试声明",
            "reason": "合成测试资料更正", "evidence_url": row["original_url"]})
        update_metadata(self.session, "episode", row["episode_id"], {
            "episode_number": 2, "content_kind": "single", "content_note": "标题明确第二集",
            "reason": "合成测试集号更正", "evidence_url": row["original_url"]})
        after = self.ranking()
        self.assertEqual(after["items"][0]["series_title"], "整理后的剧名")
        self.assertEqual(after["items"][0]["episode_number"], 2)
        self.assertEqual(after["items"][0]["series_status"], "approved")
        self.assertEqual(after["items"][0]["series_reviews"][-1]["status"], "metadata_updated")
        saved = json.loads(self.session.get(RankingRun, before["id"]).snapshot)
        self.assertEqual(saved["items"][0]["series_title"], row["series_title"])

    def test_conflicting_review_text_cannot_be_approved(self):
        self.import_rows([self.row()])
        series = self.session.scalar(select(Series))
        with self.assertRaisesRegex(ValueError, "矛盾"):
            review(self.session, {"target_type": "series", "target_id": series.id, "status": "approved",
                                 "reason": "测试", "evidence_url": "https://www.douyin.com/", "ai_type": "AI漫剧",
                                 "ai_basis": "没有剧情，不是短剧", "checks": {"story": True,"ai": True,"original_author": True}})


class MigrationTests(unittest.TestCase):
    def test_migration_backup_copy_validation_and_legacy_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            engine = create_engine(f"sqlite:///{path.as_posix()}")
            Drama.__table__.create(engine)
            with Session(engine) as session:
                session.add(Drama(title="原始剧集", cover_url="cover", description="原始简介", official_url="url",
                                  ai_summary="summary", ai_score=9.9, tags="[]"))
                session.commit()
            upgrade_database(engine)
            with Session(engine) as session:
                self.assertEqual(session.scalar(select(Drama)).title, "原始剧集")
                self.assertEqual(session.scalar(select(func.count()).select_from(Series)), 0)
            upgrade_database(engine)
            self.assertEqual(len(list((path.parent / "data/backups").glob("*.db"))), 1)
            with closing(sqlite3.connect(str(path))) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            engine.dispose()


class APITests(unittest.TestCase):
    def test_workflow_and_export_http(self):
        import main
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        with factory() as session:
            session.add(Drama(title="合成首页隔离测试资料", cover_url="https://example.com/cover.jpg",
                              description="合成测试简介", official_url="https://example.com/drama",
                              ai_summary="合成测试摘要", ai_score=8.0, tags="[]"))
            session.commit()
        def isolated():
            with Session(engine, autoflush=False) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        main.app.dependency_overrides[main.database_session] = isolated
        try:
            with patch.object(main, "init_db"), patch.object(main, "recover_jobs"), \
                 patch.object(main, "SessionLocal", factory), TestClient(main.app) as client:
                for path in ("/", "/legacy"):
                    page = client.get(path)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("合成首页隔离测试资料", page.text)
                self.assertEqual(client.get("/static/app.js").status_code, 200)
                self.assertEqual(client.get("/api/import-template.csv").status_code, 200)
                run = client.post("/api/runs", json={"name": "合成 API 测试", "cutoff_at": "2026-10-30T12:00:00+08:00"})
                self.assertEqual(run.status_code, 201, run.text)
                run_id = run.json()["id"]
                bad = client.post(f"/api/runs/{run_id}/import/links", json={"links": "javascript:alert(1)"})
                self.assertEqual(bad.json()["imported"], 0)
                link = client.post(f"/api/runs/{run_id}/import/links", json={"links": "https://www.douyin.com/video/12345"})
                self.assertEqual(link.json()["imported"], 1)
                ranking = client.post(f"/api/runs/{run_id}/rankings", json={})
                self.assertEqual(ranking.status_code, 201, ranking.text)
                rank_id = ranking.json()["id"]
                self.assertEqual(client.get(f"/rankings/{rank_id}/report").status_code, 200)
                exported = client.get(f"/api/rankings/{rank_id}/export")
                self.assertEqual(exported.status_code, 200)
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(exported.content)))
                self.assertEqual(client.get("/api/runs/999/candidates").status_code, 404)
        finally:
            main.app.dependency_overrides.clear()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
