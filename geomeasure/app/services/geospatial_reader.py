"""
GeoMeasure — Geospatial File Reader

Reads KML files and zipped Shapefiles, returning a normalised list of
raw feature dictionaries that downstream services can process uniformly.

KML support strategy
────────────────────
The installed GDAL/Fiona build on this Windows machine does not include
the KML or LIBKML drivers (common in pre-built wheels without full GDAL).
Therefore KML is parsed directly using Python's standard-library
xml.etree.ElementTree together with lxml for namespace handling.

This approach:
  • Extracts Placemark features with their names, descriptions, and extended data.
  • Supports all geometry types defined in the KML 2.2 spec:
      Point, LineString, LinearRing, Polygon, MultiGeometry.
  • Handles namespace prefixes from both Google and OGC KML schemas.
  • Always reports EPSG:4326 as the CRS (KML is, by specification,
    geographic WGS 84; coordinates are longitude, latitude, altitude).

Shapefile support strategy
──────────────────────────
Shapefiles are read via GeoPandas (backed by pyogrio). The caller must
first extract the ZIP to a temporary directory using the safe extraction
helper in file_validation.py.  GeoPandas automatically reads the .prj
file when present.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import geopandas as gpd
from shapely.geometry import mapping

from app.core.exceptions import (
    InvalidZipArchiveError,
    KMLParseError,
    MissingShapefileComponentError,
    ShapefileReadError,
)


# ── KML namespace map ─────────────────────────────────────────────────────────
# KML 2.2 (Google Earth) and OGC KML both use this namespace.
_KML_NS = "http://www.opengis.net/kml/2.2"
_KML_NS_GOOGLE = "http://earth.google.com/kml/2.2"
_ALT_NS = "http://earth.google.com/kml/2.1"

_ALL_KML_NAMESPACES = [_KML_NS, _KML_NS_GOOGLE, _ALT_NS]


# ── Public API ────────────────────────────────────────────────────────────────

def read_kml(content: bytes) -> dict:
    """
    Parse KML bytes and return a normalised dataset dict.

    Returns
    -------
    dict with keys:
      ``features``     — list of raw feature dicts (see _parse_placemark).
      ``source_crs``   — always "EPSG:4326" (KML is defined as WGS 84).
      ``feature_count`` — int.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise KMLParseError(f"KML XML is not well-formed: {exc}") from exc

    # Detect which namespace the document uses.
    ns = _detect_kml_namespace(root)

    features = []
    index = 0
    # Walk every Placemark anywhere in the document tree.
    for placemark in root.iter(f"{{{ns}}}Placemark"):
        feat = _parse_placemark(placemark, ns, index)
        features.append(feat)
        index += 1

    if not features:
        # Document parsed but contained no Placemarks — not an error per se,
        # but we flag it so the caller can decide how to handle it.
        pass

    return {
        "features": features,
        "source_crs": "EPSG:4326",  # KML 2.2 §4: coordinates in WGS 84
        "feature_count": len(features),
    }


