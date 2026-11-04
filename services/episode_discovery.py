"""Durable, leased discovery. All mutations run in the caller's short write transaction."""
from datetime import timedelta
import hashlib
import json
import os
import re
from urllib.parse import urlparse

from sqlalchemy import select, update
from models import (Author, Series, Episode, RankingRun, DownloadCatalog, DownloadEpisode,
                    DiscoveryTask, DiscoverySource, DiscoveryVideo, DiscoveryObservation,
                    DiscoveryMatch, DiscoverySubscription)
from services.common import utcnow, iso
from services import downloads
from sources.episode_discovery import normalized, public_metadata, validate_page
from sources.douk import SourceUnavailable

STATES = {'queued':'等待领取', 'running':'正在读取', 'paused':'管理员暂停', 'needs_login':'需要登录',
          'blocked':'需要验证', 'budget_exhausted':'本轮预算已用完', 'failed':'读取故障',
          'interrupted':'进程中断', 'completed':'本轮计划读取结束'}
LIMITS = {'detail':2, 'mix':22, 'author':18, 'search':8}
LEASE_SECONDS = 90


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def enabled():
    return os.getenv('DRAMA_EPISODE_DISCOVERY', '0') == '1' and os.getenv('DRAMA_SITE_MODE') != 'public'


def context(session, series_id):
    series = session.get(Series, series_id)
    author = session.get(Author, series.author_id)
    values = [series.author_id, author.account_id, author.homepage_url, series.series_key,
              series.title, series.collection_url, series.review_status]
    return hashlib.sha256(encode(values).encode()).hexdigest()


def config_for(session, ranking_id, series_id, aliases=()):
    item = downloads.selected_item(session, ranking_id, series_id)
    series = session.get(Series, series_id)
    author = session.get(Author, series.author_id)
    if series.review_status != 'approved' or author.account_id != item['author_account_id']:
        raise ValueError('当前作品或作者资料已变化，请先核对归属')
    sec = urlparse(author.homepage_url or item.get('author_url') or '').path.rstrip('/').split('/')[-1]
    mix = re.search(r'/(?:collection|mix)/([0-9]+)', series.collection_url or item.get('collection_url') or '')
    names = list(dict.fromkeys([series.title, *aliases]))[:4]
    queries=[(name+' '+author.name)[:100] for name in names]
    catalogs=list(session.scalars(select(DownloadCatalog).where(DownloadCatalog.series_id==series_id,
        DownloadCatalog.checked==True,DownloadCatalog.total_episodes.is_not(None)).order_by(DownloadCatalog.id.desc())))
    if catalogs:
        trusted=catalogs[0]
        numbers=set(session.scalars(select(DownloadEpisode.episode_number).where(
            DownloadEpisode.catalog_id==trusted.id,DownloadEpisode.status=='approved')))
        for number in range(1,min(trusted.total_episodes,10000)+1):
            if number not in numbers:
                queries.append((series.title+' '+author.name+f' 第{number}集')[:100])
                if len(queries)>=4:break
    return {'context':context(session,series_id), 'account_id':author.account_id,
            'sec_hint':sec, 'mix_hint':mix[1] if mix else '', 'seed':item['video_id'],
            'names':names, 'queries':queries[:4], 'author_name':author.name, 'identity':{}}


def source_for(session, task, kind, mode, object_key):
    row = session.scalar(select(DiscoverySource).where(DiscoverySource.task_id==task.id,
        DiscoverySource.kind==kind, DiscoverySource.mode==mode, DiscoverySource.object_key==object_key))
    if not row:
        row = DiscoverySource(task_id=task.id, kind=kind, mode=mode, object_key=object_key)
        session.add(row); session.flush()
    return row


def start_round(session, task, update_head=False):
    pending_update=json.loads(task.progress or '{}').get('pending_update',False) if not update_head else False
    task.progress = encode({'requests':0, 'counts':{}, 'pending_update':pending_update, 'last_source':0})
    task.started_at, task.status, task.owner, task.lease_until = None, 'queued', '', None
    task.message, task.updated_at = '等待补集工作进程；不自动下载', utcnow()
    sources=list(session.scalars(select(DiscoverySource).where(DiscoverySource.task_id==task.id)))
    for row in sources:
        data = json.loads(row.progress)
        data.update(attempts=0, requests=0)
        if row.state in ('failed','unavailable'):
            row.ended=False; row.state='pending'
        if update_head and row.kind in ('mix','author','search') and row.mode != 'history':
            row.cursor, row.ended, row.state = '0', False, 'pending'
            data.update(seen_cursors=[], boundary_pages=0, round_pages=0)
            data['baseline_ids']=sorted({vid for other in sources if other.kind==row.kind and
                other.object_key==row.object_key for vid in json.loads(other.progress).get('known_ids',[])})
        if update_head and row.kind=='detail' and row.state!='superseded':
            row.cursor,row.ended,row.state='0',False,'pending'
            data['seen_cursors']=[]
        row.progress = encode(data)


