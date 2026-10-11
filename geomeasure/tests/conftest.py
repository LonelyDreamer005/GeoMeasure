"""
GeoMeasure — Test Configuration and Fixtures

All test fixtures live here so individual test files stay focused on
behaviour, not setup.  Fixtures provide:

  - A fresh in-memory SQLite database for each test session.
  - A FastAPI TestClient pre-wired to use the test database.
  - Deterministic sample files (KML bytes, Shapefile ZIP bytes) with
    known geometries and expected measurement values.

Known geometry values (verified by hand calculation)
─────────────────────────────────────────────────────
Polygon fixture:
  A 1-degree × 1-degree square near Bengaluru, India (lat ~12–13°N,
  lon ~77–78°E), which falls in UTM Zone 44N (EPSG:32644).

  The UTM-projected area will be approximately 12,300 km² for a 1°×1°
  cell at this latitude (cos(12.5°) ≈ 0.9763, so the x-width is about
  111,320 × 0.9763 ≈ 108,700 m, and the y-height is about 111,320 m).
  Expected: ~1.21e10 sq m ± 5 % (latitude-dependent distortion).

  In tests we use a much smaller polygon (0.01° × 0.01°) to keep
  absolute error small:
    Expected area ≈ 1,083,000 m² (roughly 108 ha), ±5 %.

LineString fixture:
  A ~1° segment from (77.0, 12.0) to (77.0, 12.01), oriented N-S.
  Expected length ≈ 1,113 m ± 1 %.
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, get_db
from app.main import app


# ── In-memory test database ───────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite://"   # in-memory, discarded after each session


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    # Import models so they are registered before create_all.
    import app.models.file_record  # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine):
    """Provide a database session that rolls back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(bind=connection)
    session = TestingSessionLocal()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with the test database session injected."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # Transaction rollback handled by db_session fixture.

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── KML sample files ──────────────────────────────────────────────────────────

@pytest.fixture
def kml_polygon_bytes() -> bytes:
    """
    KML file with a single 0.01°×0.01° Polygon near Bengaluru.
    Expected area after UTM projection: ~1,200,000 m² ± 5 %.
    """
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Test Polygon</name>
    <Placemark id="poly1">
      <name>Test Survey Area</name>
      <description>A 0.01 degree square polygon near Bengaluru</description>
      <ExtendedData>
        <SchemaData>
          <SimpleData name="area_id">A001</SimpleData>
          <SimpleData name="survey_type">land_parcel</SimpleData>
        </SchemaData>
      </ExtendedData>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.0,12.0,0
              77.01,12.0,0
              77.01,12.01,0
              77.0,12.01,0
              77.0,12.0,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>"""


@pytest.fixture
def kml_linestring_bytes() -> bytes:
    """
    KML with a single N-S LineString ~1.113 km long near Bengaluru.
    Expected length after UTM projection: ~1,113 m ± 1 %.
    """
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Test LineString</name>
    <Placemark id="line1">
      <name>Survey Transect</name>
      <LineString>
        <coordinates>
          77.0,12.0,0
          77.0,12.01,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>"""


@pytest.fixture
def kml_point_bytes() -> bytes:
    """KML with a single Point feature."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark id="pt1">
      <name>Ground Control Point</name>
      <Point>
        <coordinates>77.5,12.5,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""


@pytest.fixture
def kml_mixed_bytes() -> bytes:
    """KML with polygon, linestring, and point in the same file."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark id="poly1">
      <name>Area Feature</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.0,12.0,0 77.01,12.0,0 77.01,12.01,0 77.0,12.01,0 77.0,12.0,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark id="line1">
      <name>Path Feature</name>
      <LineString>
        <coordinates>77.0,12.0,0 77.0,12.01,0</coordinates>
      </LineString>
    </Placemark>
    <Placemark id="pt1">
      <name>Point Feature</name>
      <Point>
        <coordinates>77.5,12.5,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""


@pytest.fixture
def kml_no_features_bytes() -> bytes:
    """Valid KML document with no Placemarks."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Empty Document</name>
  </Document>
</kml>"""


@pytest.fixture
def kml_corrupt_bytes() -> bytes:
    """Malformed XML that will fail parsing."""
    return b"<kml><this is not valid XML & will crash parsers"


