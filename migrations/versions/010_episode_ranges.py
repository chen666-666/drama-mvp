"""Preserve explicit video episode ranges alongside single episode numbers."""
from alembic import op
import sqlalchemy as sa

revision = "010_episode_ranges"
down_revision = "009_episode_discovery"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("episodes", "download_episodes"):
        op.add_column(table, sa.Column("episode_range", sa.String(31), nullable=True))


def downgrade():
    raise RuntimeError("集号范围包含人工整理依据；请保留010及业务数据并回退兼容的应用")
