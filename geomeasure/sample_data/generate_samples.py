"""
GeoMeasure Sample Data Generator

Run this script once to create sample KML and Shapefile ZIP files
in the sample_data/ directory for manual testing with Swagger UI or curl.

Usage:
  cd geomeasure
  python sample_data/generate_samples.py
"""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

OUTPUT_DIR = Path(__file__).parent


def write_kml(name: str, content: str) -> None:
    path = OUTPUT_DIR / name
    path.write_text(content, encoding="utf-8")
    print(f"  Created: {path}")


def write_zip(name: str, gdf: gpd.GeoDataFrame, shp_name: str) -> None:
    import tempfile
    path = OUTPUT_DIR / name
    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / shp_name
        gdf.to_file(shp_path, driver="ESRI Shapefile")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).iterdir():
                zf.write(f, f.name)
        path.write_bytes(buf.getvalue())
    print(f"  Created: {path}")


def main():
    print("GeoMeasure — Generating sample data files…\n")

    # ── 1. KML with a survey polygon ─────────────────────────────────────────
    write_kml("sample_polygon.kml", """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Bengaluru Survey Parcel</name>
    <Placemark id="parcel1">
      <name>Land Parcel A</name>
      <description>Sample agricultural survey polygon near Bengaluru, India.</description>
      <ExtendedData>
        <SchemaData>
          <SimpleData name="parcel_id">BLR-001</SimpleData>
          <SimpleData name="land_use">agriculture</SimpleData>
          <SimpleData name="survey_date">2024-01-15</SimpleData>
        </SchemaData>
      </ExtendedData>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.580,12.970,0
              77.600,12.970,0
              77.600,12.990,0
              77.580,12.990,0
              77.580,12.970,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
""")

    # ── 2. KML with a survey transect (LineString) ────────────────────────────
    write_kml("sample_linestring.kml", """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Drone Survey Transects</name>
    <Placemark id="transect1">
      <name>Transect 1</name>
      <description>North-south drone flight path</description>
      <ExtendedData>
        <SchemaData>
          <SimpleData name="transect_id">T001</SimpleData>
          <SimpleData name="altitude_m">120</SimpleData>
        </SchemaData>
      </ExtendedData>
      <LineString>
        <coordinates>
          77.580,12.970,120
          77.580,12.990,120
          77.590,12.990,120
          77.590,12.970,120
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
""")

    # ── 3. KML with mixed geometry types ─────────────────────────────────────
    write_kml("sample_mixed.kml", """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Mixed Geometry Survey</name>
    <Placemark id="area1">
      <name>Survey Area</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.580,12.970,0 77.600,12.970,0 77.600,12.990,0
              77.580,12.990,0 77.580,12.970,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark id="path1">
      <name>Access Road</name>
      <LineString>
        <coordinates>77.580,12.970,0 77.600,12.970,0</coordinates>
      </LineString>
    </Placemark>
    <Placemark id="gcp1">
      <name>Ground Control Point 1</name>
      <ExtendedData>
        <SchemaData>
          <SimpleData name="gcp_id">GCP-001</SimpleData>
          <SimpleData name="accuracy_cm">2.5</SimpleData>
        </SchemaData>
      </ExtendedData>
      <Point>
        <coordinates>77.590,12.980,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>
""")

    # ── 4. Shapefile ZIP — polygon ────────────────────────────────────────────
    poly_gdf = gpd.GeoDataFrame(
        [
            {
                "parcel_id": "SHP-001",
                "land_use": "agriculture",
                "area_ha": 4.0,
                "geometry": Polygon([
                    (77.580, 12.970), (77.600, 12.970),
                    (77.600, 12.990), (77.580, 12.990), (77.580, 12.970),
                ]),
            },
            {
                "parcel_id": "SHP-002",
                "land_use": "urban",
                "area_ha": 2.0,
                "geometry": Polygon([
                    (77.610, 12.970), (77.620, 12.970),
                    (77.620, 12.980), (77.610, 12.980), (77.610, 12.970),
                ]),
            },
        ],
        crs="EPSG:4326",
    )
    write_zip("sample_polygon.zip", poly_gdf, "survey_parcels.shp")

    # ── 5. Shapefile ZIP — linestring ─────────────────────────────────────────
    line_gdf = gpd.GeoDataFrame(
        [
            {
                "transect_id": "T-001",
                "geometry": LineString([(77.580, 12.970), (77.600, 12.990)]),
            },
        ],
        crs="EPSG:4326",
    )
    write_zip("sample_linestring.zip", line_gdf, "transects.shp")

    print("\nOK All sample files created in:", OUTPUT_DIR)
    print("\nUpload via Swagger UI at: http://localhost:8000/docs")
    print("Or use the curl examples in README.md")


if __name__ == "__main__":
    main()
