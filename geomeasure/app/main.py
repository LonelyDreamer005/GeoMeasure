"""
GeoMeasure — FastAPI Application Entry Point

Initialises the FastAPI app, registers exception handlers, includes the
files router, creates database tables, and exposes a health-check endpoint.

Running locally:
  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.routes import files as files_router
from app.core.config import settings
from app.core.exceptions import (
    EmptyFileError,
    FileSizeLimitExceededError,
    FileRecordNotFoundError,
    GeoMeasureError,
    GeospatialParseError,
    InvalidZipArchiveError,
    MissingCRSError,
    MissingShapefileComponentError,
    UnsupportedFileTypeError,
    ZipPathTraversalError,
)
from app.models.database import Base, engine

# ── Create all database tables at startup ─────────────────────────────────────
# This is idempotent: tables that already exist are not re-created.
# For production, use Alembic migrations instead.
Base.metadata.create_all(bind=engine)

# ── Import models so SQLAlchemy registers them with the metadata ───────────────
import app.models.file_record  # noqa: F401  (side-effect import)


# ── Application instance ──────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={
        "name": "GeoMeasure — Geospatial Measurement API",
        "url": "https://github.com/LonelyDreamer005/GeoMeasure",
    },
    license_info={
        "name": "MIT",
    },
)

# ── CORS (permissive for local development) ───────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(files_router.router)


# ── Exception handlers ────────────────────────────────────────────────────────
# Map domain exceptions to clean HTTP responses without leaking tracebacks.

@app.exception_handler(UnsupportedFileTypeError)
@app.exception_handler(EmptyFileError)
@app.exception_handler(FileSizeLimitExceededError)
async def bad_request_handler(request: Request, exc: GeoMeasureError):
    return JSONResponse(
        status_code=400,
        content={"code": type(exc).__name__, "message": str(exc)},
    )


@app.exception_handler(FileRecordNotFoundError)
async def not_found_handler(request: Request, exc: GeoMeasureError):
    return JSONResponse(
        status_code=404,
        content={"code": "FILE_NOT_FOUND", "message": str(exc)},
    )


@app.exception_handler(InvalidZipArchiveError)
@app.exception_handler(MissingShapefileComponentError)
@app.exception_handler(ZipPathTraversalError)
async def zip_error_handler(request: Request, exc: GeoMeasureError):
    return JSONResponse(
        status_code=400,
        content={"code": type(exc).__name__, "message": str(exc)},
    )


@app.exception_handler(GeospatialParseError)
async def parse_error_handler(request: Request, exc: GeoMeasureError):
    return JSONResponse(
        status_code=422,
        content={"code": type(exc).__name__, "message": str(exc)},
    )


@app.exception_handler(GeoMeasureError)
async def generic_domain_error_handler(request: Request, exc: GeoMeasureError):
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "An internal processing error occurred."},
    )


# ── Health check ──────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns the application name, version, and status. Use this to verify the service is running.",
)
def health_check():
    """Lightweight health-check endpoint."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "tagline": "Reliable Spatial Measurements. Traceable Geospatial Data.",
    }


@app.get("/", tags=["UI"], include_in_schema=False, response_class=HTMLResponse)
@app.get("/ui", tags=["UI"], include_in_schema=False, response_class=HTMLResponse)
def root(request: Request):
    """
    Serve the GeoMeasure Visual Dashboard at / and /ui.
    If the client explicitly requests JSON (e.g. cURL or scripts), return API metadata.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(
            content={
                "message": f"Welcome to {settings.APP_NAME} v{settings.APP_VERSION}",
                "ui": "/",
                "docs": "/docs",
                "redoc": "/redoc",
                "health": "/health",
            }
        )

    index_path = settings.BASE_DIR / "app" / "static" / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))

    return HTMLResponse(
        content=f"<h1>Welcome to {settings.APP_NAME}</h1><p>Visit <a href='/docs'>/docs</a></p>"
    )
