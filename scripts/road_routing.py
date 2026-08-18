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
import os
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
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


def number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def pairwise(values: list[tuple]) -> list[tuple[tuple, tuple]]:
    return [(values[index - 1], values[index]) for index in range(1, len(values))]


HIGHWAY_TYPES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "service", "living_street", "road", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
}
BLOCKED_ACCESS_VALUES = {"no", "private", "restricted"}
WEEKDAY_INDEX = {name: index for index, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))}
CONDITIONAL_RESTRICTION_RE = re.compile(
    r"^\s*((?:no|only)_[A-Za-z0-9_]+)\s*@\s*(.+?)\s*$"
)
DAY_RANGE_RE = re.compile(
    r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$"
)
TIME_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")


def is_access_restricted(tags: object) -> bool:
    """Return whether the most specific motor-vehicle access tag blocks routing."""
    for key in ("motor_vehicle", "motorcar", "vehicle", "access"):
        try:
            value = str(tags.get(key, "")).lower()
        except AttributeError:
            return False
        if value:
            return value in BLOCKED_ACCESS_VALUES
    return False


@dataclass(frozen=True)
class WeeklySchedule:
    """A deliberately small, deterministic subset of OSM opening-hours syntax."""

    intervals: tuple[tuple[frozenset[int], int, int], ...]

    def active_at(self, moment: datetime) -> bool:
        minute = moment.hour * 60 + moment.minute
        weekday = moment.weekday()
        return any(
            weekday in days and start <= minute < end
            for days, start, end in self.intervals
        )


def parse_weekly_schedule(value: str) -> WeeklySchedule | None:
    """Parse weekday/time windows used by the cached conditional restrictions."""
    condition = str(value).strip()
    if condition.startswith("(") and condition.endswith(")"):
        condition = condition[1:-1].strip()
    if not condition or ">" in condition or ";" in condition:
        return None
    tokens = condition.split(None, 1)
    days = frozenset(range(7))
    if len(tokens) == 2:
        day_match = DAY_RANGE_RE.fullmatch(tokens[0])
        if day_match:
            first = WEEKDAY_INDEX[day_match.group(1)]
            last = WEEKDAY_INDEX[day_match.group(2) or day_match.group(1)]
            if first <= last:
                days = frozenset(range(first, last + 1))
            else:
                days = frozenset((*range(first, 7), *range(last + 1)))
            time_text = tokens[1]
        else:
            time_text = condition
    else:
        time_text = condition
    intervals: list[tuple[frozenset[int], int, int]] = []
    for part in time_text.split(","):
        match = TIME_RANGE_RE.fullmatch(part.strip())
        if not match:
            return None
        values: list[int] = []
        for clock in match.groups():
            hour, minute = (int(piece) for piece in clock.split(":"))
            if hour > 24 or minute > 59 or (hour == 24 and minute != 0):
                return None
            values.append(hour * 60 + minute)
        if values[0] >= values[1]:
            return None
        intervals.append((days, values[0], values[1]))
    return WeeklySchedule(tuple(intervals)) if intervals else None


def parse_conditional_restriction(value: str) -> tuple[str, str] | None:
    """Return ``(no|only, condition)`` when a condition is supported."""
    match = CONDITIONAL_RESTRICTION_RE.fullmatch(str(value).strip())
    if not match or parse_weekly_schedule(match.group(2)) is None:
        return None
    kind = "no" if match.group(1).startswith("no_") else "only"
    return kind, match.group(2).strip()


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


