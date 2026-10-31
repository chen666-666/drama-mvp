"""SQLite database configuration for the drama application."""

from pathlib import Path
import os
import threading
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


def init_db() -> None:
    """Back up, validate on a copy, then apply the versioned migration."""
    from services.migration import upgrade_database
    with _migration_lock:
        upgrade_database(engine)
