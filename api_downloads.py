"""HTTP control for step two. Video transfers run only in workers.download."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from models import DownloadCatalog, DownloadEpisode, DownloadJob, ExportRun, MediaFile, RankingRun
from services.common import utcnow
from services import downloads as service
from sources.douk import SourceUnavailable


class EpisodeLinks(BaseModel):
    links: str = Field(min_length=1, max_length=100000)


class EpisodeReview(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    episode_number: int | None = Field(default=None, ge=1)
    status: str
    access: str
    note: str = Field(min_length=1, max_length=10000)
    evidence_url: str
    ownership: bool = False
    main_episode: bool = False
    not_duplicate: bool = False


class CatalogReview(BaseModel):
    checked: bool = False
    total_episodes: int | None = Field(default=None, ge=1)
    note: str = Field(min_length=1, max_length=10000)


class QueueInput(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=1000)
    trial: bool = False


class ControlInput(BaseModel):
    action: str


class ManualInput(BaseModel):
    status: str
    note: str = Field(min_length=1, max_length=10000)
    beginning: bool = False
    middle: bool = False
    ending: bool = False
    sound: bool = False


def router_for(database_session, local_write):
    router = APIRouter()

    def local(request: Request):
        local_write(request)

    def found(session, model, identifier):
        row = session.get(model, identifier)
        if not row:
            raise HTTPException(404, "记录不存在")
        return row

    @router.get("/api/downloads/status")
    def status():
        return service.worker_status()

    @router.get("/api/downloads/rankings")
    def rankings(session=Depends(database_session)):
        return [{"id": row.id, "version": row.version, "name": json.loads(row.snapshot)["run_name"]}
                for row in session.scalars(select(RankingRun).order_by(RankingRun.id.desc()))]

    @router.get("/api/downloads/rankings/{ranking_id}")
    def detail(ranking_id: int, session=Depends(database_session)):
        ranking = found(session, RankingRun, ranking_id)
        snapshot = {"id": ranking.id, **json.loads(ranking.snapshot)}
        return {"ranking": snapshot, "catalogs": [service.catalog_detail(session, service.catalog_for(session, ranking_id, row["series_id"]))
                for row in snapshot["items"] if row["selected"]]}

    @router.post("/api/downloads/catalogs/{catalog_id}/links", dependencies=[Depends(local)])
    def links(catalog_id: int, data: EpisodeLinks, session=Depends(database_session)):
        return service.add_links(session, found(session, DownloadCatalog, catalog_id), data.links)

    @router.post("/api/downloads/catalogs/{catalog_id}/discover", dependencies=[Depends(local)])
    def discover(catalog_id: int, session=Depends(database_session)):
        try:
            return service.discover(session, found(session, DownloadCatalog, catalog_id))
        except SourceUnavailable as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/downloads/catalogs/{catalog_id}/review", dependencies=[Depends(local)])
    def review_catalog(catalog_id: int, data: CatalogReview, session=Depends(database_session)):
        row = found(session, DownloadCatalog, catalog_id)
        row.checked, row.total_episodes, row.note, row.updated_at = data.checked, data.total_episodes, data.note, utcnow()
        return {"message": "目录核对与缺集说明已保存"}

    @router.post("/api/downloads/episodes/{episode_id}/review", dependencies=[Depends(local)])
    def review_episode(episode_id: int, data: EpisodeReview, session=Depends(database_session)):
        service.review_episode(session, found(session, DownloadEpisode, episode_id), data.model_dump())
        return {"message": "正片核验已保存"}

    @router.post("/api/downloads/catalogs/{catalog_id}/queue", dependencies=[Depends(local)])
    def enqueue(catalog_id: int, data: QueueInput, session=Depends(database_session)):
        return service.enqueue(session, found(session, DownloadCatalog, catalog_id), data.episode_ids, data.trial)

    @router.post("/api/downloads/jobs/{job_id}/control", dependencies=[Depends(local)])
    def control(job_id: int, data: ControlInput, session=Depends(database_session)):
        service.control_job(session, found(session, DownloadJob, job_id), data.action)
        return {"message": "任务状态已更新"}

    @router.post("/api/downloads/media/{media_id}/review", dependencies=[Depends(local)])
    def manual(media_id: int, data: ManualInput, session=Depends(database_session)):
        service.check_media(session, found(session, MediaFile, media_id), data.model_dump())
        return {"message": "人工抽检结果已保存"}

    @router.get("/api/downloads/media/{media_id}/file")
    def media_file(media_id: int, session=Depends(database_session)):
        media = found(session, MediaFile, media_id)
        path = service.inside(service.DATA, media.relative_path)
        if not path.is_file():
            raise HTTPException(404, "本地视频文件已缺失")
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    @router.get("/api/downloads/catalogs/{catalog_id}/cover")
    def cover(catalog_id: int, session=Depends(database_session)):
        found(session, DownloadCatalog, catalog_id)
        path = service.inside(service.DATA, f"thumbnails/catalog-{catalog_id}.jpg")
        if not path.is_file():
            raise HTTPException(404, "封面尚未生成")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/api/downloads/rankings/{ranking_id}/export", dependencies=[Depends(local)])
    def export(ranking_id: int, session=Depends(database_session)):
        from services.package import build_package
        result = build_package(session, found(session, RankingRun, ranking_id))
        summary = json.loads(result.summary)
        return {"id": result.id, "url": f"/api/downloads/exports/{result.id}/file", "message": f"资料包已生成，收入 {summary['files']} 个已校验视频，并保留逐集缺口记录"}

    @router.get("/api/downloads/exports/{export_id}/file")
    def export_file(export_id: int, session=Depends(database_session)):
        row = found(session, ExportRun, export_id)
        path = service.inside(service.DATA, row.relative_path)
        if not path.is_file():
            raise HTTPException(404, "导出文件已缺失")
        return FileResponse(path, filename=f"douyin-top3-{row.ranking_id}-export-{row.id}.zip")

    return router
