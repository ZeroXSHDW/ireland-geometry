#!/usr/bin/env python3
"""Run one point-to-point route query against a persisted road graph.

The command is deliberately separate from the pipeline's sampled
target/control diagnostic.  It accepts WGS84 coordinates, snaps them to the
nearest graph nodes, applies the same one-way and turn-restriction semantics,
and emits a small JSON result suitable for scripts or interactive inspection.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Union

try:
    from road_routing import (
        SQLiteRoadGraph,
        build_node_index,
        load_graph,
        nearest_node,
        parse_departure,
        shortest_path,
        straight_distance,
    )
    from runtime import atomic_write_json, package_version, project_path
except ImportError:
    from scripts.road_routing import (
        SQLiteRoadGraph,
        build_node_index,
        load_graph,
        nearest_node,
        parse_departure,
        shortest_path,
        straight_distance,
    )
    from scripts.runtime import atomic_write_json, package_version, project_path


ROUTE_CONTRACT = "ireland-geometry.route.v1"


Coordinates = dict[str, tuple[float, float]]
Graph = Union[tuple[Coordinates, dict[str, list[tuple[str, float]]]], SQLiteRoadGraph]  # noqa: UP007 - Python 3.9 import compatibility


def _point(lat: float, lon: float) -> dict[str, str]:
    return {"lat": str(lat), "lon": str(lon)}


def _node_coordinates(graph: Graph, coordinates: Coordinates, node: str) -> tuple[float, float] | None:
    if isinstance(graph, SQLiteRoadGraph):
        row = graph.connection.execute(
            "SELECT lat, lon FROM nodes WHERE node_id = ?",
            (node,),
        ).fetchone()
        return (float(row[0]), float(row[1])) if row else None
    return coordinates.get(node)


def _graph_summary(graph: Graph, coordinates: Coordinates) -> dict[str, Any]:
    if isinstance(graph, SQLiteRoadGraph):
        def table_count(name: str) -> int:
            exists = graph.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (name,),
            ).fetchone()
            if not exists:
                return 0
            return int(graph.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])

        return {
            "backend": "sqlite",
            "node_n": graph.node_count,
            "edge_n": graph.edge_count,
            "ferry_edge_n": graph.ferry_edge_count,
            "turn_restriction_n": table_count("turn_restrictions"),
            "conditional_restriction_n": table_count("conditional_turn_restrictions"),
        }
    return {
        "backend": "portable",
        "node_n": len(coordinates),
        "edge_n": sum(len(edges) for edges in graph[1].values()),
        "ferry_edge_n": 0,
        "turn_restriction_n": 0,
        "conditional_restriction_n": 0,
    }


def _method(
    graph: Graph,
    departure: datetime | None,
    speed_kmh: float,
    include_ferries: bool,
) -> str:
    if not isinstance(graph, SQLiteRoadGraph):
        method = "Dijkstra on supplied road graph; nearest graph node snap"
        if include_ferries:
            method += "; ferry geometry unavailable in portable graph"
        return method
    method = "Dijkstra on SQLite road graph; indexed nearest graph node snap"
    if graph.has_turn_restrictions:
        method += "; OSM via-node and validated via-way turn restrictions"
    if graph.conditional_restriction_n:
        if departure is None:
            method += "; conditional windows retained but inactive"
        else:
            method += f"; conditional windows evaluated from {departure.isoformat()} at {speed_kmh:g} km/h"
    if graph.ferry_edge_count:
        method += (
            "; ferry geometry included; ferry schedules not modeled"
            if include_ferries
            else "; ferry geometry available but excluded"
        )
    elif include_ferries:
        method += "; ferry geometry unavailable in this graph"
    return method


def _path_coordinates(
    graph: Graph,
    coordinates: Coordinates,
    path_nodes: list[str],
) -> list[list[float]]:
    result = []
    for node in path_nodes:
        point = _node_coordinates(graph, coordinates, node)
        if point:
            result.append([round(point[1], 7), round(point[0], 7)])
    return result


def route_geojson(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a path-enabled route result to one GeoJSON feature."""
    coordinates = result.get("path_coordinates", [])
    if not coordinates:
        geometry = None
    elif len(coordinates) == 1:
        geometry = {"type": "Point", "coordinates": coordinates[0]}
    else:
        geometry = {"type": "LineString", "coordinates": coordinates}
    properties = {
        key: value
        for key, value in result.items()
        if key not in {"path_node_ids", "path_coordinates"}
    }
    return {
        "type": "Feature",
        "contract": ROUTE_CONTRACT,
        "properties": properties,
        "geometry": geometry,
    }


