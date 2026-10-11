"""
GeoMeasure — ORM Models

Two tables:
  FileRecord   — one row per uploaded file (metadata + processing status).
  FeatureResult — one row per geospatial feature inside a file.

JSON columns store geometry, properties, and measurement provenance so the
schema stays simple while remaining flexible enough to support arbitrary
feature attributes and geometry types.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.models.database import Base


class FileRecord(Base):
    """Metadata for a single uploaded geospatial file."""

    __tablename__ = "file_records"

    id = Column(String(36), primary_key=True)          # UUID string
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)     # "kml" or "shapefile"
    status = Column(String(20), nullable=False, default="PENDING")
    # Status values: PENDING | PROCESSING | COMPLETED | FAILED

    source_crs = Column(String(100), nullable=True)    # e.g., "EPSG:4326"
    feature_count = Column(Integer, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)

    # Structured error details (JSON-encoded string)
    error_detail = Column(Text, nullable=True)

    # Validation-summary JSON (missing CRS, invalid geoms, etc.)
    validation_summary = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at = Column(DateTime, nullable=True)

    # One-to-many: a file has many features
    features = relationship(
        "FeatureResult",
        back_populates="file_record",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def get_validation_summary(self) -> dict | None:
        if self.validation_summary:
            return json.loads(self.validation_summary)
        return None

    def set_validation_summary(self, summary: dict) -> None:
        self.validation_summary = json.dumps(summary)

    def get_error_detail(self) -> dict | None:
        if self.error_detail:
            return json.loads(self.error_detail)
        return None

    def set_error_detail(self, detail: dict) -> None:
        self.error_detail = json.dumps(detail)


class FeatureResult(Base):
    """
    Measurement result for a single geospatial feature.

    Geometry and properties are stored as JSON text so that arbitrary
    attribute sets and geometry objects can be persisted without a
    rigid schema for every possible file structure.
    """

    __tablename__ = "feature_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(
        String(36),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Feature identity ──────────────────────────────────────────────────────
    feature_index = Column(Integer, nullable=False)    # 0-based row index
    feature_id = Column(String(255), nullable=True)    # From source data if present
    geometry_type = Column(String(50), nullable=True)  # e.g., "Polygon"

    # ── Serialised data (stored as JSON text) ─────────────────────────────────
    geometry_json = Column(Text, nullable=True)        # GeoJSON geometry dict
    properties_json = Column(Text, nullable=True)      # Feature attribute dict

    # ── Measurement results ───────────────────────────────────────────────────
    measurement_type = Column(String(20), nullable=True)   # "area" | "length" | "n/a" | "unsupported"
    measurement_value = Column(Float, nullable=True)       # Full-precision internal value
    measurement_unit = Column(String(20), nullable=True)   # "sq_m" | "m"
    measurement_status = Column(String(20), nullable=False, default="PENDING")
    # Status: MEASURED | NOT_APPLICABLE | UNSUPPORTED | FAILED

    # ── CRS provenance ────────────────────────────────────────────────────────
    source_crs = Column(String(100), nullable=True)
    measurement_crs = Column(String(100), nullable=True)
    crs_method = Column(String(255), nullable=True)    # Human-readable explanation

    # ── Relationship ──────────────────────────────────────────────────────────
    file_record = relationship("FileRecord", back_populates="features")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def get_geometry(self) -> dict | None:
        if self.geometry_json:
            return json.loads(self.geometry_json)
        return None

    def set_geometry(self, geom_dict: dict) -> None:
        self.geometry_json = json.dumps(geom_dict)

    def get_properties(self) -> dict:
        if self.properties_json:
            return json.loads(self.properties_json)
        return {}

    def set_properties(self, props: dict) -> None:
        self.properties_json = json.dumps(props, default=str)
