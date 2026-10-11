"""
GeoMeasure — SQLAlchemy Database Setup

Uses SQLite for persistence (no external database server required).
The database file is created beside the geomeasure/ package directory.
All tables are created at application startup via create_all().
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


# ── Engine ───────────────────────────────────────────────────────────────────
# check_same_thread=False is required for SQLite when used with FastAPI because
# FastAPI can call the same session from multiple threads (e.g., sync routes in
# an async context). SQLAlchemy manages thread safety via the session factory.
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,   # Set True to log all SQL statements during debugging.
)

# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Declarative base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Shared base class for all ORM models."""


# ── Dependency injection helper ───────────────────────────────────────────────
def get_db():
    """
    FastAPI dependency that provides a SQLAlchemy session per request.
    Guarantees the session is closed even when an exception is raised.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
