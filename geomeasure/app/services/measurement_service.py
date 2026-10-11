"""
GeoMeasure — Measurement Service

Calculates area (Polygon/MultiPolygon → sq metres) and length
(LineString/MultiLineString → metres) for individual features.

Processing flow for each feature
─────────────────────────────────
1. Inspect geometry type.
2. Call crs_service.select_measurement_crs() to get the target CRS.
3. If the source CRS is geographic, reproject using PyProj Transformer.
4. Calculate area or length using Shapely in projected coordinates.
5. Convert the raw value to metres / sq metres using the unit factor.
6. Return a FeatureMeasurementResult with full provenance.

Geometry type handling
──────────────────────
• Polygon / MultiPolygon  → area in sq metres (and hectares in provenance)
• LineString / MultiLineString / LinearRing → length in metres
• Point / MultiPoint / GeometryCollection → NOT_APPLICABLE or UNSUPPORTED
• Unknown / null geometry → FAILED

Zero vs not-applicable vs unsupported vs failed
───────────────────────────────────────────────
• measurement_value = 0.0 means the geometry genuinely has zero area/length.
• measurement_status = "NOT_APPLICABLE" means the geometry type has no
  applicable measurement (Points).
• measurement_status = "UNSUPPORTED" means the geometry type is recognised
  but no measurement rule is defined (GeometryCollection children).
• measurement_status = "FAILED" means an exception occurred or the CRS
  is missing/invalid.

Rounding
────────
Raw floating-point values are preserved internally; display-rounded values
are exposed in measurement_value.  Full-precision values are available in
the provenance (method_description notes the raw result).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as ops_transform

from app.core.config import settings
from app.services.crs_service import CRSDecision, select_measurement_crs


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_MEASURED = "MEASURED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_FAILED = "FAILED"

# Geometry types for which area measurement applies.
_AREA_TYPES = {"Polygon", "MultiPolygon"}
# Geometry types for which length measurement applies.
_LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
# Geometry types that have no defined measurement (not an error).
_NOT_APPLICABLE_TYPES = {"Point", "MultiPoint"}
# Geometry types that are structurally unsupported.
_UNSUPPORTED_TYPES = {"GeometryCollection"}


@dataclass
class FeatureMeasurementResult:
    """Complete measurement result for one feature."""
    feature_index: int
    feature_id: str | None
    geometry_type: str | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None   # Original source geometry

    measurement_type: str | None      # "area" | "length" | "n/a" | "unsupported"
    measurement_value: float | None   # Rounded for display
    measurement_unit: str | None      # "sq_m" | "m" | None
    measurement_status: str           # STATUS_* constant

    source_crs: str | None
    measurement_crs: str | None
    crs_method: str                   # Provenance description


def measure_feature(raw_feature: dict, source_crs: str | None) -> FeatureMeasurementResult:
    """
    Calculate the measurement for a single raw feature dict.

    Parameters
    ----------
    raw_feature:
        Dict from geospatial_reader with keys: feature_index, feature_id,
        geometry_type, geometry, properties.
    source_crs:
        CRS string for the file (e.g. "EPSG:4326"), or None if unknown.

    Returns
    -------
    FeatureMeasurementResult — never raises; failures are captured as status=FAILED.
    """
    fidx = raw_feature.get("feature_index", 0)
    fid = raw_feature.get("feature_id")
    geom_type = raw_feature.get("geometry_type")
    geometry = raw_feature.get("geometry")
    props = raw_feature.get("properties", {})

    base = dict(
        feature_index=fidx,
        feature_id=fid,
        geometry_type=geom_type,
        properties=props,
        geometry=geometry,
        source_crs=source_crs,
    )

    # ── No geometry ───────────────────────────────────────────────────────────
    if geometry is None or geom_type is None:
        return FeatureMeasurementResult(
            **base,
            measurement_type=None,
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_FAILED,
            measurement_crs=None,
            crs_method="Feature has no parseable geometry; measurement is unavailable.",
        )

    # ── NOT_APPLICABLE: Point types ───────────────────────────────────────────
    if geom_type in _NOT_APPLICABLE_TYPES:
        decision = select_measurement_crs(source_crs, geometry)
        return FeatureMeasurementResult(
            **base,
            measurement_type="n/a",
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_NOT_APPLICABLE,
            measurement_crs=decision.measurement_crs,
            crs_method=(
                "Point geometry has no applicable area or length measurement. "
                + decision.method_description
            ),
        )

    # ── UNSUPPORTED: GeometryCollection ──────────────────────────────────────
    if geom_type in _UNSUPPORTED_TYPES:
        return FeatureMeasurementResult(
            **base,
            measurement_type="unsupported",
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_UNSUPPORTED,
            measurement_crs=None,
            crs_method=(
                f"Geometry type '{geom_type}' is not supported for direct "
                "measurement. Each constituent geometry would need to be "
                "measured individually."
            ),
        )

    # ── Area or Length measurement ────────────────────────────────────────────
    decision = select_measurement_crs(source_crs, geometry)

    if not decision.can_measure:
        return FeatureMeasurementResult(
            **base,
            measurement_type=None,
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_FAILED,
            measurement_crs=None,
            crs_method=decision.method_description,
        )

    try:
        projected_geom = _project_geometry(geometry, source_crs, decision)
    except Exception as exc:
        return FeatureMeasurementResult(
            **base,
            measurement_type=None,
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_FAILED,
            measurement_crs=decision.measurement_crs,
            crs_method=(
                f"Reprojection to {decision.measurement_crs} failed: {exc}. "
                + decision.method_description
            ),
        )

    if geom_type in _AREA_TYPES:
        return _measure_area(
            base=base,
            projected_geom=projected_geom,
            decision=decision,
        )
    elif geom_type in _LENGTH_TYPES:
        return _measure_length(
            base=base,
            projected_geom=projected_geom,
            decision=decision,
        )
    else:
        # Unknown geometry type encountered after the explicit checks above.
        return FeatureMeasurementResult(
            **base,
            measurement_type="unsupported",
            measurement_value=None,
            measurement_unit=None,
            measurement_status=STATUS_UNSUPPORTED,
            measurement_crs=decision.measurement_crs,
            crs_method=(
                f"Geometry type '{geom_type}' is unrecognised; no measurement "
                "rule is available."
            ),
        )


def measure_all_features(
    raw_features: list[dict],
    source_crs: str | None,
) -> list[FeatureMeasurementResult]:
    """
    Measure all features in the list.  Individual failures are captured and
    recorded as STATUS_FAILED so that one bad feature never aborts the entire
    file.
    """
    results = []
    for feat in raw_features:
        try:
            result = measure_feature(feat, source_crs)
        except Exception as exc:
            # Belt-and-suspenders: catch any unexpected exception.
            result = FeatureMeasurementResult(
                feature_index=feat.get("feature_index", 0),
                feature_id=feat.get("feature_id"),
                geometry_type=feat.get("geometry_type"),
                properties=feat.get("properties", {}),
                geometry=feat.get("geometry"),
                measurement_type=None,
                measurement_value=None,
                measurement_unit=None,
                measurement_status=STATUS_FAILED,
                source_crs=source_crs,
                measurement_crs=None,
                crs_method=f"Unexpected measurement error: {exc}",
            )
        results.append(result)
    return results


# ── Private helpers ───────────────────────────────────────────────────────────

def _project_geometry(
    geometry_geojson: dict,
    source_crs_str: str | None,
    decision: CRSDecision,
):
    """
    Return a Shapely geometry object in the measurement CRS.

    If no reprojection is needed, the geometry is returned as-is (parsed
    from GeoJSON) in the source CRS's coordinate space.
    """
    geom = shape(geometry_geojson)

    if not decision.needs_transform:
        return geom

    transformer = Transformer.from_crs(
        source_crs_str,
        decision.measurement_crs,
        always_xy=True,  # Always treat axes as (lon/lat) → (x/y) regardless of CRS axis order.
    )
    return ops_transform(transformer.transform, geom)


def _measure_area(
    base: dict,
    projected_geom,
    decision: CRSDecision,
) -> FeatureMeasurementResult:
    """Calculate area in square metres."""
    # Raw area in the projected CRS native unit.
    raw_area = projected_geom.area

    # Convert to sq metres using the unit factor.
    area_sq_m = raw_area * (decision.unit_conversion_to_m ** 2)
    area_ha = area_sq_m / 10_000

    # Rounded value for display.
    display_value = round(area_sq_m, settings.AREA_DISPLAY_DECIMALS)

    return FeatureMeasurementResult(
        **base,
        measurement_type="area",
        measurement_value=display_value,
        measurement_unit="sq_m",
        measurement_status=STATUS_MEASURED,
        measurement_crs=decision.measurement_crs,
        crs_method=(
            f"{decision.method_description} "
            f"Raw area: {raw_area:.6f} {decision.unit}². "
            f"Converted to sq metres: {area_sq_m:.4f} sq m "
            f"({area_ha:.6f} ha)."
        ),
    )


def _measure_length(
    base: dict,
    projected_geom,
    decision: CRSDecision,
) -> FeatureMeasurementResult:
    """Calculate length in metres."""
    raw_length = projected_geom.length

    # Convert to metres.
    length_m = raw_length * decision.unit_conversion_to_m

    display_value = round(length_m, settings.LENGTH_DISPLAY_DECIMALS)

    return FeatureMeasurementResult(
        **base,
        measurement_type="length",
        measurement_value=display_value,
        measurement_unit="m",
        measurement_status=STATUS_MEASURED,
        measurement_crs=decision.measurement_crs,
        crs_method=(
            f"{decision.method_description} "
            f"Raw length: {raw_length:.6f} {decision.unit}. "
            f"Converted to metres: {length_m:.4f} m."
        ),
    )
