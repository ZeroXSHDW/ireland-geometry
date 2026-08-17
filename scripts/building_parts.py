#!/usr/bin/env python3
"""Aggregate OSM building-part geometry and an optional LiDAR contract.

OSM building footprints are often a plan-view envelope.  ``building:part``
features and height tags provide a limited vertical/complexity signal, while
LiDAR can provide a much better roof-height and stepped-massing measurement.
This stage keeps those sources separate and reports their coverage honestly.

The optional LiDAR input is a normalized CSV or GeoJSON FeatureCollection with
an ``osm_id`` column/property and any of ``roof_height_m``, ``elevation_m``,
``coverage_m2``, ``source`` and ``quality``.  A missing input still produces a
complete coverage table with ``lidar_available=0`` so downstream reports do
not confuse unavailable data with zero height.

Reads:  data/combined.json, output/analysis_results.csv
Writes: output/building_parts.csv
        output/lidar_coverage.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from shapely.strtree import STRtree

try:
    from geometry import geometry_from_element, repair_geometry
    from runtime import atomic_write_csv, project_path
except ImportError:
    from scripts.geometry import geometry_from_element, repair_geometry
    from scripts.runtime import atomic_write_csv, project_path


def number(value, default=0.0) -> float:
    try:
        value = float(str(value).replace("m", "").strip())
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def is_part(element: dict) -> bool:
    tags = element.get("tags", {})
    return bool(tags.get("building:part")) or tags.get("building:part") == "yes"


def parse_lidar(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    if path.suffix.lower() in {".json", ".geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_rows = payload.get("features", []) if payload.get("type") == "FeatureCollection" else payload
        rows = []
        for feature in source_rows if isinstance(source_rows, list) else []:
            properties = feature.get("properties", feature) if isinstance(feature, dict) else {}
            rows.append(properties)
    else:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    out = {}
    for row in rows:
        osm_id = str(row.get("osm_id", "")).strip()
        if not osm_id:
            continue
        out[osm_id] = {
            "roof_height_m": number(row.get("roof_height_m"), 0.0),
            "elevation_m": number(row.get("elevation_m"), 0.0),
            "coverage_m2": number(row.get("coverage_m2"), 0.0),
            "source": str(row.get("source", path.name)),
            "quality": str(row.get("quality", "provided")),
        }
    return out


def aggregate_parts(elements: list[dict], analysis_rows: list[dict]) -> list[dict]:
    by_id = {f"{el.get('osm_type', 'way')}/{el['id']}": el for el in elements}
    parts = []
    geometries = []
    for element in elements:
        if not is_part(element):
            continue
        geom, repaired, _warning = repair_geometry(geometry_from_element(element))
        if geom is None or geom.is_empty or not geom.is_valid:
            continue
        parts.append((element, geom, repaired))
        geometries.append(geom)
    tree = STRtree(geometries) if geometries else None
    output = []
    for row in analysis_rows:
        element = by_id.get(row["osm_id"])
        target, _repaired, _warning = (
            repair_geometry(geometry_from_element(element)) if element else (None, False, "")
        )
        matched = []
        if target is not None and tree is not None:
            for index in tree.query(target):
                part, part_geom, was_repaired = parts[int(index)]
                if not target.covers(part_geom.representative_point()):
                    continue
                matched.append((part, part_geom, was_repaired))
        heights = [number(part.get("tags", {}).get("height"), 0.0) for part, _, _ in matched]
        levels = [number(part.get("tags", {}).get("building:levels"), 0.0) for part, _, _ in matched]
        heights = [value for value in heights if value > 0]
        levels = [value for value in levels if value > 0]
        part_area = sum(geom.area for _, geom, _ in matched)
        target_area = max(number(row.get("area_m2"), 0.0), 1e-9)
        output.append(
            {
                "osm_id": row["osm_id"],
                "group": row.get("group", ""),
                "part_count": len(matched),
                "part_area_m2": round(part_area, 2),
                "part_coverage_pct": round(min(1000.0, part_area / target_area * 100.0), 2),
                "max_part_height_m": round(max(heights, default=0.0), 2),
                "known_part_heights": len(heights),
                "max_part_levels": round(max(levels, default=0.0), 2),
                "known_part_levels": len(levels),
                "part_repaired_n": sum(int(was_repaired) for _, _, was_repaired in matched),
                "part_geometry_status": "matched" if matched else "no_mapped_part",
                "method": "centroid containment of OSM building:part geometries",
            }
        )
    return output


def lidar_rows(analysis_rows: list[dict], lidar: dict[str, dict]) -> list[dict]:
    out = []
    for row in analysis_rows:
        record = lidar.get(row["osm_id"])
        if record:
            out.append({"osm_id": row["osm_id"], "group": row.get("group", ""), "lidar_available": 1, **record})
        else:
            out.append(
                {
                    "osm_id": row["osm_id"],
                    "group": row.get("group", ""),
                    "lidar_available": 0,
                    "roof_height_m": "",
                    "elevation_m": "",
                    "coverage_m2": "",
                    "source": "unavailable",
                    "quality": "not_provided",
                }
            )
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--lidar", default=None, help="optional normalized LiDAR CSV/GeoJSON")
    args = parser.parse_args(argv)
    data = project_path(args.data_root, "data")
    out = project_path(args.out_dir, "output")
    combined = data / "combined.json"
    results = out / "analysis_results.csv"
    if not combined.exists() or not results.exists():
        raise SystemExit("Missing combined.json or analysis_results.csv. Run fetch and analyze first.")
    elements = json.loads(combined.read_text(encoding="utf-8"))["elements"]
    with results.open(newline="", encoding="utf-8") as fh:
        analysis_rows = list(csv.DictReader(fh))
    parts = aggregate_parts(elements, analysis_rows)
    atomic_write_csv(
        out / "building_parts.csv",
        [
            "osm_id",
            "group",
            "part_count",
            "part_area_m2",
            "part_coverage_pct",
            "max_part_height_m",
            "known_part_heights",
            "max_part_levels",
            "known_part_levels",
            "part_repaired_n",
            "part_geometry_status",
            "method",
        ],
        parts,
    )
    lidar_path = project_path(args.lidar, str(data / "lidar" / "building_heights.csv")) if args.lidar else data / "lidar" / "building_heights.csv"
    coverage = lidar_rows(analysis_rows, parse_lidar(lidar_path if lidar_path.exists() else None))
    atomic_write_csv(
        out / "lidar_coverage.csv",
        [
            "osm_id",
            "group",
            "lidar_available",
            "roof_height_m",
            "elevation_m",
            "coverage_m2",
            "source",
            "quality",
        ],
        coverage,
    )
    mapped = sum(row["part_count"] != 0 for row in parts)
    available = sum(row["lidar_available"] == 1 for row in coverage)
    print(f"[parts] analyzed {len(parts):,} footprints; {mapped:,} have mapped building parts")
    print(f"[parts] LiDAR coverage: {available:,}/{len(coverage):,}; source={lidar_path if lidar_path.exists() else 'unavailable'}")


if __name__ == "__main__":
    main()