class SQLiteRoadGraph:
    """Disk-backed graph for country-scale routing without an adjacency dump."""

    CELL_DEG = 0.01

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.node_count = int(self.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        self.edge_count = int(self.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        self.bounds = self.connection.execute(
            "SELECT MIN(cell_lat), MAX(cell_lat), MIN(cell_lon), MAX(cell_lon) FROM nodes"
        ).fetchone()
        edge_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(edges)")
        }
        self.has_way_ids = "way_id" in edge_columns
        turn_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(turn_restrictions)")
        }
        conditional_columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(conditional_turn_restrictions)")
        }
        turn_table_exists = bool(self.has_way_ids and turn_columns)
        conditional_table_exists = bool(self.has_way_ids and conditional_columns)
        self.has_via_way_json = "via_way_json" in turn_columns
        ferry_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(ferry_edges)")
        }
        ferry_table_exists = bool(ferry_columns)
        self.forbidden_turns: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        self.only_turns: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        self.conditional_forbidden_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, WeeklySchedule]]
        ] = defaultdict(list)
        self.conditional_only_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, WeeklySchedule]]
        ] = defaultdict(list)
        self._unconditional_prefixes: set[tuple[str, ...]] = set()
        self._conditional_prefixes: set[tuple[str, ...]] = set()
        self.restriction_prefixes: set[tuple[str, ...]] = set()
        def load_rows(table: str, columns: set[str], conditional: bool) -> None:
            if not columns:
                return
            selected = "via_node, from_way, to_way, kind"
            has_via_json = "via_way_json" in columns
            has_condition = "condition" in columns
            if has_via_json:
                selected += ", via_way_json"
            if has_condition:
                selected += ", condition"
            for row in self.connection.execute(f"SELECT {selected} FROM {table}"):
                via_node, from_way, to_way, kind = row[:4]
                offset = 4
                if has_via_json:
                    try:
                        via_ways = tuple(str(value) for value in json.loads(row[offset]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        via_ways = ()
                    offset += 1
                else:
                    via_ways = ()
                prefix = (str(from_way), *via_ways)
                key = (prefix, str(via_node))
                if conditional:
                    condition = str(row[offset]) if has_condition else ""
                    schedule = parse_weekly_schedule(condition)
                    if schedule is None:
                        continue
                    target = self.conditional_only_turns if kind == "only" else self.conditional_forbidden_turns
                    target[key].append((str(to_way), schedule))
                    prefixes = self._conditional_prefixes
                else:
                    target = self.only_turns if kind == "only" else self.forbidden_turns
                    target[key].add(str(to_way))
                    prefixes = self._unconditional_prefixes
                for length in range(1, len(prefix) + 1):
                    prefixes.add(prefix[:length])

        if turn_table_exists:
            load_rows("turn_restrictions", turn_columns, conditional=False)
        if conditional_table_exists:
            load_rows("conditional_turn_restrictions", conditional_columns, conditional=True)
        self.conditional_restriction_n = sum(
            len(values)
            for values in self.conditional_forbidden_turns.values()
        ) + sum(len(values) for values in self.conditional_only_turns.values())
        self.turn_restriction_n = sum(len(values) for values in self.forbidden_turns.values()) + sum(
            len(values) for values in self.only_turns.values()
        )
        self.restriction_nodes = {
            via_node
            for _prefix, via_node in self.forbidden_turns
        } | {
            via_node
            for _prefix, via_node in self.only_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_forbidden_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_only_turns
        }
        self.departure: datetime | None = None
        self.speed_mps = 50.0 / 3.6
        self.include_ferries = False
        self.ferry_edge_count = int(
            self.connection.execute("SELECT COUNT(*) FROM ferry_edges").fetchone()[0]
        ) if ferry_table_exists else 0
        self.has_conditional_restrictions = bool(
            conditional_table_exists and self.conditional_restriction_n
        )
        self.set_time_profile(None)

    def set_time_profile(
        self,
        departure: datetime | None,
        speed_kmh: float = 50.0,
        include_ferries: bool = False,
    ) -> None:
        """Configure optional local departure-time evaluation for conditions."""
        if not math.isfinite(speed_kmh) or speed_kmh <= 0:
            raise ValueError("routing speed must be a finite positive number")
        self.departure = departure
        self.speed_mps = speed_kmh / 3.6
        self.include_ferries = bool(include_ferries and self.ferry_edge_count)
        self.restriction_prefixes = set(self._unconditional_prefixes)
        if departure is not None:
            self.restriction_prefixes.update(self._conditional_prefixes)
        self.max_restriction_prefix_n = max(
            (len(prefix) for prefix in self.restriction_prefixes),
            default=0,
        )
        self.has_turn_restrictions = bool(
            self.restriction_prefixes
            and (self.turn_restriction_n or (departure is not None and self.conditional_restriction_n))
        )

    def close(self) -> None:
        self.connection.close()

    def nearest_node(self, row: dict[str, str]) -> str | None:
        if not self.node_count or self.bounds[0] is None:
            return None
        lat = number(row.get("lat"))
        lon = number(row.get("lon"))
        cell_lat = math.floor(lat / self.CELL_DEG)
        cell_lon = math.floor(lon / self.CELL_DEG)
        min_lat_cell, max_lat_cell, min_lon_cell, max_lon_cell = (int(value) for value in self.bounds)
        max_ring = max(
            abs(cell_lat - min_lat_cell),
            abs(cell_lat - max_lat_cell),
            abs(cell_lon - min_lon_cell),
            abs(cell_lon - max_lon_cell),
        )
        best: str | None = None
        best_distance = float("inf")
        for ring in range(max_ring + 1):
            for lat_cell in range(cell_lat - ring, cell_lat + ring + 1):
                for lon_cell in range(cell_lon - ring, cell_lon + ring + 1):
                    if max(abs(lat_cell - cell_lat), abs(lon_cell - cell_lon)) != ring:
                        continue
                    for node, node_lat, node_lon in self.connection.execute(
                        "SELECT node_id, lat, lon FROM nodes WHERE cell_lat = ? AND cell_lon = ?",
                        (lat_cell, lon_cell),
                    ):
                        distance = (node_lat - lat) ** 2 + (node_lon - lon) ** 2
                        if distance < best_distance or (distance == best_distance and node < (best or node)):
                            best = node
                            best_distance = distance
            if best is not None:
                lat_min = (cell_lat - ring) * self.CELL_DEG
                lat_max = (cell_lat + ring + 1) * self.CELL_DEG
                lon_min = (cell_lon - ring) * self.CELL_DEG
                lon_max = (cell_lon + ring + 1) * self.CELL_DEG
                outside_distance = min(
                    lat - lat_min,
                    lat_max - lat,
                    lon - lon_min,
                    lon_max - lon,
                ) ** 2
                if best_distance <= outside_distance:
                    return best
        return best

    def turn_allowed(
        self,
        via_node: str,
        incoming_way: str | None,
        outgoing_way: str,
        restriction_state: tuple[tuple[str, ...], ...] | None = None,
        elapsed_distance_m: float = 0.0,
    ) -> bool:
        """Apply via-node and via-way no/only turn rules for one transition."""
        if not incoming_way or not self.has_turn_restrictions:
            return True
        active = restriction_state
        if active is None:
            active = ((incoming_way,),)
        forbidden: set[str] = set()
        allowed: set[str] = set()
        for prefix in active:
            key = (prefix, via_node)
            forbidden.update(self.forbidden_turns.get(key, set()))
            allowed.update(self.only_turns.get(key, set()))
            if self.departure is not None:
                arrival = self.departure + timedelta(
                    seconds=max(0.0, elapsed_distance_m) / self.speed_mps
                )
                for target, schedule in self.conditional_forbidden_turns.get(key, ()):
                    if schedule.active_at(arrival):
                        forbidden.add(target)
                for target, schedule in self.conditional_only_turns.get(key, ()):
                    if schedule.active_at(arrival):
                        allowed.add(target)
        if allowed and outgoing_way not in allowed:
            return False
        return outgoing_way not in forbidden

    def advance_restriction_state(
        self,
        active: tuple[tuple[str, ...], ...],
        last_way: str,
        outgoing_way: str,
    ) -> tuple[tuple[str, ...], ...]:
        """Advance the compact way-run suffix state used by restricted routing."""
        if not self.has_turn_restrictions:
            return ()
        if last_way and outgoing_way == last_way:
            return active
        candidates = [(outgoing_way,)]
        candidates.extend(prefix + (outgoing_way,) for prefix in active)
        next_active: set[tuple[str, ...]] = set()
        for sequence in candidates:
            for length in range(1, min(self.max_restriction_prefix_n, len(sequence)) + 1):
                suffix = sequence[-length:]
                if suffix in self.restriction_prefixes:
                    next_active.add(suffix)
        return tuple(sorted(next_active, key=lambda prefix: (len(prefix), prefix)))

    def neighbours(
        self,
        node: str,
        incoming_way: str | None = None,
        restriction_state: tuple[tuple[str, ...], ...] | None = None,
        elapsed_distance_m: float = 0.0,
    ):
        """Yield directed neighbours while applying one-way and turn semantics."""
        if self.has_way_ids:
            road_query = """
                SELECT v, length_m, way_id FROM edges
                WHERE u = ? AND oneway NOT IN ('-1')
                UNION ALL
                SELECT u, length_m, way_id FROM edges
                WHERE v = ? AND oneway NOT IN ('yes', '1', 'true')
            """
        else:
            road_query = """
                SELECT v, length_m, '' FROM edges
                WHERE u = ? AND oneway NOT IN ('-1')
                UNION ALL
                SELECT u, length_m, '' FROM edges
                WHERE v = ? AND oneway NOT IN ('yes', '1', 'true')
            """
        parameters: tuple[str, ...] = (node, node)
        query = road_query
        if self.include_ferries:
            query = f"""
                {road_query}
                UNION ALL
                SELECT v, length_m, way_id FROM ferry_edges
                WHERE u = ? AND oneway NOT IN ('-1')
                UNION ALL
                SELECT u, length_m, way_id FROM ferry_edges
                WHERE v = ? AND oneway NOT IN ('yes', '1', 'true')
            """
            parameters = (node, node, node, node)
        for neighbour, weight, way_id in self.connection.execute(query, parameters):
            way_id = str(way_id)
            if self.turn_allowed(
                node,
                incoming_way,
                way_id,
                restriction_state,
                elapsed_distance_m,
            ):
                yield neighbour, weight, way_id


def write_sqlite_graph(
    path: Path,
    coordinates: dict[str, tuple[float, float]],
    edges: list[dict[str, str]],
    *,
    source: str,
    complete: bool = False,
    max_ways: int | None = None,
    way_n: int | None = None,
) -> None:
    """Write a portable SQLite graph from physical edges and node coordinates."""
    path.mkdir(parents=True, exist_ok=True)
    database = path / "road_graph.sqlite"
    temporary = path / ".road_graph.sqlite.tmp"
    temporary.unlink(missing_ok=True)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                cell_lat INTEGER NOT NULL,
                cell_lon INTEGER NOT NULL
            );
            CREATE TABLE edges (
                u TEXT NOT NULL,
                v TEXT NOT NULL,
                length_m REAL NOT NULL,
                oneway TEXT NOT NULL,
                way_id TEXT NOT NULL
            );
            CREATE TABLE ferry_edges (
                u TEXT NOT NULL,
                v TEXT NOT NULL,
                length_m REAL NOT NULL,
                oneway TEXT NOT NULL,
                way_id TEXT NOT NULL
            );
            CREATE TABLE turn_restrictions (
                relation_id TEXT NOT NULL,
                via_node TEXT NOT NULL,
                from_way TEXT NOT NULL,
                to_way TEXT NOT NULL,
                kind TEXT NOT NULL,
                via_way_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE conditional_turn_restrictions (
                relation_id TEXT NOT NULL,
                via_node TEXT NOT NULL,
                from_way TEXT NOT NULL,
                to_way TEXT NOT NULL,
                kind TEXT NOT NULL,
                via_way_json TEXT NOT NULL DEFAULT '[]',
                condition TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO nodes(node_id, lat, lon, cell_lat, cell_lon) VALUES (?, ?, ?, ?, ?)",
            [
                (node, lat, lon, math.floor(lat / SQLiteRoadGraph.CELL_DEG), math.floor(lon / SQLiteRoadGraph.CELL_DEG))
                for node, (lat, lon) in coordinates.items()
            ],
        )
        connection.executemany(
            "INSERT INTO edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(row.get("u", row.get("from", ""))).strip(),
                    str(row.get("v", row.get("to", ""))).strip(),
                    number(row.get("length_m"), 0.0),
                    str(row.get("oneway", "")).lower(),
                    str(row.get("way_id", "")),
                )
                for row in edges
                if str(row.get("u", row.get("from", ""))).strip()
                and str(row.get("v", row.get("to", ""))).strip()
            ],
        )
        connection.execute("CREATE INDEX nodes_cell ON nodes(cell_lat, cell_lon)")
        connection.execute("CREATE INDEX edges_u ON edges(u)")
        connection.execute("CREATE INDEX edges_v ON edges(v)")
        connection.execute("CREATE INDEX ferry_edges_u ON ferry_edges(u)")
        connection.execute("CREATE INDEX ferry_edges_v ON ferry_edges(v)")
        connection.execute("ANALYZE")
        connection.commit()
        connection.close()
        os.replace(temporary, database)
    except Exception:
        try:
            connection.close()
        except (UnboundLocalError, AttributeError):
            pass
        temporary.unlink(missing_ok=True)
        raise
    atomic_write_json(
        path / "road_graph_metadata.json",
        {
            "format": "ireland-geometry-road-sqlite-v6",
            "storage": "sqlite",
            "directed": True,
            "source": source,
            "node_n": len(coordinates),
            "edge_n": len(edges),
            "complete": complete,
            "max_ways": max_ways,
            "way_n": way_n,
            "ferry_way_n": 0,
            "ferry_segment_n": 0,
            "ferry_edge_n": 0,
            "ferry_relation_n": 0,
            "ferry_schedules_modeled": False,
            "turn_restriction_n": 0,
            "restriction_conditional_n": 0,
            "restriction_conditional_supported_n": 0,
            "restriction_conditional_stored_n": 0,
            "restriction_conditional_unsupported_n": 0,
            "restriction_conditional_unresolved_n": 0,
            "restriction_conditional_geometry_unsupported_n": 0,
        },
        indent=2,
    )


