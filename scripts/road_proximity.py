#!/usr/bin/env python3
"""Measure proximity of sampled buildings to the mapped road network.

This is a network-exposure diagnostic, not a shortest-path route distance:
for each sampled footprint centroid it reports the distance to the nearest
mapped drivable-road geometry in the same Geofabrik extract.  It complements
the existing road-bearing histogram and makes the sampling limitation
explicit in the output.

Reads:  data/raw/ireland-latest.osm.pbf, output/analysis_results.csv
Writes: output/road_proximity.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

try:
    import osmium
except ImportError as exc:  # pragma: no cover - dependency is part of the runtime package
    raise SystemExit("the osmium package (PyOsmium bindings) is required for road proximity") from exc

try:
    from runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )


ROAD_HIGHWAYS = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "service",
    "living_street",
    "track",
    "road",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
SAMPLE_LIMIT = 4000


class RoadHandler(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[LineString] = []

    def way(self, way) -> None:
        if way.tags.get("highway") not in ROAD_HIGHWAYS:
            return
        coords = [(node.lon, node.lat) for node in way.nodes if node.location.valid()]
        if len(coords) >= 2:
            self.lines.append(LineString(coords))


def sample(rows: list[dict], seed: int) -> list[dict]:
    if len(rows) <= SAMPLE_LIMIT:
        return rows
    return random.Random(seed).sample(rows, SAMPLE_LIMIT)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--pbf", default=None, help="OSM PBF path")
    parser.add_argument("--seed", type=int, default=20260816)
    args = parser.parse_args(argv)
    data = project_data_tree_path(args.data_root)
    out = project_output_tree_path(args.out_dir)
    pbf = project_input_path(
        args.pbf,
        str(data / "raw" / "ireland-latest.osm.pbf"),
        label="road proximity PBF",
    )
    results_path = out / "analysis_results.csv"
    if not pbf.exists() or not results_path.exists():
        raise SystemExit("Missing PBF or analysis_results.csv. Run fetch and analyze first.")
    with results_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    handler = RoadHandler()
    handler.apply_file(str(pbf), locations=True)
    if not handler.lines:
        raise SystemExit("No mapped road geometries were found in the PBF")
    tree = STRtree(handler.lines)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_group["controls" if row.get("is_control") == "1" else row.get("group", "other")].append(row)
    output = []
    for index, group in enumerate(sorted(by_group)):
        selected = sample(by_group[group], args.seed + index)
        distances = []
        for row in selected:
            point = Point(float(row["lon"]), float(row["lat"]))
            nearest_index = int(tree.nearest(point))
            distance_deg = point.distance(handler.lines[nearest_index])
            # This local conversion is sufficiently accurate for a proximity
            # diagnostic and avoids presenting degrees as metres.
            metres = distance_deg * 110540.0
            distances.append(metres)
        distances.sort()
        if not distances:
            continue
        median = distances[len(distances) // 2]
        p90 = distances[min(len(distances) - 1, int(len(distances) * 0.9))]
        output.append(
            {
                "group": group,
                "sample_n": len(distances),
                "road_way_n": len(handler.lines),
                "mean_distance_m": round(sum(distances) / len(distances), 2),
                "median_distance_m": round(median, 2),
                "p90_distance_m": round(p90, 2),
                "within_25m_pct": round(sum(value <= 25 for value in distances) / len(distances) * 100, 2),
                "within_100m_pct": round(sum(value <= 100 for value in distances) / len(distances) * 100, 2),
                "method": "nearest mapped drivable-road geometry; sampled centroid proximity, not routing distance",
                "seed": args.seed + index,
            }
        )
    atomic_write_csv(
        out / "road_proximity.csv",
        ["group", "sample_n", "road_way_n", "mean_distance_m", "median_distance_m", "p90_distance_m", "within_25m_pct", "within_100m_pct", "method", "seed"],
        output,
    )
    print(f"[road-proximity] wrote {len(output)} group summaries from {len(handler.lines):,} road ways")


if __name__ == "__main__":
    main()
