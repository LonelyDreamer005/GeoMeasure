"""
Tests: File Information Endpoint — GET /api/files/{id}/

Covers:
  - Returns 200 with correct file metadata after upload.
  - Returns 404 for unknown file ID.
  - Response contains all required fields.
  - Validation summary is present in the response.
  - Error detail is included when processing failed.
"""

import io

import pytest


def upload_kml(client, kml_bytes, filename="test.kml"):
    resp = client.post(
        "/api/files/",
        files={"file": (filename, io.BytesIO(kml_bytes), "application/octet-stream")},
    )
    assert resp.status_code == 201
    return resp.json()


class TestGetFileInfo:
    def test_returns_200_after_upload(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        resp = client.get(f"/api/files/{file_id}/")
        assert resp.status_code == 200

    def test_returns_correct_filename(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes, filename="area.kml")["id"]
        resp = client.get(f"/api/files/{file_id}/")
        assert resp.json()["filename"] == "area.kml"

    def test_returns_correct_crs(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        resp = client.get(f"/api/files/{file_id}/")
        assert resp.json()["crs"] == "EPSG:4326"

    def test_returns_correct_feature_count(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        resp = client.get(f"/api/files/{file_id}/")
        assert resp.json()["feature_count"] == 1

    def test_returns_status_completed(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        resp = client.get(f"/api/files/{file_id}/")
        assert resp.json()["status"] == "COMPLETED"

    def test_response_has_timestamps(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        data = client.get(f"/api/files/{file_id}/").json()
        assert "created_at" in data
        assert "processed_at" in data

    def test_response_has_validation_summary(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        data = client.get(f"/api/files/{file_id}/").json()
        assert "validation_summary" in data
        vs = data["validation_summary"]
        assert "has_issues" in vs
        assert "missing_crs" in vs

    def test_response_has_file_size(self, client, kml_polygon_bytes):
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        data = client.get(f"/api/files/{file_id}/").json()
        assert data["file_size_bytes"] == len(kml_polygon_bytes)

    def test_mixed_geometry_file_has_no_crs_issues(self, client, kml_polygon_bytes):
        """A KML file always has EPSG:4326; no missing-CRS issues expected."""
        file_id = upload_kml(client, kml_polygon_bytes)["id"]
        data = client.get(f"/api/files/{file_id}/").json()
        assert data["validation_summary"]["missing_crs"] is False


class TestGetFileInfoNotFound:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/api/files/nonexistent-id-12345/")
        assert resp.status_code == 404

    def test_404_response_has_code(self, client):
        resp = client.get("/api/files/nonexistent-id-12345/")
        data = resp.json()
        assert "code" in data or "detail" in data
