#!/usr/bin/env python3
"""Compute actual shortest-path distances on a supplied road graph.

The graph contract is intentionally simple and portable: ``road_nodes.csv``
contains ``node_id,lat,lon`` and ``road_edges.csv`` contains ``u,v,length_m``
with optional ``oneway`` and ``highway`` fields.  A JSON object with ``nodes``
and ``edges`` arrays is also accepted.  A PBF conversion is available with
``--from-pbf`` but is opt-in because a country-wide graph can be large.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

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


def pairwise(values: list[tuple]) -> list[tuple[tuple, tuple]]:
    return [(values[index - 1], values[index]) for index in range(1, len(values))]


def straight_distance(a: dict[str, str], b: dict[str, str]) -> float:
    lat0 = math.radians((number(a.get("lat")) + number(b.get("lat"))) / 2)
    dx = (number(a.get("lon")) - number(b.get("lon"))) * 111320 * math.cos(lat0)
    dy = (number(a.get("lat")) - number(b.get("lat"))) * 110540
    return math.hypot(dx, dy)


def graph_from_rows(nodes: list[dict], edges: list[dict]) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]]:
    coordinates = {
        str(row.get("node_id", row.get("id", ""))): (number(row.get("lat")), number(row.get("lon")))
        for row in nodes
        if str(row.get("node_id", row.get("id", ""))).strip()
    }
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in edges:
        u = str(row.get("u", row.get("from", ""))).strip()
        v = str(row.get("v", row.get("to", ""))).strip()
        if not u or not v or u not in coordinates or v not in coordinates:
            continue
        length = number(row.get("length_m"), -1)
        if length < 0:
            lat_u, lon_u = coordinates[u]
            lat_v, lon_v = coordinates[v]
            length = straight_distance(
                {"lat": str(lat_u), "lon": str(lon_u)},
                {"lat": str(lat_v), "lon": str(lon_v)},
            )
        if not math.isfinite(length) or length < 0:
            continue
        oneway = str(row.get("oneway", "")).lower()
        adjacency[u].append((v, length))
        if oneway not in {"yes", "1", "true", "-1"}:
            adjacency[v].append((u, length))
        elif oneway == "-1":
            adjacency[u].pop()
            adjacency[v].append((u, length))
    return coordinates, dict(adjacency)


def load_graph(path: Path) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]]:
    if path.is_dir():
        return graph_from_rows(read_csv(path / "road_nodes.csv"), read_csv(path / "road_edges.csv"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return graph_from_rows(payload.get("nodes", []), payload.get("edges", []))
    raise ValueError("road graph JSON must be an object containing nodes and edges")


def graph_from_pbf(path: Path, max_ways: int) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]]:
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - optional adapter
        raise SystemExit("pyosmium is required for --from-pbf") from exc

    highways = {
        "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
        "residential", "service", "living_street", "road", "motorway_link", "trunk_link",
        "primary_link", "secondary_link", "tertiary_link",
    }

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.coordinates: dict[str, tuple[float, float]] = {}
            self.edges: list[dict[str, str]] = []
            self.count = 0

        def way(self, way) -> None:
            if self.count >= max_ways or way.tags.get("highway") not in highways:
                return
            points = [(str(node.ref), node.lat, node.lon) for node in way.nodes if node.location.valid()]
            if len(points) < 2:
                return
            self.count += 1
            for node_id, lat, lon in points:
                self.coordinates[node_id] = (float(lat), float(lon))
            oneway = str(way.tags.get("oneway", "")).lower()
            for first, second in pairwise(points):
                a, b = first[0], second[0]
                length = straight_distance(
                    {"lat": str(first[1]), "lon": str(first[2])},
                    {"lat": str(second[1]), "lon": str(second[2])},
                )
                self.edges.append({"u": a, "v": b, "length_m": str(length), "oneway": oneway})

    handler = Handler()
    handler.apply_file(str(path), locations=True)
    nodes = [{"node_id": key, "lat": value[0], "lon": value[1]} for key, value in handler.coordinates.items()]
    return graph_from_rows(nodes, handler.edges)


def nearest_node(row: dict[str, str], coordinates: dict[str, tuple[float, float]]) -> str | None:
    if not coordinates:
        return None
    lat = number(row.get("lat"))
    lon = number(row.get("lon"))
    return min(
        coordinates,
        key=lambda node: (coordinates[node][0] - lat) ** 2 + (coordinates[node][1] - lon) ** 2,
    )


def shortest_path(start: str, goal: str, adjacency: dict[str, list[tuple[str, float]]]) -> float | None:
    if start == goal:
        return 0.0
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        if node == goal:
            return distance
        for neighbour, weight in adjacency.get(node, []):
            candidate = distance + weight
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                heapq.heappush(queue, (candidate, neighbour))
    return None


def write_unavailable(out: Path, reason: str) -> None:
    atomic_write_csv(
        out / "road_routing_pairs.csv",
        ["target_osm_id", "control_osm_id", "target_group", "straight_distance_m", "route_distance_m", "reachable", "status", "method"],
        [{"target_osm_id": "", "control_osm_id": "", "target_group": "", "straight_distance_m": "", "route_distance_m": "", "reachable": 0, "status": reason, "method": "no road graph supplied"}],
    )
    atomic_write_csv(
        out / "road_routing.csv",
        ["group", "sample_n", "route_n", "reachable_pct", "median_route_m", "p90_route_m", "median_straight_m", "network_to_straight_ratio", "status", "source", "method"],
        [{"group": "all", "sample_n": 0, "route_n": 0, "reachable_pct": 0, "median_route_m": "", "p90_route_m": "", "median_straight_m": "", "network_to_straight_ratio": "", "status": reason, "source": "unavailable", "method": "actual shortest-path distance requires a supplied graph"}],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--road-graph", default=None, help="directory or JSON graph contract")
    parser.add_argument("--from-pbf", action="store_true", help="build an in-memory graph from --pbf")
    parser.add_argument("--pbf", default=None)
    parser.add_argument("--max-ways", type=int, default=100_000)
    parser.add_argument("--max-pairs", type=int, default=5000)
    args = parser.parse_args(argv)
    data = project_path(args.data_root, "data")
    out = project_path(args.out_dir, "output")
    graph_path = project_path(args.road_graph, str(data / "roads")) if args.road_graph else data / "roads"
    source = str(graph_path)
    try:
        if args.from_pbf:
            pbf = project_path(args.pbf, str(data / "raw" / "ireland-latest.osm.pbf"))
            coordinates, adjacency = graph_from_pbf(pbf, args.max_ways)
            source = str(pbf)
        elif graph_path.is_dir() and not (graph_path / "road_nodes.csv").exists():
            write_unavailable(out, "not_provided")
            print("[road-routing] graph directory has no road_nodes.csv; wrote explicit not_provided outputs")
            return
        elif graph_path.exists():
            coordinates, adjacency = load_graph(graph_path)
        else:
            write_unavailable(out, "not_provided")
            print("[road-routing] no road graph supplied; wrote explicit not_provided outputs")
            return
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        write_unavailable(out, "provided_but_unreadable")
        print(f"[road-routing] graph unavailable: {exc}")
        return
    if not coordinates or not adjacency:
        write_unavailable(out, "empty_graph")
        return
    analysis_rows = read_csv(out / "analysis_results.csv")
    by_id = {row.get("osm_id", ""): row for row in analysis_rows}
    pairs = read_csv(out / "matched_controls_strict.csv") or read_csv(out / "matched_controls.csv")
    pairs = sorted(pairs, key=lambda row: (row.get("target_osm_id", ""), row.get("control_osm_id", "")))[: args.max_pairs]
    routed = []
    for pair in pairs:
        target = by_id.get(pair.get("target_osm_id", ""))
        control = by_id.get(pair.get("control_osm_id", ""))
        if not target or not control:
            continue
        start = nearest_node(target, coordinates)
        goal = nearest_node(control, coordinates)
        route = shortest_path(start, goal, adjacency) if start and goal else None
        routed.append(
            {
                "target_osm_id": target["osm_id"],
                "control_osm_id": control["osm_id"],
                "target_group": target.get("group", "other"),
                "straight_distance_m": round(straight_distance(target, control), 2),
                "route_distance_m": round(route, 2) if route is not None else "",
                "reachable": int(route is not None),
                "status": "provided",
                "method": "Dijkstra on supplied road graph; nearest graph node snap",
            }
        )
    atomic_write_csv(
        out / "road_routing_pairs.csv",
        ["target_osm_id", "control_osm_id", "target_group", "straight_distance_m", "route_distance_m", "reachable", "status", "method"],
        routed,
    )
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in routed:
        by_group[row["target_group"]].append(row)
    summaries = []
    for group, group_rows in sorted(by_group.items()):
        routes = sorted(number(row["route_distance_m"]) for row in group_rows if row["route_distance_m"] != "")
        straight = sorted(number(row["straight_distance_m"]) for row in group_rows)
        ratio = [route / line for route, line in zip(routes, straight[: len(routes)]) if line > 0]
        summaries.append(
            {
                "group": group,
                "sample_n": len(group_rows),
                "route_n": len(routes),
                "reachable_pct": round(100 * len(routes) / len(group_rows), 2),
                "median_route_m": round(routes[len(routes) // 2], 2) if routes else "",
                "p90_route_m": round(routes[min(len(routes) - 1, int(len(routes) * 0.9))], 2) if routes else "",
                "median_straight_m": round(straight[len(straight) // 2], 2) if straight else "",
                "network_to_straight_ratio": round(sum(ratio) / len(ratio), 4) if ratio else "",
                "status": "provided",
                "source": source,
                "method": "Dijkstra on supplied road graph; nearest graph node snap",
            }
        )
    atomic_write_csv(
        out / "road_routing.csv",
        ["group", "sample_n", "route_n", "reachable_pct", "median_route_m", "p90_route_m", "median_straight_m", "network_to_straight_ratio", "status", "source", "method"],
        summaries or [{"group": "all", "sample_n": 0, "route_n": 0, "reachable_pct": 0, "median_route_m": "", "p90_route_m": "", "median_straight_m": "", "network_to_straight_ratio": "", "status": "no_pairs", "source": source, "method": "Dijkstra"}],
    )
    print(f"[road-routing] routed {len(routed):,} pairs on {len(coordinates):,} graph nodes")


if __name__ == "__main__":
    main()
