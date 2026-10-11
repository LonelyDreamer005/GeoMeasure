"""
GeoMeasure — Automated Batch Processing Pipeline

Automates the ingestion, measurement, and validation of geospatial files
across an entire directory without manual UI interaction.

Usage:
  python automate_pipeline.py --input sample_data/
  python automate_pipeline.py --input sample_data/ --output results/
  python automate_pipeline.py --api-url http://localhost:8000

Generates:
  - Terminal summary table
  - CSV report (results/batch_measurements.csv)
  - Full JSON report (results/batch_measurements.json)
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import sys
from pathlib import Path
import urllib.request
import urllib.error


def upload_and_process(api_url: str, file_path: Path) -> dict:
    """Upload a file using multipart/form-data via standard library urllib."""
    boundary = "----GeoMeasureBoundaryX987123"
    filename = file_path.name

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/files/",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_measurements(api_url: str, file_id: str) -> dict:
    """Fetch feature measurements and provenance."""
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/files/{file_id}/measurements/",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_pipeline(input_dir: Path, api_url: str, output_dir: Path) -> None:
    """Run batch audit over directory."""
    print("=" * 80)
    print(f"GeoMeasure Automated Pipeline: Auditing directory '{input_dir}'")
    print(f"API Target: {api_url}")
    print("=" * 80)

    output_dir.mkdir(parents=True, exist_ok=True)
    files_to_process = sorted(
        [p for p in input_dir.iterdir() if p.suffix.lower() in {".kml", ".zip"}]
    )

    if not files_to_process:
        print(f"No .kml or .zip files found in {input_dir}")
        return

    print(f"Found {len(files_to_process)} geospatial files to audit.\n")

    summary_records = []
    full_reports = []

    for idx, fpath in enumerate(files_to_process, 1):
        print(f"[{idx}/{len(files_to_process)}] Processing: {fpath.name} ... ", end="", flush=True)
        try:
            upload_res = upload_and_process(api_url, fpath)
            file_id = upload_res["id"]
            measurements_res = get_measurements(api_url, file_id)

            features = measurements_res.get("features", [])
            total_area = sum(
                f.get("measurement_value", 0) or 0
                for f in features
                if f.get("measurement_type") == "area" and f.get("measurement_status") == "MEASURED"
            )
            total_length = sum(
                f.get("measurement_value", 0) or 0
                for f in features
                if f.get("measurement_type") == "length" and f.get("measurement_status") == "MEASURED"
            )

            target_crs = "N/A"
            for f in features:
                if f.get("provenance", {}).get("measurement_crs"):
                    target_crs = f["provenance"]["measurement_crs"]
                    break

            val_summary = upload_res.get("validation_summary", {})
            issues_count = len(val_summary.get("issues", []))
            has_issues = "ISSUE" if issues_count > 0 or val_summary.get("missing_crs") else "PASSED"

            record = {
                "file_id": file_id,
                "filename": fpath.name,
                "file_type": upload_res.get("file_type"),
                "total_features": len(features),
                "measured_features": measurements_res.get("summary", {}).get("measured", 0),
                "total_area_sq_m": round(total_area, 4),
                "total_area_hectares": round(total_area / 10000.0, 4),
                "total_length_m": round(total_length, 4),
                "source_crs": upload_res.get("crs"),
                "measurement_crs": target_crs,
                "validation_status": has_issues,
            }
            summary_records.append(record)
            full_reports.append(measurements_res)

            print(
                f"OK -> {len(features)} feat, Area: {record['total_area_sq_m']} m2, Target CRS: {target_crs}"
            )

        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            print(f"FAILED (HTTP {e.code}): {err_msg}")
        except Exception as e:
            print(f"ERROR: {e}")

    # Output Console Summary Table
    print("\n" + "=" * 80)
    print("PIPELINE AUDIT SUMMARY REPORT")
    print("=" * 80)
    header = f"{'Filename':<25} | {'Feat':<5} | {'Measured':<8} | {'Area (sq m)':<15} | {'Length (m)':<12} | {'CRS':<10}"
    print(header)
    print("-" * len(header))
    for r in summary_records:
        area_str = f"{r['total_area_sq_m']:,.1f}" if r["total_area_sq_m"] > 0 else "-"
        len_str = f"{r['total_length_m']:,.1f}" if r["total_length_m"] > 0 else "-"
        print(
            f"{r['filename']:<25} | {r['total_features']:<5} | {r['measured_features']:<8} | {area_str:<15} | {len_str:<12} | {r['measurement_crs']:<10}"
        )
    print("=" * 80)

    # Save CSV
    csv_file = output_dir / "batch_measurements.csv"
    if summary_records:
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary_records[0].keys())
            writer.writeheader()
            writer.writerows(summary_records)
        print(f"CSV report written to: {csv_file}")

    # Save JSON
    json_file = output_dir / "batch_measurements.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(full_reports, f, indent=2)
    print(f"JSON report written to: {json_file}")
    print("\nPipeline execution completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="GeoMeasure Automated Processing Pipeline")
    parser.add_argument(
        "--input",
        "-i",
        default="sample_data",
        help="Input folder with KML or ZIP files (default: sample_data)",
    )
    parser.add_argument(
        "--api-url",
        "-u",
        default="http://localhost:8000",
        help="GeoMeasure API URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="results",
        help="Output folder for CSV and JSON reports (default: results)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path(__file__).resolve().parent / args.input

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parent / args.output

    run_pipeline(input_path, args.api_url, output_path)


if __name__ == "__main__":
    main()
