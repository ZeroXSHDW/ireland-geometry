#!/usr/bin/env python3
"""Attach administrative, settlement, and mapping-density covariates.

Boundary data are deliberately optional.  When a GeoJSON boundary layer is
not supplied, the stage falls back to NIAH county fields and OSM address-city
tags, while retaining a machine-readable ``boundary_status`` so a missing
boundary is never mistaken for an empty county or settlement.

Reads:  output/analysis_results.csv, optional output/niah_join.csv,
        optional boundary and settlement GeoJSON files
Writes: output/spatial_covariates.csv
        output/spatial_covariates_summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape

try:
    from runtime import atomic_write_csv, project_path
except ImportError:
    from scripts.runtime import atomic_write_csv, project_path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def load_features(path: Path | None) -> list[tuple[object, dict[str, str]]]:
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    features = payload.get("features", []) if isinstance(payload, dict) else payload
    loaded = []
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict) or not feature.get("geometry"):
            continue
        try:
            geometry = shape(feature["geometry"])
            if geometry.is_empty:
                continue
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            properties = {
                str(key): str(value)
                for key, value in (feature.get("properties") or {}).items()
                if value is not None
            }
            loaded.append((geometry, properties))
        except (TypeError, ValueError):
            continue
    return loaded


def property_value(properties: dict[str, str], *names: str) -> str:
    for name in names:
        value = properties.get(name, "").strip()
        if value:
            return value
    return ""


def find_properties(point: Point, features: list[tuple[object, dict[str, str]]]) -> dict[str, str]:
    for geometry, properties in features:
        try:
            if geometry.contains(point) or geometry.covers(point):
                return properties
        except (AttributeError, ValueError):
            continue
    return {}


def density_bin(count: int) -> str:
    if count <= 1:
        return "sparse"
    if count <= 5:
        return "low"
    if count <= 20:
        return "medium"
    return "high"


def build_covariates(
    analysis: list[dict[str, str]],
    niah: list[dict[str, str]],
    boundaries: list[tuple[object, dict[str, str]]],
    settlements: list[tuple[object, dict[str, str]]],
    *,
    boundary_source: str,
    settlement_source: str,
    grid_deg: float = 0.05,
) -> list[dict[str, str]]:
    niah_by_id = {row.get("osm_id", ""): row for row in niah}
    cell_counts: Counter[tuple[int, int]] = Counter()
    for row in analysis:
        cell = (
            math.floor(number(row.get("lat")) / grid_deg),
            math.floor(number(row.get("lon")) / grid_deg),
        )
        cell_counts[cell] += 1
    output = []
    for row in analysis:
        osm_id = row.get("osm_id", "")
        lat = number(row.get("lat"))
        lon = number(row.get("lon"))
        point = Point(lon, lat)
        cell = (math.floor(lat / grid_deg), math.floor(lon / grid_deg))
        boundary = find_properties(point, boundaries)
        settlement = find_properties(point, settlements)
        niah_row = niah_by_id.get(osm_id, {})
        county = property_value(boundary, "county", "name", "NAME_2", "admin2")
        admin1 = property_value(boundary, "admin_level_1", "admin1", "NAME_1", "province")
        if not county:
            county = niah_row.get("county", "").strip()
        settlement_name = property_value(settlement, "name", "NAME", "place_name")
        if not settlement_name:
            settlement_name = row.get("address_city", "").strip()
        settlement_class = property_value(
            settlement, "settlement_class", "class", "place", "type", "settlement_type"
        )
        if not settlement_class:
            settlement_class = "named_place" if settlement_name else "unknown"
        mapping_count = cell_counts[cell]
        # The cell area is approximate, but the bin is the important matching
        # covariate and is stable across runs without an external GIS index.
        cell_area_km2 = max(
            (grid_deg * 110.54)
            * (grid_deg * 111.32 * max(math.cos(math.radians(lat)), 0.2)),
            1e-6,
        )
        output.append(
            {
                "osm_id": osm_id,
                "is_control": row.get("is_control", ""),
                "group": row.get("group", ""),
                "admin_level_1": admin1,
                "county": county,
                "settlement_name": settlement_name,
                "settlement_class": settlement_class,
                "mapping_cell": f"{cell[0]}:{cell[1]}",
                "mapping_count": mapping_count,
                "mapping_density_per_km2": round(mapping_count / cell_area_km2, 6),
                "mapping_density_bin": density_bin(mapping_count),
                "boundary_status": "provided" if boundaries else "not_provided",
                "settlement_status": "provided" if settlements else "fallback_osm_address",
                "boundary_source": boundary_source if boundaries else "unavailable",
                "settlement_source": settlement_source if settlements else "analysis_results.address_city",
            }
        )
    return output


def write_summary(out: Path, rows: list[dict[str, str]], boundary_path: Path | None, settlement_path: Path | None) -> None:
    summary = [
        {
            "covariate": "administrative_boundary",
            "status": "provided" if boundary_path and boundary_path.exists() else "not_provided",
            "rows": len(rows),
            "unique_values": len({row["county"] for row in rows if row["county"]}),
            "source": str(boundary_path) if boundary_path and boundary_path.exists() else "unavailable",
            "notes": "county falls back to NIAH county when no boundary layer is supplied",
        },
        {
            "covariate": "settlement_layer",
            "status": "provided" if settlement_path and settlement_path.exists() else "fallback",
            "rows": len(rows),
            "unique_values": len({row["settlement_name"] for row in rows if row["settlement_name"]}),
            "source": str(settlement_path) if settlement_path and settlement_path.exists() else "analysis_results.address_city",
            "notes": "settlement class is external when provided and heuristic named_place otherwise",
        },
        {
            "covariate": "mapping_density",
            "status": "available",
            "rows": len(rows),
            "unique_values": len({row["mapping_cell"] for row in rows}),
            "source": "analysis_results.csv",
            "notes": "fixed 0.05-degree cell count and deterministic sparse/low/medium/high bins",
        },
    ]
    atomic_write_csv(
        out / "spatial_covariates_summary.csv",
        ["covariate", "status", "rows", "unique_values", "source", "notes"],
        summary,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--boundaries", default=None, help="optional administrative GeoJSON")
    parser.add_argument("--settlements", default=None, help="optional settlement GeoJSON")
    parser.add_argument("--grid-deg", type=float, default=0.05)
    args = parser.parse_args(argv)
    data = project_path(args.data_root, "data")
    out = project_path(args.out_dir, "output")
    analysis_path = out / "analysis_results.csv"
    if not analysis_path.exists():
        raise SystemExit(f"Missing {analysis_path}. Run analyze.py first.")
    analysis = read_csv(analysis_path)
    niah = read_csv(out / "niah_join.csv")
    boundary_path = project_path(args.boundaries, str(data / "boundaries" / "admin.geojson")) if args.boundaries else data / "boundaries" / "admin.geojson"
    settlement_path = project_path(args.settlements, str(data / "boundaries" / "settlements.geojson")) if args.settlements else data / "boundaries" / "settlements.geojson"
    boundaries = load_features(boundary_path if boundary_path.exists() else None)
    settlements = load_features(settlement_path if settlement_path.exists() else None)
    rows = build_covariates(
        analysis,
        niah,
        boundaries,
        settlements,
        boundary_source=str(boundary_path),
        settlement_source=str(settlement_path),
        grid_deg=args.grid_deg,
    )
    atomic_write_csv(
        out / "spatial_covariates.csv",
        list(rows[0]) if rows else ["osm_id", "county", "mapping_cell"],
        rows,
    )
    write_summary(out, rows, boundary_path, settlement_path)
    print(
        f"[covariates] wrote {len(rows):,} rows; boundaries={'provided' if boundaries else 'not_provided'}, "
        f"settlements={'provided' if settlements else 'fallback'}"
    )


if __name__ == "__main__":
    main()
