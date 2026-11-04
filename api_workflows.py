"""Authenticated orchestration; every mutation uses the existing write guard."""
import json
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from models import CollectionRun, DownloadCatalog, ReviewWorkflow, RankingRun, SearchJob
from services import review_workflows as flows, downloads, search
from services.export import TEMPLATES


class Start(BaseModel):
    run_id: int = Field(gt=0)
    operation_key: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    search: bool = False


class Finalize(BaseModel):
    token: str = Field(min_length=64, max_length=64)
    acknowledged: bool = False


class Selection(BaseModel):
    catalog_id: int = Field(gt=0)
    episode_ids: list[int] = Field(min_length=1, max_length=1000)
    tokens: dict[str, str]


class Scope(BaseModel):
    selections: list[Selection] = Field(min_length=1, max_length=3)


class Action(BaseModel):
    action: str


class Directory(BaseModel):
    catalog_id: int = Field(gt=0)
    continue_after_limit: bool = False


class Deferred(BaseModel):
    catalog_id: int = Field(gt=0)
    media_ids: list[int] = Field(default_factory=list, max_length=1000)
    reset: bool = False


class CatalogReview(BaseModel):
    catalog_id: int = Field(gt=0)
    episode_ids: list[int] = Field(min_length=1, max_length=1000)
    tokens: dict[str, str]
    status: str = Field(pattern=r'^(approved|excluded)$')
    note: str = Field(min_length=1, max_length=10000)
    ownership: bool = False
    main_episode: bool = False
    not_duplicate: bool = False
    public_access: bool = False


class Review(BaseModel):
    operation_key: str = Field(min_length=8, max_length=64)
    episode_ids: list[int] = Field(min_length=1, max_length=180)
    tokens: dict[str, str]
    target_type: str = Field(pattern=r"^(series|episode)$")
    status: str = Field(pattern=r"^(pending|approved|excluded)$")
    reason: str = Field(min_length=1, max_length=10000)
    evidence_url: str = Field(default="", max_length=2000)
    ai_type: str = Field(default="", max_length=200)
    ai_basis: str = Field(default="", max_length=10000)
    checks: dict[str, bool] = Field(default_factory=dict)


