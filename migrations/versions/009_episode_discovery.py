"""Private episode discovery facts and durable coordinators; no historical backfill."""
from alembic import op
import sqlalchemy as sa

revision = "009_episode_discovery"
down_revision = "008_candidate_pool"
branch_labels = None
depends_on = None

TABLES = ("discovery_tasks", "discovery_sources", "discovery_videos", "discovery_observations",
          "discovery_matches", "discovery_subscriptions")


def upgrade():
    # Frozen schema, independent of future ORM model edits. No old table is rebuilt.
    def col(name, type_, *args, **kwargs):
        return sa.Column(name, type_, *args, nullable=False, **kwargs)
    def identifier():
        return col('id',sa.Integer(),primary_key=True)
    def fk(name, table):
        return col(name,sa.Integer(),sa.ForeignKey(table+'.id'))
    op.create_table('discovery_tasks',identifier(),fk('series_id','series'),
        col('status',sa.String(30)),col('round',sa.Integer()),col('config',sa.Text()),
        col('progress',sa.Text()),col('message',sa.Text()),col('owner',sa.String(64)),
        sa.Column('lease_until',sa.DateTime(),nullable=True),
        sa.Column('heartbeat_at',sa.DateTime(),nullable=True),
        sa.Column('started_at',sa.DateTime(),nullable=True),col('updated_at',sa.DateTime()),
        sa.UniqueConstraint('series_id'))
    op.create_index('ix_discovery_tasks_status','discovery_tasks',['status'])
    op.create_table('discovery_sources',identifier(),fk('task_id','discovery_tasks'),
        col('provider',sa.String()),col('revision',sa.String()),col('kind',sa.String(20)),
        col('mode',sa.String(20)),col('object_key',sa.Text()),col('cursor',sa.Text()),
        col('ended',sa.Boolean()),col('state',sa.String()),col('progress',sa.Text()),
        sa.UniqueConstraint('task_id','kind','mode','object_key'))
    op.create_table('discovery_videos',identifier(),col('platform',sa.String()),
        col('video_id',sa.String(100)),col('metadata_json',sa.Text()),col('discovered_at',sa.DateTime()),
        sa.UniqueConstraint('platform','video_id'))
    op.create_table('discovery_observations',identifier(),fk('source_id','discovery_sources'),
        col('round',sa.Integer()),col('cursor',sa.Text()),fk('video_id','discovery_videos'),
        col('metadata_json',sa.Text()),col('observed_at',sa.DateTime()),
        sa.UniqueConstraint('source_id','round','cursor','video_id'))
    op.create_table('discovery_matches',identifier(),fk('series_id','series'),fk('video_id','discovery_videos'),
        col('kind',sa.String(30)),col('basis',sa.Text()),col('context',sa.String(64)),
        sa.UniqueConstraint('series_id','video_id'))
    op.create_table('discovery_subscriptions',identifier(),fk('task_id','discovery_tasks'),
        fk('ranking_id','ranking_runs'),fk('series_id','series'),fk('catalog_id','download_catalogs'),
        col('active',sa.Boolean()),col('message',sa.Text()),sa.UniqueConstraint('ranking_id','series_id'))


def downgrade():
    raise RuntimeError("补集记录包含人工衔接依据；请按兼容回退说明保留009及业务数据")
