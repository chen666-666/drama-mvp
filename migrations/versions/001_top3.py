"""Add the first-step workflow without modifying legacy drama rows."""
from alembic import op
import sqlalchemy as sa

revision = "001_top3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL keeps this revision independent of future ORM changes.
    statements = [
        '''CREATE TABLE IF NOT EXISTS dramas (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL,
        cover_url VARCHAR(1000) NOT NULL, description TEXT NOT NULL, official_url VARCHAR(1000) NOT NULL,
        ai_summary TEXT NOT NULL, ai_score FLOAT NOT NULL, tags TEXT NOT NULL,
        category VARCHAR(100), created_at DATETIME NOT NULL)''',
        '''CREATE TABLE collection_runs (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
        mode VARCHAR(20) NOT NULL, cutoff_at DATETIME NOT NULL, window_hours INTEGER NOT NULL,
        keywords TEXT NOT NULL, rule_version VARCHAR NOT NULL, official_metadata TEXT NOT NULL,
        created_at DATETIME NOT NULL)''',
        '''CREATE TABLE authors (id INTEGER PRIMARY KEY, platform VARCHAR NOT NULL, account_id VARCHAR(255) NOT NULL,
        name VARCHAR(200) NOT NULL, homepage_url TEXT NOT NULL, UNIQUE(platform, account_id))''',
        '''CREATE TABLE series (id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL REFERENCES authors(id),
        series_key VARCHAR(255) NOT NULL, title VARCHAR(255) NOT NULL, collection_url TEXT NOT NULL,
        ai_type VARCHAR(100) NOT NULL, ai_basis TEXT NOT NULL, review_status VARCHAR NOT NULL, UNIQUE(author_id, series_key))''',
        '''CREATE TABLE episodes (id INTEGER PRIMARY KEY, platform VARCHAR NOT NULL, video_id VARCHAR(100) NOT NULL,
        series_id INTEGER REFERENCES series(id), title TEXT NOT NULL, original_url TEXT NOT NULL,
        episode_number INTEGER, published_at DATETIME, review_status VARCHAR NOT NULL, UNIQUE(platform, video_id))''',
        '''CREATE TABLE search_hits (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES collection_runs(id),
        episode_id INTEGER REFERENCES episodes(id), source_url TEXT NOT NULL, keyword TEXT NOT NULL,
        sort_order TEXT NOT NULL, position TEXT NOT NULL, searched_at DATETIME NOT NULL, official_rank INTEGER)''',
        '''CREATE TABLE metric_snapshots (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES collection_runs(id),
        episode_id INTEGER NOT NULL REFERENCES episodes(id), sampled_at DATETIME NOT NULL,
        likes INTEGER, comments INTEGER, favorites INTEGER, shares INTEGER, missing_reason TEXT NOT NULL, method VARCHAR NOT NULL)''',
        '''CREATE TABLE evidence (id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, note TEXT NOT NULL, captured_at DATETIME NOT NULL)''',
        '''CREATE TABLE review_records (id INTEGER PRIMARY KEY, target_type VARCHAR(20) NOT NULL, target_id INTEGER NOT NULL,
        status VARCHAR(20) NOT NULL, reason TEXT NOT NULL, checks TEXT NOT NULL, evidence_id INTEGER NOT NULL REFERENCES evidence(id),
        reviewed_at DATETIME NOT NULL)''',
        '''CREATE TABLE ranking_runs (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES collection_runs(id), version INTEGER NOT NULL,
        mode VARCHAR(20) NOT NULL, scoring_mode VARCHAR(20) NOT NULL, snapshot TEXT NOT NULL, created_at DATETIME NOT NULL, UNIQUE(run_id, version))''',
        '''CREATE TABLE ranking_items (id INTEGER PRIMARY KEY, ranking_id INTEGER NOT NULL REFERENCES ranking_runs(id),
        series_id INTEGER NOT NULL REFERENCES series(id), episode_id INTEGER NOT NULL REFERENCES episodes(id),
        metric_snapshot_id INTEGER REFERENCES metric_snapshots(id), position INTEGER NOT NULL, score INTEGER, detail TEXT NOT NULL,
        UNIQUE(ranking_id, series_id), UNIQUE(ranking_id, position))''',
        'CREATE INDEX ix_hits_run ON search_hits(run_id)',
        'CREATE INDEX ix_metrics_run_episode ON metric_snapshots(run_id, episode_id, sampled_at)',
    ]
    for statement in statements:
        op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError("为保留核验与榜单历史，此迁移不支持删除数据式降级；请恢复迁移前备份。")
