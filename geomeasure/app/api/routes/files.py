"""
GeoMeasure — Files API Router

Implements the three mandatory endpoints:

  POST /api/files/
    Upload a .kml or zipped Shapefile, validate it, process all features,
    calculate measurements, persist results, and return a summary.

  GET /api/files/{id}/
    Retrieve metadata and validation summary for a previously uploaded file.

  GET /api/files/{id}/measurements/
    Retrieve the complete per-feature measurement results for a file.

Design notes
────────────
• Processing is synchronous: the POST request blocks until all features
  are measured. For the file sizes in scope (≤50 MB), this is acceptable
  and simpler to reason about than a background task queue.

• Temporary files from uploads are never written to disk (bytes are held
  in memory) unless explicitly needed for ZIP extraction (which uses a
  system temp dir via tempfile).

• No Python tracebacks are returned to clients. All exceptions are mapped
  to structured JSON error responses in the exception handlers in main.py.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmptyFileError,
    FileSizeLimitExceededError,
    FileRecordNotFoundError,
    GeoMeasureError,
    GeospatialParseError,
    InvalidZipArchiveError,
    MissingShapefileComponentError,
    UnsupportedFileTypeError,
)
from app.models.database import get_db
from app.repositories.file_repository import (
    build_validation_summary,
    create_file_record,
    get_feature_results,
    get_file_record,
    save_feature_results,
    update_file_record_failure,
    update_file_record_success,
)
from app.schemas.files import (
    FeatureMeasurement,
    FileInfoResponse,
    FileUploadResponse,
    MeasurementProvenance,
    MeasurementSummary,
    MeasurementsResponse,
    ValidationIssue,
    ValidationSummary,
)
from pathlib import Path
from app.core.config import settings
from app.services.file_validation import validate_upload, validate_zip_shapefile
from app.services.geospatial_reader import read_kml, read_shapefile_from_zip
from app.services.measurement_service import measure_all_features

router = APIRouter(prefix="/api/files", tags=["Files"])


def _process_file_content(content: bytes, original_filename: str, db: Session) -> FileUploadResponse:
    """Validate, parse, measure, and persist geospatial file content."""
    # Step 1 — Validate before doing any geospatial work.
    try:
        detected_type = validate_upload(original_filename, content)
    except (EmptyFileError, FileSizeLimitExceededError, UnsupportedFileTypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": type(exc).__name__, "message": str(exc)},
        )

    file_type = "kml" if detected_type == "kml" else "shapefile"

    # Extra ZIP validation for Shapefiles.
    if file_type == "shapefile":
        try:
            validate_zip_shapefile(content)
        except (InvalidZipArchiveError, MissingShapefileComponentError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": type(exc).__name__, "message": str(exc)},
            )

    # Step 2 — Create the database record.
    record = create_file_record(
        db,
        filename=original_filename,
        file_type=file_type,
        file_size_bytes=len(content),
    )

    # Step 3 — Parse the geospatial file.
    try:
        if file_type == "kml":
            dataset = read_kml(content)
        else:
            dataset = read_shapefile_from_zip(content)
    except GeoMeasureError as exc:
        update_file_record_failure(
            db, record,
            error_detail={"code": type(exc).__name__, "message": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": type(exc).__name__, "message": str(exc)},
        )
    except Exception as exc:
        update_file_record_failure(
            db, record,
            error_detail={"code": "PARSE_ERROR", "message": "Unexpected parse error."},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PARSE_ERROR", "message": "Could not parse the uploaded file."},
        )

    source_crs = dataset.get("source_crs")
    raw_features = dataset.get("features", [])

    # Step 4 — Measure all features.
    measurement_results = measure_all_features(raw_features, source_crs)

    # Step 5 — Build validation summary and persist.
    validation_summary_dict = build_validation_summary(measurement_results, source_crs)

    update_file_record_success(
        db, record,
        source_crs=source_crs,
        feature_count=len(measurement_results),
        validation_summary=validation_summary_dict,
    )

    save_feature_results(db, record.id, measurement_results)

    # Step 6 — Return response.
    val_summary = _dict_to_validation_summary(validation_summary_dict)

    return FileUploadResponse(
        id=record.id,
        filename=record.filename,
        file_type=record.file_type,
        feature_count=record.feature_count,
        crs=record.source_crs,
        status=record.status,
        file_size_bytes=record.file_size_bytes,
        created_at=record.created_at,
        validation_summary=val_summary,
    )


# ── Endpoint 1: Upload and Process ────────────────────────────────────────────

@router.post(
    "/",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a geospatial file",
    description=(
        "Accept a `.kml` file or a `.zip` archive containing a Shapefile. "
        "The file is validated, features are extracted, measurements are calculated "
        "with appropriate CRS transformation, and results are persisted. "
        "Returns a unique file ID and processing summary."
    ),
    responses={
        400: {"description": "Invalid file (wrong type, empty, oversized, corrupt)"},
        422: {"description": "Validation error in request form"},
        500: {"description": "Internal processing error"},
    },
)
async def upload_file(
    file: UploadFile = File(..., description="A .kml file or .zip containing a Shapefile."),
    db: Session = Depends(get_db),
):
    """Upload and process a geospatial file."""
    content = await file.read()
    original_filename = file.filename or "unknown"
    return _process_file_content(content, original_filename, db)


@router.get("/samples/", summary="List available pre-generated sample files", tags=["Samples"])
def list_samples():
    """List sample files in the sample_data directory for 1-click UI demos."""
    samples_dir = settings.BASE_DIR / "sample_data"
    if not samples_dir.exists():
        return []
    return [
        {"filename": p.name, "size_bytes": p.stat().st_size}
        for p in sorted(samples_dir.iterdir())
        if p.suffix.lower() in {".kml", ".zip"}
    ]


@router.post(
    "/load-sample/{sample_name}",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Load and process a pre-generated sample file",
    tags=["Samples"],
)
def load_sample_file(sample_name: str, db: Session = Depends(get_db)):
    """Load and process a pre-generated sample file from sample_data/ with 1 click."""
    clean_name = Path(sample_name).name
    sample_path = settings.BASE_DIR / "sample_data" / clean_name
    if not sample_path.exists() or not sample_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SAMPLE_NOT_FOUND", "message": f"Sample file '{sample_name}' not found."},
        )
    content = sample_path.read_bytes()
    return _process_file_content(content, clean_name, db)


# ── Endpoint 2: Get File Information ─────────────────────────────────────────

@router.get(
    "/{file_id}/",
    response_model=FileInfoResponse,
    summary="Retrieve file information",
    description=(
        "Return metadata for a previously uploaded file by its unique ID. "
        "Includes processing status, source CRS, feature count, and validation summary."
    ),
    responses={
        404: {"description": "File ID not found"},
    },
)
def get_file_info(
    file_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve metadata for an uploaded file."""
    try:
        record = get_file_record(db, file_id)
    except FileRecordNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": str(exc)},
        )

    val_summary = None
    if record.validation_summary:
        val_summary = _dict_to_validation_summary(record.get_validation_summary())

    return FileInfoResponse(
        id=record.id,
        filename=record.filename,
        file_type=record.file_type,
        feature_count=record.feature_count,
        crs=record.source_crs,
        status=record.status,
        file_size_bytes=record.file_size_bytes,
        created_at=record.created_at,
        processed_at=record.processed_at,
        validation_summary=val_summary,
        error_detail=record.get_error_detail(),
    )


