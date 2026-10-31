"""Editable display metadata and explicit single-video classification."""
from alembic import op
import sqlalchemy as sa

revision = "003_metadata"
down_revision = "002_search"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("series", sa.Column("title_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("episodes", sa.Column("content_kind", sa.String(20), nullable=False, server_default="unknown"))
    op.add_column("episodes", sa.Column("content_note", sa.Text(), nullable=False, server_default=""))


def downgrade():
    raise RuntimeError("请使用迁移前备份恢复，保留核验历史")