def load_graph(path: Path) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]] | SQLiteRoadGraph:
    if path.is_dir():
        sqlite_path = path / "road_graph.sqlite"
        if sqlite_path.is_file():
            return SQLiteRoadGraph(sqlite_path)
        return graph_from_rows(read_csv(path / "road_nodes.csv"), read_csv(path / "road_edges.csv"))
    if path.suffix.lower() in {".sqlite", ".db"}:
        return SQLiteRoadGraph(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return graph_from_rows(payload.get("nodes", []), payload.get("edges", []))
    raise ValueError("road graph JSON must be an object containing nodes and edges")


def graph_from_pbf(
    path: Path,
    max_ways: int,
    *,
    include_restricted: bool = False,
    include_ferries: bool = False,
) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]]:
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - optional adapter
        raise SystemExit("the osmium package (PyOsmium bindings) is required for --from-pbf") from exc

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.coordinates: dict[str, tuple[float, float]] = {}
            self.edges: list[dict[str, str]] = []
            self.count = 0

        def way(self, way) -> None:
            route = str(way.tags.get("route", "")).lower()
            ferry_tag = str(way.tags.get("ferry", "")).lower()
            is_ferry = route == "ferry" or bool(ferry_tag and ferry_tag != "no")
            if (
                (not is_ferry and max_ways > 0 and self.count >= max_ways)
                or (not is_ferry and way.tags.get("highway") not in HIGHWAY_TYPES)
                or (is_ferry and not include_ferries)
                or (not include_restricted and is_access_restricted(way.tags))
            ):
                return
            points = [(str(node.ref), node.lat, node.lon) for node in way.nodes if node.location.valid()]
            if len(points) < 2:
                return
            if not is_ferry:
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


