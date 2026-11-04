"""HTTP control for step two. Video transfers run only in workers.download."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from models import Author, DownloadCatalog, DownloadEpisode, DownloadJob, ExportRun, MediaFile, RankingRun, Series
from services.common import utcnow
from services.episode_numbers import EpisodeNumberInput
from services import downloads as service
from sources.douk import SourceUnavailable
from services import quick_reviews


class EpisodeLinks(BaseModel):
    links: str = Field(min_length=1, max_length=100000)


class CollectionInput(BaseModel):
    series_id: int = Field(gt=0)
    source_url: str = Field(default="", max_length=2000)


class DiscoverInput(BaseModel):
    keyword: str | None = Field(default=None, max_length=255)
    restart: bool = False


class PublicationInput(BaseModel):
    published: bool


class EpisodeReview(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    episode_number: EpisodeNumberInput = None
    status: str
    access: str
    note: str = Field(min_length=1, max_length=10000)
    evidence_url: str
    ownership: bool = False
    main_episode: bool = False
    not_duplicate: bool = False


class CatalogReview(BaseModel):
    checked: bool = False
    total_episodes: int | None = Field(default=None, ge=1, le=10000)
    note: str = Field(min_length=1, max_length=10000)


class BatchEpisodeReview(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=1000)
    status: str
    note: str = Field(min_length=1, max_length=10000)
    ownership: bool = False
    main_episode: bool = False
    not_duplicate: bool = False
    public_access: bool = False


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


class QuickBatchInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=10)
    skip_media_ids: list[int] = Field(default_factory=list, max_length=1000)
    restart: bool = False
    episode_ids: list[int] | None = Field(default=None, max_length=1000)


class QuickWatchedInput(BaseModel):
    media_id: int = Field(gt=0)
    index: int = Field(ge=0, le=2)


class QuickDecision(BaseModel):
    media_id: int = Field(gt=0)
    status: str
    reason: str = Field(default="", max_length=2000)


class QuickSubmitInput(BaseModel):
    decisions: list[QuickDecision] = Field(min_length=1, max_length=10)
    note: str = Field(min_length=1, max_length=5000)
    confirmed: bool = False
    sound: bool = False


class QuickPreviewInput(BaseModel):
    decisions: list[QuickDecision] = Field(default_factory=list, max_length=10)


def router_for(database_session, local_write):
    router = APIRouter()

    def local(request: Request):
        local_write(request)

    def found(session, model, identifier):
        row = session.get(model, identifier)
        if not row:
            raise HTTPException(404, "记录不存在")
        return row

    def serialized(session=Depends(database_session)):
        # Take SQLite's write lock before reading, also across web processes.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return session

    @router.get("/api/downloads/status")
    def status():
        return service.worker_status()

    @router.get("/api/downloads/series")
    def collection_series(session=Depends(database_session)):
        return [{"id": series.id, "title": series.title, "author_name": author.name,
                 "author_url": author.homepage_url, "author_account_id": author.account_id}
                for series, author in session.execute(select(Series, Author).join(Author, Author.id == Series.author_id)
                    .where(Series.review_status == "approved").order_by(Series.id.desc()))]

    @router.get("/api/downloads/collections")
    def collections(session=Depends(database_session)):
        return [{"id": catalog.id, "series_id": series.id, "title": series.title, "published": catalog.published}
                for catalog, series in session.execute(select(DownloadCatalog, Series).join(Series, Series.id == DownloadCatalog.series_id)
                    .where(DownloadCatalog.ranking_id.is_(None)).order_by(DownloadCatalog.id.desc()))]

    @router.post("/api/downloads/collections", dependencies=[Depends(local)])
    def create_collection(data: CollectionInput, session=Depends(serialized)):
        catalog = service.manual_catalog(session, data.series_id, data.source_url)
        return {"id": catalog.id, "message": "本剧收录目录已准备好，已有剧集不会重复添加"}

    @router.get("/api/downloads/collections/{catalog_id}")
    def collection_detail(catalog_id: int, session=Depends(database_session)):
        from services.site import manual_detail
        return manual_detail(session, found(session, DownloadCatalog, catalog_id), public=False)

    @router.post("/api/downloads/collections/{catalog_id}/publication", dependencies=[Depends(local)])
    def collection_publication(catalog_id: int, data: PublicationInput, session=Depends(serialized)):
        catalog = found(session, DownloadCatalog, catalog_id)
        if catalog.ranking_id is not None:
            raise ValueError("此操作仅用于指定收录作品")
        if data.published:
            service.catalog_item(session, catalog)
            candidates = session.scalars(select(MediaFile).join(DownloadJob, DownloadJob.id == MediaFile.job_id)
                .join(DownloadEpisode, DownloadEpisode.id == DownloadJob.episode_id)
                .where(DownloadEpisode.catalog_id == catalog.id, MediaFile.manual_status == "passed"))
            playable = False
            for media in candidates:
                try:
                    service.delivery_path(session, media)
                    playable = True
                    break
                except (ValueError, OSError):
                    continue
            if not playable:
                raise ValueError("请至少完成一集公开正片的下载、自动校验和人工抽检，再显示到网站")
        catalog.published, catalog.updated_at = data.published, utcnow()
        return {"message": "已显示到网站；后续通过检查的剧集会自动加入播放列表" if data.published else "已从指定收录播放区隐藏，准备记录保留"}

    @router.get("/api/library/collections/{catalog_id}")
    def public_collection(catalog_id: int, session=Depends(database_session)):
        from services.site import manual_detail
        try:
            return manual_detail(session, found(session, DownloadCatalog, catalog_id))
        except ValueError:
            raise HTTPException(404, "本站暂未收录此作品")

    @router.get("/api/library/rankings")
    def daily_public_rankings(session=Depends(database_session)):
        from services.site import daily_rankings
        return [{"id": row.id, "version": row.version, "name": json.loads(row.snapshot)["run_name"]}
                for row in daily_rankings(session.scalars(select(RankingRun)).all())]

    @router.get("/api/downloads/rankings")
    def rankings(session=Depends(database_session)):
        return [{"id": row.id, "version": row.version, "name": json.loads(row.snapshot)["run_name"]}
                for row in session.scalars(select(RankingRun).order_by(RankingRun.id.desc()))]

    @router.get("/api/downloads/rankings/{ranking_id}")
    def detail(ranking_id: int, session=Depends(database_session)):
        from services.site import display_titles
        ranking = found(session, RankingRun, ranking_id)
        snapshot = display_titles(session, {"id": ranking.id, **json.loads(ranking.snapshot)})
        catalogs = {catalog.series_id: catalog for catalog in session.scalars(
            select(DownloadCatalog).where(DownloadCatalog.ranking_id == ranking_id))}
        selected = sorted((row for row in snapshot["items"] if row["selected"]), key=lambda row: row["position"])
        return {"ranking": snapshot, "catalogs": [service.catalog_detail(session, catalogs[row["series_id"]], include_discovery=True)
                for row in selected if row["series_id"] in catalogs]}

    @router.post("/api/downloads/rankings/{ranking_id}/initialize", dependencies=[Depends(local)])
    def initialize(ranking_id: int, session=Depends(serialized)):
        ranking = found(session, RankingRun, ranking_id)
        for row in json.loads(ranking.snapshot)["items"]:
            if row["selected"]:
                service.catalog_for(session, ranking_id, row["series_id"])
        return {"message": "本版本目录已准备好"}

    @router.get("/api/library/rankings/{ranking_id}")
    def public_detail(ranking_id: int, session=Depends(database_session)):
        from services.site import library_detail
        ranking = found(session, RankingRun, ranking_id)
        return library_detail(session, ranking.id, json.loads(ranking.snapshot))

    @router.api_route("/api/library/media/{media_id}", methods=["GET", "HEAD"])
    def public_media(media_id: int, session=Depends(database_session)):
        from services.site import playable_path
        try:
            path = playable_path(session, found(session, MediaFile, media_id))
        except (ValueError, OSError):
            raise HTTPException(404, "本站暂未收录此视频")
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    @router.api_route("/api/library/media/{media_id}/download", methods=["GET", "HEAD"])
    def public_download(media_id: int, session=Depends(database_session)):
        from services.site import playable_path
        try:
            path = playable_path(session, found(session, MediaFile, media_id))
        except (ValueError, OSError):
            raise HTTPException(404, "本站暂未收录此视频")
        return FileResponse(path, filename=service.safe_name(path.stem) + path.suffix,
                            content_disposition_type="attachment", media_type="video/mp4",
                            headers={"Cache-Control": "no-store"})

    @router.post("/api/downloads/catalogs/{catalog_id}/download", dependencies=[Depends(local)])
    def download(catalog_id: int, session=Depends(serialized)):
        return service.download_one(session, found(session, DownloadCatalog, catalog_id))

    @router.post("/api/downloads/catalogs/{catalog_id}/links", dependencies=[Depends(local)])
    def links(catalog_id: int, data: EpisodeLinks, session=Depends(database_session)):
        return service.add_links(session, found(session, DownloadCatalog, catalog_id), data.links)

    @router.post("/api/downloads/catalogs/{catalog_id}/discover", dependencies=[Depends(local)])
    def discover(catalog_id: int, data: DiscoverInput, session=Depends(database_session)):
        try:
            return service.discover(session, found(session, DownloadCatalog, catalog_id), data.keyword, data.restart)
        except SourceUnavailable as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/downloads/catalogs/{catalog_id}/review", dependencies=[Depends(local)])
    def review_catalog(catalog_id: int, data: CatalogReview, session=Depends(database_session)):
        row = found(session, DownloadCatalog, catalog_id)
        row.checked, row.total_episodes, row.note, row.updated_at = data.checked, data.total_episodes, data.note, utcnow()
        return {"message": "目录核对与缺集说明已保存"}

    @router.post("/api/downloads/catalogs/{catalog_id}/episodes/review", dependencies=[Depends(local)])
    def review_episodes(catalog_id: int, data: BatchEpisodeReview, session=Depends(serialized)):
        return service.review_episodes(session, found(session, DownloadCatalog, catalog_id), data.model_dump())

    @router.post("/api/downloads/episodes/{episode_id}/review", dependencies=[Depends(local)])
    def review_episode(episode_id: int, data: EpisodeReview, session=Depends(database_session)):
        service.review_episode(session, found(session, DownloadEpisode, episode_id), data.model_dump())
        return {"message": "正片核验已保存"}

    @router.post("/api/downloads/catalogs/{catalog_id}/queue", dependencies=[Depends(local)])
    def enqueue(catalog_id: int, data: QueueInput, session=Depends(serialized)):
        return service.enqueue(session, found(session, DownloadCatalog, catalog_id), data.episode_ids, data.trial)

    @router.post("/api/downloads/jobs/{job_id}/control", dependencies=[Depends(local)])
    def control(job_id: int, data: ControlInput, session=Depends(database_session)):
        service.control_job(session, found(session, DownloadJob, job_id), data.action)
        return {"message": "任务状态已更新"}

    @router.post("/api/downloads/catalogs/{catalog_id}/quick-reviews", dependencies=[Depends(local)])
    def prepare_quick(catalog_id: int, data: QuickBatchInput, session=Depends(database_session)):
        return quick_reviews.create(session, found(session, DownloadCatalog, catalog_id), data.limit, data.skip_media_ids, data.restart, data.episode_ids)

    @router.get("/api/downloads/quick-reviews/{batch_id}")
    def quick_status(batch_id: str):
        return quick_reviews.status(batch_id)

    @router.get("/api/downloads/quick-reviews/{batch_id}/clips/{media_id}/{index}")
    def quick_clip(batch_id: str, media_id: int, index: int, session=Depends(database_session)):
        return FileResponse(quick_reviews.clip(session, batch_id, media_id, index), media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @router.post("/api/downloads/quick-reviews/{batch_id}/watched", dependencies=[Depends(local)])
    def quick_watched(batch_id: str, data: QuickWatchedInput, session=Depends(database_session)):
        return quick_reviews.watched(session, batch_id, data.media_id, data.index)

    @router.post("/api/downloads/quick-reviews/{batch_id}/submit", dependencies=[Depends(local)])
    def quick_submit(batch_id: str, data: QuickSubmitInput, session=Depends(serialized)):
        return quick_reviews.submit(session, batch_id, data.model_dump())

    @router.post('/api/downloads/quick-reviews/{batch_id}/preview', dependencies=[Depends(local)])
    def quick_preview(batch_id: str, data: QuickPreviewInput, session=Depends(database_session)):
        return quick_reviews.preview(session, batch_id, [d.model_dump() for d in data.decisions])

    @router.post('/api/downloads/media/{media_id}/analysis', dependencies=[Depends(local)])
    def start_analysis(media_id: int, session=Depends(database_session)):
        from services import media_analysis
        return media_analysis.request_scan(session, found(session, MediaFile, media_id))

    @router.get('/api/downloads/media/{media_id}/analysis')
    def analysis_status(media_id: int, session=Depends(database_session)):
        from services import media_analysis
        return media_analysis.status(session, found(session, MediaFile, media_id))

    @router.post('/api/downloads/quick-reviews/{batch_id}/prefetch', dependencies=[Depends(local)])
    def quick_prefetch(batch_id: str, data: QuickBatchInput, session=Depends(database_session)):
        return quick_reviews.prefetch(session, batch_id, data.skip_media_ids, data.episode_ids)

    @router.post("/api/downloads/media/{media_id}/review", dependencies=[Depends(local)])
    def manual(media_id: int, data: ManualInput, session=Depends(database_session)):
        service.check_media(session, found(session, MediaFile, media_id), data.model_dump())
        return {"message": "人工抽检结果已保存"}

    @router.get("/api/downloads/media/{media_id}/file")
    def media_file(media_id: int, session=Depends(database_session)):
        media = found(session, MediaFile, media_id)
        path = service.delivery_path(session, media)
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    @router.get("/api/downloads/media/{media_id}/download")
    def media_download(media_id: int, session=Depends(database_session)):
        media = found(session, MediaFile, media_id)
        path = service.delivery_path(session, media)
        return FileResponse(path, filename=service.safe_name(path.stem) + path.suffix, content_disposition_type="attachment", media_type="video/mp4")

    @router.get("/api/downloads/catalogs/{catalog_id}/cover")
    def cover(catalog_id: int, session=Depends(database_session)):
        catalog = found(session, DownloadCatalog, catalog_id)
        if catalog.ranking_id is None:
            from services.site import manual_detail
            try:
                if not manual_detail(session, catalog)["catalogs"][0]["episodes"]:
                    raise ValueError("未公开")
            except ValueError:
                raise HTTPException(404, "封面尚未公开")
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
