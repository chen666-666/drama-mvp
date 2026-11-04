"""Reference previously reviewed bytes without duplicating jobs or human decisions."""
from alembic import op
import sqlalchemy as sa

revision = "007_reused_media"
down_revision = "006_review_workflow"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("download_episodes") as batch:
        batch.add_column(sa.Column("reused_media_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reused_sha256", sa.String(64), nullable=False, server_default=""))
        batch.create_foreign_key("fk_episode_reused_media", "media_files", ["reused_media_id"], ["id"])


def downgrade():
    raise RuntimeError("请恢复迁移前备份，保留资源沿用及人工核验记录")