def router_for(database_session, local_write):
    router = APIRouter()
    def writing(request: Request, session=Depends(database_session)):
        local_write(request)
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return session

    @router.get('/control/tasks', response_class=HTMLResponse)
    def page():
        return TEMPLATES.get_template('tasks.html').render(control_area='tasks')

    @router.get('/api/control/tasks')
    def listing(session=Depends(database_session)):
        return [{"id": f.id, "run_id": f.run_id, "status": f.status, "stage": f.stage, "message": f.message,
                 "name": session.get(CollectionRun, f.run_id).name} for f in session.scalars(select(ReviewWorkflow).order_by(ReviewWorkflow.id.desc()).limit(100))]

    @router.post('/api/control/tasks')
    def start(data: Start, background: BackgroundTasks, session=Depends(writing)):
        run = session.get(CollectionRun, data.run_id)
        if not run:
            raise ValueError("批次不存在，请先创建批次")
        flow = flows.create(session, run, data.operation_key, data.search)
        session.commit()
        if flow.search_job_id and session.get(SearchJob, flow.search_job_id).status == "queued":
            background.add_task(search.dispatch, flow.search_job_id)
        return {"id": flow.id}

    @router.get('/api/control/tasks/{identifier}')
    def detail(identifier: int, session=Depends(database_session)):
        return flows.view(session, flows.get(session, identifier))

    @router.post('/api/control/tasks/{identifier}/supplement')
    def supplement(identifier: int, data: Start, session=Depends(writing)):
        flow = flows.get(session, identifier)
        if data.run_id != flow.run_id or data.search:
            raise ValueError('补充准备须沿用原固定榜单，不能启动新搜索')
        result = flows.supplement(session, flow, data.operation_key)
        return {'id':result.id,'message':'已新建补充准备流程，原完成记录保留'}

    @router.post('/api/control/tasks/{identifier}/reviews')
    def review(identifier: int, data: Review, session=Depends(writing)):
        flows.batch_review(session, flows.get(session, identifier), data.model_dump())
        session.commit()
        return {"message": "已分别记录所选对象的核验依据"}

    @router.post('/api/control/tasks/{identifier}/finalize')
    def finalize(identifier: int, data: Finalize, session=Depends(writing)):
        result = {"ranking_id": flows.finalize(session, flows.get(session, identifier), data.token, data.acknowledged)}
        session.commit()
        return result

    @router.post('/api/control/tasks/{identifier}/refresh-candidates')
    def refresh_candidates(identifier: int, background: BackgroundTasks, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        run = session.get(CollectionRun, flow.run_id)
        job = search.prepare_job(session, run, [], 1, refresh_only=True)
        flow.search_job_id = job.id
        if not flow.ranking_id:
            flow.stage = 'search'
        flow.message = '正在补齐已有候选并更新本批次互动；固定版本保留'
        session.commit()
        background.add_task(search.dispatch, job.id)
        return {'message': flow.message}

    @router.post('/api/control/tasks/{identifier}/retry-candidates')
    def retry_candidates(identifier: int, background: BackgroundTasks, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        job = session.get(SearchJob, flow.search_job_id) if flow.search_job_id else None
        if not job:
            raise ValueError('请先开始补齐候选')
        search.prepare_resume(session, job)
        session.commit()
        background.add_task(search.dispatch, job.id)
        return {'message': '从已保存进度继续采集及互动更新'}

    @router.post('/api/control/tasks/{identifier}/initialize')
    def initialize(identifier: int, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        if not flow.ranking_id:
            raise ValueError("请先固定正式榜单")
        for row in json.loads(session.get(RankingRun, flow.ranking_id).snapshot)["items"]:
            if row['selected']:
                downloads.catalog_for(session, flow.ranking_id, row['series_id'])
        session.commit()
        return {"message": "目录已就绪；请确认本次允许准备的条目"}

    @router.post('/api/control/tasks/{identifier}/scope')
    def scope(identifier: int, data: Scope, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.confirm_scope(session, flow, [s.model_dump() for s in data.selections])
        session.commit()
        return {"message": "已确认处理范围，后台将先试下载，再等待人工快审"}

    @router.post('/api/control/tasks/{identifier}/defer')
    def defer(identifier: int, data: Deferred, session=Depends(writing)):
        from models import DownloadEpisode, DownloadJob, MediaFile
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        payload = json.loads(flow.payload)
        info = payload['catalogs'].get(str(data.catalog_id))
        if not info:
            raise ValueError('目录不在已确认范围内')
        allowed = set(session.scalars(select(MediaFile.id).join(DownloadJob).join(DownloadEpisode)
            .where(DownloadEpisode.id.in_(info['episode_ids']))))
        if not set(data.media_ids) <= allowed:
            raise ValueError('待审条目不在本次范围内')
        info['deferred'] = [] if data.reset else sorted(set(info.get('deferred', [])) | set(data.media_ids))
        if data.reset:
            info.pop('batch_id', None)
            info['state'] = 'waiting'
        flows.save(flow, payload)
        session.commit()
        return {'message': '暂缓项已保存，可从详细页重开'}

    @router.post('/api/control/tasks/{identifier}/catalog-review')
    def catalog_review(identifier: int, data: CatalogReview, session=Depends(writing)):
        from models import DownloadEpisode
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        catalog = session.get(DownloadCatalog, data.catalog_id)
        if not catalog or flow.ranking_id is None or catalog.ranking_id != flow.ranking_id:
            raise ValueError('目录不在本次固定榜单内')
        for identifier in data.episode_ids:
            episode = session.get(DownloadEpisode, identifier)
            if not episode or episode.catalog_id != catalog.id or data.tokens.get(str(identifier)) != flows.episode_stamp(episode):
                raise ValueError('目录条目资料已变化，请刷新后重新核对')
        result = downloads.review_episodes(session, catalog, data.model_dump())
        session.commit()
        return result

    @router.post('/api/control/tasks/{identifier}/discover')
    def discover(identifier: int, data: Directory, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        catalog = session.get(DownloadCatalog, data.catalog_id)
        if not catalog or catalog.ranking_id != flow.ranking_id or flow.ranking_id is None:
            raise ValueError('目录不在当前固定榜单内')
        payload = json.loads(flow.payload)
        pages = payload.setdefault('discovery_pages', {}).get(str(catalog.id), 0)
        if pages >= 3 and not data.continue_after_limit:
            raise ValueError('已达到本轮三页发现上限；请在详细页核对，或明确确认继续一页')
        result = downloads.discover(session, catalog)
        payload['discovery_pages'][str(catalog.id)] = pages + 1
        flows.save(flow, payload)
        session.commit()
        return result

    @router.post('/api/control/tasks/{identifier}/control')
    def control(identifier: int, data: Action, background: BackgroundTasks, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.control(session, flow, data.action)
        if data.action == "resume" and flow.search_job_id:
            job = session.get(SearchJob, flow.search_job_id)
            if job.status in search.RESUMABLE:
                search.prepare_resume(session, job)
                session.commit()
                background.add_task(search.dispatch, job.id)
        session.commit()
        return {"message": flow.message}

    @router.post('/api/control/tasks/{identifier}/complete')
    def complete(identifier: int, session=Depends(writing)):
        flow = flows.get(session, identifier)
        flows.require_active(flow)
        if not flows.view(session, flow)["all_scoped_complete"]:
            raise ValueError("本次范围仍有未处理条目，请先完成或在详细页处理")
        flow.status, flow.message = "complete", "本次范围已处理。这是汇总，公开资格继续按各集状态检查。"
        session.commit()
        return {"message": flow.message}

    return router
