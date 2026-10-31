"""Persistent second-step catalogs, queue, media checks and exports."""
from alembic import op

revision = "004_downloads"
down_revision = "003_metadata"
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE download_catalogs (id INTEGER PRIMARY KEY, ranking_id INTEGER NOT NULL REFERENCES ranking_runs(id),
        series_id INTEGER NOT NULL REFERENCES series(id), cursor TEXT NOT NULL, source TEXT NOT NULL, has_more BOOLEAN NOT NULL,
        checked BOOLEAN NOT NULL, total_episodes INTEGER, note TEXT NOT NULL, cover_url TEXT NOT NULL, updated_at DATETIME NOT NULL,
        UNIQUE(ranking_id,series_id))""",
        """CREATE TABLE download_episodes (id INTEGER PRIMARY KEY, catalog_id INTEGER NOT NULL REFERENCES download_catalogs(id),
        video_id VARCHAR(100) NOT NULL, title TEXT NOT NULL, original_url TEXT NOT NULL, episode_number INTEGER,
        source_duration FLOAT, status VARCHAR NOT NULL, access VARCHAR NOT NULL, note TEXT NOT NULL, evidence_url TEXT NOT NULL,
        review_history TEXT NOT NULL, UNIQUE(catalog_id,video_id))""",
        """CREATE TABLE download_jobs (id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL UNIQUE REFERENCES download_episodes(id),
        status VARCHAR NOT NULL, trial BOOLEAN NOT NULL, attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
        message TEXT NOT NULL, tool TEXT NOT NULL, bytes_downloaded INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""",
        "CREATE INDEX ix_download_jobs_status ON download_jobs(status)",
        """CREATE TABLE media_files (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES download_jobs(id),
        relative_path TEXT NOT NULL, size INTEGER NOT NULL, duration FLOAT NOT NULL, format TEXT NOT NULL, sha256 VARCHAR(64) NOT NULL,
        tracks TEXT NOT NULL, validation_note TEXT NOT NULL, manual_status VARCHAR NOT NULL, manual_note TEXT NOT NULL,
        checked_at DATETIME, created_at DATETIME NOT NULL)""",
        """CREATE TABLE export_runs (id INTEGER PRIMARY KEY, ranking_id INTEGER NOT NULL REFERENCES ranking_runs(id),
        cutoff_at DATETIME NOT NULL, relative_path TEXT NOT NULL, summary TEXT NOT NULL)""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("请恢复迁移前备份，保留下载和核验记录")
