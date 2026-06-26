"""
database.py
-----------
SQLAlchemy engine, session factory, and the declarative Base.

We use a single SQLite file (gatepass.db) in the project root. SQLite needs
`check_same_thread=False` so that the connection can be shared across FastAPI's
worker threads. For a small on-premise gatehouse app this is more than enough.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Database file lives next to the project (…/gatepass_system/gatepass.db)
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = f"sqlite:///{BASE_DIR / 'gatepass.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + threads
)

# autoflush/autocommit off: we control transactions explicitly in each request.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call repeatedly (no-op if they exist)."""
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