def register(session, ranking_id, series_id, aliases=()):
    if not enabled():
        raise ValueError('自动补集尚未启用；须先完成真实来源验证并开启功能开关')
    previous = session.scalar(select(DiscoverySubscription).where(
        DiscoverySubscription.ranking_id==ranking_id, DiscoverySubscription.series_id==series_id))
    if previous:
        return session.get(DiscoveryTask, previous.task_id)
    cfg = config_for(session, ranking_id, series_id, aliases)
    task = session.scalar(select(DiscoveryTask).where(DiscoveryTask.series_id==series_id))
    if not task:
        task = DiscoveryTask(series_id=series_id, config=encode(cfg))
        session.add(task); session.flush()
        start_round(session, task)
        source_for(session,task,'detail','identity',cfg['seed'])
    else:
        old = json.loads(task.config)
        if old['context'] != cfg['context']:
            task.status,task.owner,task.lease_until='failed','',None
            task.message='作品资料变化；新榜单已关联，自动同步等待管理员重新核对身份及来源'
        elif task.status in ('queued','running'):
            progress=json.loads(task.progress); progress['pending_update']=True; task.progress=encode(progress)
        elif task.status == 'completed':
            task.round += 1; start_round(session,task,True)
    catalog = downloads.catalog_for(session, ranking_id, series_id)
    session.add(DiscoverySubscription(task_id=task.id, ranking_id=ranking_id,
        series_id=series_id, catalog_id=catalog.id))
    session.flush()
    if json.loads(task.config)['context']==cfg['context']:
        sync_local(session,task)
        sync(session,task)
    return task


def register_ranking(session, ranking):
    if enabled():
        for item in json.loads(ranking.snapshot)['items']:
            if item['selected']:
                register(session,ranking.id,item['series_id'])


def sync_local(session, task):
    """First-step approved facts may inherit only under existing catalog initialization rules."""
    for sub in session.scalars(select(DiscoverySubscription).where(DiscoverySubscription.task_id==task.id,
                                                                DiscoverySubscription.active==True)):
        catalog = session.get(DownloadCatalog,sub.catalog_id)
        downloads.selected_item(session,sub.ranking_id,sub.series_id)
        known = {e.video_id for e in session.scalars(select(DownloadEpisode).where(DownloadEpisode.catalog_id==catalog.id))}
        # Explicit exclusions anywhere in this series remain protected during local reuse.
        excluded = set(session.scalars(select(DownloadEpisode.video_id).join(DownloadCatalog).where(
            DownloadCatalog.series_id==task.series_id, DownloadEpisode.status=='excluded')))
        for episode in session.scalars(select(Episode).where(Episode.series_id==task.series_id)):
            if episode.video_id in known or episode.video_id in excluded or episode.review_status=='excluded':
                continue
            approved=episode.review_status=='approved' and episode.content_kind!='compilation'
            session.add(DownloadEpisode(catalog_id=catalog.id,video_id=episode.video_id,title=episode.title,
                original_url=episode.original_url,episode_number=episode.episode_number, episode_range=episode.episode_range,
                status='approved' if approved else 'pending',access='public' if approved else 'unknown',
                evidence_url=episode.original_url if approved else '',note='已有第一步同剧资料补齐；未改排名或采样'))
            known.add(episode.video_id); catalog.checked=False; catalog.updated_at=utcnow()
        for episode in session.scalars(select(DownloadEpisode).join(DownloadCatalog).where(
                DownloadCatalog.series_id==task.series_id,DownloadCatalog.ranking_id<sub.ranking_id,
                DownloadEpisode.status!='excluded')):
            if episode.video_id in known or episode.video_id in excluded:
                continue
            session.add(DownloadEpisode(catalog_id=catalog.id,video_id=episode.video_id,title=episode.title,
                original_url=episode.original_url,episode_number=episode.episode_number, episode_range=episode.episode_range,
                note='来自较早同剧目录；本目录仍须独立确认公开正片'))
            known.add(episode.video_id);catalog.checked=False;catalog.updated_at=utcnow()
        session.flush()
        downloads.inherit_media(session,catalog)


