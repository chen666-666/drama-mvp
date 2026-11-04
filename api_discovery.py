"""Administrator-only discovery controls. GET endpoints never register or dispatch work."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from models import DownloadCatalog, DiscoveryTask, DiscoverySubscription
from services import episode_discovery as service


class Command(BaseModel):
    action: str = Field(pattern=r'^(start|update|continue|pause|unlink|link|recheck)$')
    aliases: list[str] = Field(default_factory=list, max_length=3)


def router_for(database_session, local_write):
    router=APIRouter()
    def writing(request: Request, session=Depends(database_session)):
        local_write(request)
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        return session

    def catalog_for(session, identifier):
        catalog=session.get(DownloadCatalog,identifier)
        if not catalog or catalog.ranking_id is None:
            raise ValueError('自动补集仅用于实际入选的榜单作品')
        return catalog

    @router.get('/api/control/discovery/status')
    def worker_status():
        from workers.episode_discovery import HEARTBEAT
        import json
        from services.common import parse_time, utcnow
        try:
            status=json.loads(HEARTBEAT.read_text(encoding='utf-8'))
            running=(utcnow()-parse_time(status['at'])).total_seconds()<120
        except (OSError,ValueError,KeyError,TypeError):
            running=False
        return {'running':running,'enabled':service.enabled()}

    @router.get('/api/control/discovery/catalogs/{identifier}')
    def status(identifier: int, session=Depends(database_session)):
        return service.view(session,catalog_for(session,identifier))

    @router.post('/api/control/discovery/catalogs/{identifier}')
    def control(identifier: int, data: Command, session=Depends(writing)):
        catalog=catalog_for(session,identifier)
        sub=session.scalar(select(DiscoverySubscription).where(DiscoverySubscription.catalog_id==catalog.id))
        aliases=[s.strip() for s in data.aliases]
        if any(not s or len(s)>100 for s in aliases):
            raise ValueError('每个剧名别名须为1—100字')
        if data.action=='start':
            task=service.register(session,catalog.ranking_id,catalog.series_id,aliases)
        elif not sub:
            raise ValueError('请先启动本榜单的补集关联')
        elif data.action in ('unlink','link'):
            sub.active=data.action=='link'
            sub.message='已暂停本目录同步；其他榜单可继续查找' if not sub.active else '已恢复本目录同步'
            if sub.active:
                service.sync(session,session.get(DiscoveryTask,sub.task_id))
        else:
            service.control(session,session.get(DiscoveryTask,sub.task_id),data.action,aliases=aliases)
        return {'message':'补集操作已保存；候选仍须核验及明确确认准备范围'}

    @router.post('/api/control/discovery/resume-source')
    def resume_source(session=Depends(writing)):
        # Network I/O is outside the database write transaction.
        session.commit()
        from sources.download import bridge_request
        bridge_request('/resume',{'video_id':'0'})
        return {'message':'已明确恢复共享来源请求；请分别继续暂停的任务'}

    return router
