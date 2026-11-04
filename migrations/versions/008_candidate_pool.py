"""Durable batch membership and current-batch refresh results, without rewriting history."""
from alembic import op
import sqlalchemy as sa

revision = "008_candidate_pool"
down_revision = "007_reused_media"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("run_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("collection_runs.id"), nullable=False),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("source_hit_id", sa.Integer(), sa.ForeignKey("search_hits.id"), nullable=False),
        sa.Column("metric_snapshot_id", sa.Integer(), sa.ForeignKey("metric_snapshots.id"), nullable=True),
        sa.Column("refresh_status", sa.String(20), nullable=False),
        sa.Column("refresh_note", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "episode_id", name="uq_run_candidate"))


def downgrade():
    raise RuntimeError("请保留候选来源与刷新记录，使用迁移前备份恢复")