def classify(meta, cfg):
    identity=cfg.get('identity',{})
    if not identity or not meta['uid'] or meta['uid']!=identity.get('uid'):
        return 'conflict','作者身份不一致或尚未确认'
    if meta['sec_uid'] and identity.get('sec_uid') and meta['sec_uid']!=identity['sec_uid']:
        return 'conflict','作者公开双标识冲突'
    if meta['content']!='candidate':
        return meta['content'],'宣传或多集内容，需单独辨认'
    if cfg.get('mix_id') and meta['mix_id']==cfg['mix_id']:
        return 'matched','同作者及代表视频详情确认的合集'
    words=[name for name in cfg['names'] if len(normalized(name))>=2 and normalized(name) in normalized(meta['title'])]
    if words:
        return 'matched','同作者且命中已确认剧名或人工别名：'+words[0]
    return 'clue','同作者，标题不足以证明同剧'


def sync(session, task):
    cfg=json.loads(task.config)
    if context(session,task.series_id)!=cfg['context']:
        raise ValueError('作品资料变化，已停止自动同步；请重新核对身份及合集')
    matches=session.execute(select(DiscoveryMatch,DiscoveryVideo).join(DiscoveryVideo).where(
        DiscoveryMatch.series_id==task.series_id,DiscoveryMatch.context==cfg['context'],
        DiscoveryMatch.kind=='matched')).all()
    for sub in session.scalars(select(DiscoverySubscription).where(DiscoverySubscription.task_id==task.id,
                                                                 DiscoverySubscription.active==True)):
        catalog=session.get(DownloadCatalog,sub.catalog_id)
        downloads.selected_item(session,sub.ranking_id,sub.series_id)
        known={e.video_id for e in session.scalars(select(DownloadEpisode).where(DownloadEpisode.catalog_id==catalog.id))}
        count=0
        for match,video in matches:
            if video.video_id in known:
                continue  # Human titles, access, exclusions and media rejections are never overwritten.
            meta=json.loads(video.metadata_json)
            if classify(meta,cfg)[0]!='matched':
                continue
            session.add(DownloadEpisode(catalog_id=catalog.id,video_id=video.video_id,title=meta['title'],
                original_url=meta['url'],episode_number=meta['number'],source_duration=meta.get('duration'),
                note='自动补集待核验：'+match.basis))
            known.add(video.video_id); count+=1
        if count:
            catalog.checked=False; catalog.updated_at=utcnow()
            sub.message=f'新增 {count} 条待核验；核验后须明确追加准备范围'
    session.flush()


def prepare_sources(session, task):
    cfg=json.loads(task.config); identity=cfg.get('identity',{})
    if not identity:
        return
    def activate(kind,mode,key):
        source=source_for(session,task,kind,mode,key)
        if source.state=='superseded':
            old=json.loads(source.progress)
            source.progress=encode({'previous_contexts':[*old.get('previous_contexts',[]),
                {'cursor':source.cursor,'pages':old.get('pages',0),'round':task.round-1}]})
            source.cursor,source.ended,source.state='0',False,'pending'
        return source
    if cfg.get('mix_id'):
        activate('mix','head',cfg['mix_id'])
        # Existing history of the same confirmed object can restart after a deliberate identity recheck.
        old_history=session.scalar(select(DiscoverySource).where(DiscoverySource.task_id==task.id,
            DiscoverySource.kind=='mix',DiscoverySource.mode=='history',DiscoverySource.object_key==cfg['mix_id']))
        if old_history and old_history.state=='superseded':activate('mix','history',cfg['mix_id'])
    if identity.get('sec_uid'):
        activate('author','head',identity['sec_uid'])
        activate('author','history',identity['sec_uid'])
    for query in cfg.get('queries',[(name+' '+cfg['author_name'])[:100] for name in cfg['names']])[:4]:
        activate('search','head',query)


def recover(session):
    now=utcnow()
    for task in session.scalars(select(DiscoveryTask).where(DiscoveryTask.status=='running',
        DiscoveryTask.lease_until<=now)):
        task.status,task.owner,task.message='interrupted','','执行租约已失效；请明确继续查找'


def owns(task, owner):
    return task.status=='running' and task.owner==owner and task.lease_until and task.lease_until>utcnow()


