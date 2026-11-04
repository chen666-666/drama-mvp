"""Cross-version reuse on synthetic media; no real downloads or business database."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import main
from models import DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile, RankingRun
from services import downloads
from services.media import digest
from services.package import build_package
from services.publishing import build_bundle, verify_bundle
from services.site import daily_rankings, library_detail
from tests.auth_support import AdminClient
from tests import test_publishing


class ResourceReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_publishing.PublishingTests()
        self.fixture.setUp()
        f = self.fixture
        self.session = f.session
        self.original_snapshot = self.session.get(RankingRun, f.ranking['id']).snapshot
        self.latest = f.fixture.ranking()
        self.catalog = self.session.scalar(select(DownloadCatalog).where(
            DownloadCatalog.ranking_id == self.latest['id'], DownloadCatalog.series_id == f.catalog.series_id))
        self.session.commit()

    def tearDown(self):
        self.fixture.tearDown()

    def detail(self):
        return downloads.catalog_detail(self.session, self.catalog)

    def public(self):
        return library_detail(self.session, self.latest['id'], self.latest)['catalogs'][0]['episodes']

    def test_generation_automatically_reuses_one_file_review_and_job(self):
        self.assertIsNotNone(self.catalog)
        row = self.detail()['episodes'][0]
        self.assertTrue(row['reused'])
        self.assertEqual(row['media']['id'], self.fixture.media.id)
        self.assertEqual(self.detail()['summary']['reused'], 1)
        self.assertTrue(self.detail()['summary']['trial_passed'])
        self.assertEqual(len(self.public()), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(DownloadJob)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MediaFile)), 1)
        self.assertEqual(self.session.get(RankingRun, self.fixture.ranking['id']).snapshot, self.original_snapshot)
        result = downloads.download_one(self.session, self.catalog)
        self.assertEqual(result['media_id'], self.fixture.media.id)
        self.assertEqual(downloads.enqueue(self.session, self.catalog, [row['id']])['queued'], 0)

    def test_new_episode_is_the_only_new_job_and_new_representative_does_not_hide_old_video(self):
        f = self.fixture.fixture
        item = self.latest['items'][0]
        f.import_rows([f.row(author_id=item['author_account_id'], series_key='series-1', likes='10000')])
        f.approve_all()
        latest = f.ranking()
        catalog = downloads.catalog_for(self.session, latest['id'], self.catalog.series_id)
        detail = downloads.catalog_detail(self.session, catalog)
        self.assertEqual(detail['summary']['reused'], 1)
        new = next(row for row in detail['episodes'] if not row['reused'])
        self.assertEqual(downloads.enqueue(self.session, catalog, [row['id'] for row in detail['episodes']])['queued'], 1)
        self.assertIsNotNone(self.session.scalar(select(DownloadJob).where(DownloadJob.episode_id == new['id'])))
        self.assertEqual(len(library_detail(self.session, latest['id'], latest)['catalogs'][0]['episodes']), 1)

    def test_revocation_file_loss_tamper_and_replacement_stop_reuse(self):
        media = self.fixture.media
        media.manual_status = 'rejected'
        self.assertEqual(self.public(), [])
        self.assertEqual(self.detail()['summary']['reused'], 0)
        media.manual_status = 'passed'
        original = self.fixture.video.read_bytes()
        self.fixture.video.unlink()
        self.assertEqual(self.public(), [])
        self.fixture.video.write_bytes(original[::-1])
        self.assertEqual(self.public(), [])
        media.sha256 = digest(self.fixture.video)
        self.assertEqual(self.public(), [])  # New bytes need a fresh explicit reference, never inherit old approval.

    def test_source_directory_revocation_and_target_exclusion_are_respected(self):
        original = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.fixture.catalog.id))
        original.access = 'private'
        self.assertEqual(self.public(), [])
        original.access = 'public'
        target = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        target.status = 'excluded'
        self.assertEqual(self.public(), [])
        downloads.catalog_for(self.session, self.latest['id'], self.catalog.series_id)
        self.assertEqual(target.status, 'excluded')

    def test_existing_unfinished_duplicate_job_is_retained_and_cancelled_while_old_media_is_used(self):
        target = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        target.reused_media_id, target.reused_sha256 = None, ''
        target.review_history = json.dumps([{'status': 'approved', 'note': '原有目录确认'}])
        duplicate = DownloadJob(episode_id=target.id, status='queued', trial=True)
        self.session.add(duplicate)
        self.session.flush()
        downloads.inherit_media(self.session, self.catalog)
        self.assertEqual(duplicate.status, 'cancelled')
        self.assertTrue(self.detail()['episodes'][0]['reused'])
        self.assertEqual(self.detail()['episodes'][0]['job']['id'], self.fixture.media.job_id)
        self.assertEqual(json.loads(target.review_history)[0]['note'], '原有目录确认')
        with self.assertRaisesRegex(ValueError, '无需重复'):
            downloads.control_job(self.session, duplicate, 'resume')

    def test_active_duplicate_and_explicit_rejected_media_are_never_overwritten(self):
        target = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        target.reused_media_id, target.reused_sha256, target.review_history = None, '', '[]'
        duplicate = DownloadJob(episode_id=target.id, status='downloading', trial=True)
        self.session.add(duplicate)
        self.session.flush()
        downloads.inherit_media(self.session, self.catalog)
        self.assertIsNone(target.reused_media_id)
        self.assertEqual(duplicate.status, 'downloading')
        duplicate.status = 'verified'
        media = self.fixture.media
        self.session.add(MediaFile(job_id=duplicate.id, relative_path=media.relative_path, size=media.size,
            duration=media.duration, format=media.format, sha256=media.sha256, tracks='[]', validation_note='合成校验', manual_status='rejected'))
        self.session.flush()
        downloads.inherit_media(self.session, self.catalog)
        self.assertIsNone(target.reused_media_id)

    def test_same_title_different_author_does_not_reuse_and_old_rankings_do_not_gain_future_media(self):
        other = self.latest['items'][1]['series_id']
        self.assertIsNone(self.session.scalar(select(DownloadCatalog).where(
            DownloadCatalog.ranking_id == self.latest['id'], DownloadCatalog.series_id == other)))
        self.assertEqual(downloads.reusable_sources(self.session, self.fixture.ranking['id'], self.catalog.series_id), [])

    def test_already_existing_latest_ranking_backfill_is_idempotent_and_reads_are_clean(self):
        # Simulate a new version that existed before this feature, without deleting business records.
        row = self.session.scalar(select(DownloadEpisode).where(DownloadEpisode.catalog_id == self.catalog.id))
        row.reused_media_id, row.reused_sha256, row.review_history = None, '', '[]'
        self.session.commit()
        downloads.backfill_reused_media(self.session)
        self.session.commit()
        counts = [self.session.scalar(select(func.count()).select_from(model)) for model in (DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile)]
        downloads.backfill_reused_media(self.session)
        self.session.commit()
        self.assertEqual(counts, [self.session.scalar(select(func.count()).select_from(model)) for model in (DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile)])
        self.public()
        self.assertFalse(self.session.new)
        self.assertFalse(self.session.dirty)

    def test_daily_latest_across_runs_beijing_midnight_and_generation_ties(self):
        def rank(identifier, cutoff, created, version):
            return RankingRun(id=identifier, run_id=identifier, version=version, created_at=created,
                snapshot=json.dumps({'cutoff_at': cutoff}))
        stamp = datetime(2026, 11, 2, 1)
        a = rank(1, '2026-11-01T15:59:59+00:00', stamp, 99)
        b = rank(2, '2026-11-01T15:59:59+00:00', stamp + timedelta(seconds=1), 1)
        c = rank(3, '2026-11-01T16:00:00+00:00', stamp, 1)
        d = rank(4, '2026-11-01T16:00:00+00:00', stamp, 1)
        self.assertEqual([r.id for r in daily_rankings([a, d, c, b])], [4, 2])

    def test_home_daily_options_public_and_admin_playback_download_and_no_repeated_todo(self):
        f = self.fixture
        factory = sessionmaker(bind=f.fixture.engine, autoflush=False)
        self.session.commit()
        with patch.object(main, 'SessionLocal', factory), patch.object(main, 'init_db'), patch.object(main, 'recover_jobs'):
            def dependency():
                yield self.session
            main.app.dependency_overrides[main.database_session] = dependency
            try:
                with AdminClient(main.app) as client:
                    html = client.get('/').text
                    options = re.search(r'<select id="library-ranking">(.*?)</select>', html, re.S)[1]
                    self.assertEqual(options.count('<option '), 1)
                    self.assertIn(f'value="{self.latest["id"]}"', options)
                    result = client.get(f'/api/playback/rankings/{self.latest["id"]}/series/{self.catalog.series_id}').json()
                    self.assertEqual(result['episodes'][0]['id'], self.detail()['episodes'][0]['id'])
                    self.assertTrue(result['episodes'][0]['file_url'])
                    todo = client.get('/api/control/overview').json()
                    self.assertTrue(all(g['count'] == 0 for g in todo['groups']))
                    client.cookies.clear()
                    self.assertEqual(len(client.get('/api/library/rankings').json()), 1)
                    self.assertEqual(len(client.get('/api/downloads/rankings').json()), 2)
                    response = client.get(self.public()[0]['download_url'], headers={'Range': 'bytes=0-19'})
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.content, f.video.read_bytes()[:20])
                    self.assertIn('attachment', response.headers['content-disposition'])
            finally:
                main.app.dependency_overrides.clear()

    def test_public_bundle_carries_reference_and_original_review_without_duplicate_video(self):
        f = self.fixture
        f.save_source()
        result = build_bundle(f.source, f.data, f.root / 'reuse-publish', [self.latest['id']], preview=False, release_id='reuse-test')
        directory = f.root / 'reuse-publish/reuse-test'
        verify_bundle(directory)
        self.assertEqual(result['video_count'], 1)
        with closing(sqlite3.connect(directory / 'public.db')) as db:
            self.assertEqual(db.execute('SELECT reused_media_id FROM download_episodes WHERE catalog_id=?', (self.catalog.id,)).fetchone()[0], f.media.id)
            self.assertEqual(db.execute('SELECT count(*) FROM download_jobs').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_package_contains_reused_bytes_and_source_audit(self):
        import zipfile
        f = self.fixture
        with patch('services.package.DATA', f.data):
            export = build_package(self.session, self.session.get(RankingRun, self.latest['id']))
        with zipfile.ZipFile(f.data / export.relative_path) as archive:
            videos = [n for n in archive.namelist() if n.endswith('.mp4')]
            self.assertEqual(len(videos), 1)
            self.assertEqual(archive.read(videos[0]), f.video.read_bytes())
            audit = next(n for n in archive.namelist() if n.endswith('第二步目录与核验.json'))
            self.assertIn('source_media_id', archive.read(audit).decode())

    def test_existing_populated_006_database_migrates_without_changing_business_fields(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine
        from services.migration import ROOT, upgrade_database
        path = self.fixture.root / 'before-007.db'
        engine = create_engine(f'sqlite:///{path.as_posix()}')
        config = Config(str(ROOT/'alembic.ini'))
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '006_review_workflow')
        self.session.commit()
        with closing(sqlite3.connect(path)) as target, self.fixture.fixture.engine.connect() as source:
            raw = source.connection.driver_connection
            for (table,) in target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'").fetchall():
                columns = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
                names = ','.join(f'"{column}"' for column in columns)
                rows = raw.execute(f'SELECT {names} FROM "{table}"').fetchall()
                if rows:
                    target.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})', rows)
            target.commit()
            before = target.execute('SELECT id,snapshot FROM ranking_runs ORDER BY id').fetchall()
        try:
            upgrade_database(engine)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT id,snapshot FROM ranking_runs ORDER BY id').fetchall(), before)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT count(*) FROM media_files').fetchone()[0], 1)
                self.assertTrue(all(value is None for (value,) in db.execute('SELECT reused_media_id FROM download_episodes')))
        finally:
            engine.dispose()
