#!/usr/bin/env python3
"""Aggregate OSM building-part geometry and optional LiDAR/DSM data.

OSM building footprints are often a plan-view envelope.  ``building:part``
features and height tags provide a limited vertical/complexity signal, while
LiDAR can provide a much better roof-height and stepped-massing measurement.
This stage keeps those sources separate and reports their coverage honestly.

The optional LiDAR input is a normalized CSV/JSON/GeoJSON FeatureCollection, a
GeoTIFF DSM/DTM, or (when ``laspy`` is installed) LAS/LAZ.  A missing or
unreadable input still produces a complete coverage table with
``lidar_available=0`` so downstream reports do not confuse unavailable data
with zero height.

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
    try:
        if path.suffix.lower() in {".json", ".geojson"}:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
                source_rows = payload.get("features", [])
            elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                source_rows = payload["rows"]
            else:
                source_rows = payload
            rows = []
            for feature in source_rows if isinstance(source_rows, list) else []:
                properties = feature.get("properties", feature) if isinstance(feature, dict) else {}
                if isinstance(properties, dict):
                    rows.append(properties)
        else:
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error):
        return {}
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


def parse_raster_lidar(path: Path, analysis_rows: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Sample a DSM/DTM around each footprint centroid when rasterio is present."""
    try:
        import rasterio
        from rasterio.warp import transform
    except ImportError:
        return {}, {
            "status": "provided_but_unreadable",
            "quality": "missing_optional_dependency",
            "method": "GeoTIFF centroid window; install rasterio",
            "source_type": "GeoTIFF",
        }
    records: dict[str, dict] = {}
    try:
        with rasterio.open(path) as source:
            for row in analysis_rows:
                lon = number(row.get("lon"), float("nan"))
                lat = number(row.get("lat"), float("nan"))
                if not math.isfinite(lon) or not math.isfinite(lat):
                    continue
                x, y = lon, lat
                if source.crs and source.crs.to_epsg() != 4326:
                    try:
                        x, y = transform("EPSG:4326", source.crs, [lon], [lat])
                        x, y = x[0], y[0]
                    except (TypeError, ValueError, RuntimeError):
                        continue
                try:
                    pixel_row, pixel_col = source.index(x, y)
                    window = rasterio.windows.Window(pixel_col - 1, pixel_row - 1, 3, 3)
                    values = source.read(1, window=window, masked=True).compressed().tolist()
                except (IndexError, ValueError, RuntimeError):
                    continue
                values = [number(value, float("nan")) for value in values]
                values = [value for value in values if math.isfinite(value)]
                if not values:
                    continue
                records[row["osm_id"]] = {
                    "roof_height_m": round(max(values), 3),
                    "elevation_m": round(sum(values) / len(values), 3),
                    "coverage_m2": "",
                    "point_n": len(values),
                    "min_height_m": round(min(values), 3),
                    "max_height_m": round(max(values), 3),
                    "source": path.name,
                    "quality": "provided",
                    "source_type": "GeoTIFF",
                    "method": "DSM/DTM 3x3 centroid window",
                }
    except (OSError, ValueError, RuntimeError) as exc:
        return {}, {
            "status": "provided_but_unreadable",
            "quality": f"raster_error:{type(exc).__name__}",
            "method": "GeoTIFF centroid window",
            "source_type": "GeoTIFF",
        }
    return records, {
        "status": "provided",
        "quality": "provided",
        "method": "DSM/DTM 3x3 centroid window",
        "source_type": "GeoTIFF",
    }


def parse_las_lidar(
    path: Path,
    analysis_rows: list[dict],
    elements: list[dict],
) -> tuple[dict[str, dict], dict[str, str]]:
    """Aggregate LAS/LAZ points into footprints for geographic-coordinate files."""
    try:
        import laspy
        from shapely.geometry import Point
        from shapely.strtree import STRtree
    except ImportError:
        return {}, {
            "status": "provided_but_unreadable",
            "quality": "missing_optional_dependency",
            "method": "LAS/LAZ point-in-footprint aggregation; install laspy",
            "source_type": "LAS/LAZ",
        }
    geometry_by_id = {}
    for element in elements:
        key = f"{element.get('osm_type', 'way')}/{element.get('id')}"
        geometry, _repaired, _warning = repair_geometry(geometry_from_element(element))
        if geometry is not None and not geometry.is_empty and geometry.is_valid:
            geometry_by_id[key] = geometry
    geometries = list(geometry_by_id.values())
    if not geometries:
        return {}, {
            "status": "provided_but_unreadable",
            "quality": "no_target_geometries",
            "method": "LAS/LAZ point-in-footprint aggregation",
            "source_type": "LAS/LAZ",
        }
    try:
        cloud = laspy.read(path)
        xs = list(cloud.x)
        ys = list(cloud.y)
        zs = list(cloud.z)
        if not xs or max(abs(value) for value in xs) > 180 or max(abs(value) for value in ys) > 90:
            return {}, {
                "status": "provided_but_unreadable",
                "quality": "projected_coordinates_require_normalized_export",
                "method": "LAS/LAZ requires EPSG:4326 coordinates or pre-normalization",
                "source_type": "LAS/LAZ",
            }
        tree = STRtree(geometries)
        reverse = {id(geometry): osm_id for osm_id, geometry in geometry_by_id.items()}
        values: dict[str, list[float]] = {}
        for x, y, z in zip(xs, ys, zs):
            point = Point(float(x), float(y))
            for index in tree.query(point):
                geometry = geometries[int(index)]
                if geometry.covers(point):
                    values.setdefault(reverse[id(geometry)], []).append(float(z))
        records = {}
        for row in analysis_rows:
            numbers = values.get(row["osm_id"], [])
            if not numbers:
                continue
            records[row["osm_id"]] = {
                "roof_height_m": round(max(numbers), 3),
                "elevation_m": round(sum(numbers) / len(numbers), 3),
                "coverage_m2": "",
                "point_n": len(numbers),
                "min_height_m": round(min(numbers), 3),
                "max_height_m": round(max(numbers), 3),
                "source": path.name,
                "quality": "provided",
                "source_type": "LAS/LAZ",
                "method": "LAS/LAZ point-in-footprint aggregation",
            }
    except (OSError, ValueError, RuntimeError) as exc:
        return {}, {
            "status": "provided_but_unreadable",
            "quality": f"point_cloud_error:{type(exc).__name__}",
            "method": "LAS/LAZ point-in-footprint aggregation",
            "source_type": "LAS/LAZ",
        }
    return records, {
        "status": "provided",
        "quality": "provided",
        "method": "LAS/LAZ point-in-footprint aggregation",
        "source_type": "LAS/LAZ",
    }


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