def claim(session, owner):
    if not enabled():
        return None
    task=session.scalar(select(DiscoveryTask).where(DiscoveryTask.status=='queued').order_by(
        DiscoveryTask.updated_at,DiscoveryTask.id).limit(1))
    if not task:
        return None
    now=utcnow()
    result=session.execute(update(DiscoveryTask).where(DiscoveryTask.id==task.id,DiscoveryTask.status=='queued').values(
        status='running',owner=owner,lease_until=now+timedelta(seconds=LEASE_SECONDS),heartbeat_at=now))
    if result.rowcount!=1:
        return None
    session.refresh(task)
    if not task.started_at:
        task.started_at=now
    return task.id


def reserve(session, task, owner):
    if not enabled() or not owns(task,owner):
        return None
    if not session.scalar(select(DiscoverySubscription.id).where(DiscoverySubscription.task_id==task.id,
                                                               DiscoverySubscription.active==True)):
        task.status,task.message='paused','所有榜单关联均已暂停，请先恢复需要的目录同步'
        task.owner,task.lease_until='',None
        return None
    cfg=json.loads(task.config); progress=json.loads(task.progress)
    if cfg['context']!=context(session,task.series_id):
        task.status,task.message='failed','作者、作品或合集资料变化，请明确重新核对来源'
        return None
    prepare_sources(session,task)
    # Reserve enough remaining time for a bounded 50-second response wait and 5-second retry delay.
    if progress['requests']>=50 or (utcnow()-task.started_at).total_seconds()>=545:
        task.status,task.message='budget_exhausted','本轮额度到限或剩余时间不足以发起请求；保留分页位置，请继续查找'
        return None
    sources=list(session.scalars(select(DiscoverySource).where(DiscoverySource.task_id==task.id).order_by(DiscoverySource.id)))
    pending=[s for s in sources if not s.ended]
    if any(s.kind=='detail' for s in pending):
        pending=[s for s in pending if s.kind=='detail']
    # Head and history cursors are independent. History begins after head reaches its boundary.
    pending=[s for s in pending if not(s.mode=='history' and any(
        p.kind==s.kind and p.object_key==s.object_key and p.mode=='head' and not p.ended for p in sources))]
    pending=[s for s in pending if s.kind!='search' or json.loads(s.progress).get('requests',0)<2]
    pending=[s for s in pending if s.kind!='detail' or progress['counts'].get('detail',0)<2]
    if not pending:
        unfinished=[s for s in sources if not s.ended]
        task.status='budget_exhausted' if unfinished else 'failed' if any(s.state in ('failed','unavailable') for s in sources) else 'completed'
        task.message='仍有分页待继续；来源读完不等于全剧完整' if unfinished else '本轮计划读取结束；全剧完整性未确认'
        if task.status=='completed' and progress.get('pending_update'):
            task.round+=1; start_round(session,task,True)
        return None
    # First give every applicable stage a turn; thereafter respect allocations, borrowing unused quotas.
    counts=progress['counts']
    eligible=[s for s in pending if counts.get(s.kind,0)<LIMITS[s.kind]]
    if not eligible:
        eligible=pending
    eligible.sort(key=lambda s:(counts.get(s.kind,0)>0, json.loads(s.progress).get('requests',0),s.id))
    source=eligible[0]
    data=json.loads(source.progress)
    if data.get('attempts',0)>=3:
        task.status,task.message='failed','同一页三次网络尝试未成功，请检查来源后明确继续'
        return None
    progress['requests']+=1; counts[source.kind]=counts.get(source.kind,0)+1
    data['requests']=data.get('requests',0)+1; data['attempts']=data.get('attempts',0)+1
    task.progress,source.progress=encode(progress),encode(data)
    task.message='正在读取：'+{'detail':'代表视频身份详情','mix':'合集','author':'作者主页','search':'不限时间补漏搜索'}[source.kind]
    return {'task_id':task.id,'source_id':source.id,'kind':source.kind,'object_key':source.object_key,
            'cursor':source.cursor,'retry':data['attempts']-1,'round':task.round,
            'remaining':max(0,600-(utcnow()-task.started_at).total_seconds())}


