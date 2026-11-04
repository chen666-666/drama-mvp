"""Exercise actual bridge handlers without external requests or tool credentials."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sources.douk import DouKSource, SourceUnavailable


class DetailFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('scripts/run_douk.py').read_text(encoding='utf-8'))
        cls.helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'detail_error')
        main = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'main')
        cls.handlers = {n.name: n for n in ast.walk(main) if isinstance(n, ast.AsyncFunctionDef) and n.name in ('detail', 'media')}

    def invoke(self, route, data, source_status='ok', restriction=''):
        class Operation:
            def __init__(self, *args, **kwargs):
                self.detail_filter = restriction

            async def run(self):
                return data

        namespace = {'PublicDetail': Operation, 'tool': SimpleNamespace(parameter=None),
                     'MediaRequest': object, 'response_status': lambda op, value: source_status,
                     'public_video': lambda value: value, 'TOOL': Path('data/tools/douk')}
        handler = self.handlers[route]
        handler.decorator_list = []
        exec(compile(ast.Module(body=[self.helper, handler], type_ignores=[]), '<bridge-handler>', 'exec'), namespace)
        return asyncio.run(namespace[route](SimpleNamespace(video_id='30001', cookie='synthetic')))

    def test_empty_details_never_report_success(self):
        for route in ('detail', 'media'):
            for value in (None, [], {}, ''):
                with self.subTest(route=route, value=value):
                    self.assertEqual(self.invoke(route, value), {'status': 'failed', 'reason': 'empty_detail'})

    def test_upstream_audit_filter_is_reported_without_raw_payload(self):
        for route in ('detail', 'media'):
            self.assertEqual(self.invoke(route, [], restriction='status_audit_not_pass'),
                             {'status': 'unavailable', 'reason': 'audit_not_pass'})

    def test_author_only_audit_filter_is_not_a_retryable_empty_response(self):
        for route in ('detail', 'media'):
            self.assertEqual(self.invoke(route, [], restriction='status_audit_self_see'),
                             {'status': 'unavailable', 'reason': 'self_only'})

    def test_login_and_access_blocks_are_not_overridden(self):
        for status in ('blocked', 'needs_login'):
            for route in ('detail', 'media'):
                self.assertEqual(self.invoke(route, [], status, 'status_audit_not_pass'), {'status': status})

    def test_identity_restrictions_and_valid_detail(self):
        self.assertEqual(self.invoke('detail', {'aweme_id': '30002'})['reason'], 'identity_mismatch')
        for field in ({'is_delete': True}, {'status': {'is_private': True}}, {'series_paid_info': {'item_price': 1}}):
            self.assertEqual(self.invoke('detail', {'aweme_id': '30001', **field})['reason'], 'restricted')
        self.assertEqual(self.invoke('detail', {'aweme_id': '30001', 'status': []})['status'], 'ok')
        self.assertEqual(self.invoke('detail', {'aweme_id': '30001', 'status': 'invalid'})['reason'], 'malformed_detail')
        self.assertEqual(self.invoke('detail', {'aweme_id': '30001'}), {'status': 'ok', 'video': {'aweme_id': '30001'}})

    def test_adapter_accepts_only_allowlisted_reason_codes(self):
        response = Mock()
        response.raise_for_status.return_value = None
        with patch('sources.douk._cookie', return_value='synthetic'), patch('sources.douk.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value = response
            response.json.return_value = {'status': 'unavailable', 'reason': 'audit_not_pass', 'message': 'cookie=private'}
            with self.assertRaises(SourceUnavailable) as caught:
                DouKSource().refresh_video('30001')
            self.assertEqual(caught.exception.reason_code, 'audit_not_pass')
            self.assertIn('审核未通过', str(caught.exception))
            self.assertNotIn('private', str(caught.exception))
            response.json.return_value = {'status': 'failed', 'reason': 'cookie=private'}
            with self.assertRaises(SourceUnavailable) as caught:
                DouKSource().refresh_video('30001')
            self.assertIsNone(caught.exception.reason_code)
            self.assertNotIn('private', str(caught.exception))
            response.json.return_value = {'status': 'ok'}
            with self.assertRaises(SourceUnavailable) as caught:
                DouKSource().refresh_video('30001')
            self.assertEqual(caught.exception.reason_code, 'empty_detail')


    def test_audit_filtered_detail_keeps_review_and_blocks_until_real_sample(self):
        from tests.test_candidate_pool import CandidatePoolTests, DetailSource, video
        from sqlalchemy import select, func
        from models import ReviewRecord, CollectionRun, SearchJob
        from services import search
        from services.ranking import make_ranking
        fixture=CandidatePoolTests();fixture.setUp()
        try:
            fixture.old_videos(('30001',));rid=fixture.new_run();jid=fixture.job(rid,True)
            with fixture.factory() as session:
                before=session.scalar(select(func.count(ReviewRecord.id)))
            source=DetailSource(details={'30001':SourceUnavailable('unavailable','untrusted cookie=private','audit_not_pass')})
            search.run_job(jid,fixture.factory,source,lambda _:None)
            row=fixture.rows(rid)[0]
            self.assertEqual(row['membership']['refresh_status'],'unavailable')
            self.assertIn('审核未通过',row['membership']['refresh_note'])
            self.assertNotIn('private',row['membership']['refresh_note'])
            self.assertEqual(row['episode_status'],'approved')
            self.assertIsNone(row['metric_snapshot_id'])
            with fixture.factory() as session:
                self.assertEqual(session.scalar(select(func.count(ReviewRecord.id))),before)
                self.assertTrue(make_ranking(session,session.get(CollectionRun,rid),persist=False)['generation_blockers'])
                search.prepare_resume(session,session.get(SearchJob,jid));session.commit()
            search.run_job(jid,fixture.factory,DetailSource(details={'30001':video()}),lambda _:None)
            with fixture.factory() as session:
                self.assertEqual(session.scalar(select(func.count(ReviewRecord.id))),before)
                self.assertFalse(make_ranking(session,session.get(CollectionRun,rid),persist=False)['generation_blockers'])
        finally:
            fixture.tearDown()

if __name__ == '__main__':
    unittest.main()
