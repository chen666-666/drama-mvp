"""Database models for short dramas."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Drama(Base):
    """A short drama collected from an official viewing platform."""

    __tablename__ = "dramas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cover_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    official_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    ai_summary: Mapped[str] = mapped_column(Text, nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, nullable=False)
    tags: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=False,
    )


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20), default="sample")
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    window_hours: Mapped[int] = mapped_column(default=168)
    keywords: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(default="public-interactions-v1")
    official_metadata: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (UniqueConstraint("platform", "account_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(default="douyin")
    account_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    homepage_url: Mapped[str] = mapped_column(Text, default="")


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("author_id", "series_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    series_key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    collection_url: Mapped[str] = mapped_column(Text, default="")
    title_note: Mapped[str] = mapped_column(Text, default="")
    ai_type: Mapped[str] = mapped_column(String(100), default="")
    ai_basis: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(default="pending")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("platform", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(default="douyin")
    video_id: Mapped[str] = mapped_column(String(100))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    original_url: Mapped[str] = mapped_column(Text)
    episode_number: Mapped[int | None] = mapped_column(nullable=True)
    episode_range: Mapped[str | None] = mapped_column(String(31), nullable=True)
    content_kind: Mapped[str] = mapped_column(String(20), default="unknown")
    content_note: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_status: Mapped[str] = mapped_column(default="pending")


class SearchHit(Base):
    __tablename__ = "search_hits"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    keyword: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[str] = mapped_column(Text, default="")
    searched_at: Mapped[datetime] = mapped_column(DateTime)
    official_rank: Mapped[int | None] = mapped_column(nullable=True)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    sampled_at: Mapped[datetime] = mapped_column(DateTime)
    likes: Mapped[int | None] = mapped_column(nullable=True)
    comments: Mapped[int | None] = mapped_column(nullable=True)
    favorites: Mapped[int | None] = mapped_column(nullable=True)
    shares: Mapped[int | None] = mapped_column(nullable=True)
    missing_reason: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(default="manual")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime)


class ReviewRecord(Base):
    __tablename__ = "review_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    checks: Mapped[str] = mapped_column(Text)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime)


class RankingRun(Base):
    __tablename__ = "ranking_runs"
    __table_args__ = (UniqueConstraint("run_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    version: Mapped[int] = mapped_column()
    mode: Mapped[str] = mapped_column(String(20))
    scoring_mode: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RankingItem(Base):
    __tablename__ = "ranking_items"
    __table_args__ = (UniqueConstraint("ranking_id", "series_id"), UniqueConstraint("ranking_id", "position"))
    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_id: Mapped[int] = mapped_column(ForeignKey("ranking_runs.id"))
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    metric_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("metric_snapshots.id"), nullable=True)
    position: Mapped[int] = mapped_column()
    score: Mapped[int | None] = mapped_column(nullable=True)
    detail: Mapped[str] = mapped_column(Text)


class RunCandidate(Base):
    """Explicit carry-over membership; never pretend an old hit is a new search."""
    __tablename__ = "run_candidates"
    __table_args__ = (UniqueConstraint("run_id", "episode_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    source_hit_id: Mapped[int] = mapped_column(ForeignKey("search_hits.id"))
    metric_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("metric_snapshots.id"), nullable=True)
    refresh_status: Mapped[str] = mapped_column(String(20), default="pending")
    refresh_note: Mapped[str] = mapped_column(Text, default="等待更新本批次互动")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class SearchJob(Base):
    __tablename__ = "search_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    config: Mapped[str] = mapped_column(Text)
    progress: Mapped[str] = mapped_column(Text, default="{}")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class DownloadCatalog(Base):
    __tablename__ = "download_catalogs"
    __table_args__ = (UniqueConstraint("ranking_id", "series_id"),
                     Index("uq_manual_catalog_series", "series_id", unique=True, sqlite_where=text("ranking_id IS NULL")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_id: Mapped[int | None] = mapped_column(ForeignKey("ranking_runs.id"), nullable=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
    published: Mapped[bool] = mapped_column(default=False, server_default="0")
    source_url: Mapped[str] = mapped_column(Text, default="", server_default="")
    cursor: Mapped[str] = mapped_column(Text, default="0")
    source: Mapped[str] = mapped_column(Text, default="")
    has_more: Mapped[bool] = mapped_column(default=True)
    checked: Mapped[bool] = mapped_column(default=False)
    total_episodes: Mapped[int | None] = mapped_column(nullable=True)
    note: Mapped[str] = mapped_column(Text, default="公开总集数未知，目录待核对")
    cover_url: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DownloadEpisode(Base):
    __tablename__ = "download_episodes"
    __table_args__ = (UniqueConstraint("catalog_id", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("download_catalogs.id"))
    video_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    episode_number: Mapped[int | None] = mapped_column(nullable=True)
    episode_range: Mapped[str | None] = mapped_column(String(31), nullable=True)
    source_duration: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default="pending")
    access: Mapped[str] = mapped_column(default="unknown")
    note: Mapped[str] = mapped_column(Text, default="")
    evidence_url: Mapped[str] = mapped_column(Text, default="")
    review_history: Mapped[str] = mapped_column(Text, default="[]")
    reused_media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id"), nullable=True)
    reused_sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    __table_args__ = (UniqueConstraint("episode_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("download_episodes.id"))
    status: Mapped[str] = mapped_column(default="queued", index=True)
    trial: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    message: Mapped[str] = mapped_column(Text, default="等待下载工作进程")
    tool: Mapped[str] = mapped_column(Text, default="")
    bytes_downloaded: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (UniqueConstraint("job_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("download_jobs.id"))
    relative_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column()
    duration: Mapped[float] = mapped_column()
    format: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    tracks: Mapped[str] = mapped_column(Text)
    validation_note: Mapped[str] = mapped_column(Text)
    manual_status: Mapped[str] = mapped_column(default="pending")
    manual_note: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ReviewWorkflow(Base):
    __tablename__ = "review_workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(64), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"))
    ranking_id: Mapped[int | None] = mapped_column(ForeignKey("ranking_runs.id"), nullable=True)
    search_job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    stage: Mapped[str] = mapped_column(String(30), default="review")
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ExportRun(Base):
    __tablename__ = "export_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_id: Mapped[int] = mapped_column(ForeignKey("ranking_runs.id"))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    relative_path: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)


class DiscoveryTask(Base):
    """One durable coordinator per stable series; rounds share historical cursors."""
    __tablename__ = "discovery_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    round: Mapped[int] = mapped_column(default=1)
    config: Mapped[str] = mapped_column(Text, default="{}")
    progress: Mapped[str] = mapped_column(Text, default="{}")
    message: Mapped[str] = mapped_column(Text, default="等待补集工作进程")
    owner: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DiscoverySource(Base):
    __tablename__ = "discovery_sources"
    __table_args__ = (UniqueConstraint("task_id", "kind", "mode", "object_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("discovery_tasks.id"))
    provider: Mapped[str] = mapped_column(default="DouK")
    revision: Mapped[str] = mapped_column(default="e327b9549d284a8b1613566f949fe2dbb8a6c2a1")
    kind: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(Text)
    cursor: Mapped[str] = mapped_column(Text, default="0")
    ended: Mapped[bool] = mapped_column(default=False)
    state: Mapped[str] = mapped_column(default="pending")
    progress: Mapped[str] = mapped_column(Text, default="{}")


class DiscoveryVideo(Base):
    __tablename__ = "discovery_videos"
    __table_args__ = (UniqueConstraint("platform", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(default="douyin")
    video_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[str] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DiscoveryObservation(Base):
    __tablename__ = "discovery_observations"
    __table_args__ = (UniqueConstraint("source_id", "round", "cursor", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("discovery_sources.id"))
    round: Mapped[int] = mapped_column()
    cursor: Mapped[str] = mapped_column(Text)
    video_id: Mapped[int] = mapped_column(ForeignKey("discovery_videos.id"))
    metadata_json: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DiscoveryMatch(Base):
    __tablename__ = "discovery_matches"
    __table_args__ = (UniqueConstraint("series_id", "video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
    video_id: Mapped[int] = mapped_column(ForeignKey("discovery_videos.id"))
    kind: Mapped[str] = mapped_column(String(30))
    basis: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(String(64))


class DiscoverySubscription(Base):
    __tablename__ = "discovery_subscriptions"
    __table_args__ = (UniqueConstraint("ranking_id", "series_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("discovery_tasks.id"))
    ranking_id: Mapped[int] = mapped_column(ForeignKey("ranking_runs.id"))
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
    catalog_id: Mapped[int] = mapped_column(ForeignKey("download_catalogs.id"))
    active: Mapped[bool] = mapped_column(default=True)
    message: Mapped[str] = mapped_column(Text, default="待核验候选不会自动准备或公开")