def lidar_rows(
    analysis_rows: list[dict],
    lidar: dict[str, dict],
    source_info: dict[str, str] | None = None,
) -> list[dict]:
    source_info = source_info or {
        "status": "not_provided",
        "quality": "not_provided",
        "method": "no LiDAR/DSM source supplied",
        "source_type": "none",
    }
    out = []
    for row in analysis_rows:
        record = lidar.get(row["osm_id"])
        if record:
            out.append(
                {
                    "osm_id": row["osm_id"],
                    "group": row.get("group", ""),
                    "lidar_available": 1,
                    "status": "provided",
                    "point_n": record.get("point_n", ""),
                    "min_height_m": record.get("min_height_m", ""),
                    "max_height_m": record.get("max_height_m", record.get("roof_height_m", "")),
                    "source_type": record.get("source_type", "normalized"),
                    "method": record.get("method", "normalized per-building LiDAR records"),
                    **record,
                }
            )
        else:
            out.append(
                {
                    "osm_id": row["osm_id"],
                    "group": row.get("group", ""),
                    "lidar_available": 0,
                    "roof_height_m": "",
                    "elevation_m": "",
                    "coverage_m2": "",
                    "point_n": "",
                    "min_height_m": "",
                    "max_height_m": "",
                    "source": "unavailable" if source_info["status"] == "not_provided" else source_info.get("source_type", "provided"),
                    "quality": source_info.get("quality", "not_provided"),
                    "source_type": source_info.get("source_type", "none"),
                    "method": source_info.get("method", ""),
                    "status": source_info.get("status", "not_provided"),
                }
            )
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--lidar", default=None, help="optional normalized LiDAR CSV/JSON/GeoJSON, GeoTIFF DSM, or LAS/LAZ")
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
    source_info = {
        "status": "not_provided",
        "quality": "not_provided",
        "method": "no LiDAR/DSM source supplied",
        "source_type": "none",
    }
    lidar_records: dict[str, dict] = {}
    if lidar_path.exists() and lidar_path.suffix.lower() in {".tif", ".tiff"}:
        lidar_records, source_info = parse_raster_lidar(lidar_path, analysis_rows)
    elif lidar_path.exists() and lidar_path.suffix.lower() in {".las", ".laz"}:
        lidar_records, source_info = parse_las_lidar(lidar_path, analysis_rows, elements)
    elif lidar_path.exists():
        lidar_records = parse_lidar(lidar_path)
        source_info = {
            "status": "provided" if lidar_records else "provided_but_unreadable",
            "quality": "provided" if lidar_records else "empty_or_invalid",
            "method": "normalized per-building LiDAR records",
            "source_type": lidar_path.suffix.lower().lstrip(".") or "normalized",
        }
    coverage = lidar_rows(analysis_rows, lidar_records, source_info)
    atomic_write_csv(
        out / "lidar_coverage.csv",
        [
            "osm_id",
            "group",
            "lidar_available",
            "roof_height_m",
            "elevation_m",
            "coverage_m2",
            "point_n",
            "min_height_m",
            "max_height_m",
            "source",
            "quality",
            "source_type",
            "method",
            "status",
        ],
        coverage,
    )
    mapped = sum(row["part_count"] != 0 for row in parts)
    available = sum(row["lidar_available"] == 1 for row in coverage)
    print(f"[parts] analyzed {len(parts):,} footprints; {mapped:,} have mapped building parts")
    print(f"[parts] LiDAR coverage: {available:,}/{len(coverage):,}; source={lidar_path if lidar_path.exists() else 'unavailable'}")


if __name__ == "__main__":
    main()
