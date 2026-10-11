"""
GeoMeasure — Custom Exception Classes

All domain-level errors are represented as explicit exception types.
FastAPI exception handlers in main.py convert these into structured
HTTP responses — Python tracebacks are never leaked to clients.
"""


class GeoMeasureError(Exception):
    """Root exception for all GeoMeasure domain errors."""


# ── Upload & validation errors ───────────────────────────────────────────────

class UnsupportedFileTypeError(GeoMeasureError):
    """Raised when the uploaded file is not .kml or .zip."""


class FileSizeLimitExceededError(GeoMeasureError):
    """Raised when the uploaded file exceeds the configured size limit."""


class EmptyFileError(GeoMeasureError):
    """Raised when the uploaded file is empty (zero bytes)."""


class InvalidZipArchiveError(GeoMeasureError):
    """Raised when a .zip file cannot be opened or is otherwise corrupt."""


class ZipPathTraversalError(GeoMeasureError):
    """Raised when a ZIP entry would write outside the extraction directory."""


class MissingShapefileComponentError(GeoMeasureError):
    """Raised when the ZIP does not contain the mandatory .shp file."""


# ── Geospatial parsing errors ─────────────────────────────────────────────────

class GeospatialParseError(GeoMeasureError):
    """Raised when a geospatial file cannot be parsed (corrupt KML, bad SHP…)."""


class KMLParseError(GeospatialParseError):
    """Raised for KML-specific parsing failures."""


class ShapefileReadError(GeospatialParseError):
    """Raised for Shapefile-specific read failures."""


# ── CRS errors ────────────────────────────────────────────────────────────────

class CRSError(GeoMeasureError):
    """Raised when a CRS cannot be determined, parsed, or transformed."""


class MissingCRSError(CRSError):
    """Raised when a file has no CRS and none can be inferred."""


# ── Measurement errors ────────────────────────────────────────────────────────

class MeasurementError(GeoMeasureError):
    """Raised when a measurement cannot be completed."""


# ── Persistence errors ────────────────────────────────────────────────────────

class FileRecordNotFoundError(GeoMeasureError):
    """Raised when a requested file ID does not exist in the database."""


class DatabaseError(GeoMeasureError):
    """Raised for unexpected database-level failures."""
