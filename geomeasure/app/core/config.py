"""
GeoMeasure — Application Configuration
Centralises all tuneable settings. Values can be overridden via environment
variables or a .env file (see .env.example at the project root).
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Application identity ────────────────────────────────────────────────
    APP_NAME: str = "GeoMeasure"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = (
        "Reliable Spatial Measurements. Traceable Geospatial Data.\n\n"
        "GeoMeasure is a geospatial file measurement and validation API that accepts "
        "KML files and zipped Shapefiles, extracts feature geometries, reprojects them "
        "to an appropriate coordinate system, and returns accurate area/length measurements "
        "with full measurement provenance."
    )

    # ── Storage paths ───────────────────────────────────────────────────────
    # Root of the project (two levels up from this file: app/core/ → app/ → geomeasure/)
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/geomeasure.db"

    # ── File-upload limits ──────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB limit (configurable via MAX_UPLOAD_SIZE_BYTES in .env)
    ALLOWED_EXTENSIONS: set[str] = {".kml", ".zip"}

    # ── Processing behaviour ────────────────────────────────────────────────
    # Area / length display rounding (decimal places for serialised output).
    # Full-precision values are stored internally; rounding is applied only to
    # the human-readable response fields.
    AREA_DISPLAY_DECIMALS: int = 4
    LENGTH_DISPLAY_DECIMALS: int = 4

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure the upload directory exists at import time so the app never
# has to check for it on every request.
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
