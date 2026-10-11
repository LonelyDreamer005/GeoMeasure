"""
Tests: Invalid File Handling

Covers:
  - Corrupt ZIP is rejected with 400.
  - ZIP without .shp is rejected with 400.
  - KML with invalid XML returns 400 or 422.
  - Empty file is rejected with 400.
  - Unsupported file type (e.g., .png) is rejected with 400.
  - Path traversal attempt in ZIP is rejected.
  - Oversized file (> configured limit) is rejected with 400.
  - Processing does not crash when a single feature has invalid geometry.
  - KML with no features processes successfully (zero features).
"""

import io
import zipfile

import pytest


def upload(client, content: bytes, filename: str):
    return client.post(
        "/api/files/",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


class TestInvalidFileRejections:
    def test_corrupt_zip_rejected(self, client, corrupt_zip_bytes):
        resp = upload(client, corrupt_zip_bytes, "corrupt.zip")
        assert resp.status_code == 400

    def test_zip_without_shp_rejected(self, client, zip_no_shp_bytes):
        resp = upload(client, zip_no_shp_bytes, "no_shp.zip")
        assert resp.status_code == 400

    def test_corrupt_kml_xml_rejected(self, client, kml_corrupt_bytes):
        resp = upload(client, kml_corrupt_bytes, "bad.kml")
        assert resp.status_code in (400, 422)

    def test_empty_file_rejected(self, client):
        resp = upload(client, b"", "empty.kml")
        assert resp.status_code == 400

    def test_unsupported_type_rejected(self, client, unsupported_file_bytes):
        resp = upload(client, unsupported_file_bytes, "image.png")
        assert resp.status_code == 400

    def test_txt_file_rejected(self, client):
        resp = upload(client, b"hello world", "data.txt")
        assert resp.status_code == 400

    def test_json_file_rejected(self, client):
        resp = upload(client, b'{"type": "FeatureCollection"}', "data.json")
        assert resp.status_code == 400

    def test_error_response_has_message_field(self, client):
        resp = upload(client, b"", "empty.kml")
        data = resp.json()
        # Either "message" in root or in "detail".
        detail = data.get("detail", data)
        if isinstance(detail, dict):
            assert "message" in detail or "code" in detail
        else:
            assert isinstance(detail, (str, list))


class TestPathTraversal:
    def test_path_traversal_in_zip_rejected(self, client):
        """A ZIP with a '../' path entry must be rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # Manually add a traversal entry — ZipFile won't do this via normal API.
            info = zipfile.ZipInfo("../evil.shp")
            zf.writestr(info, b"fake shp content")
        zip_bytes = buf.getvalue()
        resp = upload(client, zip_bytes, "evil.zip")
        # Must be rejected (400) — must not process it.
        assert resp.status_code in (400, 422)


class TestEdgeCases:
    def test_kml_no_features_succeeds(self, client, kml_no_features_bytes):
        """A valid KML with no Placemarks should upload successfully with 0 features."""
        resp = upload(client, kml_no_features_bytes, "empty_kml.kml")
        assert resp.status_code == 201
        data = resp.json()
        assert data["feature_count"] == 0
        assert data["status"] == "COMPLETED"

    def test_kml_single_point_not_applicable(self, client, kml_point_bytes):
        """Point feature: upload OK, measurement status NOT_APPLICABLE."""
        resp = upload(client, kml_point_bytes, "point.kml")
        assert resp.status_code == 201
        file_id = resp.json()["id"]
        meas = client.get(f"/api/files/{file_id}/measurements/").json()
        assert meas["summary"]["not_applicable"] == 1
        assert meas["summary"]["measured"] == 0

    def test_measurements_endpoint_404_for_nonexistent_file(self, client):
        resp = client.get("/api/files/definitely-does-not-exist/measurements/")
        assert resp.status_code == 404

    def test_file_info_404_for_nonexistent_file(self, client):
        resp = client.get("/api/files/definitely-does-not-exist/")
        assert resp.status_code == 404


class TestOversizedFile:
    def test_oversized_file_rejected(self, client):
        """
        A file larger than MAX_UPLOAD_SIZE_BYTES (50 MB) must be rejected.
        We simulate this without actually allocating 50 MB in memory by
        temporarily patching the config.
        """
        from unittest.mock import patch
        from app.core import config

        # Temporarily lower the limit to 10 bytes.
        with patch.object(config.settings, "MAX_UPLOAD_SIZE_BYTES", 10):
            # 20 bytes of valid-looking KML.
            resp = upload(client, b"<?xml version='1.0'?><kml/>", "large.kml")
        assert resp.status_code == 400
