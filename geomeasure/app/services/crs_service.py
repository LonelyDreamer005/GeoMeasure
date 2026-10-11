"""
GeoMeasure — CRS Service

Determines a suitable projected CRS for geospatial measurement, given
the source CRS and the bounding extent of a geometry.

Design decisions
────────────────
1.  If the source CRS is already a valid projected CRS whose linear
    units are metres, we use it directly (no reprojection needed).

2.  If the source CRS is geographic (lat/lon, e.g. EPSG:4326), we
    select a local UTM zone based on the geometry's centroid.  UTM
    provides metre-based planar coordinates with <0.1 % distortion
    for geometries that fit within the zone's coverage (~6° longitude).

3.  If the geometry spans multiple UTM zones or covers a very large
    area (>180° longitude spread), we fall back to the World Mercator
    projection (EPSG:3857) with an explicit warning in the provenance.
    World Mercator is NOT area-preserving globally, but it is well-
    defined for any longitude and gives consistent metre-based results.
    The provenance description flags this so reviewers understand the
    limitation.

4.  If CRS is entirely absent (None), the measurement is marked
    FAILED and the feature's measurement_status reflects this.

5.  Projected CRS with non-metre units (e.g. US Survey Feet, EPSG:2274)
    is detected and the measurement values are converted to metres.
    The unit detected is recorded in provenance.

This module never performs the reprojection itself — it only selects and
documents the target CRS.  The measurement_service does the actual
coordinate transformation via GeoPandas / PyProj.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyproj
from pyproj import CRS, Transformer
from shapely.geometry import shape


@dataclass
class CRSDecision:
    """Result of asking the CRS service which projection to use."""
    source_crs: str | None           # Input CRS string
    measurement_crs: str | None      # Selected projected CRS string
    needs_transform: bool            # Whether a reprojection is needed
    unit: str                        # Linear unit of the measurement CRS
    unit_conversion_to_m: float      # Multiply measurement by this → metres
    method_description: str          # Human-readable explanation
    can_measure: bool                # False when CRS is unavailable / unsuitable


def select_measurement_crs(
    source_crs_str: str | None,
    geometry_geojson: dict[str, Any] | None,
) -> CRSDecision:
    """
    Select an appropriate projected CRS for area / length measurement.

    Parameters
    ----------
    source_crs_str:
        The source CRS as a string, e.g. "EPSG:4326" or a WKT string.
        Pass None when the file has no CRS.
    geometry_geojson:
        GeoJSON geometry dict used to determine the geographic extent
        (centroid longitude/latitude for UTM zone selection).

    Returns
    -------
    CRSDecision with all fields populated.
    """
    # ── Case 1: No CRS available ──────────────────────────────────────────────
    if source_crs_str is None:
        return CRSDecision(
            source_crs=None,
            measurement_crs=None,
            needs_transform=False,
            unit="unknown",
            unit_conversion_to_m=1.0,
            method_description=(
                "No CRS information is available for this file. "
                "Measurement cannot be performed without a known coordinate "
                "reference system. Feature information is preserved but "
                "measurement is unavailable."
            ),
            can_measure=False,
        )

    # Parse the source CRS.
    try:
        src_crs = CRS.from_user_input(source_crs_str)
    except Exception:
        return CRSDecision(
            source_crs=source_crs_str,
            measurement_crs=None,
            needs_transform=False,
            unit="unknown",
            unit_conversion_to_m=1.0,
            method_description=(
                f"The CRS string '{source_crs_str}' could not be parsed. "
                "Measurement is unavailable."
            ),
            can_measure=False,
        )

    # ── Case 2: Source is already a projected CRS ─────────────────────────────
    if src_crs.is_projected:
        unit_name, conv = _get_linear_unit(src_crs)
        return CRSDecision(
            source_crs=source_crs_str,
            measurement_crs=source_crs_str,
            needs_transform=False,
            unit=unit_name,
            unit_conversion_to_m=conv,
            method_description=(
                f"Source CRS '{src_crs.name}' is already a projected CRS. "
                f"Measurements are computed directly in the source CRS without "
                f"reprojection. Linear unit: {unit_name} "
                f"(×{conv:.6g} to convert to metres)."
            ),
            can_measure=True,
        )

    # ── Case 3: Source is geographic (lat/lon) — select a projected CRS ───────
    centroid_lon, centroid_lat = _get_centroid(geometry_geojson)
    lon_span = _get_longitude_span(geometry_geojson)

    if centroid_lon is None:
        # No geometry to inspect — use a safe global fallback.
        return _world_mercator_decision(
            source_crs_str,
            reason="geometry is absent or unparseable",
        )

    if lon_span is not None and lon_span > 20:
        # Geometry spans more than 20 degrees longitude — UTM would distort.
        return _world_mercator_decision(
            source_crs_str,
            reason=(
                f"geometry longitude span ({lon_span:.1f}°) exceeds 20°, "
                "which is too large for a single UTM zone"
            ),
        )

    # Determine UTM zone from centroid.
    utm_epsg = _utm_epsg_for_lonlat(centroid_lon, centroid_lat)
    utm_crs = CRS.from_epsg(utm_epsg)

    return CRSDecision(
        source_crs=source_crs_str,
        measurement_crs=f"EPSG:{utm_epsg}",
        needs_transform=True,
        unit="metres",
        unit_conversion_to_m=1.0,
        method_description=(
            f"Source CRS '{src_crs.name}' is geographic (angular units). "
            f"Geometries were reprojected to UTM zone "
            f"EPSG:{utm_epsg} ({utm_crs.name}), selected from the geometry "
            f"centroid at ({centroid_lon:.4f}°, {centroid_lat:.4f}°). "
            f"Measurements are in metres (sq metres for area)."
        ),
        can_measure=True,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _world_mercator_decision(source_crs_str: str, reason: str) -> CRSDecision:
    """Return a fallback World Mercator CRS decision with a clear explanation."""
    return CRSDecision(
        source_crs=source_crs_str,
        measurement_crs="EPSG:3857",
        needs_transform=True,
        unit="metres",
        unit_conversion_to_m=1.0,
        method_description=(
            f"Fell back to Web Mercator (EPSG:3857) because {reason}. "
            "Web Mercator is NOT equal-area; area measurements may be "
            "distorted at high latitudes. For production use, prefer an "
            "equal-area projection such as Mollweide (EPSG:54009) or "
            "a local equal-area CRS."
        ),
        can_measure=True,
    )


def _utm_epsg_for_lonlat(lon: float, lat: float) -> int:
    """
    Calculate the EPSG code for the UTM zone that contains the given
    longitude / latitude.

    UTM zone number = floor((lon + 180) / 6) + 1
    Northern hemisphere: EPSG 326xx
    Southern hemisphere: EPSG 327xx
    """
    zone = int((lon + 180) / 6) + 1
    # Clamp to [1, 60].
    zone = max(1, min(60, zone))
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return epsg


def _get_linear_unit(crs: CRS) -> tuple[str, float]:
    """
    Return (unit_name, conversion_factor_to_metres) for a projected CRS.

    The conversion factor multiplied by a measurement value gives metres.
    """
    try:
        axis = crs.axis_info[0]
        unit_name = axis.unit_name.lower()
        conv = axis.unit_conversion_factor  # metres per unit
        return unit_name, conv
    except Exception:
        return "unknown", 1.0


def _get_centroid(geometry_geojson: dict | None) -> tuple[float | None, float | None]:
    """Return (longitude, latitude) of the geometry centroid, or (None, None)."""
    if geometry_geojson is None:
        return None, None
    try:
        geom = shape(geometry_geojson)
        c = geom.centroid
        return c.x, c.y
    except Exception:
        return None, None


def _get_longitude_span(geometry_geojson: dict | None) -> float | None:
    """Return the longitude span (degrees) of the geometry bounding box."""
    if geometry_geojson is None:
        return None
    try:
        geom = shape(geometry_geojson)
        minx, _, maxx, _ = geom.bounds
        return abs(maxx - minx)
    except Exception:
        return None
