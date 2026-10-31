"""SQLite database configuration for the drama application."""

from pathlib import Path
import os
import threading
import time
from contextlib import contextmanager
from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DATABASE_PATH = Path(__file__).resolve().parent / "drama.db"
load_dotenv(DATABASE_PATH.parent / ".env")
DATABASE_URL = os.getenv("DRAMA_DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}")


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy models."""


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
from sqlalchemy import event


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
_migration_lock = threading.Lock()


@contextmanager
def migration_file_lock():
    if engine.url.database in (None, "", ":memory:"):
        yield
        return
    lock_path = Path(engine.url.database).resolve().with_suffix(".migration.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        for attempt in range(600):
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if attempt == 599:
                    raise RuntimeError("数据库升级正在进行，请稍后重试")
                time.sleep(.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def init_db() -> None:
    """Back up, validate on a copy, then apply the versioned migration."""
    from services.migration import upgrade_database
    with _migration_lock:
        with migration_file_lock():
            upgrade_database(engine)
