"""Continuous human review uses synthetic bytes, isolated storage and actual FFmpeg clips."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import main
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from models import DownloadEpisode, DownloadJob, MediaFile
from services import quick_reviews as quick
from services.media import executable, validate
from tests import test_collections as fixtures
from tests.auth_support import AdminClient


class QuickReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.CollectionTests.setUp(self)
        self.patches.extend([patch.object(quick, 'SessionLocal', sessionmaker(bind=self.f.engine)), patch.object(quick, 'dispatch')])
        for item in self.patches[-2:]:
            item.start()

    tearDown = fixtures.CollectionTests.tearDown

    def media_rows(self, count=2):
        seed=self.session.scalar(select(DownloadEpisode))
        result=[]
        for index in range(count):
            episode=seed if index==0 else DownloadEpisode(catalog_id=self.catalog.id, video_id=str(800+index),
                title=f'合成第{index+1}集', episode_number=index+1, original_url=f'https://www.douyin.com/video/{800+index}', status='approved', access='public')
            self.session.add(episode)
            self.session.flush()
            job=DownloadJob(episode_id=episode.id,status='verified',trial=index==0,attempts=1)
            self.session.add(job)
            self.session.flush()
            path=self.data/f'video-{index}.mp4';path.write_bytes(bytes([index])*2048)
            from services.media import digest
            media=MediaFile(job_id=job.id,relative_path=path.name,size=2048,duration=1,format='mp4',sha256=digest(path),tracks='["video","audio"]',validation_note='合成校验')
            self.session.add(media);self.session.flush();result.append(media)
        self.session.commit()
        return result

    def ready(self, count=2):
        media=self.media_rows(count)
        batch=quick.create(self.session,self.catalog)
        data=quick.read(batch['id']);data['status']='ready'
        for row in data['rows']:
            row.update(state='ready',segments=[{'index':i,'label':label,'start':0,'duration':1,'size':1024,'sha256':'test','url':'/private'} for i,label in enumerate(['开头','中段','结尾'])],watched=[0,1,2])
        quick.write(data)
        return batch['id'],media

    def decision(self, media, **changes):
        return {'decisions':[{'media_id':row.id,'status':'passed','reason':''} for row in media], 'note':'实际抽看三个片段并确认声音画面', 'confirmed':True,'sound':True,**changes}

    def test_create_is_bounded_reusable_pending_only_and_private(self):
        self.media_rows(12)
        batch=quick.create(self.session,self.catalog)
        self.assertEqual(len(batch['rows']),10)
        self.assertEqual(quick.create(self.session,self.catalog)['id'],batch['id'])
        self.assertNotIn('stamp',json.dumps(batch))
        self.assertNotIn('relative_path',json.dumps(batch))
        self.assertTrue(all(row.manual_status=='pending' for row in self.session.scalars(select(MediaFile))))
        self.assertFalse(self.catalog.published)

    def test_atomic_submit_explicit_checks_preserves_unsubmitted_and_retries(self):
        bid,media=self.ready(3)
        for key in ['confirmed','sound']:
            with self.assertRaises(ValueError):quick.submit(self.session,bid,self.decision(media,**{key:False}))
        data=quick.read(bid);next(row for row in data['rows'] if row['media_id']==media[1].id)['watched']=[0,1];quick.write(data)
        with self.assertRaisesRegex(ValueError,'三个片段'):quick.submit(self.session,bid,self.decision(media))
        self.assertTrue(all(row.manual_status=='pending' for row in media))
        result=quick.submit(self.session,bid,self.decision([media[0]]))
        self.assertEqual(result['passed'],1)
        self.assertEqual(media[0].manual_status,'passed')
        self.assertTrue(all(row.manual_status=='pending' for row in media[1:]))
        self.assertIn('连续快审',media[0].manual_note)
        self.assertEqual(quick.submit(self.session,bid,self.decision([media[0]]))['passed'],1)

    def test_changed_digest_review_or_scope_blocks_stale_batch(self):
        bid,media=self.ready()
        changed=self.data/media[1].relative_path
        changed.write_bytes(b'x'*2048)
        with self.assertRaisesRegex(ValueError,'摘要'):quick.submit(self.session,bid,self.decision(media))
        self.assertTrue(all(row.manual_status=='pending' for row in media))
        changed.write_bytes(bytes([1])*2048)
        media[1].manual_note='另一次抽检已更正';self.session.commit()
        with self.assertRaisesRegex(ValueError,'变化'):quick.submit(self.session,bid,self.decision(media))
        self.assertEqual(media[0].manual_status,'pending')

    def test_reject_requires_reason_and_cannot_approve_foreign_or_duplicate(self):
        bid,media=self.ready()
        for decisions in [[{'media_id':99999,'status':'passed'}], [{'media_id':media[0].id,'status':'passed'}]*2, [{'media_id':media[0].id,'status':'rejected','reason':''}]]:
            with self.assertRaises(ValueError):quick.submit(self.session,bid,self.decision(media,decisions=decisions))
        result=quick.submit(self.session,bid,self.decision(media,decisions=[{'media_id':media[0].id,'status':'rejected','reason':'开头声音异常'}],sound=False,confirmed=False))
        self.assertEqual(result['rejected'],1)
        self.assertEqual(media[0].manual_status,'rejected')
        self.assertEqual(media[1].manual_status,'pending')

    def test_watched_requires_served_time_and_restart_or_expiry_safe(self):
        bid,media=self.ready()
        data=quick.read(bid);row=next(row for row in data['rows'] if row['media_id']==media[0].id);row['watched']=[];quick.write(data)
        with self.assertRaisesRegex(ValueError,'播放完'):quick.watched(self.session,bid,media[0].id,0)
        row['served']={'0':0};quick.write(data)
        result=quick.watched(self.session,bid,media[0].id,0)
        self.assertEqual(next(item for item in result['rows'] if item['media_id']==media[0].id)['watched'],[0])
        self.assertEqual(quick.create(self.session,self.catalog)['id'],bid)
        data['status']='preparing';data['process']='old-process';quick.write(data)
        self.assertEqual(quick.status(bid)['status'],'interrupted')
        data['created']=0;quick.write(data)
        with self.assertRaisesRegex(ValueError,'过期'):quick.read(bid)
        with self.assertRaises(ValueError):quick.read('../secret')
        import os
        cache=self.data/f'private/quick-review-clips/{bid}'
        cache.mkdir(parents=True);(cache/'expired.mp4').write_bytes(b'old')
        os.utime(quick.folder()/(bid+'.json'),(0,0));quick.cleanup()
        self.assertFalse(cache.exists())

    def test_actual_ffmpeg_decode_three_clips_and_mutation_denied(self):
        if not executable('ffmpeg') or not executable('ffprobe'):self.skipTest('media tools unavailable')
        media=self.media_rows(1)[0];path=self.data/media.relative_path
        subprocess.run([executable('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:d=1',
            '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],check=True)
        info=validate(path)
        media.sha256,media.size,media.duration=info['sha256'],info['size'],info['duration'];self.session.commit()
        batch=quick.create(self.session,self.catalog);quick.prepare(batch['id'])
        detail=quick.status(batch['id']);row=detail['rows'][0]
        self.assertEqual(row['state'],'ready',row['message']);self.assertEqual(len(row['segments']),3)
        for index in range(3):
            clip=quick.clip(self.session,batch['id'],media.id,index)
            self.assertTrue(clip.is_file());self.assertIn('audio',validate(clip)['tracks'])
        clip.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'片段已变化'):quick.clip(self.session,batch['id'],media.id,2)
        self.session.expire_all();self.assertEqual(self.session.get(MediaFile,media.id).manual_status,'pending')

    def test_private_api_csrf_and_payload_validation(self):
        media=self.media_rows(1)
        path=f'/api/downloads/catalogs/{self.catalog.id}/quick-reviews'
        with AdminClient(main.app) as client:
            self.assertEqual(client.post(path,json={'limit':11}).status_code,422)
            self.assertEqual(client.post(path,json={},headers={'Origin':'https://evil.example'}).status_code,403)
            response=client.post(path,json={});self.assertEqual(response.status_code,200,response.text)
            bid=response.json()['id']
            self.assertEqual(client.post(f'/api/downloads/quick-reviews/{bid}/submit',json=self.decision(media)).status_code,400)
            client.cookies.clear()
            for url in [f'/api/downloads/quick-reviews/{bid}',f'/api/downloads/quick-reviews/{bid}/clips/{media[0].id}/0']:
                self.assertEqual(client.get(url).status_code,401)

    def test_lost_completion_file_retries_saved_operation_and_changed_content_rejected(self):
        bid, media = self.ready(1)
        data = self.decision(media)
        original = quick.write
        def fail_completion(batch):
            if batch['status'] == 'submitted':
                raise OSError('synthetic lost completion')
            original(batch)
        with patch.object(quick, 'write', side_effect=fail_completion):
            with self.assertRaises(OSError): quick.submit(self.session, bid, data)
        self.assertEqual(media[0].manual_status, 'passed')
        with self.assertRaisesRegex(ValueError, '相同内容'): quick.submit(self.session, bid, {**data, 'note':'changed'})
        self.assertEqual(quick.submit(self.session, bid, data)['passed'], 1)
        with self.assertRaisesRegex(ValueError, '不同'): quick.submit(self.session,bid,{**data,'note':'changed'})
