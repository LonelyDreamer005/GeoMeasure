"""
GeoMeasure — File Repository

Data access layer for FileRecord and FeatureResult persistence.

All database interaction is routed through this module.  Routes and
services never call SQLAlchemy directly — they call functions here.
This keeps the database concern in one place and makes testing easier.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, FileRecordNotFoundError
from app.models.file_record import FeatureResult, FileRecord
from app.services.measurement_service import FeatureMeasurementResult


# ── FileRecord operations ─────────────────────────────────────────────────────

def create_file_record(
    db: Session,
    *,
    filename: str,
    file_type: str,
    file_size_bytes: int,
) -> FileRecord:
    """Insert a new FileRecord with status=PROCESSING and return it."""
    try:
        record = FileRecord(
            id=str(uuid.uuid4()),
            filename=filename,
            file_type=file_type,
            status="PROCESSING",
            file_size_bytes=file_size_bytes,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    except Exception as exc:
        db.rollback()
        raise DatabaseError(f"Failed to create file record: {exc}") from exc


def update_file_record_success(
    db: Session,
    record: FileRecord,
    *,
    source_crs: str | None,
    feature_count: int,
    validation_summary: dict,
) -> FileRecord:
    """Mark a FileRecord as COMPLETED and store processing metadata."""
    try:
        record.status = "COMPLETED"
        record.source_crs = source_crs
        record.feature_count = feature_count
        record.processed_at = datetime.now(timezone.utc)
        record.set_validation_summary(validation_summary)
        db.commit()
        db.refresh(record)
        return record
    except Exception as exc:
        db.rollback()
        raise DatabaseError(f"Failed to update file record: {exc}") from exc


def update_file_record_failure(
    db: Session,
    record: FileRecord,
    *,
    error_detail: dict,
) -> FileRecord:
    """Mark a FileRecord as FAILED and store error information."""
    try:
        record.status = "FAILED"
        record.processed_at = datetime.now(timezone.utc)
        record.set_error_detail(error_detail)
        db.commit()
        db.refresh(record)
        return record
    except Exception as exc:
        db.rollback()
        raise DatabaseError(f"Failed to update file record as failed: {exc}") from exc


def get_file_record(db: Session, file_id: str) -> FileRecord:
    """
    Retrieve a FileRecord by primary key.

    Raises FileRecordNotFoundError if the ID does not exist.
    """
    record = db.get(FileRecord, file_id)
    if record is None:
        raise FileRecordNotFoundError(
            f"No file found with ID '{file_id}'."
        )
    return record


# ── FeatureResult operations ──────────────────────────────────────────────────

def save_feature_results(
    db: Session,
    file_id: str,
    results: list[FeatureMeasurementResult],
) -> None:
    """Bulk-insert FeatureResult rows for all features in a processed file."""
    try:
        rows = []
        for r in results:
            fr = FeatureResult(
                file_id=file_id,
                feature_index=r.feature_index,
                feature_id=r.feature_id,
                geometry_type=r.geometry_type,
                measurement_type=r.measurement_type,
                measurement_value=r.measurement_value,
                measurement_unit=r.measurement_unit,
                measurement_status=r.measurement_status,
                source_crs=r.source_crs,
                measurement_crs=r.measurement_crs,
                crs_method=r.crs_method,
            )
            fr.set_geometry(r.geometry or {})
            fr.set_properties(r.properties or {})
            rows.append(fr)

        db.bulk_save_objects(rows)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise DatabaseError(f"Failed to save feature results: {exc}") from exc


def get_feature_results(db: Session, file_id: str) -> list[FeatureResult]:
    """Return all FeatureResult rows for a given file ID, ordered by feature_index."""
    return (
        db.query(FeatureResult)
        .filter(FeatureResult.file_id == file_id)
        .order_by(FeatureResult.feature_index)
        .all()
    )


# ── Validation summary helper ─────────────────────────────────────────────────

def build_validation_summary(
    results: list[FeatureMeasurementResult],
    source_crs: str | None,
) -> dict:
    """
    Build a structured validation summary from measurement results.

    This is stored as JSON in FileRecord.validation_summary and returned
    in the API response to help callers understand data quality.
    """
    issues = []
    invalid_geom_count = 0
    unsupported_geom_count = 0
    failed_count = 0

    if source_crs is None:
        issues.append(
            {
                "code": "MISSING_CRS",
                "detail": (
                    "The file has no coordinate reference system (CRS). "
                    "Measurements cannot be calculated without a known CRS."
                ),
                "feature_index": None,
            }
        )

    for r in results:
        if r.measurement_status == "FAILED":
            failed_count += 1
            if r.geometry is None:
                invalid_geom_count += 1
                issues.append(
                    {
                        "code": "INVALID_GEOMETRY",
                        "detail": f"Feature {r.feature_index} has no parseable geometry.",
                        "feature_index": r.feature_index,
                    }
                )
            else:
                issues.append(
                    {
                        "code": "MEASUREMENT_FAILED",
                        "detail": f"Feature {r.feature_index}: {r.crs_method[:200]}",
                        "feature_index": r.feature_index,
                    }
                )
        elif r.measurement_status == "UNSUPPORTED":
            unsupported_geom_count += 1
            issues.append(
                {
                    "code": "UNSUPPORTED_GEOMETRY",
                    "detail": (
                        f"Feature {r.feature_index} has geometry type "
                        f"'{r.geometry_type}' which is not supported for measurement."
                    ),
                    "feature_index": r.feature_index,
                }
            )

    return {
        "has_issues": bool(issues),
        "issues": issues,
        "missing_crs": source_crs is None,
        "invalid_geometry_count": invalid_geom_count,
        "unsupported_geometry_count": unsupported_geom_count,
        "failed_measurement_count": failed_count,
    }
