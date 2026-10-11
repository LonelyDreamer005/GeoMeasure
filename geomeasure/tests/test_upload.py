"""
Tests: File Upload Endpoint — POST /api/files/

Covers:
  - Valid KML upload returns 201 with correct structure.
  - Valid zipped Shapefile upload returns 201.
  - Unsupported file type returns 400.
  - Corrupt ZIP returns 400.
  - ZIP with no .shp returns 400.
  - Empty file returns 400.
  - Response contains required fields (id, filename, status, feature_count, crs).
"""

import io

import pytest


def upload(client, content: bytes, filename: str):
    """Helper: POST a file upload."""
    return client.post(
        "/api/files/",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


class TestKMLUpload:
    def test_valid_kml_returns_201(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "survey.kml")
        assert resp.status_code == 201

    def test_valid_kml_response_has_required_fields(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "survey.kml")
        data = resp.json()
        assert "id" in data
        assert "filename" in data
        assert "status" in data
        assert "feature_count" in data
        assert "crs" in data
        assert "created_at" in data

    def test_valid_kml_status_completed(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "survey.kml")
        assert resp.json()["status"] == "COMPLETED"

    def test_valid_kml_filename_preserved(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "my_survey.kml")
        assert resp.json()["filename"] == "my_survey.kml"

    def test_valid_kml_crs_is_epsg4326(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "survey.kml")
        assert resp.json()["crs"] == "EPSG:4326"

    def test_valid_kml_feature_count(self, client, kml_polygon_bytes):
        resp = upload(client, kml_polygon_bytes, "survey.kml")
        assert resp.json()["feature_count"] == 1

    def test_valid_kml_id_is_unique(self, client, kml_polygon_bytes):
        r1 = upload(client, kml_polygon_bytes, "survey.kml").json()
        r2 = upload(client, kml_polygon_bytes, "survey.kml").json()
        assert r1["id"] != r2["id"]


class TestShapefileUpload:
    def test_valid_shapefile_zip_returns_201(self, client, shapefile_polygon_zip_bytes):
        resp = upload(client, shapefile_polygon_zip_bytes, "parcel.zip")
        assert resp.status_code == 201

    def test_valid_shapefile_status_completed(self, client, shapefile_polygon_zip_bytes):
        resp = upload(client, shapefile_polygon_zip_bytes, "parcel.zip")
        assert resp.json()["status"] == "COMPLETED"

    def test_valid_shapefile_has_crs(self, client, shapefile_polygon_zip_bytes):
        resp = upload(client, shapefile_polygon_zip_bytes, "parcel.zip")
        assert resp.json()["crs"] is not None

    def test_valid_shapefile_feature_count(self, client, shapefile_polygon_zip_bytes):
        resp = upload(client, shapefile_polygon_zip_bytes, "parcel.zip")
        assert resp.json()["feature_count"] == 1


class TestUploadRejections:
    def test_unsupported_extension_returns_400(self, client, unsupported_file_bytes):
        resp = upload(client, unsupported_file_bytes, "image.png")
        assert resp.status_code == 400

    def test_corrupt_zip_returns_400(self, client, corrupt_zip_bytes):
        resp = upload(client, corrupt_zip_bytes, "bad.zip")
        assert resp.status_code == 400

    def test_zip_without_shp_returns_400(self, client, zip_no_shp_bytes):
        resp = upload(client, zip_no_shp_bytes, "no_shp.zip")
        assert resp.status_code == 400

    def test_empty_file_returns_400(self, client):
        resp = upload(client, b"", "empty.kml")
        assert resp.status_code == 400

    def test_wrong_extension_for_kml_content_returns_400(self, client, kml_polygon_bytes):
        # KML bytes but with a .zip extension — should fail extension cross-check.
        resp = upload(client, kml_polygon_bytes, "survey.zip")
        # The ZIP magic check will fail since KML bytes aren't a ZIP.
        assert resp.status_code in (400, 422)
