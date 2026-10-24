#!/usr/bin/env python3
"""Run a bounded origin/destination route matrix against a persisted road graph.

The command uses the same WGS84 snapping, one-way, turn-restriction, ferry,
conditional-access, and vehicle-profile semantics as
``ireland-geometry-route``. Repeat ``--origin LAT,LON`` and
``--destination LAT,LON`` to form a Cartesian matrix; the shared route
contract caps the request at 25 pairs. The default JSON is compact and
contains one summary per pair. ``--include-path`` adds the complete existing
point-to-point route response under each pair.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from route_query import (
        ROUTE_MATRIX_MAX_PAIRS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        query_route_matrix,
        validate_vehicle_axleload_t,
        validate_vehicle_class,
        validate_vehicle_height_m,
        validate_vehicle_length_m,
        validate_vehicle_rating_t,
        validate_vehicle_weight_t,
        validate_vehicle_width_m,
    )
    from runtime import (
        atomic_write_json,
        package_version,
        project_data_tree_path,
        project_input_path,
        project_output_file_path,
    )
except ImportError:
    from scripts.road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from scripts.route_query import (
        ROUTE_MATRIX_MAX_PAIRS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        query_route_matrix,
        validate_vehicle_axleload_t,
        validate_vehicle_class,
        validate_vehicle_height_m,
        validate_vehicle_length_m,
        validate_vehicle_rating_t,
        validate_vehicle_weight_t,
        validate_vehicle_width_m,
    )
    from scripts.runtime import (
        atomic_write_json,
        package_version,
        project_data_tree_path,
        project_input_path,
        project_output_file_path,
    )


def _matrix_point(parser: argparse.ArgumentParser, raw: str, label: str) -> tuple[float, float]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2:
        parser.error(f"{label} must use LAT,LON")
    try:
        lat, lon = (float(parts[0]), float(parts[1]))
    except ValueError:
        parser.error(f"{label} must use finite numeric LAT,LON")
    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        parser.error(f"{label} latitude must be finite and within [-90.0, 90.0]")
    if not math.isfinite(lon) or not -180.0 <= lon <= 180.0:
        parser.error(f"{label} longitude must be finite and within [-180.0, 180.0]")
    return lat, lon


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    parser.add_argument("--data-root", default=None)
    parser.add_argument(
        "--road-graph",
        default=None,
        help="graph directory, SQLite file, or JSON graph; defaults to data/roads",
    )
    parser.add_argument(
        "--origin",
        action="append",
        required=True,
        metavar="LAT,LON",
        help="origin WGS84 point; repeat for multiple origins",
    )
    parser.add_argument(
        "--destination",
        action="append",
        required=True,
        metavar="LAT,LON",
        help="destination WGS84 point; repeat for multiple destinations",
    )
    parser.add_argument("--departure", default=None, help="optional ISO-8601 departure time")
    parser.add_argument(
        "--objective",
        choices=ROUTE_OBJECTIVES,
        default="distance",
        help="route objective: shortest physical distance or fastest estimated duration",
    )
    parser.add_argument(
        "--speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for duration and conditional windows (default: 50)",
    )
    parser.add_argument(
        "--weight-t",
        type=float,
        default=None,
        help="optional vehicle weight profile in metric tonnes",
    )
    parser.add_argument(
        "--rating-t",
        type=float,
        default=None,
        help="optional HGV permitted gross-weight rating in metric tonnes",
    )
    parser.add_argument(
        "--height-m",
        type=float,
        default=None,
        help="optional vehicle height profile in metres",
    )
    parser.add_argument(
        "--width-m",
        type=float,
        default=None,
        help="optional vehicle width profile in metres",
    )
    parser.add_argument(
        "--length-m",
        type=float,
        default=None,
        help="optional vehicle length profile in metres",
    )
    parser.add_argument(
        "--axleload-t",
        type=float,
        default=None,
        help="optional vehicle axle-load profile in metric tonnes",
    )
    parser.add_argument(
        "--vehicle-class",
        choices=VEHICLE_CLASSES,
        default="general",
        help="vehicle-class profile for class-specific conditional access",
    )
    parser.add_argument(
        "--allow-hgv-destination",
        action="store_true",
        help="allow destination-only ways and supported HGV destination exceptions",
    )
    parser.add_argument(
        "--include-path",
        action="store_true",
        help="include full point-to-point route responses with path evidence per pair",
    )
    parser.add_argument(
        "--include-ferries",
        action="store_true",
        help=(
            "include persisted ferry geometry; SQLite graphs may also apply "
            "schedules, waits, and durations"
        ),
    )
    parser.add_argument("--out", default=None, help="optional JSON output path")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    origins = [
        _matrix_point(parser, raw, f"--origin[{index}]")
        for index, raw in enumerate(args.origin)
    ]
    destinations = [
        _matrix_point(parser, raw, f"--destination[{index}]")
        for index, raw in enumerate(args.destination)
    ]
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    if len(origins) * len(destinations) > ROUTE_MATRIX_MAX_PAIRS:
        parser.error(
            "origin × destination pair count must not exceed "
            f"{ROUTE_MATRIX_MAX_PAIRS}"
        )
    try:
        weight_t = validate_vehicle_weight_t(args.weight_t)
        rating_t = validate_vehicle_rating_t(args.rating_t)
        height_m = validate_vehicle_height_m(args.height_m)
        width_m = validate_vehicle_width_m(args.width_m)
        length_m = validate_vehicle_length_m(args.length_m)
        axleload_t = validate_vehicle_axleload_t(args.axleload_t)
        vehicle_class = validate_vehicle_class(args.vehicle_class)
        departure = parse_departure(args.departure)
    except ValueError as exc:
        parser.error(str(exc))

    data = project_data_tree_path(args.data_root)
    graph_path = (
        project_input_path(
            args.road_graph,
            str(data / "roads"),
            label="route graph input",
        )
        if args.road_graph
        else data / "roads"
    )
    if not graph_path.exists():
        parser.error(f"road graph does not exist: {graph_path}")
    try:
        graph = load_graph(graph_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"could not load road graph: {exc}")
    if isinstance(graph, SQLiteRoadGraph):
        empty = graph.node_count == 0 or (
            graph.edge_count == 0 and graph.ferry_edge_count == 0
        )
    else:
        coordinates, adjacency = graph
        empty = not coordinates or not adjacency
    if empty:
        if isinstance(graph, SQLiteRoadGraph):
            graph.close()
        parser.error(f"road graph is empty: {graph_path}")

    try:
        result = query_route_matrix(
            origins,
            destinations,
            graph,
            departure=departure,
            speed_kmh=args.speed_kmh,
            include_path=args.include_path,
            include_ferries=args.include_ferries,
            objective=args.objective,
            vehicle_weight_t=weight_t,
            vehicle_rating_t=rating_t,
            vehicle_height_m=height_m,
            vehicle_width_m=width_m,
            vehicle_length_m=length_m,
            vehicle_axleload_t=axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=args.allow_hgv_destination,
        )
    finally:
        if isinstance(graph, SQLiteRoadGraph):
            graph.close()
    if args.out:
        destination = project_output_file_path(
            args.out,
            str(Path(args.out)),
            label="route matrix output",
        )
        atomic_write_json(destination, result, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
