"""Local short-drama library with ranking and durable downloads."""
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import SessionLocal, init_db
from models import CollectionRun, Episode, Evidence, RankingRun, ReviewRecord, SearchJob, Series
from services.common import KEYWORDS, beijing, iso, parse_time, public_url, utcnow
from services.export import TEMPLATES, csv_text, export_zip, report_html
from services.ranking import candidates, make_ranking
from services.review import merge_series, review
from services.metadata import update_metadata
from sources.manual import CSV_FIELDS, import_csv, import_links
from sources.douk import DouKSource, SESSION_PATH, save_session, session_status
from services.search import dispatch, job_dict, prepare_job, prepare_resume, recover_jobs

ROOT = Path(__file__).resolve().parent
search_start_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    init_db()
    recover_jobs()
    yield


app = FastAPI(title="抖音 AI 短剧 Top3 工作台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def database_session():
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@app.exception_handler(ValueError)
async def bad_input(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(IntegrityError)
async def conflict(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "记录发生冲突，请刷新后重试；请勿同时生成同一批次的榜单。"})


def get_run(session, run_id):
    run = session.get(CollectionRun, run_id)
    if run is None:
        raise HTTPException(404, "采集批次不存在")
    return run


def run_dict(run):
    return {"id": run.id, "name": run.name, "mode": run.mode, "cutoff_at": iso(run.cutoff_at),
            "cutoff_at_beijing": beijing(run.cutoff_at), "window_start": iso(run.cutoff_at - timedelta(hours=run.window_hours)),
            "window_hours": run.window_hours, "keywords": json.loads(run.keywords), "rule_version": run.rule_version,
            "official_metadata": json.loads(run.official_metadata)}


class RunInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cutoff_at: str
    mode: str = "sample"
    keywords: list[str] = Field(default_factory=lambda: KEYWORDS.copy(), max_length=30)


class CSVInput(BaseModel):
    content: str = Field(max_length=5_000_000)


class SearchInput(BaseModel):
    sorts: list[str] = Field(default_factory=lambda: ["general", "likes", "latest"], max_length=3)
    pages: int = Field(default=1, ge=1, le=3)


def local_write(request):
    """Login data and collection control are accepted only by the local workbench."""
    allowed = ("127.0.0.1", "localhost", "::1", "testserver")
    if request.url.hostname not in allowed or request.client and request.client.host not in allowed + ("testclient",):
        raise HTTPException(403, "请在本机工作台操作")
    origin = request.headers.get("origin")
    if origin and (urlparse(origin).hostname not in allowed or
                   urlparse(origin).netloc != request.url.netloc):
        raise HTTPException(403, "请从当前工作台页面操作")


@app.get("/api/source/douk/status")
def source_status():
    return {**DouKSource().health(), "session": session_status()}


@app.post("/api/source/douk/session")
async def configure_session(request: Request):
    local_write(request)
    # Parse explicitly so validation failures never echo a credential value.
    if int(request.headers.get("content-length", "0")) > 60000:
        raise HTTPException(400, "登录配置过长")
    try:
        body = await request.json()
        cookie = body.get("cookie") if isinstance(body, dict) else None
        if not isinstance(cookie, str) or len(cookie) > 50000:
            raise ValueError("登录配置格式无效")
        save_session(cookie)
    except (ValueError, TypeError):
        raise HTTPException(400, "请粘贴请求头中的完整 Cookie 值；内容只保存在本机")
    return session_status()


@app.delete("/api/source/douk/session")
def clear_session(request: Request):
    local_write(request)
    SESSION_PATH.unlink(missing_ok=True)
    return session_status()


@app.get("/api/runs/{run_id}/search-jobs")
def search_jobs(run_id: int, session=Depends(database_session)):
    get_run(session, run_id)
    return [job_dict(job) for job in session.scalars(select(SearchJob).where(SearchJob.run_id == run_id).order_by(SearchJob.id.desc()))]


@app.post("/api/runs/{run_id}/search-jobs", status_code=201)
def start_search(run_id: int, data: SearchInput, request: Request, session=Depends(database_session)):
    local_write(request)
    with search_start_lock:
        job = prepare_job(session, get_run(session, run_id), data.sorts, data.pages)
        result = job_dict(job)
        session.commit()
        dispatch(result["id"])
    return result


@app.post("/api/search-jobs/{job_id}/resume")
def resume_search(job_id: int, request: Request, session=Depends(database_session)):
    local_write(request)
    with search_start_lock:
        job = session.get(SearchJob, job_id)
        if not job:
            raise HTTPException(404, "搜索任务不存在")
        prepare_resume(session, job)
        result = job_dict(job)
        session.commit()
        dispatch(result["id"])
    return result


class LinksInput(BaseModel):
    links: str = Field(max_length=100_000)
    keyword: str = ""
    sort_order: str = ""
    position: str = ""
    source_url: str = ""
    searched_at: str = ""


class ReviewInput(BaseModel):
    target_type: str
    target_id: int
    status: str
    reason: str = Field(max_length=10_000)
    evidence_url: str
    checks: dict[str, bool] = Field(default_factory=dict)
    ai_type: str = ""
    ai_basis: str = Field(default="", max_length=10_000)


class MergeInput(BaseModel):
    source_id: int
    target_id: int
    reason: str
    evidence_url: str


class AssignInput(BaseModel):
    series_id: int
    reason: str
    evidence_url: str


class SeriesMetadataInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    title_note: str = Field(default="", max_length=5000)
    ai_type: str = Field(default="", max_length=100)
    ai_basis: str = Field(default="", max_length=10000)
    reason: str = Field(min_length=1, max_length=10000)
    evidence_url: str


class EpisodeMetadataInput(BaseModel):
    episode_number: int | None = Field(default=None, ge=1)
    content_kind: str = "unknown"
    content_note: str = Field(default="", max_length=5000)
    reason: str = Field(min_length=1, max_length=10000)
    evidence_url: str


class OfficialInput(BaseModel):
    publisher: str
    category: str
    source_url: str
    updated_at: str
    period_start: str
    period_end: str
    metric: str
    evidence_note: str
    confirmed: bool


@app.get("/", response_class=HTMLResponse)
def home():
    return library_page()


@app.get("/workbench", response_class=HTMLResponse)
def workbench():
    return TEMPLATES.get_template("index.html").render()


@app.get("/legacy", response_class=HTMLResponse)
def legacy():
    return library_page("hongguo")


def library_page(active="all"):
    from models import Drama
    from legacy import parse_tags
    with SessionLocal() as session:
        dramas = session.scalars(select(Drama).order_by(Drama.id)).all()
    return TEMPLATES.get_template("library.html").render(dramas=[{"drama": row, "tags": parse_tags(row.tags)} for row in dramas], active=active)


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page():
    return TEMPLATES.get_template("downloads.html").render()


@app.get("/health")
def health():
    return {"status": "ok", "phase": "downloads"}


@app.get("/api/runs")
def list_runs(session=Depends(database_session)):
    return [run_dict(run) for run in session.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()))]