def query_route(
    start_lat: float,
    start_lon: float,
    goal_lat: float,
    goal_lon: float,
    graph: Graph,
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    include_path: bool = False,
    include_ferries: bool = False,
) -> dict[str, Any]:
    """Return a JSON-serializable route result for two WGS84 points."""
    if isinstance(graph, SQLiteRoadGraph):
        coordinates: Coordinates = {}
        graph_coordinates: Coordinates | SQLiteRoadGraph = graph
        node_index = None
    else:
        coordinates, _adjacency = graph
        graph_coordinates = coordinates
        node_index = build_node_index(coordinates)

    start_point = _point(start_lat, start_lon)
    goal_point = _point(goal_lat, goal_lon)
    start_node = nearest_node(start_point, graph_coordinates, node_index)
    goal_node = nearest_node(goal_point, graph_coordinates, node_index)
    start_node_point = _node_coordinates(graph, coordinates, start_node) if start_node else None
    goal_node_point = _node_coordinates(graph, coordinates, goal_node) if goal_node else None
    result: dict[str, Any] = {
        "contract": ROUTE_CONTRACT,
        "status": "provided",
        "reachable": False,
        "start": {
            "lat": start_lat,
            "lon": start_lon,
            "node_id": start_node,
            "snap_distance_m": round(
                straight_distance(start_point, _point(*start_node_point)), 2
            ) if start_node_point else None,
        },
        "goal": {
            "lat": goal_lat,
            "lon": goal_lon,
            "node_id": goal_node,
            "snap_distance_m": round(
                straight_distance(goal_point, _point(*goal_node_point)), 2
            ) if goal_node_point else None,
        },
        "straight_distance_m": round(straight_distance(start_point, goal_point), 2),
        "route_distance_m": None,
        "estimated_duration_s": None,
        "departure": departure.isoformat() if departure else None,
        "arrival": None,
        "speed_kmh": speed_kmh,
        "method": _method(graph, departure, speed_kmh, include_ferries),
        "graph": _graph_summary(graph, (coordinates if not isinstance(graph, SQLiteRoadGraph) else {})),
    }
    if include_path:
        result["path_node_n"] = 0
        result["path_node_ids"] = []
        result["path_coordinates"] = []
    if not start_node or not goal_node:
        result["status"] = "provided_but_unreachable"
        return result

    adjacency = graph if isinstance(graph, SQLiteRoadGraph) else graph[1]
    route_result = shortest_path(
        start_node,
        goal_node,
        adjacency,
        departure=departure,
        speed_kmh=speed_kmh,
        return_path=include_path,
        include_ferries=include_ferries,
    )
    if route_result is None:
        result["status"] = "provided_but_unreachable"
        return result
    if include_path:
        route_distance, path_nodes = route_result
        result["path_node_n"] = len(path_nodes)
        result["path_node_ids"] = path_nodes
        result["path_coordinates"] = _path_coordinates(graph, coordinates, path_nodes)
    else:
        route_distance = route_result

    duration_s = route_distance / (speed_kmh / 3.6)
    result["reachable"] = True
    result["route_distance_m"] = round(route_distance, 2)
    result["estimated_duration_s"] = round(duration_s, 2)
    if departure:
        result["arrival"] = (departure + timedelta(seconds=duration_s)).isoformat()
    return result


def _validate_coordinate(parser: argparse.ArgumentParser, label: str, value: float, minimum: float, maximum: float) -> None:
    if not math.isfinite(value) or not minimum <= value <= maximum:
        parser.error(f"{label} must be finite and within [{minimum}, {maximum}]")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--road-graph", default=None, help="graph directory, SQLite file, or JSON graph")
    parser.add_argument("--start-lat", type=float, required=True)
    parser.add_argument("--start-lon", type=float, required=True)
    parser.add_argument("--goal-lat", type=float, required=True)
    parser.add_argument("--goal-lon", type=float, required=True)
    parser.add_argument("--departure", default=None, help="optional ISO-8601 departure time")
    parser.add_argument(
        "--speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for duration and conditional windows (default: 50)",
    )
    parser.add_argument("--out", default=None, help="optional JSON output path")
    parser.add_argument(
        "--include-path",
        action="store_true",
        help="include graph node IDs and GeoJSON-order coordinates in the JSON result",
    )
    parser.add_argument(
        "--geojson-out",
        default=None,
        help="optional GeoJSON Feature output path; implies --include-path",
    )
    parser.add_argument(
        "--include-ferries",
        action="store_true",
        help="include persisted ferry geometry; ferry schedules are not modeled",
    )
    args = parser.parse_args(argv)
    _validate_coordinate(parser, "--start-lat", args.start_lat, -90.0, 90.0)
    _validate_coordinate(parser, "--start-lon", args.start_lon, -180.0, 180.0)
    _validate_coordinate(parser, "--goal-lat", args.goal_lat, -90.0, 90.0)
    _validate_coordinate(parser, "--goal-lon", args.goal_lon, -180.0, 180.0)
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    try:
        departure = parse_departure(args.departure)
    except ValueError as exc:
        parser.error(str(exc))

    data = project_path(args.data_root, "data")
    graph_path = project_path(args.road_graph, str(data / "roads"))
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
        result = query_route(
            args.start_lat,
            args.start_lon,
            args.goal_lat,
            args.goal_lon,
            graph,
            departure=departure,
            speed_kmh=args.speed_kmh,
            include_path=args.include_path or bool(args.geojson_out),
            include_ferries=args.include_ferries,
        )
    finally:
        if isinstance(graph, SQLiteRoadGraph):
            graph.close()
    if args.out:
        destination = project_path(args.out, str(Path(args.out)))
        atomic_write_json(destination, result, indent=2)
    if args.geojson_out:
        destination = project_path(args.geojson_out, str(Path(args.geojson_out)))
        atomic_write_json(destination, route_geojson(result), indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
