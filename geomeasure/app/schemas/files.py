"""
GeoMeasure — Pydantic Response Schemas

All API responses are typed via these schemas so that:
  • FastAPI generates accurate OpenAPI docs.
  • Clients receive consistent, documented JSON.
  • Internal model fields (e.g. raw DB columns) are never accidentally leaked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Shared primitives ─────────────────────────────────────────────────────────

class ValidationIssue(BaseModel):
    """A single issue identified during file-level validation."""
    code: str = Field(..., description="Machine-readable issue code.")
    detail: str = Field(..., description="Human-readable description.")
    feature_index: int | None = Field(
        None, description="0-based feature index if issue is feature-specific."
    )


class ValidationSummary(BaseModel):
    """Aggregated validation findings for the uploaded file."""
    has_issues: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    missing_crs: bool = False
    invalid_geometry_count: int = 0
    unsupported_geometry_count: int = 0
    failed_measurement_count: int = 0


# ── Upload / File-info schemas ────────────────────────────────────────────────

class FileUploadResponse(BaseModel):
    """Returned immediately after a successful upload and processing."""
    id: str = Field(..., description="Unique file ID (UUID).")
    filename: str = Field(..., description="Original uploaded filename.")
    file_type: str = Field(..., description="Detected file type: 'kml' or 'shapefile'.")
    feature_count: int | None = Field(None, description="Number of features extracted.")
    crs: str | None = Field(None, description="Source CRS (e.g. 'EPSG:4326').")
    status: str = Field(..., description="Processing status: COMPLETED | FAILED.")
    file_size_bytes: int | None = Field(None, description="Size of uploaded file in bytes.")
    created_at: datetime = Field(..., description="UTC timestamp when the record was created.")
    validation_summary: ValidationSummary | None = None
    error_detail: dict | None = Field(None, description="Structured error info when status=FAILED.")

    model_config = {"from_attributes": True}


class FileInfoResponse(BaseModel):
    """Detailed file record returned by GET /api/files/{id}/."""
    id: str
    filename: str
    file_type: str
    feature_count: int | None
    crs: str | None = Field(None, description="Source CRS string.")
    status: str
    file_size_bytes: int | None
    created_at: datetime
    processed_at: datetime | None
    validation_summary: ValidationSummary | None = None
    error_detail: dict | None = None

    model_config = {"from_attributes": True}


# ── Measurement schemas ───────────────────────────────────────────────────────

class MeasurementProvenance(BaseModel):
    """
    Explains how a measurement was obtained.

    This is the 'Measurement Provenance' feature — every measurement carries
    the CRS it was calculated in and a plain-English method description so
    that a reviewer can understand exactly what was done and why.
    """
    source_crs: str | None = Field(None, description="CRS from the source file.")
    measurement_crs: str | None = Field(
        None,
        description="CRS used for the actual area/length calculation.",
    )
    measurement_type: str | None = Field(
        None, description="'area', 'length', 'n/a', or 'unsupported'."
    )
    measurement_unit: str | None = Field(None, description="'sq_m' or 'm'.")
    method_description: str | None = Field(
        None,
        description=(
            "Plain-English description of the CRS transformation and "
            "measurement strategy applied to this feature."
        ),
    )
    status: str = Field(..., description="MEASURED | NOT_APPLICABLE | UNSUPPORTED | FAILED")


class FeatureMeasurement(BaseModel):
    """Complete measurement result for one geospatial feature."""
    feature_index: int = Field(..., description="0-based index in the source file.")
    feature_id: str | None = Field(None, description="Feature ID from source data, if present.")
    geometry_type: str | None
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Original feature attributes from the source file.",
    )
    geometry: dict[str, Any] | None = Field(
        None,
        description="Original source geometry as a GeoJSON-compatible dict.",
    )
    measurement_type: str | None
    measurement_value: float | None = Field(
        None,
        description="Rounded measurement value for display (see provenance for full precision).",
    )
    measurement_unit: str | None
    measurement_status: str
    provenance: MeasurementProvenance

    model_config = {"from_attributes": True}


class MeasurementSummary(BaseModel):
    """Aggregate counts over all features in the file."""
    total_features: int
    measured: int = Field(..., description="Features with a successful measurement.")
    not_applicable: int = Field(..., description="Point features (no area/length applies).")
    unsupported: int = Field(..., description="Geometry types with no defined measurement.")
    failed: int = Field(..., description="Features that raised a measurement exception.")


class MeasurementsResponse(BaseModel):
    """Full measurement payload returned by GET /api/files/{id}/measurements/."""
    file_id: str
    filename: str
    status: str
    source_crs: str | None
    summary: MeasurementSummary
    features: list[FeatureMeasurement]