def write_sqlite_graph_from_pbf(
    path: Path,
    output: Path,
    max_ways: int = 0,
    *,
    include_restricted: bool = False,
) -> dict[str, int | bool]:
    """Stream a PBF highway scan into a disk-backed graph.

    ``max_ways=0`` means all supported highway ways.  Nodes and edges are
    batched into SQLite so the complete graph does not require a Python
    adjacency list containing millions of objects.
    """
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - optional adapter
        raise SystemExit("the osmium package (PyOsmium bindings) is required for --from-pbf") from exc

    output.mkdir(parents=True, exist_ok=True)
    database = output / "road_graph.sqlite"
    temporary = output / ".road_graph.sqlite.tmp"
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            cell_lat INTEGER NOT NULL,
            cell_lon INTEGER NOT NULL
        );
        CREATE TABLE edges (
            u TEXT NOT NULL,
            v TEXT NOT NULL,
            length_m REAL NOT NULL,
            oneway TEXT NOT NULL,
            way_id TEXT NOT NULL
        );
        CREATE TABLE ferry_edges (
            u TEXT NOT NULL,
            v TEXT NOT NULL,
            length_m REAL NOT NULL,
            oneway TEXT NOT NULL,
            way_id TEXT NOT NULL
        );
        CREATE TABLE ways (
            way_id TEXT PRIMARY KEY,
            node_json TEXT NOT NULL,
            oneway TEXT NOT NULL
        );
        CREATE TABLE turn_restrictions (
            relation_id TEXT NOT NULL,
            via_node TEXT NOT NULL,
            from_way TEXT NOT NULL,
            to_way TEXT NOT NULL,
            kind TEXT NOT NULL,
            via_way_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE conditional_turn_restrictions (
            relation_id TEXT NOT NULL,
            via_node TEXT NOT NULL,
            from_way TEXT NOT NULL,
            to_way TEXT NOT NULL,
            kind TEXT NOT NULL,
            via_way_json TEXT NOT NULL DEFAULT '[]',
            condition TEXT NOT NULL
        );
        """
    )

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.way_n = 0
            self.segment_n = 0
            self.node_refs = 0
            self.ferry_way_n = 0
            self.ferry_segment_n = 0
            self.ferry_node_refs = 0
            self.ferry_relation_n = 0
            self.excluded_access_way_n = 0
            self.restriction_relation_n = 0
            self.restriction_conditional_n = 0
            self.restriction_conditional_supported_n = 0
            self.restriction_conditional_unsupported_n = 0
            self.restriction_conditional_unresolved_n = 0
            self.restriction_conditional_geometry_unsupported_n = 0
            self.restriction_via_way_n = 0
            self.restriction_via_way_unresolved_n = 0
            self.restriction_via_way_unsupported_n = 0
            self.restriction_unsupported_n = 0
            self.node_rows: list[tuple[str, float, float, int, int]] = []
            self.way_rows: list[tuple[str, str, str]] = []
            self.edge_rows: list[tuple[str, str, float, str, str]] = []
            self.ferry_edge_rows: list[tuple[str, str, float, str, str]] = []
            self.restriction_rows: list[tuple[str, str, str, str, str, str]] = []
            self.conditional_restriction_rows: list[tuple[str, str, str, str, str, str, str]] = []

        def flush(self) -> None:
            if self.node_rows:
                connection.executemany(
                    "INSERT OR IGNORE INTO nodes(node_id, lat, lon, cell_lat, cell_lon) VALUES (?, ?, ?, ?, ?)",
                    self.node_rows,
                )
                self.node_rows.clear()
            if self.way_rows:
                connection.executemany(
                    "INSERT OR REPLACE INTO ways(way_id, node_json, oneway) VALUES (?, ?, ?)",
                    self.way_rows,
                )
                self.way_rows.clear()
            if self.edge_rows:
                connection.executemany(
                    "INSERT INTO edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
                    self.edge_rows,
                )
                self.edge_rows.clear()
            if self.ferry_edge_rows:
                connection.executemany(
                    "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
                    self.ferry_edge_rows,
                )
                self.ferry_edge_rows.clear()
            connection.commit()

        def way(self, way) -> None:
            highway = str(way.tags.get("highway", ""))
            route = str(way.tags.get("route", "")).lower()
            ferry_tag = str(way.tags.get("ferry", "")).lower()
            is_ferry = route == "ferry" or bool(ferry_tag and ferry_tag != "no")
            if not is_ferry and highway not in HIGHWAY_TYPES:
                return
            if not include_restricted and is_access_restricted(way.tags):
                self.excluded_access_way_n += 1
                return
            if not is_ferry and max_ways > 0 and self.way_n >= max_ways:
                return
            points = [(str(node.ref), node.lat, node.lon) for node in way.nodes if node.location.valid()]
            if len(points) < 2:
                return
            way_id = str(way.id)
            oneway = str(way.tags.get("oneway", "")).lower()
            for node_id, lat, lon in points:
                self.node_rows.append(
                    (
                        node_id,
                        float(lat),
                        float(lon),
                        math.floor(float(lat) / SQLiteRoadGraph.CELL_DEG),
                        math.floor(float(lon) / SQLiteRoadGraph.CELL_DEG),
                    )
                )
            if is_ferry:
                self.ferry_way_n += 1
                self.ferry_node_refs += len(points)
                for first, second in pairwise(points):
                    length = straight_distance(
                        {"lat": str(first[1]), "lon": str(first[2])},
                        {"lat": str(second[1]), "lon": str(second[2])},
                    )
                    self.ferry_edge_rows.append((first[0], second[0], length, oneway, way_id))
                    self.ferry_segment_n += 1
                if len(self.ferry_edge_rows) >= 100_000:
                    self.flush()
                return
            self.way_n += 1
            self.node_refs += len(points)
            self.way_rows.append((way_id, json.dumps([point[0] for point in points]), oneway))
            for first, second in pairwise(points):
                length = straight_distance(
                    {"lat": str(first[1]), "lon": str(first[2])},
                    {"lat": str(second[1]), "lon": str(second[2])},
                )
                self.edge_rows.append((first[0], second[0], length, oneway, way_id))
                self.segment_n += 1
            if len(self.edge_rows) >= 100_000:
                self.flush()

        def relation(self, relation) -> None:
            relation_type = str(relation.tags.get("type", ""))
            if relation_type == "route" and str(relation.tags.get("route", "")).lower() == "ferry":
                self.ferry_relation_n += 1
            if relation_type != "restriction":
                return
            self.restriction_relation_n += 1
            conditional_value = next(
                (
                    relation.tags.get(key)
                    for key in (
                        "restriction:motor_vehicle:conditional",
                        "restriction:motorcar:conditional",
                        "restriction:vehicle:conditional",
                        "restriction:conditional",
                    )
                    if relation.tags.get(key)
                ),
                None,
            )
            condition = ""
            if conditional_value:
                self.restriction_conditional_n += 1
                parsed = parse_conditional_restriction(str(conditional_value))
                if parsed is None:
                    self.restriction_conditional_unsupported_n += 1
                    return
                kind, condition = parsed
                self.restriction_conditional_supported_n += 1
            else:
                restriction = str(
                    relation.tags.get("restriction:motor_vehicle", "")
                    or relation.tags.get("restriction:motorcar", "")
                    or relation.tags.get("restriction", "")
                ).lower()
                if restriction.startswith("no_"):
                    kind = "no"
                elif restriction.startswith("only_"):
                    kind = "only"
                else:
                    self.restriction_unsupported_n += 1
                    return
            from_ways = [str(member.ref) for member in relation.members if member.role == "from" and member.type == "w"]
            to_ways = [str(member.ref) for member in relation.members if member.role == "to" and member.type == "w"]
            via_nodes = [str(member.ref) for member in relation.members if member.role == "via" and member.type == "n"]
            via_ways = [str(member.ref) for member in relation.members if member.role == "via" and member.type == "w"]
            if via_ways:
                self.restriction_via_way_n += 1
                if len(from_ways) != 1 or len(to_ways) != 1 or via_nodes:
                    self.restriction_via_way_unsupported_n += 1
                    self.restriction_unsupported_n += 1
                    return
                row = (f"relation/{relation.id}", "", from_ways[0], to_ways[0], kind, json.dumps(via_ways))
                if condition:
                    self.conditional_restriction_rows.append((*row, condition))
                else:
                    self.restriction_rows.append(row)
                return
            if len(from_ways) != 1 or len(to_ways) != 1 or len(via_nodes) != 1:
                self.restriction_unsupported_n += 1
                return
            row = (f"relation/{relation.id}", via_nodes[0], from_ways[0], to_ways[0], kind, "[]")
            if condition:
                self.conditional_restriction_rows.append((*row, condition))
            else:
                self.restriction_rows.append(row)

    handler = Handler()
    try:
        handler.apply_file(str(path), locations=True)
        handler.flush()
        way_sequences: dict[str, list[str]] = {}
        restriction_rows: list[tuple[str, str, str, str, str, str]] = []
        conditional_restriction_rows: list[tuple[str, str, str, str, str, str, str]] = []
        unresolved_restriction_n = 0
        source_rows = (*handler.restriction_rows, *handler.conditional_restriction_rows)
        referenced_way_ids = {
            way_id
            for _relation_id, _via_node, from_way, to_way, _kind, via_way_json, *_condition in source_rows
            for way_id in (from_way, to_way, *json.loads(via_way_json))
        }
        for way_id in referenced_way_ids:
            row = connection.execute(
                "SELECT node_json FROM ways WHERE way_id = ?",
                (way_id,),
            ).fetchone()
            if row is not None:
                try:
                    way_sequences[way_id] = [str(node_id) for node_id in json.loads(row[0])]
                except (TypeError, ValueError, json.JSONDecodeError):
                    way_sequences[way_id] = []
        def resolve_row(
            relation_id: str,
            via_node: str,
            from_way: str,
            to_way: str,
            kind: str,
            via_way_json: str,
            *,
            conditional: bool = False,
        ) -> tuple[str, str, str, str, str, str] | None:
            nonlocal unresolved_restriction_n
            via_ways = tuple(str(value) for value in json.loads(via_way_json))
            if via_ways:
                sequence_ids = (from_way, *via_ways, to_way)
                sequences = [way_sequences.get(way_id, []) for way_id in sequence_ids]
                if any(not sequence for sequence in sequences):
                    handler.restriction_via_way_unresolved_n += 1
                    if conditional:
                        handler.restriction_conditional_unresolved_n += 1
                    unresolved_restriction_n += 1
                    return None
                junction_nodes: list[str] = []
                valid_chain = True
                for first, second in pairwise(sequences):
                    shared_endpoints = {first[0], first[-1]} & {second[0], second[-1]}
                    if len(shared_endpoints) != 1:
                        valid_chain = False
                        break
                    junction_nodes.append(next(iter(shared_endpoints)))
                if not valid_chain:
                    handler.restriction_via_way_unsupported_n += 1
                    if conditional:
                        handler.restriction_conditional_geometry_unsupported_n += 1
                    handler.restriction_unsupported_n += 1
                    return None
                via_node = junction_nodes[-1]
                if not connection.execute(
                    "SELECT 1 FROM nodes WHERE node_id = ?",
                    (via_node,),
                ).fetchone():
                    handler.restriction_via_way_unresolved_n += 1
                    if conditional:
                        handler.restriction_conditional_unresolved_n += 1
                    unresolved_restriction_n += 1
                    return None
                return (relation_id, via_node, from_way, to_way, kind, json.dumps(list(via_ways)))
            from_sequence = way_sequences.get(from_way, [])
            to_sequence = way_sequences.get(to_way, [])
            via_exists = connection.execute(
                "SELECT 1 FROM nodes WHERE node_id = ?",
                (via_node,),
            ).fetchone()
            if not from_sequence or not to_sequence or not via_exists:
                if conditional:
                    handler.restriction_conditional_unresolved_n += 1
                unresolved_restriction_n += 1
                return None
            if (
                via_node not in (from_sequence[0], from_sequence[-1])
                or via_node not in (to_sequence[0], to_sequence[-1])
            ):
                if conditional:
                    handler.restriction_conditional_geometry_unsupported_n += 1
                handler.restriction_unsupported_n += 1
                return None
            return (relation_id, via_node, from_way, to_way, kind, "[]")

        for row in handler.restriction_rows:
            resolved = resolve_row(*row)
            if resolved is not None:
                restriction_rows.append(resolved)
        for row in handler.conditional_restriction_rows:
            resolved = resolve_row(*row[:6], conditional=True)
            if resolved is not None:
                conditional_restriction_rows.append((*resolved, row[6]))
        if restriction_rows:
            connection.executemany(
                "INSERT INTO turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json) VALUES (?, ?, ?, ?, ?, ?)",
                restriction_rows,
            )
        if conditional_restriction_rows:
            connection.executemany(
                "INSERT INTO conditional_turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json, condition) VALUES (?, ?, ?, ?, ?, ?, ?)",
                conditional_restriction_rows,
            )
        connection.execute("CREATE INDEX nodes_cell ON nodes(cell_lat, cell_lon)")
        connection.execute("CREATE INDEX edges_u ON edges(u)")
        connection.execute("CREATE INDEX edges_v ON edges(v)")
        connection.execute("CREATE INDEX ferry_edges_u ON ferry_edges(u)")
        connection.execute("CREATE INDEX ferry_edges_v ON ferry_edges(v)")
        connection.execute("CREATE INDEX turns_via_from ON turn_restrictions(via_node, from_way)")
        connection.execute(
            "CREATE INDEX conditional_turns_via_from ON conditional_turn_restrictions(via_node, from_way)"
        )
        connection.execute("ANALYZE")
        node_n = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        edge_n = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        ferry_edge_n = int(connection.execute("SELECT COUNT(*) FROM ferry_edges").fetchone()[0])
        turn_restriction_n = int(connection.execute("SELECT COUNT(*) FROM turn_restrictions").fetchone()[0])
        restriction_via_way_applied_n = int(connection.execute(
            "SELECT COUNT(*) FROM turn_restrictions WHERE via_way_json != '[]'"
        ).fetchone()[0])
        conditional_restriction_n = int(
            connection.execute("SELECT COUNT(*) FROM conditional_turn_restrictions").fetchone()[0]
        )
        conditional_via_way_applied_n = int(connection.execute(
            "SELECT COUNT(*) FROM conditional_turn_restrictions WHERE via_way_json != '[]'"
        ).fetchone()[0])
        restriction_via_way_applied_n += conditional_via_way_applied_n
        connection.commit()
        connection.close()
        os.replace(temporary, database)
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    metadata = {
        "format": "ireland-geometry-road-sqlite-v6",
        "storage": "sqlite",
        "directed": True,
        "source": str(path),
        "node_n": node_n,
        "edge_n": edge_n,
        "way_n": handler.way_n,
        "segment_n": handler.segment_n,
        "node_refs": handler.node_refs,
        "ferry_way_n": handler.ferry_way_n,
        "ferry_segment_n": handler.ferry_segment_n,
        "ferry_node_refs": handler.ferry_node_refs,
        "ferry_edge_n": ferry_edge_n,
        "ferry_relation_n": handler.ferry_relation_n,
        "ferry_schedules_modeled": False,
        "max_ways": max_ways,
        "complete": max_ways == 0,
        "include_restricted": include_restricted,
        "excluded_access_way_n": handler.excluded_access_way_n,
        "turn_restriction_n": turn_restriction_n,
        "restriction_relation_n": handler.restriction_relation_n,
        "restriction_applied_n": turn_restriction_n,
        "restriction_unresolved_n": unresolved_restriction_n,
        "restriction_conditional_n": handler.restriction_conditional_n,
        "restriction_conditional_supported_n": handler.restriction_conditional_supported_n,
        "restriction_conditional_unsupported_n": handler.restriction_conditional_unsupported_n,
        "restriction_conditional_stored_n": conditional_restriction_n,
        "restriction_conditional_unresolved_n": handler.restriction_conditional_unresolved_n,
        "restriction_conditional_geometry_unsupported_n": handler.restriction_conditional_geometry_unsupported_n,
        "restriction_via_way_n": handler.restriction_via_way_n,
        "restriction_via_way_applied_n": restriction_via_way_applied_n,
        "restriction_via_way_unresolved_n": handler.restriction_via_way_unresolved_n,
        "restriction_via_way_unsupported_n": handler.restriction_via_way_unsupported_n,
        "restriction_unsupported_n": handler.restriction_unsupported_n,
        "turn_restrictions_supported": True,
    }
    atomic_write_json(output / "road_graph_metadata.json", metadata, indent=2)
    return {
        "node_n": node_n,
        "edge_n": edge_n,
        "way_n": handler.way_n,
        "ferry_way_n": handler.ferry_way_n,
        "ferry_edge_n": ferry_edge_n,
        "ferry_relation_n": handler.ferry_relation_n,
        "turn_restriction_n": turn_restriction_n,
        "restriction_conditional_stored_n": conditional_restriction_n,
        "restriction_via_way_applied_n": restriction_via_way_applied_n,
        "complete": max_ways == 0,
    }


def write_graph(
    path: Path,
    coordinates: dict[str, tuple[float, float]],
    adjacency: dict[str, list[tuple[str, float]]],
    *,
    source: str,
) -> None:
    """Persist a directed graph in the portable node/edge contract."""
    path.mkdir(parents=True, exist_ok=True)
    node_rows = [
        {"node_id": node, "lat": lat, "lon": lon}
        for node, (lat, lon) in sorted(coordinates.items())
    ]
    edge_rows = [
        {"u": node, "v": neighbour, "length_m": round(length, 3), "oneway": "yes"}
        for node in sorted(adjacency)
        for neighbour, length in sorted(adjacency[node], key=lambda item: (item[0], item[1]))
    ]
    atomic_write_csv(path / "road_nodes.csv", ["node_id", "lat", "lon"], node_rows)
    atomic_write_csv(path / "road_edges.csv", ["u", "v", "length_m", "oneway"], edge_rows)
    atomic_write_json(
        path / "road_graph_metadata.json",
        {
            "format": "ireland-geometry-road-graph-v1",
            "directed": True,
            "source": source,
            "node_n": len(node_rows),
            "edge_n": len(edge_rows),
        },
        indent=2,
    )


@dataclass(frozen=True)
class NodeIndex:
    """Deterministic nearest-node index with a coarse 2-D spatial grid.

    The latitude-sorted arrays are retained as a compatibility fallback.  The
    grid avoids scanning every road node in a latitude band, which becomes
    expensive on country-scale graphs with more than a million nodes.
    """

    latitudes: list[float]
    nodes: list[str]
    cells: dict[tuple[int, int], tuple[str, ...]]
    bounds: tuple[int, int, int, int] | None
    cell_deg: float = 0.01


def build_node_index(coordinates: dict[str, tuple[float, float]]) -> NodeIndex:
    """Build a deterministic 2-D grid plus a latitude-sorted fallback index."""
    cell_deg = 0.01
    ordered = sorted((lat, node) for node, (lat, _lon) in coordinates.items())
    cells: dict[tuple[int, int], list[str]] = defaultdict(list)
    for node, (lat, lon) in coordinates.items():
        cells[(math.floor(lat / cell_deg), math.floor(lon / cell_deg))].append(node)
    return NodeIndex(
        latitudes=[lat for lat, _node in ordered],
        nodes=[node for _lat, node in ordered],
        cells={key: tuple(sorted(value)) for key, value in cells.items()},
        bounds=(
            min(key[0] for key in cells),
            max(key[0] for key in cells),
            min(key[1] for key in cells),
            max(key[1] for key in cells),
        ) if cells else None,
        cell_deg=cell_deg,
    )


def _nearest_node_from_grid(
    row: dict[str, str],
    coordinates: dict[str, tuple[float, float]],
    index: NodeIndex,
) -> str | None:
    lat = number(row.get("lat"))
    lon = number(row.get("lon"))
    cell_deg = index.cell_deg
    cell_lat = math.floor(lat / cell_deg)
    cell_lon = math.floor(lon / cell_deg)
    best: str | None = None
    best_distance = float("inf")
    ring = 0
    if index.bounds is None:
        return None
    min_lat_cell, max_lat_cell, min_lon_cell, max_lon_cell = index.bounds
    max_ring = max(
        abs(cell_lat - min_lat_cell),
        abs(cell_lat - max_lat_cell),
        abs(cell_lon - min_lon_cell),
        abs(cell_lon - max_lon_cell),
    )
    while ring <= max_ring:
        for lat_cell in range(cell_lat - ring, cell_lat + ring + 1):
            for lon_cell in range(cell_lon - ring, cell_lon + ring + 1):
                if max(abs(lat_cell - cell_lat), abs(lon_cell - cell_lon)) != ring:
                    continue
                for node in index.cells.get((lat_cell, lon_cell), ()):
                    node_lat, node_lon = coordinates[node]
                    distance = (node_lat - lat) ** 2 + (node_lon - lon) ** 2
                    if distance < best_distance or (distance == best_distance and node < (best or node)):
                        best = node
                        best_distance = distance
        if best is not None:
            lat_min = (cell_lat - ring) * cell_deg
            lat_max = (cell_lat + ring + 1) * cell_deg
            lon_min = (cell_lon - ring) * cell_deg
            lon_max = (cell_lon + ring + 1) * cell_deg
            outside_distance = min(
                lat - lat_min,
                lat_max - lat,
                lon - lon_min,
                lon_max - lon,
            ) ** 2
            if best_distance <= outside_distance:
                return best
        ring += 1
    return best


def nearest_node(
    row: dict[str, str],
    coordinates: dict[str, tuple[float, float]] | SQLiteRoadGraph,
    index: NodeIndex | tuple[list[float], list[str]] | None = None,
) -> str | None:
    if isinstance(coordinates, SQLiteRoadGraph):
        return coordinates.nearest_node(row)
    if not coordinates:
        return None
    lat = number(row.get("lat"))
    lon = number(row.get("lon"))
    if index is None:
        return min(
            coordinates,
            key=lambda node: (coordinates[node][0] - lat) ** 2 + (coordinates[node][1] - lon) ** 2,
        )
    if isinstance(index, NodeIndex):
        return _nearest_node_from_grid(row, coordinates, index)
    latitudes, nodes = index
    if not latitudes:
        return None
    seed = min(bisect_left(latitudes, lat), len(latitudes) - 1)
    if seed and abs(latitudes[seed - 1] - lat) < abs(latitudes[seed] - lat):
        seed -= 1
    best = nodes[seed]
    best_distance = (coordinates[best][0] - lat) ** 2 + (coordinates[best][1] - lon) ** 2
    radius = math.sqrt(best_distance)
    left = bisect_left(latitudes, lat - radius)
    right = bisect_right(latitudes, lat + radius)
    for node in nodes[left:right]:
        distance = (coordinates[node][0] - lat) ** 2 + (coordinates[node][1] - lon) ** 2
        if distance < best_distance:
            best = node
            best_distance = distance
    return best


def _reconstruct_state_path(state: tuple, predecessors: dict[tuple, tuple]) -> list[str]:
    nodes = [str(state[0])]
    while state in predecessors:
        state = predecessors[state]
        nodes.append(str(state[0]))
    nodes.reverse()
    return nodes


def _reconstruct_node_path(goal: str, predecessors: dict[str, str]) -> list[str]:
    nodes = [goal]
    while nodes[-1] in predecessors:
        nodes.append(predecessors[nodes[-1]])
    nodes.reverse()
    return nodes


def shortest_path(
    start: str,
    goal: str,
    adjacency: dict[str, list[tuple[str, float]]] | SQLiteRoadGraph,
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    return_path: bool = False,
    include_ferries: bool = False,
) -> float | tuple[float, list[str]] | None:
    """Return shortest distance, optionally with the traversed node path."""
    if isinstance(adjacency, SQLiteRoadGraph):
        adjacency.set_time_profile(departure, speed_kmh, include_ferries=include_ferries)
    if start == goal:
        return (0.0, [start]) if return_path else 0.0
    if isinstance(adjacency, SQLiteRoadGraph) and adjacency.has_turn_restrictions:
        # Keep only active suffixes of restriction way-runs in the search
        # state. Ordinary graph nodes still collapse to a single state once
        # no restriction prefix can continue from them.
        empty_state: tuple[tuple[str, ...], ...] = ()
        State = tuple[str, str, tuple[tuple[str, ...], ...]]
        distances: dict[State, float] = {
            (start, "", empty_state): 0.0
        }
        predecessors: dict[State, State] = {}
        queue: list[tuple[float, str, str, tuple[tuple[str, ...], ...]]] = [
            (0.0, start, "", empty_state)
        ]
        while queue:
            distance, node, incoming_way, restriction_state = heapq.heappop(queue)
            state = (node, incoming_way, restriction_state)
            if distance != distances.get(state):
                continue
            if node == goal:
                return (
                    (distance, _reconstruct_state_path(state, predecessors))
                    if return_path
                    else distance
                )
            for neighbour, weight, way_id in adjacency.neighbours(
                node,
                incoming_way,
                restriction_state,
                distance,
            ):
                next_restriction_state = adjacency.advance_restriction_state(
                    restriction_state,
                    incoming_way,
                    way_id,
                )
                next_incoming_way = way_id if next_restriction_state else ""
                next_state = (neighbour, next_incoming_way, next_restriction_state)
                candidate = distance + weight
                if candidate < distances.get(next_state, float("inf")):
                    distances[next_state] = candidate
                    if return_path:
                        predecessors[next_state] = state
                    heapq.heappush(
                        queue,
                        (candidate, neighbour, next_incoming_way, next_restriction_state),
                    )
        return None
    if isinstance(adjacency, SQLiteRoadGraph):
        distances = {start: 0.0}
        predecessors: dict[str, str] = {}
        queue = [(0.0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            if node == goal:
                return (
                    (distance, _reconstruct_node_path(node, predecessors))
                    if return_path
                    else distance
                )
            for neighbour, weight, _way_id in adjacency.neighbours(node):
                candidate = distance + weight
                if candidate < distances.get(neighbour, float("inf")):
                    distances[neighbour] = candidate
                    if return_path:
                        predecessors[neighbour] = node
                    heapq.heappush(queue, (candidate, neighbour))
        return None
    distances = {start: 0.0}
    predecessors: dict[str, str] = {}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        if node == goal:
            return (
                (distance, _reconstruct_node_path(node, predecessors))
                if return_path
                else distance
            )
        for neighbour, weight in adjacency.get(node, []):
            candidate = distance + weight
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                if return_path:
                    predecessors[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    return None


def parse_departure(value: str | None) -> datetime | None:
    """Parse a local ISO-8601 departure timestamp for conditional routing."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("departure must be an ISO-8601 date/time") from exc


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
    parser.add_argument("--from-pbf", action="store_true", help="build a graph from --pbf")
    parser.add_argument("--write-graph", default=None, help="persist a PBF-built graph to this directory")
    parser.add_argument(
        "--graph-format",
        choices=("csv", "sqlite"),
        default="csv",
        help="graph format for --write-graph; SQLite supports complete disk-backed exports",
    )
    parser.add_argument("--pbf", default=None)
    parser.add_argument("--max-ways", type=int, default=100_000, help="0 scans all supported highway ways")
    parser.add_argument("--max-pairs", type=int, default=5000)
    parser.add_argument(
        "--departure",
        default=None,
        help="optional ISO-8601 local departure time for conditional turn restrictions",
    )
    parser.add_argument(
        "--speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for advancing conditional turn windows (default: 50)",
    )
    parser.add_argument(
        "--include-restricted",
        action="store_true",
        help="retain ways tagged private/restricted/no for motor-vehicle routing",
    )
    parser.add_argument(
        "--include-ferries",
        action="store_true",
        help="include persisted ferry geometry in routing; ferry schedules are not modeled",
    )
    args = parser.parse_args(argv)
    if args.max_ways < 0:
        parser.error("--max-ways must be non-negative (0 means all supported highway ways)")
    if args.max_pairs < 0:
        parser.error("--max-pairs must be non-negative")
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    try:
        departure = parse_departure(args.departure)
    except ValueError as exc:
        parser.error(str(exc))
    if args.from_pbf and args.max_ways == 0 and (not args.write_graph or args.graph_format != "sqlite"):
        parser.error("complete PBF scans require --write-graph and --graph-format sqlite")
    data = project_path(args.data_root, "data")
    out = project_path(args.out_dir, "output")
    if args.write_graph and not args.from_pbf:
        raise SystemExit("--write-graph requires --from-pbf")
    graph_path = project_path(args.road_graph, str(data / "roads")) if args.road_graph else data / "roads"
    source = str(graph_path)
    coordinates: dict[str, tuple[float, float]] = {}
    adjacency: dict[str, list[tuple[str, float]]] = {}
    graph: SQLiteRoadGraph | None = None
    try:
        if args.from_pbf:
            pbf = project_path(args.pbf, str(data / "raw" / "ireland-latest.osm.pbf"))
            source = str(pbf)
            if args.write_graph:
                graph_output = project_path(args.write_graph, str(data / "roads"))
                if args.graph_format == "sqlite":
                    write_sqlite_graph_from_pbf(
                        pbf,
                        graph_output,
                        max_ways=args.max_ways,
                        include_restricted=args.include_restricted,
                    )
                    graph = SQLiteRoadGraph(graph_output / "road_graph.sqlite")
                else:
                    coordinates, adjacency = graph_from_pbf(
                        pbf,
                        args.max_ways,
                        include_restricted=args.include_restricted,
                        include_ferries=args.include_ferries,
                    )
                    write_graph(graph_output, coordinates, adjacency, source=str(pbf))
                source = str(graph_output)
            else:
                coordinates, adjacency = graph_from_pbf(
                    pbf,
                    args.max_ways,
                    include_restricted=args.include_restricted,
                    include_ferries=args.include_ferries,
                )
        elif graph_path.is_dir() and not (
            (graph_path / "road_nodes.csv").exists() or (graph_path / "road_graph.sqlite").exists()
        ):
            write_unavailable(out, "not_provided")
            print("[road-routing] graph directory has no supported graph; wrote explicit not_provided outputs")
            return
        elif graph_path.exists():
            loaded = load_graph(graph_path)
            if isinstance(loaded, SQLiteRoadGraph):
                graph = loaded
            else:
                coordinates, adjacency = loaded
        else:
            write_unavailable(out, "not_provided")
            print("[road-routing] no road graph supplied; wrote explicit not_provided outputs")
            return
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        write_unavailable(out, "provided_but_unreadable")
        print(f"[road-routing] graph unavailable: {exc}")
        return
    if graph is not None:
        graph_empty = graph.node_count == 0 or (
            graph.edge_count == 0 and graph.ferry_edge_count == 0
        )
        graph_node_n = graph.node_count
    else:
        graph_empty = not coordinates or not adjacency
        graph_node_n = len(coordinates)
    if graph_empty:
        if graph is not None:
            graph.close()
        write_unavailable(out, "empty_graph")
        return
    analysis_rows = read_csv(out / "analysis_results.csv")
    by_id = {row.get("osm_id", ""): row for row in analysis_rows}
    pairs = read_csv(out / "matched_controls_strict.csv") or read_csv(out / "matched_controls.csv")
    pairs = sorted(pairs, key=lambda row: (row.get("target_osm_id", ""), row.get("control_osm_id", "")))[: args.max_pairs]
    node_index = None if graph is not None else build_node_index(coordinates)
    graph_coordinates = graph if graph is not None else coordinates
    graph_adjacency = graph if graph is not None else adjacency
    if graph is not None:
        routing_method = "Dijkstra on SQLite road graph; indexed nearest graph node snap"
        if graph.has_turn_restrictions:
            routing_method += "; OSM via-node and validated via-way turn restrictions"
        if graph.conditional_restriction_n:
            if departure is None:
                routing_method += "; conditional windows retained but inactive"
            else:
                routing_method += f"; conditional windows evaluated from {args.departure} at {args.speed_kmh:g} km/h"
        if graph.ferry_edge_count:
            routing_method += (
                "; ferry geometry included; ferry schedules not modeled"
                if args.include_ferries
                else "; ferry geometry available but excluded"
            )
    else:
        routing_method = "Dijkstra on supplied road graph; nearest graph node snap"
        if args.include_ferries:
            routing_method += "; ferry geometry unavailable in portable graph"
    routed = []
    for pair in pairs:
        target = by_id.get(pair.get("target_osm_id", ""))
        control = by_id.get(pair.get("control_osm_id", ""))
        if not target or not control:
            continue
        start = nearest_node(target, graph_coordinates, node_index)
        goal = nearest_node(control, graph_coordinates, node_index)
        route = (
            shortest_path(
                start,
                goal,
                graph_adjacency,
                departure=departure,
                speed_kmh=args.speed_kmh,
                include_ferries=args.include_ferries,
            )
            if start and goal
            else None
        )
        routed.append(
            {
                "target_osm_id": target["osm_id"],
                "control_osm_id": control["osm_id"],
                "target_group": target.get("group", "other"),
                "straight_distance_m": round(straight_distance(target, control), 2),
                "route_distance_m": round(route, 2) if route is not None else "",
                "reachable": int(route is not None),
                "status": "provided",
                "method": routing_method,
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
                "method": routing_method,
            }
        )
    atomic_write_csv(
        out / "road_routing.csv",
        ["group", "sample_n", "route_n", "reachable_pct", "median_route_m", "p90_route_m", "median_straight_m", "network_to_straight_ratio", "status", "source", "method"],
        summaries or [{"group": "all", "sample_n": 0, "route_n": 0, "reachable_pct": 0, "median_route_m": "", "p90_route_m": "", "median_straight_m": "", "network_to_straight_ratio": "", "status": "no_pairs", "source": source, "method": "Dijkstra"}],
    )
    if graph is not None:
        graph.close()
    print(f"[road-routing] routed {len(routed):,} pairs on {graph_node_n:,} graph nodes")


if __name__ == "__main__":
    main()
