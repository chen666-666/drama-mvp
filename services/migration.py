"""Safe, additive SQLite migrations, including verification on a database copy."""
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
REVISION = "010_episode_ranges"


def _upgrade(engine):
    config = Config(str(ROOT / "alembic.ini"))
    # SQLite must disable FK enforcement outside a transaction when rebuilding a
    # referenced table. Check every relationship before committing the upgrade.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("迁移后的外键检查失败，请保留备份并检查数据库")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def upgrade_database(engine):
    with engine.connect() as connection:
        if "alembic_version" in inspect(connection).get_table_names():
            if connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION:
                return
    path = Path(engine.url.database)
    if path.exists() and path.stat().st_size:
        backups = path.parent / "data" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"{path.stem}_{stamp}_before_top3.db"
        with closing(sqlite3.connect(str(path))) as source, closing(sqlite3.connect(str(backup))) as target:
            source.backup(target)
        with tempfile.TemporaryDirectory(dir=backups) as directory:
            preview = Path(directory) / "preview.db"
            with closing(sqlite3.connect(str(backup))) as source, closing(sqlite3.connect(str(preview))) as target:
                source.backup(target)
                has_legacy = source.execute("SELECT count(*) FROM sqlite_master WHERE name='dramas'").fetchone()[0]
                legacy = source.execute("SELECT * FROM dramas ORDER BY id").fetchall() if has_legacy else []
                # Verify every pre-existing business column, including snapshots and human conclusions.
                previous = {}
                for (table,) in source.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    if table.startswith('sqlite_') or table == 'alembic_version':
                        continue
                    quoted = '"' + table.replace('"', '""') + '"'
                    columns = [row[1] for row in source.execute(f'PRAGMA table_info({quoted})')]
                    names = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
                    previous[quoted] = (names, source.execute(f'SELECT {names} FROM {quoted} ORDER BY rowid').fetchall())
            preview_engine = create_engine(f"sqlite:///{preview.as_posix()}")
            try:
                _upgrade(preview_engine)
                with closing(sqlite3.connect(str(preview))) as checked:
                    if checked.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("迁移副本完整性检查失败，尚未升级原数据库")
                    if checked.execute("SELECT * FROM dramas ORDER BY id").fetchall() != legacy:
                        raise RuntimeError("迁移副本中的旧资料不一致，尚未升级原数据库")
                    for table, (names, rows) in previous.items():
                        if checked.execute(f'SELECT {names} FROM {table} ORDER BY rowid').fetchall() != rows:
                            raise RuntimeError("迁移副本中的既有业务字段不一致，尚未升级原数据库")
            finally:
                preview_engine.dispose()
    _upgrade(engine)
