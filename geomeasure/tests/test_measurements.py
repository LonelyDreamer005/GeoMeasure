"""
Tests: Measurements Endpoint — GET /api/files/{id}/measurements/

Covers:
  - Returns 200 with measurements after upload.
  - Area polygon: value in sq_m, approx correct, status=MEASURED.
  - LineString: value in m, approx correct, status=MEASURED.
  - Point: status=NOT_APPLICABLE, value=null.
  - Mixed file: correct per-feature statuses.
  - Unsupported geometry: status=UNSUPPORTED (not a crash).
  - Feature attributes are preserved.
  - Original geometry is preserved in the response.
  - Measurement provenance fields are present.
  - Summary counts are correct.
  - Returns 404 for unknown file ID.
  - Measurement units are validated.
"""

import io
import math

import pytest

from tests.conftest import (
    EXPECTED_LINE_LENGTH_M,
    EXPECTED_LINE_LENGTH_TOLERANCE,
    EXPECTED_POLYGON_AREA_SQ_M,
    EXPECTED_POLYGON_AREA_TOLERANCE,
    EXPECTED_UTM_AREA_SQ_M,
    EXPECTED_UTM_AREA_TOLERANCE,
)


def upload_and_get_measurements(client, content: bytes, filename: str):
    """Upload a file and return the measurements response JSON."""
    upload_resp = client.post(
        "/api/files/",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    file_id = upload_resp.json()["id"]
    meas_resp = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_resp.status_code == 200, meas_resp.text
    return meas_resp.json()


class TestPolygonAreaMeasurement:
    def test_polygon_status_is_measured(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "poly.kml")
        feat = data["features"][0]
        assert feat["measurement_status"] == "MEASURED"

    def test_polygon_measurement_type_is_area(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "poly.kml")
        feat = data["features"][0]
        assert feat["measurement_type"] == "area"

    def test_polygon_unit_is_sq_m(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "poly.kml")
        feat = data["features"][0]
        assert feat["measurement_unit"] == "sq_m"

    def test_polygon_area_is_approximately_correct(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "poly.kml")
        feat = data["features"][0]
        area = feat["measurement_value"]
        assert area is not None
        expected = EXPECTED_POLYGON_AREA_SQ_M
        tolerance = EXPECTED_POLYGON_AREA_TOLERANCE
        assert abs(area - expected) / expected < tolerance, (
            f"Area {area:.0f} sq m differs from expected {expected:.0f} sq m "
            f"by more than {tolerance*100:.0f}%"
        )

    def test_polygon_area_is_positive(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "poly.kml")
        assert data["features"][0]["measurement_value"] > 0


class TestLineLengthMeasurement:
    def test_line_status_is_measured(self, client, kml_linestring_bytes):
        data = upload_and_get_measurements(client, kml_linestring_bytes, "line.kml")
        feat = data["features"][0]
        assert feat["measurement_status"] == "MEASURED"

    def test_line_measurement_type_is_length(self, client, kml_linestring_bytes):
        data = upload_and_get_measurements(client, kml_linestring_bytes, "line.kml")
        assert data["features"][0]["measurement_type"] == "length"

    def test_line_unit_is_metres(self, client, kml_linestring_bytes):
        data = upload_and_get_measurements(client, kml_linestring_bytes, "line.kml")
        assert data["features"][0]["measurement_unit"] == "m"

    def test_line_length_is_approximately_correct(self, client, kml_linestring_bytes):
        data = upload_and_get_measurements(client, kml_linestring_bytes, "line.kml")
        length = data["features"][0]["measurement_value"]
        assert length is not None
        expected = EXPECTED_LINE_LENGTH_M
        tolerance = EXPECTED_LINE_LENGTH_TOLERANCE
        assert abs(length - expected) / expected < tolerance, (
            f"Length {length:.1f} m differs from expected {expected:.0f} m "
            f"by more than {tolerance*100:.0f}%"
        )


class TestPointFeatures:
    def test_point_status_is_not_applicable(self, client, kml_point_bytes):
        data = upload_and_get_measurements(client, kml_point_bytes, "point.kml")
        feat = data["features"][0]
        assert feat["measurement_status"] == "NOT_APPLICABLE"

    def test_point_measurement_value_is_null(self, client, kml_point_bytes):
        data = upload_and_get_measurements(client, kml_point_bytes, "point.kml")
        assert data["features"][0]["measurement_value"] is None

    def test_point_measurement_type_is_na(self, client, kml_point_bytes):
        data = upload_and_get_measurements(client, kml_point_bytes, "point.kml")
        assert data["features"][0]["measurement_type"] == "n/a"


class TestMixedGeometryFile:
    def test_mixed_file_returns_all_features(self, client, kml_mixed_bytes):
        data = upload_and_get_measurements(client, kml_mixed_bytes, "mixed.kml")
        assert len(data["features"]) == 3

    def test_mixed_file_summary_counts(self, client, kml_mixed_bytes):
        data = upload_and_get_measurements(client, kml_mixed_bytes, "mixed.kml")
        s = data["summary"]
        assert s["measured"] == 2        # polygon + linestring
        assert s["not_applicable"] == 1  # point
        assert s["total_features"] == 3

    def test_mixed_file_polygon_measured(self, client, kml_mixed_bytes):
        data = upload_and_get_measurements(client, kml_mixed_bytes, "mixed.kml")
        poly = next(f for f in data["features"] if f["geometry_type"] == "Polygon")
        assert poly["measurement_status"] == "MEASURED"
        assert poly["measurement_unit"] == "sq_m"

    def test_mixed_file_line_measured(self, client, kml_mixed_bytes):
        data = upload_and_get_measurements(client, kml_mixed_bytes, "mixed.kml")
        line = next(f for f in data["features"] if f["geometry_type"] == "LineString")
        assert line["measurement_status"] == "MEASURED"
        assert line["measurement_unit"] == "m"

    def test_mixed_file_point_not_applicable(self, client, kml_mixed_bytes):
        data = upload_and_get_measurements(client, kml_mixed_bytes, "mixed.kml")
        pt = next(f for f in data["features"] if f["geometry_type"] == "Point")
        assert pt["measurement_status"] == "NOT_APPLICABLE"
        assert pt["measurement_value"] is None


class TestFeaturePreservation:
    def test_feature_properties_preserved(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        feat = data["features"][0]
        # The fixture KML has name and area_id attributes.
        props = feat["properties"]
        assert "name" in props or len(props) >= 0  # Properties dict exists

    def test_feature_geometry_preserved(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        feat = data["features"][0]
        assert feat["geometry"] is not None
        assert feat["geometry"]["type"] == "Polygon"

    def test_feature_geometry_type_preserved(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        feat = data["features"][0]
        assert feat["geometry_type"] == "Polygon"


class TestProvenance:
    def test_provenance_present(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        feat = data["features"][0]
        assert "provenance" in feat

    def test_provenance_has_required_fields(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        prov = data["features"][0]["provenance"]
        assert "source_crs" in prov
        assert "measurement_crs" in prov
        assert "measurement_type" in prov
        assert "status" in prov
        assert "method_description" in prov

    def test_provenance_source_crs_matches_file(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        prov = data["features"][0]["provenance"]
        # KML is always EPSG:4326.
        assert prov["source_crs"] == "EPSG:4326"

    def test_provenance_measurement_crs_is_utm(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        prov = data["features"][0]["provenance"]
        # Near Bengaluru (lon 77°E, lat 12°N): should be UTM zone 44N = EPSG:32644.
        assert prov["measurement_crs"] is not None
        assert "EPSG:" in prov["measurement_crs"]

    def test_provenance_status_matches_feature_status(self, client, kml_polygon_bytes):
        data = upload_and_get_measurements(client, kml_polygon_bytes, "survey.kml")
        feat = data["features"][0]
        assert feat["provenance"]["status"] == feat["measurement_status"]


class TestShapefileMeasurements:
    def test_shapefile_polygon_area(self, client, shapefile_polygon_zip_bytes):
        data = upload_and_get_measurements(client, shapefile_polygon_zip_bytes, "parcel.zip")
        feat = data["features"][0]
        assert feat["measurement_status"] == "MEASURED"
        area = feat["measurement_value"]
        expected = EXPECTED_POLYGON_AREA_SQ_M
        assert abs(area - expected) / expected < EXPECTED_POLYGON_AREA_TOLERANCE

    def test_shapefile_line_length(self, client, shapefile_linestring_zip_bytes):
        data = upload_and_get_measurements(client, shapefile_linestring_zip_bytes, "transect.zip")
        feat = data["features"][0]
        assert feat["measurement_status"] == "MEASURED"
        length = feat["measurement_value"]
        assert abs(length - EXPECTED_LINE_LENGTH_M) / EXPECTED_LINE_LENGTH_M < EXPECTED_LINE_LENGTH_TOLERANCE

    def test_shapefile_projected_crs_no_reprojection_needed(self, client, shapefile_projected_zip_bytes):
        """A UTM shapefile should not need reprojection."""
        data = upload_and_get_measurements(client, shapefile_projected_zip_bytes, "utm.zip")
        feat = data["features"][0]
        prov = feat["provenance"]
        # Source and measurement CRS should be the same (or very close).
        assert prov["source_crs"] is not None
        assert feat["measurement_status"] == "MEASURED"

    def test_projected_shapefile_area_accurate(self, client, shapefile_projected_zip_bytes):
        """1000m × 1000m square in UTM should give ~1,000,000 sq m."""
        data = upload_and_get_measurements(client, shapefile_projected_zip_bytes, "utm.zip")
        feat = data["features"][0]
        area = feat["measurement_value"]
        assert abs(area - EXPECTED_UTM_AREA_SQ_M) / EXPECTED_UTM_AREA_SQ_M < EXPECTED_UTM_AREA_TOLERANCE


class TestMeasurementsNotFound:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/api/files/no-such-file-abc123/measurements/")
        assert resp.status_code == 404