def read_shapefile_from_zip(content: bytes) -> dict:
    """
    Extract a ZIP archive in memory, locate the .shp file, and read it
    with GeoPandas.

    Returns
    -------
    dict with keys:
      ``features``      — list of raw feature dicts.
      ``source_crs``    — CRS string (e.g. "EPSG:4326") or None.
      ``feature_count`` — int.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise InvalidZipArchiveError(f"Cannot open ZIP: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="geomeasure_shp_") as tmpdir:
        tmp_path = Path(tmpdir)
        try:
            with zf:
                zf.extractall(tmp_path)
        except Exception as exc:
            raise InvalidZipArchiveError(f"ZIP extraction failed: {exc}") from exc

        shp_files = list(tmp_path.rglob("*.shp"))
        if not shp_files:
            raise MissingShapefileComponentError(
                "No .shp file found after extracting the ZIP archive."
            )

        shp_path = shp_files[0]
        try:
            gdf = gpd.read_file(shp_path)
        except Exception as exc:
            raise ShapefileReadError(
                f"GeoPandas could not read the Shapefile: {exc}"
            ) from exc

        crs_str = _crs_to_string(gdf.crs)
        features = _gdf_to_features(gdf)

    return {
        "features": features,
        "source_crs": crs_str,
        "feature_count": len(features),
    }


# ── KML parsing helpers ───────────────────────────────────────────────────────

def _detect_kml_namespace(root: ET.Element) -> str:
    """Return the KML namespace URI used by the document root."""
    tag = root.tag
    if tag.startswith("{"):
        ns_uri = tag[1: tag.index("}")]
        if ns_uri in _ALL_KML_NAMESPACES:
            return ns_uri
        # Non-standard but XML-namespaced KML — use whatever the root declares.
        return ns_uri
    # No namespace — common in simplified KML exports.
    return ""


def _parse_placemark(pm: ET.Element, ns: str, index: int) -> dict:
    """
    Extract geometry and properties from a single KML Placemark element.

    Returns a raw feature dict:
      feature_index, feature_id, name, description, extended_data,
      geometry_type, geometry (GeoJSON-style dict or None), properties.
    """
    def _tag(local: str) -> str:
        return f"{{{ns}}}{local}" if ns else local

    # ── Properties ────────────────────────────────────────────────────────────
    name_el = pm.find(_tag("name"))
    name = name_el.text.strip() if name_el is not None and name_el.text else None

    desc_el = pm.find(_tag("description"))
    description = (
        desc_el.text.strip() if desc_el is not None and desc_el.text else None
    )

    # Parse ExtendedData / SchemaData key-value pairs.
    extended_data: dict[str, Any] = {}
    for ed in pm.iter(_tag("SimpleData")):
        attr_name = ed.get("name", "")
        extended_data[attr_name] = ed.text

    # feature_id: prefer id attribute, then name, then index.
    fid = pm.get("id") or name or str(index)

    properties: dict[str, Any] = {}
    if name:
        properties["name"] = name
    if description:
        properties["description"] = description
    properties.update(extended_data)

    # ── Geometry ──────────────────────────────────────────────────────────────
    geom_dict, geom_type = _extract_kml_geometry(pm, ns)

    return {
        "feature_index": index,
        "feature_id": fid,
        "geometry_type": geom_type,
        "geometry": geom_dict,
        "properties": properties,
    }


def _extract_kml_geometry(pm: ET.Element, ns: str) -> tuple[dict | None, str | None]:
    """
    Find the first recognised geometry element in a Placemark and convert
    it to a GeoJSON-compatible dict.

    Returns (geom_dict, geometry_type_str).
    """
    def _tag(local: str) -> str:
        return f"{{{ns}}}{local}" if ns else local

    # Priority order matches KML spec nesting.
    for geom_type_name, extractor in [
        ("Point", _parse_point),
        ("LineString", _parse_linestring),
        ("LinearRing", _parse_linearring),
        ("Polygon", _parse_polygon),
        ("MultiGeometry", _parse_multigeometry),
    ]:
        el = pm.find(_tag(geom_type_name))
        if el is not None:
            try:
                coords = extractor(el, ns)
                return coords, _kml_type_to_geojson_type(geom_type_name)
            except Exception:
                return None, geom_type_name

    return None, None


def _parse_coords(coord_text: str) -> list[list[float]]:
    """
    Parse a KML <coordinates> text block into a list of [lon, lat] or
    [lon, lat, alt] coordinate pairs.

    KML coordinates: "lon,lat[,alt] lon,lat[,alt] …"  (space-separated tuples)
    """
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                coords.append([lon, lat])
            except ValueError:
                continue
    return coords


def _parse_point(el: ET.Element, ns: str) -> dict:
    coord_tag = f"{{{ns}}}coordinates" if ns else "coordinates"
    coords_el = el.find(coord_tag)
    if coords_el is None or not coords_el.text:
        raise KMLParseError("Point has no coordinates.")
    pts = _parse_coords(coords_el.text)
    if not pts:
        raise KMLParseError("Could not parse Point coordinates.")
    return {"type": "Point", "coordinates": pts[0]}


def _parse_linestring(el: ET.Element, ns: str) -> dict:
    coord_tag = f"{{{ns}}}coordinates" if ns else "coordinates"
    coords_el = el.find(coord_tag)
    if coords_el is None or not coords_el.text:
        raise KMLParseError("LineString has no coordinates.")
    pts = _parse_coords(coords_el.text)
    return {"type": "LineString", "coordinates": pts}


def _parse_linearring(el: ET.Element, ns: str) -> dict:
    coord_tag = f"{{{ns}}}coordinates" if ns else "coordinates"
    coords_el = el.find(coord_tag)
    if coords_el is None or not coords_el.text:
        raise KMLParseError("LinearRing has no coordinates.")
    pts = _parse_coords(coords_el.text)
    return {"type": "LineString", "coordinates": pts}


def _parse_polygon(el: ET.Element, ns: str) -> dict:
    coord_tag = f"{{{ns}}}coordinates" if ns else "coordinates"
    outer_ring_tag = f"{{{ns}}}outerBoundaryIs" if ns else "outerBoundaryIs"
    inner_ring_tag = f"{{{ns}}}innerBoundaryIs" if ns else "innerBoundaryIs"
    linear_ring_tag = f"{{{ns}}}LinearRing" if ns else "LinearRing"

    rings = []

    outer = el.find(f"{outer_ring_tag}/{linear_ring_tag}/{coord_tag}")
    if outer is None or not outer.text:
        # Fallback: look for coordinates directly inside Polygon
        outer = el.find(coord_tag)
    if outer is not None and outer.text:
        rings.append(_parse_coords(outer.text))

    for inner_el in el.findall(f"{inner_ring_tag}/{linear_ring_tag}/{coord_tag}"):
        if inner_el.text:
            rings.append(_parse_coords(inner_el.text))

    if not rings:
        raise KMLParseError("Polygon has no parseable rings.")

    return {"type": "Polygon", "coordinates": rings}


def _parse_multigeometry(el: ET.Element, ns: str) -> dict:
    """Convert a KML MultiGeometry to a GeoJSON GeometryCollection."""
    geometries = []
    for child in el:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        for geom_type_name, extractor in [
            ("Point", _parse_point),
            ("LineString", _parse_linestring),
            ("Polygon", _parse_polygon),
            ("LinearRing", _parse_linearring),
        ]:
            if local == geom_type_name:
                try:
                    geometries.append(extractor(child, ns))
                except Exception:
                    pass
    return {"type": "GeometryCollection", "geometries": geometries}


def _kml_type_to_geojson_type(kml_type: str) -> str:
    mapping_table = {
        "Point": "Point",
        "LineString": "LineString",
        "LinearRing": "LineString",
        "Polygon": "Polygon",
        "MultiGeometry": "GeometryCollection",
    }
    return mapping_table.get(kml_type, kml_type)


# ── Shapefile helpers ─────────────────────────────────────────────────────────

def _crs_to_string(crs) -> str | None:
    """Convert a GeoPandas/PyProj CRS object to a human-readable string."""
    if crs is None:
        return None
    try:
        # Try to get the EPSG code first.
        epsg = crs.to_epsg()
        if epsg:
            return f"EPSG:{epsg}"
        # Fallback to WKT name / authority code.
        return crs.name
    except Exception:
        return str(crs)


def _gdf_to_features(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Convert a GeoPandas GeoDataFrame to a list of raw feature dicts."""
    features = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        geom_dict = None
        geom_type = None

        if geom is not None and not geom.is_empty:
            try:
                geom_dict = mapping(geom)
                geom_type = geom.geom_type
            except Exception:
                geom_dict = None
                geom_type = None

        # Properties: all non-geometry columns.
        props = {}
        for col in gdf.columns:
            if col != "geometry":
                val = row[col]
                # Ensure values are JSON-serialisable.
                try:
                    import json
                    json.dumps(val)
                    props[col] = val
                except (TypeError, ValueError):
                    props[col] = str(val)

        # Try to extract a feature ID from common columns.
        fid_candidates = ["id", "ID", "fid", "FID", "OBJECTID", "objectid"]
        fid = None
        for c in fid_candidates:
            if c in props and props[c] is not None:
                fid = str(props[c])
                break
        if fid is None:
            fid = str(idx)

        features.append(
            {
                "feature_index": int(idx),
                "feature_id": fid,
                "geometry_type": geom_type,
                "geometry": geom_dict,
                "properties": props,
            }
        )
    return features
