#!/usr/bin/env python3
"""Compare bounded vehicle profiles over one origin/destination route.

Repeat ``--profile NAME;key=value`` to compare the same trip under different
vehicle restrictions. The first profile is the baseline. Supported options are
``vehicle_class``, ``weight_t``, ``rating_t``, ``height_m``, ``width_m``,
``length_m``, ``axleload_t``, and ``allow_hgv_destination``. The command uses
the same route graph, timing, ferry, turn-restriction, and path-evidence
semantics as ``ireland-geometry-route``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from route_query import (
        ROUTE_COMPARISON_MAX_PROFILES,
        ROUTE_COMPARISON_MIN_PROFILES,
        ROUTE_OBJECTIVES,
        parse_route_profile_spec,
        query_route_comparison,
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
        ROUTE_COMPARISON_MAX_PROFILES,
        ROUTE_COMPARISON_MIN_PROFILES,
        ROUTE_OBJECTIVES,
        parse_route_profile_spec,
        query_route_comparison,
    )
    from scripts.runtime import (
        atomic_write_json,
        package_version,
        project_data_tree_path,
        project_input_path,
        project_output_file_path,
    )


def _coordinate(parser: argparse.ArgumentParser, raw: str, label: str) -> tuple[float, float]:
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
    parser.add_argument("--start", required=True, metavar="LAT,LON", help="trip origin")
    parser.add_argument("--goal", required=True, metavar="LAT,LON", help="trip destination")
    parser.add_argument(
        "--profile",
        action="append",
        required=True,
        metavar="NAME[;key=value]",
        help=(
            f"vehicle profile specification; repeat at least {ROUTE_COMPARISON_MIN_PROFILES} "
            f"times and at most {ROUTE_COMPARISON_MAX_PROFILES} times"
        ),
    )
    parser.add_argument("--departure", default=None, help="optional ISO-8601 departure time")
    parser.add_argument(
        "--objective",
        choices=ROUTE_OBJECTIVES,
        default="distance",
        help="route objective shared by all profiles",
    )
    parser.add_argument(
        "--speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for duration and conditional windows (default: 50)",
    )
    parser.add_argument(
        "--include-path",
        action="store_true",
        help="include full point-to-point route responses with path evidence per profile",
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
    start_lat, start_lon = _coordinate(parser, args.start, "--start")
    goal_lat, goal_lon = _coordinate(parser, args.goal, "--goal")
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    try:
        profiles = [parse_route_profile_spec(value) for value in args.profile]
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
        result = query_route_comparison(
            start_lat,
            start_lon,
            goal_lat,
            goal_lon,
            graph,
            profiles,
            departure=departure,
            speed_kmh=args.speed_kmh,
            include_path=args.include_path,
            include_ferries=args.include_ferries,
            objective=args.objective,
        )
    finally:
        if isinstance(graph, SQLiteRoadGraph):
            graph.close()
    if args.out:
        destination = project_output_file_path(
            args.out,
            str(Path(args.out)),
            label="route comparison output",
        )
        atomic_write_json(destination, result, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
