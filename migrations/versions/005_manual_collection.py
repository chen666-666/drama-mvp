"""Independent series catalogs, without creating or rewriting rankings."""
from alembic import op
import sqlalchemy as sa

revision = "005_manual_collection"
down_revision = "004_downloads"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("download_catalogs") as batch:
        batch.alter_column("ranking_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("published", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("source_url", sa.Text(), nullable=False, server_default=""))
    op.create_index("uq_manual_catalog_series", "download_catalogs", ["series_id"], unique=True,
                    sqlite_where=sa.text("ranking_id IS NULL"))


def downgrade():
    raise RuntimeError("请恢复迁移前备份，保留独立收录与下载记录")
