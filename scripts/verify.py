#!/usr/bin/env python3
"""Validate the complete Ireland geometry artifact set.

The verifier is intentionally independent of the report builder and analysis
stages. It checks row contracts, geometry/report alignment, statistical
fields, provenance, and (when Node is available) inline report JavaScript
syntax. A machine-readable result is written to ``output/verification.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

try:
    from runtime import (
        atomic_write_json,
        git_revision,
        output_counts,
        project_path,
        sha256_file,
        utc_now,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_json,
        git_revision,
        output_counts,
        project_path,
        sha256_file,
        utc_now,
    )


ANALYSIS_REQUIRED = {
    "osm_id",
    "group",
    "is_control",
    "area_m2",
    "convexity",
    "valid",
    "repaired",
    "multipart",
    "hole_count",
}
SIGNIFICANCE_REQUIRED = {"p_adjusted", "method", "verdict"}
VERDICTS = {"SIGNAL", "suggestive", "background"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def require_file(path: Path, errors: list[str]) -> bool:
    if not path.exists() or not path.is_file():
        errors.append(f"missing required artifact: {path}")
        return False
    if path.stat().st_size == 0:
        errors.append(f"empty artifact: {path}")
        return False
    return True


def check_columns(
    rows: list[dict[str, str]], required: set[str], label: str, errors: list[str]
) -> None:
    if not rows:
        errors.append(f"{label} has no data rows")
        return
    missing = sorted(required - set(rows[0]))
    if missing:
        errors.append(f"{label} is missing columns: {', '.join(missing)}")


def check_probabilities(rows: list[dict[str, str]], label: str, errors: list[str]) -> None:
    check_columns(rows, SIGNIFICANCE_REQUIRED, label, errors)
    if rows and not {"p_value", "p"}.intersection(rows[0]):
        errors.append(f"{label} is missing a raw p-value column")
    for index, row in enumerate(rows, 2):
        for field in ("p_value", "p_adjusted", "p"):
            raw = row.get(field, "")
            if raw == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                errors.append(f"{label} row {index} has non-numeric {field}: {raw!r}")
                continue
            if not 0.0 < value <= 1.0:
                errors.append(f"{label} row {index} has invalid {field}: {value}")
        verdict = row.get("verdict", "")
        if verdict and verdict not in VERDICTS:
            errors.append(f"{label} row {index} has invalid verdict: {verdict!r}")


def check_report(path: Path, errors: list[str], warnings: list[str]) -> None:
    if not require_file(path, errors):
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    for token in ("__DATA__", "__OUTLINES__", "__MARKER_LIMIT__", "${TOP_N_MARKERS}"):
        if token in text:
            errors.append(f"report still contains template token: {token}")
    if "const PACK =" not in text or "function init" not in text:
        errors.append("report is missing its data pack or initialization script")

    node = shutil.which("node")
    if not node:
        warnings.append("Node.js unavailable; skipped inline report JavaScript syntax check")
        return
    blocks = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>", text, flags=re.IGNORECASE | re.DOTALL
    )
    inline = "\n".join(blocks)
    result = subprocess.run(
        [node, "--check"], input=inline, capture_output=True, text=True, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        errors.append("report inline JavaScript failed syntax check: " + " ".join(detail[:3]))


def verify_outputs(data_root: Path, out_dir: Path, manifest_path: Path | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    combined = data_root / "combined.json"
    if require_file(combined, errors):
        try:
            payload = json.loads(combined.read_text(encoding="utf-8"))
            if not isinstance(payload.get("elements"), list) or not payload["elements"]:
                errors.append("combined.json has no non-empty elements list")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"combined.json is not valid JSON: {exc}")

    analysis_path = out_dir / "analysis_results.csv"
    analysis = read_csv(analysis_path) if require_file(analysis_path, errors) else []
    check_columns(analysis, ANALYSIS_REQUIRED, "analysis_results.csv", errors)
    analysis_ids = [row.get("osm_id", "") for row in analysis]
    if len(analysis_ids) != len(set(analysis_ids)):
        errors.append("analysis_results.csv contains duplicate osm_id values")
    targets = []
    controls = []
    for index, row in enumerate(analysis, 2):
        if row.get("is_control") == "1":
            controls.append(row)
        elif row.get("is_control") == "0":
            targets.append(row)
        else:
            errors.append(f"analysis_results.csv row {index} has invalid is_control")
        try:
            area = float(row.get("area_m2", ""))
            convexity = float(row.get("convexity", ""))
        except ValueError:
            errors.append(f"analysis_results.csv row {index} has invalid numeric geometry")
            continue
        if area <= 0:
            errors.append(f"analysis_results.csv row {index} has non-positive area")
        if not 0.0 <= convexity <= 1.000001:
            errors.append(f"analysis_results.csv row {index} has convexity outside [0,1]")

    geojson_path = out_dir / "ireland_buildings.geojson"
    if require_file(geojson_path, errors):
        try:
            geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
            features = geojson.get("features", [])
            feature_ids = [feature.get("properties", {}).get("osm_id") for feature in features]
            if geojson.get("type") != "FeatureCollection":
                errors.append("ireland_buildings.geojson is not a FeatureCollection")
            if len(features) != len(targets):
                errors.append(
                    f"GeoJSON feature count {len(features)} != target CSV count {len(targets)}"
                )
            if len(feature_ids) != len(set(feature_ids)):
                errors.append("ireland_buildings.geojson contains duplicate osm_id values")
            if set(feature_ids) != {row["osm_id"] for row in targets}:
                errors.append("GeoJSON ids do not exactly match non-control analysis ids")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"ireland_buildings.geojson is not valid JSON: {exc}")

    significance = out_dir / "significance.csv"
    if require_file(significance, errors):
        check_probabilities(read_csv(significance), "significance.csv", errors)

    join_path = out_dir / "niah_join.csv"
    if require_file(join_path, errors):
        joins = read_csv(join_path)
        check_columns(
            joins, {"osm_id", "source_region", "match_mode", "dist_m"}, "niah_join.csv", errors
        )
        if any(not row.get("source_region") for row in joins):
            errors.append("niah_join.csv contains a blank source_region")
        if len({row.get("osm_id") for row in joins}) != len(joins):
            errors.append("niah_join.csv contains duplicate osm_id values")
        for index, row in enumerate(joins, 2):
            if row.get("match_mode") not in {"contained", "near"}:
                errors.append(f"niah_join.csv row {index} has invalid match_mode")
            try:
                distance = float(row.get("dist_m", ""))
            except ValueError:
                errors.append(f"niah_join.csv row {index} has invalid dist_m")
                continue
            if distance < 0 or (row.get("match_mode") == "near" and distance > 50.01):
                errors.append(f"niah_join.csv row {index} has invalid match distance")

    niah_sig = out_dir / "niah_significance.csv"
    if require_file(niah_sig, errors):
        check_probabilities(read_csv(niah_sig), "niah_significance.csv", errors)
    decade_path = out_dir / "niah_decades.csv"
    if require_file(decade_path, errors):
        check_probabilities(read_csv(decade_path), "niah_decades.csv", errors)

    for name in (
        "point_pattern.csv",
        "point_pattern_turns.csv",
        "roads_compare.csv",
        "architects.csv",
        "architects_binary.csv",
        "architects_evidence.csv",
    ):
        require_file(out_dir / name, errors)
    check_report(out_dir / "report.html", errors, warnings)

    manifest_path = manifest_path or out_dir / "manifest.json"
    manifest = {}
    if require_file(manifest_path, errors):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("manifest_version", "generated_at", "git_revision", "sources", "counts"):
                if key not in manifest:
                    errors.append(f"manifest missing key: {key}")
            current_revision = git_revision()
            if current_revision and manifest.get("git_revision") != current_revision:
                errors.append(
                    f"manifest Git revision {manifest.get('git_revision')} != {current_revision}"
                )
            for source in manifest.get("sources", []):
                source_path = Path(source.get("path", ""))
                if source_path.exists():
                    actual_hash = sha256_file(source_path)
                    if not source.get("sha256"):
                        errors.append(f"manifest source has no sha256: {source_path}")
                    elif source["sha256"] != actual_hash:
                        errors.append(f"manifest source hash mismatch: {source_path}")
                    if source.get("bytes") != source_path.stat().st_size:
                        errors.append(f"manifest source byte-size mismatch: {source_path}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"manifest is not valid JSON: {exc}")

    result = {
        "verified_at": utc_now(),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "git_revision": git_revision(),
        "counts": output_counts(out_dir),
        "analysis_rows": len(analysis),
        "target_rows": len(targets),
        "control_rows": len(controls),
        "manifest_revision": manifest.get("git_revision"),
    }
    atomic_write_json(out_dir / "verification.json", result, indent=2)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root", default=None, help="data directory; defaults to project data/"
    )
    parser.add_argument(
        "--out-dir", default=None, help="output directory; defaults to project output/"
    )
    parser.add_argument(
        "--manifest", default=None, help="manifest path; defaults to output/manifest.json"
    )
    args = parser.parse_args(argv)
    data_root = project_path(args.data_root, "data")
    out_dir = project_path(args.out_dir, "output")
    manifest = (
        project_path(args.manifest, str(out_dir / "manifest.json")) if args.manifest else None
    )
    result = verify_outputs(data_root, out_dir, manifest)
    if result["passed"]:
        print(
            f"[verify] PASS ({result['analysis_rows']} analysis rows, {result['target_rows']} targets)"
        )
        for warning in result["warnings"]:
            print(f"[verify] warning: {warning}")
        return
    print("[verify] FAIL", flush=True)
    for error in result["errors"]:
        print(f"[verify]   {error}", flush=True)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