def save_page(session, task, owner, request, result):
    if not owns(task,owner) or not enabled():
        return False
    if json.loads(task.config)['context']!=context(session,task.series_id):
        task.status,task.message='failed','资料变化，返回页未同步；请重新核对来源'
        return False
    source=session.get(DiscoverySource,request['source_id'])
    if source.cursor!=request['cursor'] or task.round!=request['round']:
        return False
    validate_page(result,source.cursor)
    data=json.loads(source.progress)
    next_cursor=str(result['cursor'])
    if result['has_more'] and next_cursor in data.get('seen_cursors',[]):
        raise SourceUnavailable('parse_error','游标出现循环，未推进分页位置')
    cfg=json.loads(task.config)
    metas=[public_metadata(raw) for raw in result['videos']]
    if source.kind=='detail':
        if len(metas)!=1 or metas[0]['video_id']!=cfg['seed']:
            raise SourceUnavailable('parse_error','代表视频详情不一致')
        meta=metas[0]
        # uid and sec_uid are distinct fields, mapped only by the seed's public detail response.
        if not meta['uid'] or not (cfg['account_id']==meta['uid'] or (
            cfg['account_id']==meta['sec_uid'] and cfg['sec_hint']==meta['sec_uid'])):
            raise SourceUnavailable('identity_conflict','无法用公开详情确认原作者身份')
        if cfg['sec_hint'] and meta['sec_uid'] and cfg['sec_hint']!=meta['sec_uid']:
            raise SourceUnavailable('identity_conflict','代表视频作者与主页标识冲突')
        previous=cfg.get('identity',{})
        if previous and (previous.get('uid')!=meta['uid'] or
            previous.get('sec_uid')!=meta['sec_uid'] or cfg.get('mix_id','')!=meta['mix_id']):
            raise SourceUnavailable('identity_conflict','公开身份或可信合集变化，须重新核对来源')
        cfg['identity']={'uid':meta['uid'],'sec_uid':meta['sec_uid'],'seed':meta['video_id']}
        if cfg['mix_hint'] and cfg['mix_hint']!=meta['mix_id']:
            raise SourceUnavailable('identity_conflict','作品合集与代表视频详情冲突')
        cfg['mix_id']=cfg['mix_hint'] or meta['mix_id']
        task.config=encode(cfg)
    old_ids=set(data.get('known_ids',[]))
    boundary=bool(metas) and all(meta['video_id'] in set(data.get('baseline_ids',old_ids)) for meta in metas)
    for meta in metas:
        video=session.scalar(select(DiscoveryVideo).where(DiscoveryVideo.platform=='douyin',
                                                         DiscoveryVideo.video_id==meta['video_id']))
        if not video:
            video=DiscoveryVideo(video_id=meta['video_id'],metadata_json=encode(meta))
            session.add(video); session.flush()
        # Observations are immutable; latest public facts are separate from directory decisions.
        video.metadata_json=encode(meta)
        if not session.scalar(select(DiscoveryObservation.id).where(DiscoveryObservation.source_id==source.id,
            DiscoveryObservation.round==task.round,DiscoveryObservation.cursor==source.cursor,
            DiscoveryObservation.video_id==video.id)):
            session.add(DiscoveryObservation(source_id=source.id,round=task.round,cursor=source.cursor,
                video_id=video.id,metadata_json=encode(meta)))
        kind,basis=classify(meta,cfg)
        match=session.scalar(select(DiscoveryMatch).where(DiscoveryMatch.series_id==task.series_id,
                                                         DiscoveryMatch.video_id==video.id))
        if not match:
            match=DiscoveryMatch(series_id=task.series_id,video_id=video.id)
            session.add(match)
        match.kind,match.basis,match.context=kind,basis,cfg['context']
    session.flush()
    data['known_ids']=sorted(old_ids|{m['video_id'] for m in metas})
    data['boundary_pages']=data.get('boundary_pages',0)+1 if boundary else 0
    data['seen_cursors']=[*data.get('seen_cursors',[]),source.cursor]
    data['attempts']=0; data['pages']=data.get('pages',0)+1
    data['round_pages']=data.get('round_pages',0)+1
    source.cursor,source.ended=next_cursor,not result['has_more']
    source.state='ended' if source.ended else 'more'
    if source.kind in ('mix','author') and source.mode=='head':
        history=source_for(session,task,source.kind,'history',source.object_key)
        # First scan carries the head position forward into history; later rounds never rewind it.
        hdata=json.loads(history.progress)
        if not hdata.get('initialized') and not hdata.get('pages'):
            history.cursor=next_cursor; history.ended=not result['has_more']; history.state=source.state
            hdata['initialized']=True;history.progress=encode(hdata)
        if data['boundary_pages']>=2 or data['round_pages']==1 and task.round==1:
            source.ended=True
            source.state='boundary' if result['has_more'] else 'ended'
    source.progress=encode(data)
    sync(session,task)
    task.status,task.owner,task.lease_until,task.updated_at='queued','',None,utcnow()
    task.message='本页已保存，等待轮转下一页；新增候选须核验和确认准备范围'
    return True


