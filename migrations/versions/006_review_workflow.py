"""Private, resumable human review workflows; existing business rows are untouched."""
from alembic import op
import sqlalchemy as sa

revision = "006_review_workflow"
down_revision = "005_manual_collection"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("review_workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_key", sa.String(64), nullable=False, unique=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("collection_runs.id"), nullable=False),
        sa.Column("ranking_id", sa.Integer(), sa.ForeignKey("ranking_runs.id")),
        sa.Column("search_job_id", sa.Integer(), sa.ForeignKey("search_jobs.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))


def downgrade():
    raise RuntimeError("请恢复迁移前备份，保留流程与人工核验记录")
