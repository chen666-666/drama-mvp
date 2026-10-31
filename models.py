"""Database models for short dramas."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("ranking_id", "series_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_id: Mapped[int] = mapped_column(ForeignKey("ranking_runs.id"))
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"))
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
    source_duration: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default="pending")
    access: Mapped[str] = mapped_column(default="unknown")
    note: Mapped[str] = mapped_column(Text, default="")
    evidence_url: Mapped[str] = mapped_column(Text, default="")
    review_history: Mapped[str] = mapped_column(Text, default="[]")


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


class ExportRun(Base):
    __tablename__ = "export_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_id: Mapped[int] = mapped_column(ForeignKey("ranking_runs.id"))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    relative_path: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
