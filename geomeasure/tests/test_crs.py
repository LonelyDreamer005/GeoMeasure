"""
Tests: CRS Handling and Transformation

Covers:
  - Geographic source CRS (EPSG:4326) triggers reprojection.
  - Projected source CRS skips reprojection.
  - Missing CRS produces FAILED measurement with clear provenance.
  - UTM zone selection is correct for Indian subcontinent geometry.
  - Measurement values after reprojection are in metres / sq metres.
  - Provenance correctly identifies source and measurement CRS.
"""

import io

import pytest

from app.services.crs_service import select_measurement_crs


class TestCRSServiceUnit:
    """Unit tests for the CRS selection service (no HTTP layer)."""

    def test_geographic_crs_triggers_transform(self):
        geom = {"type": "Point", "coordinates": [77.5, 12.5]}
        decision = select_measurement_crs("EPSG:4326", geom)
        assert decision.needs_transform is True
        assert decision.can_measure is True

    def test_projected_crs_no_transform(self):
        geom = {"type": "Point", "coordinates": [500000, 1327000]}
        decision = select_measurement_crs("EPSG:32644", geom)
        assert decision.needs_transform is False
        assert decision.can_measure is True

    def test_missing_crs_cannot_measure(self):
        decision = select_measurement_crs(None, None)
        assert decision.can_measure is False
        assert decision.measurement_crs is None
        assert "no crs" in decision.method_description.lower() or "no coordinate" in decision.method_description.lower()

    def test_utm_zone_43n_for_bengaluru(self):
        """lon=77.5°E, lat=12.5°N → UTM Zone 43N (EPSG:32643).

        UTM zone = floor((lon + 180) / 6) + 1
               = floor((77.5 + 180) / 6) + 1
               = floor(42.916) + 1 = 42 + 1 = 43.
        Northern hemisphere → EPSG:326**43**.
        """
        geom = {"type": "Point", "coordinates": [77.5, 12.5]}
        decision = select_measurement_crs("EPSG:4326", geom)
        assert decision.measurement_crs == "EPSG:32643"

    def test_utm_zone_43n_for_mumbai(self):
        """lon=72.8°E, lat=18.9°N → UTM Zone 43N (EPSG:32643).

        zone = floor((72.8 + 180) / 6) + 1 = floor(42.13) + 1 = 43.
        """
        geom = {"type": "Point", "coordinates": [72.8, 18.9]}
        decision = select_measurement_crs("EPSG:4326", geom)
        assert decision.measurement_crs == "EPSG:32643"

    def test_utm_southern_hemisphere(self):
        """lon=30°E, lat=-10°S → UTM Zone 36S (EPSG:32736)."""
        geom = {"type": "Point", "coordinates": [30.0, -10.0]}
        decision = select_measurement_crs("EPSG:4326", geom)
        assert decision.measurement_crs == "EPSG:32736"

    def test_large_span_falls_back_to_mercator(self):
        """A polygon spanning >20° longitude should fall back to EPSG:3857."""
        geom = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [30, 0], [30, 10], [0, 10], [0, 0]]],
        }
        decision = select_measurement_crs("EPSG:4326", geom)
        assert decision.measurement_crs == "EPSG:3857"

    def test_measurement_unit_is_metres_after_utm(self):
        geom = {"type": "Point", "coordinates": [77.5, 12.5]}
        decision = select_measurement_crs("EPSG:4326", geom)
        # UTM zones always use metres.
        assert "metre" in decision.unit.lower() or decision.unit_conversion_to_m == 1.0

    def test_invalid_crs_string_cannot_measure(self):
        decision = select_measurement_crs("EPSG:999999", None)
        assert decision.can_measure is False

    def test_method_description_is_nonempty(self):
        geom = {"type": "Point", "coordinates": [77.5, 12.5]}
        decision = select_measurement_crs("EPSG:4326", geom)
        assert len(decision.method_description) > 10


class TestCRSViaAPI:
    """Integration tests checking CRS behaviour through the full API."""

    def _upload_kml(self, client, kml_bytes, filename="crs_test.kml"):
        resp = client.post(
            "/api/files/",
            files={"file": (filename, io.BytesIO(kml_bytes), "application/octet-stream")},
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_kml_source_crs_is_epsg4326(self, client, kml_polygon_bytes):
        file_id = self._upload_kml(client, kml_polygon_bytes)
        info = client.get(f"/api/files/{file_id}/").json()
        assert info["crs"] == "EPSG:4326"

    def test_kml_measurement_uses_utm_not_degrees(self, client, kml_polygon_bytes):
        """If CRS were not transformed, the area in degrees² would be tiny (~0.0001)."""
        file_id = self._upload_kml(client, kml_polygon_bytes)
        data = client.get(f"/api/files/{file_id}/measurements/").json()
        area = data["features"][0]["measurement_value"]
        # In degrees², a 0.01°×0.01° polygon ≈ 0.0001 deg².
        # In sq metres it should be ~1.2 million.
        # If the value is < 1 we know we measured in degrees, which is wrong.
        assert area > 1_000, (
            f"Area {area} sq m is suspiciously small — may not have been reprojected."
        )

    def test_provenance_confirms_reprojection(self, client, kml_polygon_bytes):
        file_id = self._upload_kml(client, kml_polygon_bytes)
        data = client.get(f"/api/files/{file_id}/measurements/").json()
        prov = data["features"][0]["provenance"]
        assert prov["source_crs"] == "EPSG:4326"
        # Measurement CRS must differ (because source is geographic).
        assert prov["measurement_crs"] != "EPSG:4326"

    def test_shapefile_with_projected_crs_no_reprojection(
        self, client, shapefile_projected_zip_bytes
    ):
        resp = client.post(
            "/api/files/",
            files={"file": ("utm.zip", io.BytesIO(shapefile_projected_zip_bytes), "application/octet-stream")},
        )
        assert resp.status_code == 201
        file_id = resp.json()["id"]
        data = client.get(f"/api/files/{file_id}/measurements/").json()
        prov = data["features"][0]["provenance"]
        # Source and measurement CRS should be the same for a projected source.
        assert prov["source_crs"] == prov["measurement_crs"]