def fail(session,task,owner,error):
    if not owns(task,owner):
        return
    code=getattr(error,'status','failed')
    # Store controlled diagnostics only; never store the upstream exception string.
    messages={'needs_login':'来源要求登录，请处理后显式恢复账号请求',
              'blocked':'来源要求验证，请处理后显式恢复账号请求',
              'parse_error':'分页结构或游标异常，未确认末页；请核对来源',
              'identity_conflict':'作者或合集身份冲突，自动入目录已停止',
              'unavailable':'该对象不可访问，未确认目录完整'}
    task.status=code if code in ('needs_login','blocked') else 'failed'
    task.message=messages.get(code,'本页请求未完成，保留已提交位置；请检查来源后继续')
    task.owner,task.lease_until='',None
    task.updated_at=utcnow()


def control(session, task, action, *, aliases=None):
    if action=='pause':
        task.status,task.message='paused','已暂停整部作品的共享查找；所有关联榜单停止后续请求'
        task.owner,task.lease_until='',None
        return
    if not enabled():
        raise ValueError('请先完成真实来源验证并开启自动补集开关')
    if task.status in ('running','queued'):
        raise ValueError('共享任务正在执行，请先暂停再操作')
    if action=='recheck':
        sub=session.scalar(select(DiscoverySubscription).where(DiscoverySubscription.task_id==task.id).order_by(
            DiscoverySubscription.ranking_id.desc()))
        task.config=encode(config_for(session,sub.ranking_id,task.series_id,aliases or ()))
        # Preserve old progress as evidence; old sources are no longer eligible until explicitly reset.
        for row in session.scalars(select(DiscoverySource).where(DiscoverySource.task_id==task.id)):
            row.ended=True; row.state='superseded'
        detail=source_for(session,task,'detail','identity',json.loads(task.config)['seed'])
        detail.ended=False;detail.state='pending';detail.cursor='0';detail.progress='{}'
    elif context(session,task.series_id)!=json.loads(task.config)['context']:
        raise ValueError('作品资料变化，请先重新核对身份及来源')
    elif action not in ('update','continue'):
        raise ValueError('补集操作无效')
    task.round+=1
    start_round(session,task,action=='update')
    sync_local(session,task);sync(session,task)


def view(session, catalog):
    sub=session.scalar(select(DiscoverySubscription).where(DiscoverySubscription.catalog_id==catalog.id))
    if not sub:
        return {'enabled':enabled(),'linked':False,'message':'尚未关联自动补集；旧榜单须主动启动'}
    task=session.get(DiscoveryTask,sub.task_id)
    rows=session.execute(select(DiscoveryMatch,DiscoveryVideo).join(DiscoveryVideo).where(
        DiscoveryMatch.series_id==task.series_id)).all()
    sources=list(session.scalars(select(DiscoverySource).where(DiscoverySource.task_id==task.id)))
    from services.site import public_catalog
    item=downloads.selected_item(session,catalog.ranking_id,catalog.series_id)
    playable=len(public_catalog(session,catalog.ranking_id,item,catalog)['episodes'])
    return {'enabled':enabled(),'linked':True,'task_id':task.id,'status':task.status,
        'status_text':STATES.get(task.status,task.status),'message':task.message,'round':task.round,
        'requests':json.loads(task.progress).get('requests',0),'active':sub.active,
        'playable':playable,
        'sync_message':sub.message,'heartbeat_at':iso(task.heartbeat_at),'updated_at':iso(task.updated_at),
        'context_changed':context(session,task.series_id)!=json.loads(task.config)['context'],
        'sources':[{'kind':s.kind,'mode':s.mode,'state':s.state,'ended':s.state=='ended',
            'pages':json.loads(s.progress).get('pages',0),'cursor':s.cursor,'provider':s.provider,
            'revision':s.revision} for s in sources],
        'counts':{kind:sum(m.kind==kind for m,v in rows) for kind in ('matched','clue','conflict','compilation','promotional')},
        'candidates':[{'id':m.id,**json.loads(v.metadata_json),'kind':m.kind,'basis':m.basis} for m,v in rows]}