# ── Shapefile ZIP fixtures ────────────────────────────────────────────────────

@pytest.fixture
def shapefile_polygon_zip_bytes() -> bytes:
    """
    In-memory ZIP containing a minimal polygon Shapefile (EPSG:4326).

    The Shapefile components are generated using GeoPandas + Fiona so
    that they are valid binary Shapefiles — no hand-crafting of binary
    formats required.
    """
    import geopandas as gpd
    import tempfile
    from pathlib import Path
    from shapely.geometry import Polygon

    polygon = Polygon([
        (77.0, 12.0), (77.01, 12.0), (77.01, 12.01), (77.0, 12.01), (77.0, 12.0)
    ])
    gdf = gpd.GeoDataFrame(
        [{"id": 1, "name": "Test Parcel", "geometry": polygon}],
        crs="EPSG:4326",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / "test_parcel.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        # Pack into a ZIP in memory.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).iterdir():
                zf.write(f, f.name)
        return buf.getvalue()


@pytest.fixture
def shapefile_linestring_zip_bytes() -> bytes:
    """In-memory ZIP with a LineString Shapefile (EPSG:4326)."""
    import geopandas as gpd
    import tempfile
    from pathlib import Path
    from shapely.geometry import LineString

    line = LineString([(77.0, 12.0), (77.0, 12.01)])
    gdf = gpd.GeoDataFrame(
        [{"id": 1, "name": "Test Transect", "geometry": line}],
        crs="EPSG:4326",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / "test_transect.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).iterdir():
                zf.write(f, f.name)
        return buf.getvalue()


@pytest.fixture
def shapefile_projected_zip_bytes() -> bytes:
    """Shapefile already in a projected CRS (UTM Zone 44N, EPSG:32644)."""
    import geopandas as gpd
    import tempfile
    from pathlib import Path
    from shapely.geometry import Polygon

    # A square 1000m × 1000m in UTM coordinates.
    polygon = Polygon([
        (500000, 1327000), (501000, 1327000),
        (501000, 1328000), (500000, 1328000), (500000, 1327000)
    ])
    gdf = gpd.GeoDataFrame(
        [{"id": 1, "name": "UTM Parcel", "geometry": polygon}],
        crs="EPSG:32644",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / "utm_parcel.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).iterdir():
                zf.write(f, f.name)
        return buf.getvalue()


@pytest.fixture
def corrupt_zip_bytes() -> bytes:
    """Bytes that are not a valid ZIP archive."""
    return b"PK this is not a real zip file just garbage bytes"


@pytest.fixture
def zip_no_shp_bytes() -> bytes:
    """Valid ZIP archive that contains no .shp file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "No shapefile here.")
    return buf.getvalue()


@pytest.fixture
def unsupported_file_bytes() -> bytes:
    """A PNG file (wrong type) to test rejection."""
    # Minimal 1x1 PNG magic bytes.
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


# ── Tolerance constants ───────────────────────────────────────────────────────
# Used in numerical tests with pytest.approx or manual comparisons.

# 0.01° × 0.01° polygon near lat 12°N in UTM 44N.
# Calculated: x_width ≈ 0.01 × 111320 × cos(12°) ≈ 1088 m
#             y_height ≈ 0.01 × 111320 ≈ 1113 m
#             area ≈ 1088 × 1113 ≈ 1,210,944 m²
EXPECTED_POLYGON_AREA_SQ_M = 1_210_000   # Approximate; test with 5 % tolerance
EXPECTED_POLYGON_AREA_TOLERANCE = 0.05   # 5 %

# A ~N-S segment 0.01° long at lat 12°N.
# Length ≈ 0.01 × 111320 ≈ 1113 m
EXPECTED_LINE_LENGTH_M = 1_113           # Approximate; test with 1 % tolerance
EXPECTED_LINE_LENGTH_TOLERANCE = 0.01    # 1 %

# UTM parcel: exactly 1000m × 1000m = 1,000,000 m²
EXPECTED_UTM_AREA_SQ_M = 1_000_000
EXPECTED_UTM_AREA_TOLERANCE = 0.001      # 0.1 % (projected, minimal distortion)
