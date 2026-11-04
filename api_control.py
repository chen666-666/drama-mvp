"""Read-only control overview and minimal authenticated playback delivery data."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from models import DownloadCatalog, DownloadEpisode, DownloadJob, Episode, MediaFile, RankingRun, SearchHit, SearchJob, Series
from services.export import TEMPLATES
from services import downloads
from services.site import display_titles


def router_for(database_session):
    router = APIRouter()

    @router.get('/control', response_class=HTMLResponse)
    def overview():
        return TEMPLATES.get_template('control.html').render(control_area='overview', control_title='待办总览')

    @router.get('/control/videos', response_class=HTMLResponse)
    def videos():
        return TEMPLATES.get_template('downloads.html').render(control_area='videos', control_title='视频管理')

    @router.get('/control/reviews', response_class=HTMLResponse)
    def reviews():
        return TEMPLATES.get_template('index.html').render(control_area='videos', control_title='资料与来源核验', review_mode=True)

    @router.get('/control/collection', response_class=HTMLResponse)
    def collection():
        return TEMPLATES.get_template('index.html').render(control_area='collection', control_title='采集与榜单')

    @router.get('/control/system', response_class=HTMLResponse)
    def system(request: Request):
        return TEMPLATES.get_template('control.html').render(control_area='system', control_title='系统与交付',
            local_publish=request.client.host in ('127.0.0.1', '::1', 'testclient'))

    @router.get('/api/control/overview')
    def summary(session=Depends(database_session)):
        # Count source objects once, with links into their actual collection run.
        source_rows = session.execute(select(Series, Episode, SearchHit.run_id)
            .select_from(SearchHit).outerjoin(Episode, Episode.id == SearchHit.episode_id)
            .outerjoin(Series, Series.id == Episode.series_id).order_by(SearchHit.run_id.desc()))
        pending = {}
        for series, episode, run_id in source_rows:
            if (series and series.review_status == 'excluded') or (episode and episode.review_status == 'excluded'):
                continue
            if series and series.review_status == 'pending':
                pending.setdefault(('series', series.id), {'title': series.title, 'kind': '作品待核验',
                    'url': f'/control/reviews?run_id={run_id}&series_id={series.id}'})
            if episode and episode.review_status == 'pending':
                pending.setdefault(('episode', episode.id), {'title': episode.title or '待补全视频', 'kind': '正片待核验',
                    'url': f'/control/reviews?run_id={run_id}&episode_id={episode.id}'})
            if episode is None:
                pending.setdefault(('link', run_id), {'title': '来源链接待补全', 'kind': '资料待补全',
                    'url': f'/control/collection?run_id={run_id}&tab=import'})
        inspections, failures, directories = [], [], []
        rows = session.execute(select(DownloadEpisode, DownloadCatalog, DownloadJob, MediaFile)
            .join(DownloadCatalog, DownloadCatalog.id == DownloadEpisode.catalog_id)
            .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
            .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id))
        for episode, catalog, job, media in rows:
            context = f'catalog_id={catalog.id}' if catalog.ranking_id is None else f'ranking_id={catalog.ranking_id}'
            label = '指定收录' if catalog.ranking_id is None else f'榜单 {catalog.ranking_id}'
            url = f'/control/videos?{context}&series_id={catalog.series_id}&episode_id={episode.id}'
            if episode.status == 'pending':
                directories.append({'title': episode.title, 'kind': f'目录待核验 · {label}', 'url': url})
            if media and job and job.status == 'verified' and media.manual_status == 'pending':
                inspections.append({'title': episode.title, 'kind': f'待人工抽检 · {label}', 'url': url + '&view=inspection'})
            unavailable_reuse = (episode.reused_media_id and episode.status == 'approved' and episode.access == 'public'
                                 and not downloads.reused_resource(session, episode, catalog)[1])
            if unavailable_reuse or job and (job.status in ('failed', 'paused') or (job.status == 'verified' and (not media or media.manual_status == 'rejected'))):
                failures.append({'title': episode.title, 'kind': f'视频准备待处理 · {label}', 'url': url})
        searches = [{'title': f'搜索任务 {job.id}', 'kind': {'needs_login': '来源需要登录', 'blocked': '来源需要验证', 'failed': '搜索失败', 'interrupted': '搜索待恢复'}[job.status],
            'url': f'/control/collection?run_id={job.run_id}&tab=search'} for job in session.scalars(select(SearchJob).where(
                SearchJob.status.in_(['needs_login', 'blocked', 'failed', 'interrupted'])).order_by(SearchJob.id.desc()))]
        groups = [('sources', '来源待核验', list(pending.values())), ('directories', '目录待核验', directories),
                  ('inspections', '待人工抽检', inspections), ('failures', '准备待处理', failures), ('searches', '搜索待处理', searches)]
        from models import DiscoveryTask, DiscoverySubscription
        from services.episode_discovery import STATES
        discovery = [{'title':session.get(Series, task.series_id).title,
            'kind':f'补集 · {STATES[task.status]} · 榜单 {sub.ranking_id}',
            'url':f'/control/videos?ranking_id={sub.ranking_id}&series_id={sub.series_id}&catalog_id={sub.catalog_id}'}
            for task,sub in session.execute(select(DiscoveryTask,DiscoverySubscription).join(
                DiscoverySubscription,DiscoverySubscription.task_id==DiscoveryTask.id).where(
                DiscoverySubscription.active==True,DiscoveryTask.status!='completed'))]
        groups.append(('discovery','补集进度与待处理',discovery))
        return {'groups': [{'key': key, 'label': label, 'count': len(items), 'items': items[:12]} for key, label, items in groups]}

    @router.get('/api/playback/rankings/{ranking_id}/series/{series_id}')
    def playback_files(ranking_id: int, series_id: int, session=Depends(database_session)):
        ranking = session.get(RankingRun, ranking_id)
        if not ranking:
            raise HTTPException(404, '榜单不存在')
        snapshot = display_titles(session, json.loads(ranking.snapshot))
        item = next((row for row in snapshot['items'] if row['selected'] and row['series_id'] == series_id), None)
        if not item:
            raise HTTPException(404, '本榜单未收录此作品')
        catalog = session.scalar(select(DownloadCatalog).where(DownloadCatalog.ranking_id == ranking_id, DownloadCatalog.series_id == series_id))
        episodes = []
        if catalog:
            rows = session.execute(select(DownloadEpisode, DownloadJob, MediaFile)
                .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
                .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id)
                .where(DownloadEpisode.catalog_id == catalog.id).order_by(DownloadEpisode.episode_number, DownloadEpisode.id))
            for episode, job, media in rows:
                if episode.reused_media_id:
                    job, media = downloads.reused_resource(session, episode, catalog)
                # No diagnostics, evidence, internal paths, task details or private records in playback.
                if episode.status != 'approved' or episode.access != 'public':
                    continue
                file_url = None
                if media:
                    try:
                        downloads.delivery_path(session, media)
                        file_url = f'/api/downloads/media/{media.id}/download'
                    except (ValueError, OSError):
                        pass
                episodes.append({'id': episode.id, 'title': episode.title, 'episode_number': episode.episode_number, 'episode_range': episode.episode_range,
                    'file_url': file_url, 'representative': episode.video_id == item['video_id']})
        return {'series_title': item['series_title'], 'episodes': episodes}

    @router.get('/api/playback/collections/{catalog_id}')
    def collection_files(catalog_id: int, session=Depends(database_session)):
        catalog = session.get(DownloadCatalog, catalog_id)
        if not catalog or catalog.ranking_id is not None:
            raise HTTPException(404, '指定收录作品不存在')
        item = downloads.catalog_item(session, catalog)
        episodes = []
        for episode, job, media in session.execute(select(DownloadEpisode, DownloadJob, MediaFile)
                .outerjoin(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
                .outerjoin(MediaFile, MediaFile.job_id == DownloadJob.id)
                .where(DownloadEpisode.catalog_id == catalog.id).order_by(DownloadEpisode.episode_number, DownloadEpisode.id)):
            if episode.status != 'approved' or episode.access != 'public':
                continue
            file_url = None
            if media:
                try:
                    downloads.delivery_path(session, media)
                    file_url = f'/api/downloads/media/{media.id}/download'
                except (ValueError, OSError):
                    pass
            episodes.append({'id': episode.id, 'title': episode.title, 'episode_number': episode.episode_number, 'episode_range': episode.episode_range,
                             'file_url': file_url, 'representative': False})
        return {'series_title': item['series_title'], 'episodes': episodes}

    return router
