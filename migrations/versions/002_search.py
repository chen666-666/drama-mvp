"""Persistent automatic search jobs, separate from ranking and review state."""
from alembic import op
import sqlalchemy as sa

revision = "002_search"
down_revision = "001_top3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("search_jobs", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("run_id", sa.Integer(), sa.ForeignKey("collection_runs.id"), nullable=False),
                    sa.Column("status", sa.String(30), nullable=False), sa.Column("config", sa.Text(), nullable=False),
                    sa.Column("progress", sa.Text(), nullable=False), sa.Column("message", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_search_jobs_run", "search_jobs", ["run_id"])


def downgrade():
    raise RuntimeError("采集记录应保留，请使用备份恢复")
