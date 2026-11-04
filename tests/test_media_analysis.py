"""Synthetic signals calibrate technical behavior, not real content or model accuracy."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from services import media_analysis as analysis, quick_reviews as quick
from services.media import executable, validate
from tests import test_quick_reviews as fixtures


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.q = fixtures.QuickReviewTests(); self.q.setUp()
        self.session, self.data = self.q.session, self.q.data

    def tearDown(self):
        self.q.tearDown()

    def signal(self, anomaly=False):
        media = self.q.media_rows(1)[0]
        path = self.data/media.relative_path
        subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'color=c=black:s=160x90:d=4' if anomaly else 'testsrc2=s=160x90:d=4',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono' if anomaly else 'sine=frequency=440:duration=4',
            '-t', '4', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(path)], check=True)
        info = validate(path)
        media.sha256, media.size, media.duration = info['sha256'], info['size'], info['duration']
        self.session.commit()
        return media, path

    def test_actual_black_silence_freeze_cache_and_no_human_change(self):
        media, path = self.signal(True)
        result = analysis.analyze(path, scan=True)
        self.assertEqual({r['kind'] for r in result['findings']}, {'黑屏','静音','冻结'})
        self.assertTrue(all(r['end'] > r['start'] for r in result['findings']))
        self.assertEqual(result['config']['parameters']['black_seconds'], 2)
        self.assertTrue(analysis.analyze(path, scan=True)['cached'])
        self.assertEqual(media.manual_status, 'pending')
        self.assertFalse(self.q.catalog.published)

    def test_normal_signal_is_not_content_approval_and_tamper_not_cached(self):
        media, path = self.signal()
        result = analysis.analyze(path, scan=True)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['message'], '未发现所配置的异常')
        self.assertEqual(media.manual_status, 'pending')
        path.write_bytes(b'changed'*1024)
        with self.assertRaises(ValueError): analysis.analyze(path, scan=True)

    def test_timeout_disk_and_interrupted_jobs_leave_human_pending(self):
        media, path = self.signal()
        with patch.object(analysis, 'identity', return_value='synthetic-timeout-v1'), patch.object(analysis, 'validate', return_value=validate(path)), patch.object(analysis.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 300)):
            with self.assertRaisesRegex(ValueError, '五分钟'): analysis.analyze(path)
        with patch.object(analysis, 'identity', return_value='synthetic-disk-v1'), patch.object(analysis.shutil, 'disk_usage', return_value=type('Usage',(),{'free':0})()):
            with self.assertRaisesRegex(ValueError, '空间'): analysis.analyze(path)
        analysis.write_job(media.id, {'sha256':media.sha256, 'size':media.size, 'status':'running', 'process':'old', 'message':'old'})
        self.assertEqual(analysis.status(self.session,media)['status'], 'interrupted')
        self.assertEqual(media.manual_status, 'pending')

    def test_public_preview_matches_manual_publish_switch_and_rechecks_eligibility(self):
        bid, media = self.q.ready(2)
        decisions = self.q.decision(media)['decisions']
        preview = quick.preview(self.session, bid, decisions)
        self.assertEqual(preview['counts']['public'], 0)
        self.assertIn('仍不公开', preview['message'])
        self.q.catalog.published = True; self.session.commit()
        with self.assertRaisesRegex(ValueError, '资格'): quick.preview(self.session,bid,decisions)
        with self.assertRaisesRegex(ValueError, '资格'): quick.submit(self.session,bid,self.q.decision(media))
        self.assertTrue(all(m.manual_status=='pending' for m in media))

    def test_next_batch_bounded_excludes_current_and_pause_stops_at_boundary(self):
        self.q.media_rows(24)
        first = quick.create(self.session,self.q.catalog)
        data = quick.read(first['id']); data['status']='ready'; quick.write(data)
        nxt = quick.prefetch(self.session, first['id'])
        self.assertEqual(len(nxt['rows']),10)
        self.assertFalse(set(r['media_id'] for r in first['rows']) & set(r['media_id'] for r in nxt['rows']))
        self.assertEqual(quick.prefetch(self.session, first['id'])['id'], nxt['id'])
        quick.pause(first['id']); quick.prepare(nxt['id'])
        self.assertEqual(quick.status(nxt['id'])['status'], 'interrupted')
