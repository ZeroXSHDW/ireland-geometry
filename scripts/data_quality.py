#!/usr/bin/env python3
"""Audit completeness, validity, duplicates, and source coverage.

This stage is intentionally descriptive.  It does not delete or repair rows;
it produces a compact audit table that makes missingness and quality flags
visible by target group and for the full analysis population.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path

try:
    from runtime import atomic_write_csv, atomic_write_json, project_path
except ImportError:
    from scripts.runtime import atomic_write_csv, atomic_write_json, project_path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


FIELD_RULES: dict[str, tuple[str, tuple[float, float] | None]] = {
    "lat": ("numeric", (-90.0, 90.0)),
    "lon": ("numeric", (-180.0, 180.0)),
    "area_m2": ("numeric", (0.0, float("inf"))),
    "perimeter_m": ("numeric", (0.0, float("inf"))),
    "aspect_ratio": ("numeric", (1.0, float("inf"))),
    "convexity": ("numeric", (0.0, 1.000001)),
    "circularity": ("numeric", (0.0, 1.000001)),
    "score": ("numeric", (0.0, 100.000001)),
    "building_tag": ("categorical", None),
    "address_city": ("categorical", None),
    "height_m": ("numeric", (0.0, float("inf"))),
    "building_levels": ("numeric", (0.0, float("inf"))),
}


def field_audit(rows: list[dict[str, str]], group: str, field: str, rule: str, bounds: tuple[float, float] | None) -> dict[str, object]:
    values = [row.get(field, "").strip() for row in rows]
    missing = sum(not value or value.lower() in {"nan", "none", "null"} for value in values)
    invalid = 0
    if rule == "numeric":
        for value in values:
            if not value or value.lower() in {"nan", "none", "null"}:
                continue
            numeric = number(value)
            if not math.isfinite(numeric) or (bounds and not bounds[0] <= numeric <= bounds[1]):
                invalid += 1
    unique = len({value for value in values if value})
    status = "ok" if invalid == 0 else "check"
    notes = "numeric range check" if rule == "numeric" else "blank-rate check"
    return {
        "scope": "analysis",
        "group": group,
        "field": field,
        "row_n": len(rows),
        "missing_n": missing,
        "missing_pct": round(100.0 * missing / len(rows), 4) if rows else 0.0,
        "unique_n": unique,
        "invalid_n": invalid,
        "quality_status": status,
        "notes": notes,
    }


def build_audit(out: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    analysis = read_csv(out / "analysis_results.csv")
    if not analysis:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    audit_rows = []
    groups = [("all", analysis)]
    groups.extend(
        (group, [row for row in analysis if row.get("group") == group])
        for group in sorted({row.get("group", "") for row in analysis})
        if group
    )
    for group, rows in groups:
        for field, (rule, bounds) in FIELD_RULES.items():
            audit_rows.append(field_audit(rows, group, field, rule, bounds))

    ids = [row.get("osm_id", "") for row in analysis]
    coordinates = [(row.get("lat", ""), row.get("lon", "")) for row in analysis]
    duplicate_cells = Counter(coordinates)
    quality = {
        "analysis_rows": len(analysis),
        "target_rows": sum(row.get("is_control") == "0" for row in analysis),
        "control_rows": sum(row.get("is_control") == "1" for row in analysis),
        "duplicate_osm_id_n": len(ids) - len(set(ids)),
        "duplicate_centroid_n": sum(count - 1 for count in duplicate_cells.values() if count > 1),
        "repaired_geometry_pct": round(100.0 * sum(row.get("repaired") == "1" for row in analysis) / len(analysis), 4),
        "multipart_geometry_pct": round(100.0 * sum(row.get("multipart") == "1" for row in analysis) / len(analysis), 4),
        "valid_geometry_pct": round(100.0 * sum(row.get("valid") == "1" for row in analysis) / len(analysis), 4),
    }
    source_files = {
        "niah_join": out / "niah_join.csv",
        "mapping_history": out / "mapping_history.csv",
        "spatial_covariates": out / "spatial_covariates.csv",
        "lidar_coverage": out / "lidar_coverage.csv",
        "road_routing": out / "road_routing.csv",
        "review_queue": out / "review_queue.csv",
    }
    source_rows = []
    for name, path in source_files.items():
        rows = read_csv(path)
        statuses = Counter(row.get("status", row.get("quality", "available")) for row in rows)
        source_rows.append(
            {
                "scope": "source",
                "group": "all",
                "field": name,
                "row_n": len(rows),
                "missing_n": sum(row.get("status") in {"not_provided", "unavailable"} for row in rows),
                "missing_pct": round(100.0 * sum(row.get("status") in {"not_provided", "unavailable"} for row in rows) / len(rows), 4) if rows else 100.0,
                "unique_n": len(statuses),
                "invalid_n": 0,
                "quality_status": "not_provided" if not rows else ";".join(f"{key}:{value}" for key, value in sorted(statuses.items())),
                "notes": "source coverage/status distribution",
            }
        )
    audit_rows.extend(source_rows)
    summary = {
        **quality,
        "quality_status": "check" if quality["duplicate_osm_id_n"] or quality["duplicate_centroid_n"] else "ok",
        "source_status": {row["field"]: row["quality_status"] for row in source_rows},
    }
    return audit_rows, summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    audit, summary = build_audit(out)
    atomic_write_csv(
        out / "data_quality.csv",
        ["scope", "group", "field", "row_n", "missing_n", "missing_pct", "unique_n", "invalid_n", "quality_status", "notes"],
        audit,
    )
    atomic_write_json(out / "data_quality_summary.json", summary, indent=2)
    print(
        f"[quality] audited {summary['analysis_rows']:,} rows; geometry_valid={summary['valid_geometry_pct']}%; "
        f"duplicate_ids={summary['duplicate_osm_id_n']}; duplicate_centroids={summary['duplicate_centroid_n']}"
    )


if __name__ == "__main__":
    main()