# ── Endpoint 3: Get Measurements ──────────────────────────────────────────────

@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Retrieve feature measurements",
    description=(
        "Return per-feature measurement results for an uploaded file. "
        "Each feature includes its geometry type, original properties, source geometry, "
        "measurement value (area in sq metres or length in metres), and full provenance "
        "explaining the CRS transformation and measurement method used."
    ),
    responses={
        404: {"description": "File ID not found"},
        409: {"description": "File has not been processed yet"},
    },
)
def get_measurements(
    file_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve measurements for all features in an uploaded file."""
    try:
        record = get_file_record(db, file_id)
    except FileRecordNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": str(exc)},
        )

    if record.status == "PROCESSING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROCESSING",
                "message": "File is still being processed. Please try again shortly.",
            },
        )

    if record.status == "FAILED":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PROCESSING_FAILED",
                "message": "File processing failed. See file info endpoint for error details.",
                "error": record.get_error_detail(),
            },
        )

    feature_rows = get_feature_results(db, file_id)

    # Build per-feature response objects.
    features: list[FeatureMeasurement] = []
    measured = not_applicable = unsupported = failed = 0

    for row in feature_rows:
        if row.measurement_status == "MEASURED":
            measured += 1
        elif row.measurement_status == "NOT_APPLICABLE":
            not_applicable += 1
        elif row.measurement_status == "UNSUPPORTED":
            unsupported += 1
        else:
            failed += 1

        provenance = MeasurementProvenance(
            source_crs=row.source_crs,
            measurement_crs=row.measurement_crs,
            measurement_type=row.measurement_type,
            measurement_unit=row.measurement_unit,
            method_description=row.crs_method,
            status=row.measurement_status,
        )

        feat = FeatureMeasurement(
            feature_index=row.feature_index,
            feature_id=row.feature_id,
            geometry_type=row.geometry_type,
            properties=row.get_properties(),
            geometry=row.get_geometry() or None,
            measurement_type=row.measurement_type,
            measurement_value=row.measurement_value,
            measurement_unit=row.measurement_unit,
            measurement_status=row.measurement_status,
            provenance=provenance,
        )
        features.append(feat)

    summary = MeasurementSummary(
        total_features=len(features),
        measured=measured,
        not_applicable=not_applicable,
        unsupported=unsupported,
        failed=failed,
    )

    return MeasurementsResponse(
        file_id=record.id,
        filename=record.filename,
        status=record.status,
        source_crs=record.source_crs,
        summary=summary,
        features=features,
    )


# ── Schema conversion helper ──────────────────────────────────────────────────

def _dict_to_validation_summary(d: dict) -> ValidationSummary:
    """Convert the raw validation-summary dict into the Pydantic schema."""
    issues = [
        ValidationIssue(
            code=i["code"],
            detail=i["detail"],
            feature_index=i.get("feature_index"),
        )
        for i in d.get("issues", [])
    ]
    return ValidationSummary(
        has_issues=d.get("has_issues", False),
        issues=issues,
        missing_crs=d.get("missing_crs", False),
        invalid_geometry_count=d.get("invalid_geometry_count", 0),
        unsupported_geometry_count=d.get("unsupported_geometry_count", 0),
        failed_measurement_count=d.get("failed_measurement_count", 0),
    )
