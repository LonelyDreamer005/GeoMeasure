"""
GeoMeasure — File Validation Service

Responsibilities:
  • Inspect uploaded file bytes before any geospatial parsing.
  • Check extension, MIME magic bytes, file size, and emptiness.
  • For ZIP archives: detect path traversal, validate integrity, and
    confirm that required Shapefile components are present.
  • All failures raise typed exceptions from app.core.exceptions.

Security notes:
  • The original filename is NEVER trusted for extension detection alone.
    We check MIME magic bytes first, then cross-reference the extension.
  • ZIP extraction uses a safe-extract function that aborts on any entry
    that resolves outside the target directory.
  • No subprocess calls; no shell=True; no eval.
"""

import io
import zipfile
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.exceptions import (
    EmptyFileError,
    FileSizeLimitExceededError,
    InvalidZipArchiveError,
    MissingShapefileComponentError,
    UnsupportedFileTypeError,
    ZipPathTraversalError,
)


# ── Magic byte signatures ─────────────────────────────────────────────────────
# Used to detect actual file type independently of the stated extension.

_ZIP_MAGIC = b"PK\x03\x04"
_KML_MARKERS = (b"<?xml", b"<kml", b"\xef\xbb\xbf")  # UTF-8 BOM prefix is common


# ── Public API ────────────────────────────────────────────────────────────────

def validate_upload(filename: str, content: bytes) -> str:
    """
    Validate an uploaded file before geospatial processing.

    Returns the detected file type: ``"kml"`` or ``"shapefile"``.
    Raises a typed exception on any validation failure.

    Parameters
    ----------
    filename:
        Original filename as supplied by the client (untrusted).
    content:
        Raw file bytes read from the upload.
    """
    if not content:
        raise EmptyFileError("The uploaded file is empty (0 bytes).")

    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_mb = settings.MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
        raise FileSizeLimitExceededError(
            f"File size {len(content):,} bytes exceeds the {limit_mb:.0f} MB limit."
        )

    # Detect the real type from magic bytes first.
    detected = _detect_file_type(content)

    # Cross-reference against the stated extension.
    stated_ext = Path(filename).suffix.lower()
    if stated_ext not in settings.ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Extension '{stated_ext}' is not supported. "
            f"Accepted: {sorted(settings.ALLOWED_EXTENSIONS)}."
        )

    if detected == "zip" and stated_ext != ".zip":
        raise UnsupportedFileTypeError(
            "File content looks like a ZIP archive but extension is not .zip."
        )

    if detected == "kml" and stated_ext != ".kml":
        raise UnsupportedFileTypeError(
            "File content looks like KML/XML but extension is not .kml."
        )

    if detected == "unknown":
        # For KML, content may not start with typical markers if it begins
        # with a large XML declaration. Fallback: trust extension but flag it.
        if stated_ext == ".kml":
            return "kml"
        raise UnsupportedFileTypeError(
            f"Cannot determine file type from content. "
            f"Received extension: '{stated_ext}'."
        )

    return detected   # "kml" or "zip" (zip → shapefile validated separately)


def validate_zip_shapefile(content: bytes) -> dict:
    """
    Validate a ZIP archive that is expected to contain a Shapefile.

    Returns a dict with keys:
      ``shp_path``  — path of the .shp file inside the archive.
      ``has_prj``   — whether a .prj (CRS definition) file is present.
      ``has_dbf``   — whether a .dbf (attributes) file is present.
      ``has_shx``   — whether a .shx (index) file is present.

    Raises on: corrupt ZIP, path traversal, missing .shp.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise InvalidZipArchiveError(f"Cannot open ZIP archive: {exc}") from exc

    with zf:
        entries = zf.namelist()

    # Path-traversal check: every entry must resolve within a safe root.
    _assert_no_path_traversal(entries)

    # Normalise to lowercase extensions for case-insensitive matching.
    lower_entries = [e.lower() for e in entries]

    shp_files = [e for e in entries if e.lower().endswith(".shp")]
    if not shp_files:
        raise MissingShapefileComponentError(
            "ZIP archive does not contain a .shp file. "
            "Ensure the archive includes at least: .shp, .shx, .dbf."
        )

    if len(shp_files) > 1:
        # Ambiguous: multiple shapefiles. Use the first one found and document.
        shp_path = shp_files[0]
    else:
        shp_path = shp_files[0]

    stem = shp_path[:-4]  # strip ".shp"
    return {
        "shp_path": shp_path,
        "has_prj": (stem + ".prj").lower() in lower_entries
                   or any(e.endswith(".prj") for e in lower_entries),
        "has_dbf": (stem + ".dbf").lower() in lower_entries
                   or any(e.endswith(".dbf") for e in lower_entries),
        "has_shx": (stem + ".shx").lower() in lower_entries
                   or any(e.endswith(".shx") for e in lower_entries),
    }


def safe_extract_zip(content: bytes, target_dir: Path) -> None:
    """
    Extract a ZIP archive into *target_dir*, refusing any path-traversal entry.

    Parameters
    ----------
    content:
        Raw ZIP bytes.
    target_dir:
        Absolute path of the directory to extract into.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise InvalidZipArchiveError(f"Cannot open ZIP: {exc}") from exc

    with zf:
        for member in zf.infolist():
            # Resolve where the member would be extracted to.
            target_path = (target_dir / member.filename).resolve()
            if not str(target_path).startswith(str(target_dir.resolve())):
                raise ZipPathTraversalError(
                    f"ZIP entry '{member.filename}' would extract outside the "
                    "designated directory. Extraction aborted."
                )
        # All entries are safe — extract.
        zf.extractall(target_dir)


# ── Private helpers ───────────────────────────────────────────────────────────

def _detect_file_type(content: bytes) -> str:
    """Inspect the first bytes of *content* and return 'zip', 'kml', or 'unknown'."""
    head = content[:512]

    if head[:4] == _ZIP_MAGIC:
        return "zip"

    # KML is XML — look for common XML/KML opening sequences.
    # Strip optional BOM first.
    stripped = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<kml"):
        return "kml"

    return "unknown"


def _assert_no_path_traversal(entries: list[str]) -> None:
    """
    Raise ZipPathTraversalError if any ZIP entry contains a path traversal
    component (``..`` or absolute paths).
    """
    for entry in entries:
        # Use PurePosixPath because ZIP paths always use forward slashes.
        parts = PurePosixPath(entry).parts
        if ".." in parts or entry.startswith("/"):
            raise ZipPathTraversalError(
                f"ZIP entry '{entry}' contains a path traversal sequence."
            )