@app.post("/api/runs", status_code=201)
def create_run(data: RunInput, session=Depends(database_session)):
    if data.mode not in ("sample", "official") or not data.name.strip():
        raise ValueError("请填写批次名称并选择样本榜或官方榜")
    cutoff = parse_time(data.cutoff_at, "截止时间")
    if cutoff > utcnow():
        raise ValueError("截止时间不能在未来")
    keywords = list(dict.fromkeys(word.strip() for word in data.keywords if word.strip()))
    if not keywords:
        raise ValueError("请填写检索关键词")
    run = CollectionRun(name=data.name.strip(), mode=data.mode, cutoff_at=cutoff, window_hours=168,
                        keywords=json.dumps(keywords, ensure_ascii=False), rule_version="public-interactions-v1", official_metadata="{}")
    session.add(run)
    session.flush()
    return run_dict(run)


@app.get("/api/runs/{run_id}/candidates")
def list_candidates(run_id: int, session=Depends(database_session)):
    return candidates(session, get_run(session, run_id))


@app.get("/api/import-template.csv")
def template():
    return Response(csv_text([], CSV_FIELDS), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="douyin-candidates-template.csv"'})


@app.post("/api/runs/{run_id}/import/csv")
def csv_import(run_id: int, data: CSVInput, session=Depends(database_session)):
    get_run(session, run_id)
    return import_csv(session, run_id, data.content)


@app.post("/api/runs/{run_id}/import/links")
def link_import(run_id: int, data: LinksInput, session=Depends(database_session)):
    get_run(session, run_id)
    return import_links(session, run_id, data.links, data.model_dump(exclude={"links"}))


@app.post("/api/reviews")
def save_review(data: ReviewInput, session=Depends(database_session)):
    return review(session, data.model_dump())


@app.post("/api/series/merge")
def merge(data: MergeInput, session=Depends(database_session)):
    return merge_series(session, data.source_id, data.target_id, data.reason, data.evidence_url)


@app.post("/api/series/{series_id}/metadata")
def series_metadata(series_id: int, data: SeriesMetadataInput, session=Depends(database_session)):
    return update_metadata(session, "series", series_id, data.model_dump())


@app.post("/api/episodes/{episode_id}/metadata")
def episode_metadata(episode_id: int, data: EpisodeMetadataInput, session=Depends(database_session)):
    return update_metadata(session, "episode", episode_id, data.model_dump())


@app.post("/api/episodes/{episode_id}/assign")
def assign_episode(episode_id: int, data: AssignInput, session=Depends(database_session)):
    episode, series = session.get(Episode, episode_id), session.get(Series, data.series_id)
    if not episode or not series or not data.reason.strip():
        raise ValueError("请选择有效正片、目标作品并填写纠正依据")
    evidence = Evidence(source_url=public_url(data.evidence_url), note=data.reason, captured_at=utcnow())
    session.add(evidence)
    session.flush()
    old_series_id = episode.series_id
    episode.series_id, episode.review_status = series.id, "pending"
    session.add(ReviewRecord(target_type="episode", target_id=episode.id, status="reassigned", reason=data.reason,
                             checks=json.dumps({"from_series_id": old_series_id, "to_series_id": series.id}),
                             evidence_id=evidence.id, reviewed_at=utcnow()))
    return {"message": "归属已纠正，请重新核验该正片"}


@app.post("/api/runs/{run_id}/official-verification")
def official_verification(run_id: int, data: OfficialInput, session=Depends(database_session)):
    run = get_run(session, run_id)
    if run.mode != "official":
        raise ValueError("样本批次不能改为官方榜，请创建独立批次")
    if not data.confirmed or not all(value.strip() for value in (data.publisher, data.category, data.metric, data.evidence_note)):
        raise ValueError("官方来源各项信息均须填写并人工确认")
    metadata = data.model_dump()
    metadata["source_url"] = public_url(data.source_url)
    start, end, updated = (parse_time(value) for value in (data.period_start, data.period_end, data.updated_at))
    if start > end or end > updated or updated > run.cutoff_at:
        raise ValueError("应满足 周期开始 ≤ 周期结束 ≤ 更新时间 ≤ 批次截止时间")
    metadata.update(period_start=iso(start), period_end=iso(end), updated_at=iso(updated), verified=True, verified_at=iso(utcnow()))
    run.official_metadata = json.dumps(metadata, ensure_ascii=False)
    evidence = Evidence(source_url=metadata["source_url"], note=data.evidence_note, captured_at=utcnow())
    session.add(evidence)
    session.flush()
    session.add(ReviewRecord(target_type="run", target_id=run.id, status="approved", reason=data.evidence_note,
                             checks=json.dumps(metadata, ensure_ascii=False), evidence_id=evidence.id, reviewed_at=utcnow()))
    return metadata


@app.get("/api/runs/{run_id}/rankings")
def rankings(run_id: int, session=Depends(database_session)):
    get_run(session, run_id)
    return [{"id": row.id, "version": row.version, "scoring_mode": row.scoring_mode, "created_at_beijing": beijing(row.created_at)}
            for row in session.scalars(select(RankingRun).where(RankingRun.run_id == run_id).order_by(RankingRun.version.desc()))]


@app.post("/api/runs/{run_id}/rankings", status_code=201)
def generate_ranking(run_id: int, session=Depends(database_session)):
    return make_ranking(session, get_run(session, run_id))


def get_ranking(session, ranking_id):
    row = session.get(RankingRun, ranking_id)
    if row is None:
        raise HTTPException(404, "榜单版本不存在")
    return {"id": row.id, **json.loads(row.snapshot)}


@app.get("/api/rankings/{ranking_id}")
def ranking_detail(ranking_id: int, session=Depends(database_session)):
    return get_ranking(session, ranking_id)


@app.get("/rankings/{ranking_id}/report", response_class=HTMLResponse)
def ranking_report(ranking_id: int, session=Depends(database_session)):
    return report_html(get_ranking(session, ranking_id))


@app.get("/api/rankings/{ranking_id}/export")
def download_export(ranking_id: int, session=Depends(database_session)):
    return Response(export_zip(get_ranking(session, ranking_id)), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="douyin-top3-ranking-{ranking_id}.zip"'})


from api_downloads import router_for
app.include_router(router_for(database_session, local_write))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
