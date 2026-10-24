#!/usr/bin/env python3
"""Run one point-to-point route query against a persisted road graph.

The command is deliberately separate from the pipeline's sampled
target/control diagnostic.  It accepts WGS84 coordinates, snaps them to the
nearest graph nodes, applies the same one-way and turn-restriction semantics,
and emits a small JSON result suitable for scripts or interactive inspection.
When a SQLite graph has a ``ferry_schedules.json`` companion contract,
explicit departures enforce supported weekly or calendar-qualified service
windows, waiting for the next opening when necessary, and route timing uses
the recorded per-way crossing durations. An optional ``public_holidays.json``
companion supplies dates for ``PH`` tokens.
An optional ``--weight-t`` profile evaluates vehicle-weight conditional turn
restrictions such as ``weight>7.5``. ``--vehicle-class delivery`` activates
OSM conditional access windows tagged for delivery vehicles and
``--vehicle-class psv`` activates public-service-vehicle windows on SQLite
graphs; ``--vehicle-class taxi`` activates taxi-specific windows.
Multiple supported conditional access clauses on one way and direction are
retained and evaluated as a conservative rule set.
``--vehicle-class hgv`` activates heavy-goods-vehicle-specific actual-weight
limits when combined with ``--weight-t``; ``--rating-t`` separately activates
``maxweightrating:hgv`` limits and the Irish ``maxweightrating:goods`` alias
based on the vehicle's permitted gross-weight rating.
``--height-m`` activates numeric legal and physical height limits on SQLite
graphs.
``--width-m``, ``--length-m``, and ``--axleload-t`` activate numeric legal
width, length, and axle-load limits on SQLite graphs.
Duration estimates on v21 SQLite graphs also apply supported numeric OSM
``maxspeed`` ceilings per way and safely supported ``maxspeed:conditional``
windows when a departure profile is supplied; unsupported conditional syntax
remains retained but is not evaluated.
Supported conditional one-way schedules on v21 SQLite graphs are evaluated
against the same departure profile; unsupported syntax preserves the base
one-way semantics.
``--vehicle-class hgv`` enforces static ``hgv=destination`` ways by default;
``--allow-hgv-destination`` explicitly opts into destination-delivery access
and supported destination exceptions to HGV weight/rating limits.
Route responses expose ferry way/distance/edge metrics when ferry geometry is
included; path-enabled output additionally preserves ordered SQLite or
portable way/segment IDs and per-segment distance, duration, ferry-wait, and
ferry-status metrics when the graph supplies way IDs. Portable CSV/JSON graphs
also expose supplied basic
road context; graphs without way IDs report that segment IDs are unavailable.
SQLite-backed path segments also expose normalized static edge constraints,
including their source key, value/unit, persistence status, and whether the
active vehicle profile evaluated the constraint. They also expose conditional
speed, one-way, and access rule records with entry-time active/applied state.
They also expose nullable human-readable OSM way context such as name, ref,
highway class, route type, and oneway state.
Path-enabled SQLite responses additionally expose deterministic geometry-derived
maneuvers, including turn bearings, road context, ferry transitions, and the
distance/duration to the next maneuver.
The reusable ``query_route_matrix`` helper evaluates a bounded Cartesian set of
origins and destinations with the same route semantics and emits compact pair
summaries, optionally retaining full path-enabled route responses.
The reusable ``query_route_comparison`` helper evaluates a bounded set of
named vehicle profiles over one trip and reports reachability and metric
deltas from the first profile, optionally retaining each full route response.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Union

try:
    from road_routing import (
        CONDITIONAL_ACCESS_DIRECTIONS,
        ROAD_CONTEXT_FIELDS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        PortableRoadGraph,
        SQLiteRoadGraph,
        build_node_index,
        load_graph,
        nearest_node,
        parse_departure,
        parse_vehicle_weight_condition,
        parse_weekly_schedule,
        shortest_path,
        shortest_path_metrics,
        straight_distance,
        validate_vehicle_class,
        vehicle_weight_condition_active,
    )
    from runtime import (
        atomic_write_json,
        package_version,
        project_data_tree_path,
        project_input_path,
        project_output_file_path,
    )
except ImportError:
    from scripts.road_routing import (
        CONDITIONAL_ACCESS_DIRECTIONS,
        ROAD_CONTEXT_FIELDS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        PortableRoadGraph,
        SQLiteRoadGraph,
        build_node_index,
        load_graph,
        nearest_node,
        parse_departure,
        parse_vehicle_weight_condition,
        parse_weekly_schedule,
        shortest_path,
        shortest_path_metrics,
        straight_distance,
        validate_vehicle_class,
        vehicle_weight_condition_active,
    )
    from scripts.runtime import (
        atomic_write_json,
        package_version,
        project_data_tree_path,
        project_input_path,
        project_output_file_path,
    )


ROUTE_CONTRACT = "ireland-geometry.route.v1"
ROUTE_MATRIX_CONTRACT = "ireland-geometry.route-matrix.v1"
ROUTE_MATRIX_MAX_PAIRS = 25
ROUTE_COMPARISON_CONTRACT = "ireland-geometry.route-comparison.v1"
ROUTE_COMPARISON_MIN_PROFILES = 2
ROUTE_COMPARISON_MAX_PROFILES = 8
ROUTE_PROFILE_FIELDS = (
    "vehicle_weight_t",
    "vehicle_rating_t",
    "vehicle_height_m",
    "vehicle_width_m",
    "vehicle_length_m",
    "vehicle_axleload_t",
    "vehicle_class",
    "allow_hgv_destination",
)


def validate_route_objective(value: str | None) -> str:
    """Normalize and validate the point-to-point route objective."""
    objective = str(value or "distance").strip().lower()
    if objective not in ROUTE_OBJECTIVES:
        raise ValueError(f"objective must be one of: {', '.join(ROUTE_OBJECTIVES)}")
    return objective


def validate_vehicle_weight_t(value: object) -> float | None:
    """Normalize an optional vehicle weight profile in metric tonnes."""
    if value is None or str(value).strip() == "":
        return None
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("weight_t must be a finite positive number of tonnes") from exc
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError("weight_t must be a finite positive number of tonnes")
    return weight


def validate_vehicle_rating_t(value: object) -> float | None:
    """Normalize an optional HGV permitted-rating profile in tonnes."""
    if value is None or str(value).strip() == "":
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "rating_t must be a finite positive number of tonnes"
        ) from exc
    if not math.isfinite(rating) or rating <= 0:
        raise ValueError("rating_t must be a finite positive number of tonnes")
    return rating


def validate_vehicle_height_m(value: object) -> float | None:
    """Normalize an optional vehicle height profile in metres."""
    if value is None or str(value).strip() == "":
        return None
    try:
        height = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("height_m must be a finite positive number of metres") from exc
    if not math.isfinite(height) or height <= 0:
        raise ValueError("height_m must be a finite positive number of metres")
    return height


def _validate_positive_profile(
    value: object,
    field: str,
    unit: str,
) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        profile = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number of {unit}") from exc
    if not math.isfinite(profile) or profile <= 0:
        raise ValueError(f"{field} must be a finite positive number of {unit}")
    return profile


def validate_vehicle_width_m(value: object) -> float | None:
    """Normalize an optional vehicle width profile in metres."""
    return _validate_positive_profile(value, "width_m", "metres")


def validate_vehicle_length_m(value: object) -> float | None:
    """Normalize an optional vehicle length profile in metres."""
    return _validate_positive_profile(value, "length_m", "metres")


def validate_vehicle_axleload_t(value: object) -> float | None:
    """Normalize an optional vehicle axle-load profile in metric tonnes."""
    return _validate_positive_profile(value, "axleload_t", "tonnes")


def _profile_boolean(value: object, field: str) -> bool:
    """Parse a strict boolean used by a route-profile specification."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field} must be a boolean")


def _normalize_route_profile(profile: dict[str, object]) -> dict[str, object]:
    """Validate one named vehicle profile for a comparison request."""
    if not isinstance(profile, dict):
        raise TypeError("each route profile must be an object")
    name = str(profile.get("name", "")).strip()
    if not name:
        raise ValueError("each route profile requires a non-empty name")
    if len(name) > 64 or any(
        not (character.isalnum() or character in "._-") for character in name
    ):
        raise ValueError(
            "route profile names must be at most 64 characters and use only letters, "
            "numbers, '.', '_' or '-'"
        )
    return {
        "name": name,
        "vehicle_weight_t": validate_vehicle_weight_t(
            profile.get("vehicle_weight_t", profile.get("weight_t"))
        ),
        "vehicle_rating_t": validate_vehicle_rating_t(
            profile.get("vehicle_rating_t", profile.get("rating_t"))
        ),
        "vehicle_height_m": validate_vehicle_height_m(
            profile.get("vehicle_height_m", profile.get("height_m"))
        ),
        "vehicle_width_m": validate_vehicle_width_m(
            profile.get("vehicle_width_m", profile.get("width_m"))
        ),
        "vehicle_length_m": validate_vehicle_length_m(
            profile.get("vehicle_length_m", profile.get("length_m"))
        ),
        "vehicle_axleload_t": validate_vehicle_axleload_t(
            profile.get("vehicle_axleload_t", profile.get("axleload_t"))
        ),
        "vehicle_class": validate_vehicle_class(profile.get("vehicle_class")),
        "allow_hgv_destination": _profile_boolean(
            profile.get("allow_hgv_destination", False),
            "allow_hgv_destination",
        ),
    }


def parse_route_profile_spec(value: str) -> dict[str, object]:
    """Parse ``NAME;key=value`` syntax used by CLI and HTTP comparison clients."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("route profile specifications must not be empty")
    parts = [part.strip() for part in raw.split(";")]
    profile: dict[str, object] = {"name": parts[0]}
    aliases = {
        "weight_t": "vehicle_weight_t",
        "rating_t": "vehicle_rating_t",
        "height_m": "vehicle_height_m",
        "width_m": "vehicle_width_m",
        "length_m": "vehicle_length_m",
        "axleload_t": "vehicle_axleload_t",
    }
    allowed = {*aliases, "vehicle_class", "allow_hgv_destination"}
    seen: set[str] = set()
    for assignment in parts[1:]:
        if not assignment or "=" not in assignment:
            raise ValueError(
                "route profile options must use NAME;key=value syntax"
            )
        key, raw_value = (part.strip() for part in assignment.split("=", 1))
        if key not in allowed:
            raise ValueError(
                f"unsupported route profile option {key!r}; expected one of: "
                f"{', '.join(sorted(allowed))}"
            )
        if key in seen:
            raise ValueError(f"route profile option {key!r} was supplied more than once")
        seen.add(key)
        profile[aliases.get(key, key)] = raw_value
    return _normalize_route_profile(profile)


def _validated_route_profiles(profiles: list[dict[str, object]]) -> list[dict[str, object]]:
    """Normalize a bounded list of uniquely named route profiles."""
    if len(profiles) < ROUTE_COMPARISON_MIN_PROFILES:
        raise ValueError(
            f"at least {ROUTE_COMPARISON_MIN_PROFILES} route profiles are required"
        )
    if len(profiles) > ROUTE_COMPARISON_MAX_PROFILES:
        raise ValueError(
            f"route profile count must not exceed {ROUTE_COMPARISON_MAX_PROFILES}"
        )
    normalized = [_normalize_route_profile(profile) for profile in profiles]
    names = [str(profile["name"]) for profile in normalized]
    if len(set(names)) != len(names):
        raise ValueError("route profile names must be unique")
    return normalized


Coordinates = dict[str, tuple[float, float]]
Graph = Union[PortableRoadGraph, SQLiteRoadGraph]  # noqa: UP007 - Python 3.9 import compatibility


def _point(lat: float, lon: float) -> dict[str, str]:
    return {"lat": str(lat), "lon": str(lon)}


def _node_coordinates(graph: Graph, coordinates: Coordinates, node: str) -> tuple[float, float] | None:
    if isinstance(graph, SQLiteRoadGraph):
        row = graph.connection.execute(
            "SELECT lat, lon FROM nodes WHERE node_id = ?",
            (node,),
        ).fetchone()
        return (float(row[0]), float(row[1])) if row else None
    return graph.coordinates.get(node)


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
            "way_context": graph.has_way_context,
            "way_context_n": graph.way_context_n,
            "ferry_edge_n": graph.ferry_edge_count,
            "ferry_schedule_n": graph.ferry_schedule_n,
            "ferry_schedule_unsupported_n": graph.ferry_schedule_unsupported_n,
            "ferry_public_holiday_schedule_n": graph.ferry_public_holiday_schedule_n,
            "public_holiday_n": graph.public_holiday_n,
            "public_holiday_contract": graph.public_holiday_contract_status,
            "public_holiday_min_date": graph.public_holiday_min_date,
            "public_holiday_max_date": graph.public_holiday_max_date,
            "ferry_duration_n": graph.ferry_duration_n,
            "ferry_schedule_contract": graph.ferry_schedule_contract_status,
            "turn_restriction_n": table_count("turn_restrictions"),
            "conditional_restriction_n": table_count("conditional_turn_restrictions"),
            "conditional_weight_restriction_n": graph.conditional_weight_restriction_n,
            "conditional_access_n": graph.conditional_access_n,
            "conditional_access_vehicle_class_n": dict(graph.conditional_access_vehicle_class_n),
            "conditional_access_direction_n": dict(graph.conditional_access_direction_n),
            "conditional_access_weight_n": graph.conditional_access_weight_n,
            "conditional_access_multiclause_n": graph.conditional_access_multiclause_n,
            "conditional_access_unsupported_n": graph.conditional_access_unsupported_n,
            "conditional_access_excluded_n": graph.conditional_access_excluded_n,
            "maxweight_profiles": graph.has_maxweight_profiles,
            "maxweight_way_n": graph.maxweight_way_n,
            "maxweight_supported_way_n": graph.maxweight_supported_way_n,
            "maxweight_unlimited_way_n": graph.maxweight_unlimited_way_n,
            "maxweight_unsupported_way_n": graph.maxweight_unsupported_way_n,
            "maxweight_segment_n": graph.maxweight_segment_n,
            "maxweight_hgv_profiles": graph.has_hgv_maxweight_profiles,
            "maxweight_hgv_way_n": graph.maxweight_hgv_way_n,
            "maxweight_hgv_supported_way_n": graph.maxweight_hgv_supported_way_n,
            "maxweight_hgv_unlimited_way_n": graph.maxweight_hgv_unlimited_way_n,
            "maxweight_hgv_unsupported_way_n": graph.maxweight_hgv_unsupported_way_n,
            "maxweight_hgv_segment_n": graph.maxweight_hgv_segment_n,
            "maxweightrating_hgv_profiles": graph.has_hgv_maxweightrating_profiles,
            "maxweightrating_hgv_way_n": graph.maxweightrating_hgv_way_n,
            "maxweightrating_hgv_supported_way_n": graph.maxweightrating_hgv_supported_way_n,
            "maxweightrating_hgv_unlimited_way_n": graph.maxweightrating_hgv_unlimited_way_n,
            "maxweightrating_hgv_unsupported_way_n": graph.maxweightrating_hgv_unsupported_way_n,
            "maxweightrating_hgv_segment_n": graph.maxweightrating_hgv_segment_n,
            "hgv_destination_profiles": graph.has_hgv_destination_profiles,
            "hgv_destination_way_n": graph.hgv_destination_way_n,
            "hgv_destination_supported_way_n": graph.hgv_destination_supported_way_n,
            "hgv_destination_unsupported_way_n": graph.hgv_destination_unsupported_way_n,
            "hgv_destination_segment_n": graph.hgv_destination_segment_n,
            "maxheight_profiles": graph.has_maxheight_profiles,
            "maxheight_way_n": graph.maxheight_way_n,
            "maxheight_supported_way_n": graph.maxheight_supported_way_n,
            "maxheight_unlimited_way_n": graph.maxheight_unlimited_way_n,
            "maxheight_unsupported_way_n": graph.maxheight_unsupported_way_n,
            "maxheight_segment_n": graph.maxheight_segment_n,
            "maxheight_physical_way_n": graph.maxheight_physical_way_n,
            "maxheight_physical_supported_way_n": graph.maxheight_physical_supported_way_n,
            "maxheight_physical_unlimited_way_n": graph.maxheight_physical_unlimited_way_n,
            "maxheight_physical_unsupported_way_n": graph.maxheight_physical_unsupported_way_n,
            "maxheight_physical_segment_n": graph.maxheight_physical_segment_n,
            "vehicle_dimension_profiles": graph.has_vehicle_dimension_profiles,
            "maxwidth_profiles": graph.has_vehicle_dimension_profiles,
            "maxwidth_way_n": graph.maxwidth_way_n,
            "maxwidth_supported_way_n": graph.maxwidth_supported_way_n,
            "maxwidth_unlimited_way_n": graph.maxwidth_unlimited_way_n,
            "maxwidth_unsupported_way_n": graph.maxwidth_unsupported_way_n,
            "maxwidth_segment_n": graph.maxwidth_segment_n,
            "maxlength_profiles": graph.has_vehicle_dimension_profiles,
            "maxlength_way_n": graph.maxlength_way_n,
            "maxlength_supported_way_n": graph.maxlength_supported_way_n,
            "maxlength_unlimited_way_n": graph.maxlength_unlimited_way_n,
            "maxlength_unsupported_way_n": graph.maxlength_unsupported_way_n,
            "maxlength_segment_n": graph.maxlength_segment_n,
            "maxaxleload_profiles": graph.has_vehicle_dimension_profiles,
            "maxaxleload_way_n": graph.maxaxleload_way_n,
            "maxaxleload_supported_way_n": graph.maxaxleload_supported_way_n,
            "maxaxleload_unlimited_way_n": graph.maxaxleload_unlimited_way_n,
            "maxaxleload_unsupported_way_n": graph.maxaxleload_unsupported_way_n,
            "maxaxleload_segment_n": graph.maxaxleload_segment_n,
            "maxspeed_profiles": graph.has_maxspeed_profiles,
            "maxspeed_way_n": graph.maxspeed_way_n,
            "maxspeed_supported_way_n": graph.maxspeed_supported_way_n,
            "maxspeed_unlimited_way_n": graph.maxspeed_unlimited_way_n,
            "maxspeed_unsupported_way_n": graph.maxspeed_unsupported_way_n,
            "maxspeed_segment_n": graph.maxspeed_segment_n,
            "maxspeed_conditional_way_n": graph.maxspeed_conditional_way_n,
            "maxspeed_conditional_profiles": graph.has_maxspeed_conditional_profiles,
            "maxspeed_conditional_supported_way_n": graph.maxspeed_conditional_supported_way_n,
            "maxspeed_conditional_unsupported_way_n": graph.maxspeed_conditional_unsupported_way_n,
            "maxspeed_conditional_segment_n": graph.maxspeed_conditional_segment_n,
            "oneway_conditional_profiles": graph.has_oneway_conditional_profiles,
            "oneway_conditional_way_n": graph.oneway_conditional_way_n,
            "oneway_conditional_supported_way_n": graph.oneway_conditional_supported_way_n,
            "oneway_conditional_unsupported_way_n": graph.oneway_conditional_unsupported_way_n,
            "oneway_conditional_segment_n": graph.oneway_conditional_segment_n,
        }
    return {
        "backend": "portable",
        "node_n": graph.node_count,
        "edge_n": graph.edge_count,
        "way_context": graph.has_way_context,
        "way_context_n": graph.way_context_n,
        "path_segment_source": "portable_edges" if graph.has_way_ids else "not_available",
        "ferry_edge_n": graph.ferry_edge_count,
        "ferry_public_holiday_schedule_n": 0,
        "public_holiday_n": 0,
        "public_holiday_contract": "not_provided",
        "public_holiday_min_date": None,
        "public_holiday_max_date": None,
        "turn_restriction_n": 0,
        "conditional_restriction_n": 0,
        "conditional_weight_restriction_n": 0,
        "conditional_access_n": 0,
        "conditional_access_vehicle_class_n": {vehicle_class: 0 for vehicle_class in VEHICLE_CLASSES},
        "conditional_access_direction_n": {direction: 0 for direction in CONDITIONAL_ACCESS_DIRECTIONS},
        "conditional_access_weight_n": 0,
        "conditional_access_multiclause_n": 0,
        "conditional_access_unsupported_n": 0,
        "conditional_access_excluded_n": 0,
        "maxweight_profiles": False,
        "maxweight_way_n": 0,
        "maxweight_supported_way_n": 0,
        "maxweight_unlimited_way_n": 0,
        "maxweight_unsupported_way_n": 0,
        "maxweight_segment_n": 0,
        "maxweight_hgv_profiles": False,
        "maxweight_hgv_way_n": 0,
        "maxweight_hgv_supported_way_n": 0,
        "maxweight_hgv_unlimited_way_n": 0,
        "maxweight_hgv_unsupported_way_n": 0,
        "maxweight_hgv_segment_n": 0,
        "maxweightrating_hgv_profiles": False,
        "maxweightrating_hgv_way_n": 0,
        "maxweightrating_hgv_supported_way_n": 0,
        "maxweightrating_hgv_unlimited_way_n": 0,
        "maxweightrating_hgv_unsupported_way_n": 0,
        "maxweightrating_hgv_segment_n": 0,
        "hgv_destination_profiles": False,
        "hgv_destination_way_n": 0,
        "hgv_destination_supported_way_n": 0,
        "hgv_destination_unsupported_way_n": 0,
        "hgv_destination_segment_n": 0,
        "maxheight_profiles": False,
        "maxheight_way_n": 0,
        "maxheight_supported_way_n": 0,
        "maxheight_unlimited_way_n": 0,
        "maxheight_unsupported_way_n": 0,
        "maxheight_segment_n": 0,
        "maxheight_physical_way_n": 0,
        "maxheight_physical_supported_way_n": 0,
        "maxheight_physical_unlimited_way_n": 0,
        "maxheight_physical_unsupported_way_n": 0,
        "maxheight_physical_segment_n": 0,
        "vehicle_dimension_profiles": False,
        "maxwidth_profiles": False,
        "maxwidth_way_n": 0,
        "maxwidth_supported_way_n": 0,
        "maxwidth_unlimited_way_n": 0,
        "maxwidth_unsupported_way_n": 0,
        "maxwidth_segment_n": 0,
        "maxlength_profiles": False,
        "maxlength_way_n": 0,
        "maxlength_supported_way_n": 0,
        "maxlength_unlimited_way_n": 0,
        "maxlength_unsupported_way_n": 0,
        "maxlength_segment_n": 0,
        "maxaxleload_profiles": False,
        "maxaxleload_way_n": 0,
        "maxaxleload_supported_way_n": 0,
        "maxaxleload_unlimited_way_n": 0,
        "maxaxleload_unsupported_way_n": 0,
        "maxaxleload_segment_n": 0,
        "maxspeed_profiles": False,
        "maxspeed_way_n": 0,
        "maxspeed_supported_way_n": 0,
        "maxspeed_unlimited_way_n": 0,
        "maxspeed_unsupported_way_n": 0,
        "maxspeed_segment_n": 0,
        "maxspeed_conditional_way_n": 0,
        "maxspeed_conditional_profiles": False,
        "maxspeed_conditional_supported_way_n": 0,
        "maxspeed_conditional_unsupported_way_n": 0,
        "maxspeed_conditional_segment_n": 0,
        "oneway_conditional_profiles": False,
        "oneway_conditional_way_n": 0,
        "oneway_conditional_supported_way_n": 0,
        "oneway_conditional_unsupported_way_n": 0,
        "oneway_conditional_segment_n": 0,
    }


def _method(
    graph: Graph,
    departure: datetime | None,
    speed_kmh: float,
    include_ferries: bool,
    objective: str,
    vehicle_weight_t: float | None,
    vehicle_rating_t: float | None,
    vehicle_height_m: float | None,
    vehicle_width_m: float | None,
    vehicle_length_m: float | None,
    vehicle_axleload_t: float | None,
    vehicle_class: str,
    allow_hgv_destination: bool,
) -> str:
    if not isinstance(graph, SQLiteRoadGraph):
        method = f"Dijkstra on portable road graph; nearest graph node snap; route objective={objective}"
        if graph.has_way_ids:
            method += "; directed way IDs and road context retained"
        else:
            method += "; directed way metadata unavailable"
        if graph.ferry_edge_count:
            if include_ferries:
                method += (
                    "; ferry geometry included; ferry schedules, waits, and "
                    "crossing durations unavailable in portable graph"
                )
            else:
                method += "; ferry geometry available but excluded"
        elif include_ferries:
            method += "; ferry geometry unavailable in portable graph"
        if vehicle_class != "general":
            method += "; vehicle-class profile unavailable in portable graph"
        if vehicle_rating_t is not None:
            method += "; vehicle-rating profile unavailable in portable graph"
        if allow_hgv_destination:
            method += "; HGV destination-delivery profile unavailable in portable graph"
        if vehicle_height_m is not None:
            method += "; vehicle-height profile unavailable in portable graph"
        if vehicle_width_m is not None:
            method += "; vehicle-width profile unavailable in portable graph"
        if vehicle_length_m is not None:
            method += "; vehicle-length profile unavailable in portable graph"
        if vehicle_axleload_t is not None:
            method += "; vehicle-axle-load profile unavailable in portable graph"
        return method
    method = f"Dijkstra on SQLite road graph; indexed nearest graph node snap; route objective={objective}"
    if vehicle_class != "general":
        method += f"; vehicle-class profile={vehicle_class}"
    if graph.has_turn_restrictions:
        method += "; OSM via-node and validated via-way turn restrictions"
    if graph.conditional_restriction_n:
        if departure is None:
            method += "; conditional windows retained but inactive"
        else:
            method += f"; conditional windows evaluated from {departure.isoformat()} at {speed_kmh:g} km/h"
    if graph.conditional_access_n:
        if departure is None:
            method += "; conditional road access windows retained but inactive"
        else:
            method += f"; conditional road access windows evaluated from {departure.isoformat()} at {speed_kmh:g} km/h"
        directional_n = sum(
            graph.conditional_access_direction_n.get(direction, 0)
            for direction in ("forward", "backward")
        )
        if directional_n:
            method += "; directional conditional road access applied by traversal direction"
        if graph.conditional_access_multiclause_n:
            method += (
                f"; {graph.conditional_access_multiclause_n} multi-clause "
                "conditional access rule sets evaluated"
            )
    if graph.conditional_access_weight_n:
        method += (
            f"; vehicle-weight conditional road access evaluated at {vehicle_weight_t:g} t"
            if vehicle_weight_t is not None
            else "; vehicle-weight conditional road access retained but inactive"
        )
    if graph.conditional_access_unsupported_n:
        if graph.conditional_access_excluded_n:
            method += (
                f"; {graph.conditional_access_unsupported_n} unsupported conditional "
                f"road-access tags excluded ({graph.conditional_access_excluded_n} ways)"
            )
        else:
            method += (
                f"; {graph.conditional_access_unsupported_n} unsupported conditional "
                "road-access tags are not evaluated"
            )
    if isinstance(graph, SQLiteRoadGraph) and graph.conditional_weight_restriction_n:
        method += (
            f"; vehicle-weight conditional restrictions evaluated at {vehicle_weight_t:g} t"
            if vehicle_weight_t is not None
            else "; vehicle-weight conditional restrictions retained but inactive"
        )
    if graph.maxweight_way_n:
        if vehicle_weight_t is None:
            method += "; generic maxweight limits retained but inactive"
        else:
            method += f"; generic maxweight limits enforced at {vehicle_weight_t:g} t"
            if graph.maxweight_unsupported_way_n:
                method += (
                    f"; {graph.maxweight_unsupported_way_n} ambiguous generic "
                    "maxweight ways treated as impassable for weighted routing"
                )
    if graph.maxweight_hgv_way_n:
        if vehicle_class != "hgv":
            method += "; HGV-specific maxweight limits retained for hgv profiles"
        elif vehicle_weight_t is None:
            method += "; HGV-specific maxweight limits retained but inactive"
        else:
            method += f"; HGV-specific maxweight limits enforced at {vehicle_weight_t:g} t"
            if graph.maxweight_hgv_unsupported_way_n:
                method += (
                    f"; {graph.maxweight_hgv_unsupported_way_n} ambiguous HGV "
                    "maxweight ways treated as impassable for weighted routing"
                )
    if graph.maxweightrating_hgv_way_n:
        if vehicle_class != "hgv":
            method += "; HGV maxweightrating limits retained for hgv profiles (including maxweightrating:goods aliases)"
        elif vehicle_rating_t is None:
            method += "; HGV maxweightrating limits retained but inactive (including maxweightrating:goods aliases)"
        elif not graph.has_hgv_maxweightrating_profiles:
            method += "; HGV maxweightrating profile unavailable in this graph (including maxweightrating:goods aliases)"
        else:
            method += (
                f"; HGV maxweightrating limits enforced at {vehicle_rating_t:g} t (including maxweightrating:goods aliases)"
            )
            if graph.maxweightrating_hgv_unsupported_way_n:
                method += (
                    f"; {graph.maxweightrating_hgv_unsupported_way_n} ambiguous HGV "
                    "maxweightrating ways treated as impassable for rating-qualified routing"
                )
    if graph.hgv_destination_way_n:
        if vehicle_class != "hgv":
            method += "; HGV destination-only rules retained for hgv profiles"
        elif allow_hgv_destination:
            method += "; HGV destination-only access and supported limit exceptions explicitly enabled"
        else:
            method += "; HGV destination-only access enforced by default"
        if graph.hgv_destination_unsupported_way_n:
            method += (
                f"; {graph.hgv_destination_unsupported_way_n} HGV destination clauses "
                "retain conservative base-limit semantics"
            )
    if graph.maxheight_way_n or graph.maxheight_physical_way_n:
        if vehicle_height_m is None:
            method += "; maxheight limits retained but inactive"
        else:
            method += f"; maxheight limits enforced at {vehicle_height_m:g} m"
            unsupported_n = (
                graph.maxheight_unsupported_way_n
                + graph.maxheight_physical_unsupported_way_n
            )
            if unsupported_n:
                method += (
                    f"; {unsupported_n} ambiguous maxheight ways treated as impassable "
                    "for height-qualified routing"
                )
    dimension_profiles = (
        (
            "maxwidth",
            "vehicle_width_m",
            "width",
            "m",
            vehicle_width_m,
            graph.maxwidth_way_n,
            graph.maxwidth_unsupported_way_n,
        ),
        (
            "maxlength",
            "vehicle_length_m",
            "length",
            "m",
            vehicle_length_m,
            graph.maxlength_way_n,
            graph.maxlength_unsupported_way_n,
        ),
        (
            "maxaxleload",
            "vehicle_axleload_t",
            "axle-load",
            "t",
            vehicle_axleload_t,
            graph.maxaxleload_way_n,
            graph.maxaxleload_unsupported_way_n,
        ),
    )
    for tag, _field, label, unit, profile, way_n, unsupported_n in dimension_profiles:
        if profile is None:
            if way_n:
                method += f"; {tag} limits retained but inactive"
        elif not graph.has_vehicle_dimension_profiles:
            method += f"; vehicle-{label} profile unavailable in this graph"
        else:
            method += f"; {tag} limits enforced at {profile:g} {unit}"
            if unsupported_n:
                method += (
                    f"; {unsupported_n} ambiguous {tag} ways treated as impassable "
                    f"for {label}-qualified routing"
                )
    if graph.maxspeed_way_n:
        if graph.has_maxspeed_profiles:
            method += "; numeric OSM maxspeed ceilings applied to duration estimates"
        else:
            method += "; numeric OSM maxspeed ceilings unavailable in this graph"
        if graph.maxspeed_unsupported_way_n:
            method += (
                f"; {graph.maxspeed_unsupported_way_n} non-numeric maxspeed ways "
                "retained without a speed ceiling"
            )
    if graph.maxspeed_conditional_way_n:
        if graph.has_maxspeed_conditional_profiles:
            if departure is not None:
                method += (
                    "; supported maxspeed:conditional windows evaluated from "
                    f"{departure.isoformat()}"
                )
            else:
                method += "; maxspeed:conditional windows retained but not evaluated without departure"
            if graph.maxspeed_conditional_unsupported_way_n:
                method += (
                    f"; {graph.maxspeed_conditional_unsupported_way_n} "
                    "maxspeed:conditional ways have unsupported syntax"
                )
        else:
            method += (
                f"; {graph.maxspeed_conditional_way_n} maxspeed:conditional ways "
                "retained but not evaluated in this graph"
            )
    if graph.oneway_conditional_way_n:
        if graph.has_oneway_conditional_profiles:
            if departure is not None:
                method += (
                    "; supported conditional one-way windows evaluated from "
                    f"{departure.isoformat()}"
                )
            else:
                method += "; conditional one-way windows retained but not evaluated without departure"
            if graph.oneway_conditional_unsupported_way_n:
                method += (
                    f"; {graph.oneway_conditional_unsupported_way_n} "
                    "conditional one-way ways have unsupported syntax"
                )
        else:
            method += (
                f"; {graph.oneway_conditional_way_n} conditional one-way ways "
                "retained but not evaluated in this graph"
            )
    if graph.ferry_edge_count:
        if include_ferries:
            method += "; ferry geometry included"
            if graph.ferry_schedule_n:
                method += (
                    "; ferry service windows and waiting evaluated from "
                    f"{departure.isoformat()}"
                    if departure
                    else "; ferry service windows retained but not evaluated"
                )
            elif graph.ferry_schedule_contract_status == "available":
                method += "; ferry service windows unavailable in contract"
            else:
                method += "; ferry service schedule not provided"
            if graph.ferry_duration_n:
                method += "; ferry crossing durations applied"
            if graph.ferry_public_holiday_schedule_n:
                if departure is None:
                    method += "; public-holiday calendar retained but not evaluated"
                elif graph.public_holiday_contract_status == "available":
                    method += "; public-holiday calendar applied"
                    if graph.public_holiday_min_date and graph.public_holiday_max_date:
                        method += (
                            f" ({graph.public_holiday_min_date}.."
                            f"{graph.public_holiday_max_date})"
                        )
                else:
                    method += "; public-holiday calendar not provided"
        else:
            method += "; ferry geometry available but excluded"
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


_EDGE_CONSTRAINT_STATUSES = frozenset({"supported", "unlimited", "unsupported"})
_EDGE_NUMERIC_CONSTRAINT_SPECS = (
    ("maxweight", "maxweight_t", "maxweight_status", "t"),
    ("maxweight:hgv", "maxweight_hgv_t", "maxweight_hgv_status", "t"),
    (
        "maxweightrating:hgv",
        "maxweightrating_hgv_t",
        "maxweightrating_hgv_status",
        "t",
    ),
    ("maxheight", "maxheight_m", "maxheight_status", "m"),
    (
        "maxheight:physical",
        "maxheight_physical_m",
        "maxheight_physical_status",
        "m",
    ),
    ("maxwidth", "maxwidth_m", "maxwidth_status", "m"),
    ("maxlength", "maxlength_m", "maxlength_status", "m"),
    ("maxaxleload", "maxaxleload_t", "maxaxleload_status", "t"),
    ("maxspeed", "maxspeed_kmh", "maxspeed_status", "km/h"),
)


def _constraint_profile_state(
    key: str,
    *,
    vehicle_weight_t: float | None,
    vehicle_rating_t: float | None,
    vehicle_height_m: float | None,
    vehicle_width_m: float | None,
    vehicle_length_m: float | None,
    vehicle_axleload_t: float | None,
    vehicle_class: str,
    allow_hgv_destination: bool,
) -> tuple[str, bool]:
    """Return the profile label and availability for one edge constraint."""
    if key == "maxweight":
        return "vehicle_weight_t", vehicle_weight_t is not None
    if key == "maxweight:hgv":
        return (
            "vehicle_class=hgv + vehicle_weight_t",
            vehicle_class == "hgv" and vehicle_weight_t is not None,
        )
    if key == "maxweightrating:hgv":
        return (
            "vehicle_class=hgv + vehicle_rating_t",
            vehicle_class == "hgv" and vehicle_rating_t is not None,
        )
    if key in {"maxheight", "maxheight:physical"}:
        return "vehicle_height_m", vehicle_height_m is not None
    if key == "maxwidth":
        return "vehicle_width_m", vehicle_width_m is not None
    if key == "maxlength":
        return "vehicle_length_m", vehicle_length_m is not None
    if key == "maxaxleload":
        return "vehicle_axleload_t", vehicle_axleload_t is not None
    if key == "maxspeed":
        return "speed_kmh", True
    if key == "hgv":
        return (
            "vehicle_class=hgv + allow_hgv_destination",
            vehicle_class == "hgv",
        )
    if key == "maxweight:hgv:conditional":
        return (
            "vehicle_class=hgv + vehicle_weight_t + allow_hgv_destination",
            vehicle_class == "hgv"
            and vehicle_weight_t is not None
            and allow_hgv_destination,
        )
    if key == "maxweightrating:hgv:conditional":
        return (
            "vehicle_class=hgv + vehicle_rating_t + allow_hgv_destination",
            vehicle_class == "hgv"
            and vehicle_rating_t is not None
            and allow_hgv_destination,
        )
    return "vehicle_class=hgv", vehicle_class == "hgv"


def _constraint_value(value: object) -> float | None:
    """Normalize a persisted numeric constraint value for JSON output."""
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return round(normalized, 6) if math.isfinite(normalized) else None


def _edge_constraint_record(
    key: str,
    value: object,
    unit: str | None,
    status: object,
    *,
    profile: str,
    profile_available: bool,
) -> dict[str, object]:
    normalized_status = str(status or "not_provided")
    return {
        "key": key,
        "value": value,
        "unit": unit,
        "status": normalized_status,
        "evaluated": bool(
            profile_available and normalized_status in _EDGE_CONSTRAINT_STATUSES - {"unsupported"}
        ),
        "profile": profile,
    }


def _edge_constraint_records(
    row: dict[str, object],
    columns: set[str],
    *,
    vehicle_weight_t: float | None,
    vehicle_rating_t: float | None,
    vehicle_height_m: float | None,
    vehicle_width_m: float | None,
    vehicle_length_m: float | None,
    vehicle_axleload_t: float | None,
    vehicle_class: str,
    allow_hgv_destination: bool,
) -> list[dict[str, object]]:
    """Describe normalized static OSM constraints persisted on one edge."""
    records: list[dict[str, object]] = []
    for key, value_column, status_column, unit in _EDGE_NUMERIC_CONSTRAINT_SPECS:
        if value_column not in columns or status_column not in columns:
            continue
        status = str(row.get(status_column) or "not_provided")
        if status == "not_provided":
            continue
        profile, profile_available = _constraint_profile_state(
            key,
            vehicle_weight_t=vehicle_weight_t,
            vehicle_rating_t=vehicle_rating_t,
            vehicle_height_m=vehicle_height_m,
            vehicle_width_m=vehicle_width_m,
            vehicle_length_m=vehicle_length_m,
            vehicle_axleload_t=vehicle_axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=allow_hgv_destination,
        )
        records.append(
            _edge_constraint_record(
                key,
                _constraint_value(row.get(value_column)),
                unit,
                status,
                profile=profile,
                profile_available=profile_available,
            )
        )

    if "hgv_destination_json" in columns:
        try:
            entries = json.loads(str(row.get("hgv_destination_json") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            entries = []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                source = str(entry.get("source") or "hgv").strip() or "hgv"
                status = str(entry.get("status") or "unsupported")
                if status == "not_provided":
                    continue
                key = "hgv" if source == "hgv" else source
                profile, profile_available = _constraint_profile_state(
                    key,
                    vehicle_weight_t=vehicle_weight_t,
                    vehicle_rating_t=vehicle_rating_t,
                    vehicle_height_m=vehicle_height_m,
                    vehicle_width_m=vehicle_width_m,
                    vehicle_length_m=vehicle_length_m,
                    vehicle_axleload_t=vehicle_axleload_t,
                    vehicle_class=vehicle_class,
                    allow_hgv_destination=allow_hgv_destination,
                )
                raw_value = str(
                    entry.get("raw") or entry.get("condition") or "destination"
                ).strip()
                records.append(
                    _edge_constraint_record(
                        key,
                        raw_value or None,
                        None,
                        status,
                        profile=profile,
                        profile_available=profile_available,
                    )
                )
    return records


def _conditional_rule_context(
    graph: SQLiteRoadGraph,
    condition: object,
    elapsed_time_s: float,
) -> tuple[str, bool, bool | None]:
    """Return profile label, evaluation status, and active state."""
    condition_text = str(condition or "").strip()
    schedule = parse_weekly_schedule(condition_text, allow_calendar=True)
    if schedule is not None:
        if schedule.always_active:
            return "24/7", True, True
        if graph.departure is None:
            return "departure", False, None
        moment = graph.departure + timedelta(seconds=max(0.0, elapsed_time_s))
        active = schedule.active_at(moment, graph.public_holidays)
        profile = (
            "departure + public_holiday"
            if schedule.requires_public_holiday_calendar
            else "departure"
        )
        evaluated = not (
            schedule.requires_public_holiday_calendar
            and graph.public_holidays is None
        )
        return profile, evaluated, active
    weight_condition = parse_vehicle_weight_condition(condition_text)
    if weight_condition is not None:
        if graph.vehicle_weight_t is None:
            return "vehicle_weight_t", False, None
        return (
            "vehicle_weight_t",
            True,
            vehicle_weight_condition_active(
                graph.vehicle_weight_t,
                weight_condition,
            ),
        )
    return "unsupported", False, None


def _conditional_rule_record(
    key: str,
    value: object,
    unit: str | None,
    condition: object,
    mode: object,
    direction: object,
    vehicle_class: object,
    status: object,
    *,
    profile: str,
    evaluated: bool,
    active: bool | None,
    applied: bool,
) -> dict[str, object]:
    return {
        "key": key,
        "value": value,
        "unit": unit,
        "condition": str(condition or ""),
        "mode": str(mode or ""),
        "direction": str(direction or "both"),
        "vehicle_class": str(vehicle_class or "general"),
        "status": str(status or "unsupported"),
        "evaluated": bool(evaluated),
        "active": active,
        "applied": bool(applied),
        "profile": profile,
    }


def _json_array(row: dict[str, object], column: str) -> list[dict[str, object]]:
    """Decode one persisted edge rule array without failing the route query."""
    try:
        entries = json.loads(str(row.get(column) or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _conditional_edge_rule_records(
    graph: SQLiteRoadGraph,
    row: dict[str, object],
    columns: set[str],
    way_id: str,
    direction: str,
    elapsed_time_s: float,
) -> list[dict[str, object]]:
    """Describe conditional speed and one-way decisions for one edge."""
    records: list[dict[str, object]] = []
    if "maxspeed_conditional_json" in columns:
        for entry in _json_array(row, "maxspeed_conditional_json"):
            status = str(entry.get("status") or "unsupported")
            condition = str(entry.get("condition") or "")
            if status in {"supported", "unlimited"}:
                profile, evaluated, active = _conditional_rule_context(
                    graph,
                    condition,
                    elapsed_time_s,
                )
            else:
                profile, evaluated, active = "unsupported", False, None
            records.append(
                _conditional_rule_record(
                    "maxspeed:conditional",
                    _constraint_value(entry.get("speed_kmh")),
                    "km/h",
                    condition,
                    "speed",
                    "both",
                    "general",
                    status,
                    profile=profile,
                    evaluated=evaluated,
                    active=active,
                    applied=bool(evaluated and active),
                )
            )
    if "oneway_conditional_json" in columns:
        entries = _json_array(row, "oneway_conditional_json")
        unsupported_way = way_id in graph.oneway_conditional_unsupported_by_way
        for entry in entries:
            status = str(entry.get("status") or "unsupported")
            condition = str(entry.get("condition") or "")
            if status == "supported":
                profile, evaluated, active = _conditional_rule_context(
                    graph,
                    condition,
                    elapsed_time_s,
                )
            else:
                profile, evaluated, active = "unsupported", False, None
            records.append(
                _conditional_rule_record(
                    "oneway:conditional",
                    str(entry.get("mode") or ""),
                    None,
                    condition,
                    entry.get("mode"),
                    direction,
                    "general",
                    status,
                    profile=profile,
                    evaluated=evaluated,
                    active=active,
                    applied=bool(evaluated and active and not unsupported_way),
                )
            )
    return records


def _conditional_access_rows(
    graph: SQLiteRoadGraph,
    way_id: str,
    direction: str,
    columns: set[str],
    cache: dict[tuple[str, str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Read raw conditional-access rows for one way and traversal direction."""
    cache_key = (way_id, direction)
    if cache_key in cache:
        return cache[cache_key]
    if not {"way_id", "condition"}.issubset(columns):
        cache[cache_key] = []
        return []
    selected = ["way_id", "condition"]
    for column in ("mode", "vehicle_class", "direction", "rule_n"):
        if column in columns:
            selected.append(column)
    query = f"SELECT {', '.join(selected)} FROM conditional_access WHERE way_id = ?"
    parameters: tuple[object, ...] = (way_id,)
    if "direction" in columns:
        query += " AND direction IN (?, 'both')"
        parameters = (way_id, direction)
    order = "direction, rule_n" if {"direction", "rule_n"}.issubset(columns) else "rowid"
    query += f" ORDER BY {order}"
    rows: list[dict[str, object]] = []
    for values in graph.connection.execute(query, parameters):
        rows.append(dict(zip(selected, values, strict=False)))
    cache[cache_key] = rows
    return rows


def _conditional_access_rule_records(
    graph: SQLiteRoadGraph,
    way_id: str,
    direction: str,
    columns: set[str],
    elapsed_time_s: float,
    cache: dict[tuple[str, str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Describe conditional-access rules considered for one edge traversal."""
    rows = _conditional_access_rows(graph, way_id, direction, columns, cache)
    if not rows:
        return []
    selected_direction = (
        direction
        if graph.conditional_access_by_way.get((way_id, direction)) is not None
        else "both"
    )
    records: list[dict[str, object]] = []
    for row in rows:
        mode = str(row.get("mode") or "allow").lower()
        vehicle_class = str(row.get("vehicle_class") or "general").lower()
        rule_direction = str(row.get("direction") or "both").lower()
        condition = str(row.get("condition") or "")
        valid_identity = (
            mode in {"allow", "deny"}
            and vehicle_class in VEHICLE_CLASSES
            and rule_direction in CONDITIONAL_ACCESS_DIRECTIONS
        )
        if valid_identity:
            profile, context_evaluated, active = _conditional_rule_context(
                graph,
                condition,
                elapsed_time_s,
            )
            if profile == "unsupported":
                status = "unsupported"
                context_evaluated = False
                active = None
            else:
                status = "supported"
        else:
            profile, context_evaluated, active = "unsupported", False, None
            status = "unsupported"
        applicable = vehicle_class in {"general", graph.vehicle_class}
        selected = rule_direction == selected_direction
        evaluated = bool(context_evaluated and applicable and selected)
        records.append(
            _conditional_rule_record(
                "conditional_access",
                mode,
                None,
                condition,
                mode,
                rule_direction,
                vehicle_class,
                status,
                profile=profile,
                evaluated=evaluated,
                active=active,
                applied=bool(evaluated and active),
            )
        )
    return records


def _transition_table_rows(
    graph: SQLiteRoadGraph,
    table: str,
    columns: set[str],
    via_node: str,
    cache: dict[tuple[str, str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Read persisted turn-relation rows for one via node."""
    cache_key = (table, via_node)
    if cache_key in cache:
        return cache[cache_key]
    required = {"relation_id", "via_node", "from_way", "to_way", "kind"}
    if not required.issubset(columns):
        cache[cache_key] = []
        return []
    selected = ["relation_id", "via_node", "from_way", "to_way", "kind"]
    for column in ("via_way_json", "condition"):
        if column in columns:
            selected.append(column)
    query = (
        f"SELECT {', '.join(selected)} FROM {table} "
        "WHERE via_node = ? "
        "ORDER BY relation_id, to_way, kind"
    )
    try:
        rows = [
            dict(zip(selected, values, strict=False))
            for values in graph.connection.execute(query, (via_node,))
        ]
    except sqlite3.Error:
        rows = []
    cache[cache_key] = rows
    return rows


def _transition_via_way_ids(row: dict[str, object]) -> tuple[str, ...]:
    """Decode a persisted via-way list using the graph loader's fallback."""
    try:
        values = json.loads(str(row.get("via_way_json") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(str(value) for value in values) if isinstance(values, list) else ()


def _turn_restriction_context(
    graph: SQLiteRoadGraph,
    condition: object,
    elapsed_time_s: float,
) -> tuple[str, bool, bool | None, str]:
    """Return the same profile/evaluation state used by conditional turn routing."""
    condition_text = str(condition or "").strip()
    schedule = parse_weekly_schedule(condition_text)
    if schedule is not None:
        # The turn evaluator only enters its conditional schedule branch when
        # an explicit departure is supplied, even for a 24/7 schedule.
        if graph.departure is None:
            return "departure", False, None, "supported"
        moment = graph.departure + timedelta(seconds=max(0.0, elapsed_time_s))
        return "departure", True, schedule.active_at(moment, graph.public_holidays), "supported"
    weight_condition = parse_vehicle_weight_condition(condition_text)
    if weight_condition is not None:
        if graph.vehicle_weight_t is None:
            return "vehicle_weight_t", False, None, "supported"
        return (
            "vehicle_weight_t",
            True,
            vehicle_weight_condition_active(graph.vehicle_weight_t, weight_condition),
            "supported",
        )
    return "unsupported", False, None, "unsupported"


def _transition_rule_records(
    graph: SQLiteRoadGraph,
    previous_way_ids: list[str],
    via_node: str,
    outgoing_way: str,
    elapsed_time_s: float,
    table_columns: dict[str, set[str]],
    cache: dict[tuple[str, str], list[dict[str, object]]],
    tables_with_rows: set[str],
) -> list[dict[str, object]]:
    """Describe turn restrictions matched by one chosen path transition."""
    if not previous_way_ids or not tables_with_rows:
        return []
    records: list[dict[str, object]] = []
    for table, key in (
        ("turn_restrictions", "turn_restriction"),
        ("conditional_turn_restrictions", "conditional_turn_restriction"),
    ):
        if table not in tables_with_rows:
            continue
        columns = table_columns.get(table, set())
        for row in _transition_table_rows(
            graph,
            table,
            columns,
            via_node,
            cache,
        ):
            from_way = str(row.get("from_way") or "")
            via_way_ids = _transition_via_way_ids(row)
            prefix = (from_way, *via_way_ids)
            if len(prefix) > len(previous_way_ids):
                continue
            if tuple(previous_way_ids[-len(prefix):]) != prefix:
                continue
            kind = str(row.get("kind") or "").strip().lower()
            condition = "" if key == "turn_restriction" else str(row.get("condition") or "")
            if key == "turn_restriction":
                profile, evaluated, active, status = (
                    "unconditional",
                    True,
                    True,
                    "supported" if kind in {"no", "only"} else "unsupported",
                )
            else:
                profile, evaluated, active, status = _turn_restriction_context(
                    graph,
                    condition,
                    elapsed_time_s,
                )
                if kind not in {"no", "only"}:
                    status = "unsupported"
                    evaluated = False
                    active = None
            selected = str(row.get("to_way") or "") == outgoing_way
            records.append(
                {
                    "key": key,
                    "relation_id": str(row.get("relation_id") or ""),
                    "via_node": str(row.get("via_node") or via_node),
                    "from_way": str(row.get("from_way") or from_way),
                    "to_way": str(row.get("to_way") or ""),
                    "via_way_ids": list(via_way_ids),
                    "kind": kind,
                    "condition": condition,
                    "status": status,
                    "evaluated": bool(evaluated),
                    "active": active,
                    "applied": bool(evaluated and active),
                    "selected": selected,
                    "profile": profile,
                }
            )
    records.sort(
        key=lambda record: (
            str(record["key"]),
            str(record["relation_id"]),
            str(record["kind"]),
            str(record["to_way"]),
        )
    )
    return records


def _empty_road_context() -> dict[str, str | None]:
    """Return the stable nullable shape used for legacy graphs."""
    return {field: None for field in ROAD_CONTEXT_FIELDS}


def _road_context_value(value: object) -> str | None:
    """Normalize one optional persisted OSM way-context value."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _road_context_record(
    graph: SQLiteRoadGraph,
    way_id: str,
    edge_row: dict[str, object] | None,
    edge_columns: set[str],
    way_columns: set[str],
    cache: dict[str, dict[str, object] | None],
    *,
    ferry: bool,
) -> dict[str, str | None]:
    """Return human-readable OSM way context with legacy fallbacks."""
    context = _empty_road_context()
    if way_id and "way_id" in way_columns:
        if way_id not in cache:
            selected = [
                field for field in ROAD_CONTEXT_FIELDS if field in way_columns
            ]
            if selected:
                query = (
                    f"SELECT {', '.join(selected)} FROM ways "
                    "WHERE way_id = ? LIMIT 1"
                )
                try:
                    row = graph.connection.execute(query, (way_id,)).fetchone()
                except sqlite3.Error:
                    row = None
                cache[way_id] = (
                    dict(zip(selected, row, strict=False)) if row is not None else None
                )
            else:
                cache[way_id] = None
        way_row = cache[way_id]
        if way_row is not None:
            for field in ROAD_CONTEXT_FIELDS:
                context[field] = _road_context_value(way_row.get(field))
    if context["oneway"] is None and edge_row is not None and "oneway" in edge_columns:
        context["oneway"] = _road_context_value(edge_row.get("oneway"))
    if ferry and context["route"] is None:
        context["route"] = "ferry"
    return context


def _attach_path_constraints(
    graph: SQLiteRoadGraph,
    path_segments: list[dict[str, object]],
    *,
    vehicle_weight_t: float | None,
    vehicle_rating_t: float | None,
    vehicle_height_m: float | None,
    vehicle_width_m: float | None,
    vehicle_length_m: float | None,
    vehicle_axleload_t: float | None,
    vehicle_class: str,
    allow_hgv_destination: bool,
) -> None:
    """Attach persisted edge constraints to detailed SQLite path segments."""
    if not graph.has_way_ids or not path_segments:
        return
    column_rows = graph.connection.execute("PRAGMA table_info(edges)").fetchall()
    columns = [str(row[1]) for row in column_rows]
    column_set = set(columns)
    if "u" not in column_set or "v" not in column_set or "way_id" not in column_set:
        return
    conditional_access_columns = {
        str(row[1])
        for row in graph.connection.execute("PRAGMA table_info(conditional_access)")
    }
    way_columns = {
        str(row[1]) for row in graph.connection.execute("PRAGMA table_info(ways)")
    }
    transition_table_columns = {
        table: {
            str(row[1])
            for row in graph.connection.execute(f"PRAGMA table_info({table})")
        }
        for table in ("turn_restrictions", "conditional_turn_restrictions")
    }
    transition_tables_with_rows: set[str] = set()
    transition_required_columns = {"relation_id", "via_node", "from_way", "to_way", "kind"}
    for table, table_columns in transition_table_columns.items():
        if not transition_required_columns.issubset(table_columns):
            continue
        try:
            if graph.connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
                transition_tables_with_rows.add(table)
        except sqlite3.Error:
            continue
    edge_cache: dict[tuple[str, str, str], tuple[dict[str, object] | None, str]] = {}
    conditional_access_cache: dict[tuple[str, str], list[dict[str, object]]] = {}
    transition_cache: dict[tuple[str, str], list[dict[str, object]]] = {}
    way_context_cache: dict[str, dict[str, object] | None] = {}
    previous_way_ids: list[str] = []
    elapsed_time_s = 0.0
    for segment in path_segments:
        cache_key = (
            str(segment.get("from_node", "")),
            str(segment.get("to_node", "")),
            str(segment.get("way_id", "")),
        )
        if bool(segment.get("ferry")):
            segment["constraints"] = []
            segment["conditional_rules"] = []
            segment["transition_rules"] = []
            segment["road_context"] = _road_context_record(
                graph,
                cache_key[2],
                None,
                column_set,
                way_columns,
                way_context_cache,
                ferry=True,
            )
            previous_way_ids = []
            raw_duration = segment.get("duration_s")
            elapsed_time_s += max(0.0, float(raw_duration or 0.0))
            continue
        if cache_key not in edge_cache:
            from_node, to_node, way_id = cache_key
            row = None
            traversal_direction = "forward"
            try:
                row = graph.connection.execute(
                    "SELECT * FROM edges WHERE u = ? AND v = ? AND way_id = ? LIMIT 1",
                    (from_node, to_node, way_id),
                ).fetchone()
                if row is None:
                    traversal_direction = "backward"
                    row = graph.connection.execute(
                        "SELECT * FROM edges WHERE u = ? AND v = ? AND way_id = ? LIMIT 1",
                        (to_node, from_node, way_id),
                    ).fetchone()
            except sqlite3.Error:
                row = None
            edge_cache[cache_key] = (
                dict(zip(columns, row, strict=False)) if row is not None else None,
                traversal_direction,
            )
        edge_row, traversal_direction = edge_cache[cache_key]
        segment["road_context"] = _road_context_record(
            graph,
            cache_key[2],
            edge_row,
            column_set,
            way_columns,
            way_context_cache,
            ferry=False,
        )
        segment["constraints"] = (
            _edge_constraint_records(
                edge_row,
                column_set,
                vehicle_weight_t=vehicle_weight_t,
                vehicle_rating_t=vehicle_rating_t,
                vehicle_height_m=vehicle_height_m,
                vehicle_width_m=vehicle_width_m,
                vehicle_length_m=vehicle_length_m,
                vehicle_axleload_t=vehicle_axleload_t,
                vehicle_class=vehicle_class,
                allow_hgv_destination=allow_hgv_destination,
            )
            if edge_row is not None
            else []
        )
        segment["conditional_rules"] = (
            _conditional_edge_rule_records(
                graph,
                edge_row,
                column_set,
                cache_key[2],
                traversal_direction,
                elapsed_time_s,
            )
            + _conditional_access_rule_records(
                graph,
                cache_key[2],
                traversal_direction,
                conditional_access_columns,
                elapsed_time_s,
                conditional_access_cache,
            )
            if edge_row is not None
            else []
        )
        segment["transition_rules"] = _transition_rule_records(
            graph,
            previous_way_ids,
            str(segment.get("from_node", "")),
            cache_key[2],
            elapsed_time_s,
            transition_table_columns,
            transition_cache,
            transition_tables_with_rows,
        )
        raw_duration = segment.get("duration_s")
        elapsed_time_s += max(0.0, float(raw_duration or 0.0))
        previous_way_ids.append(cache_key[2])
        max_prefix_n = int(getattr(graph, "max_restriction_prefix_n", 0) or 0)
        if max_prefix_n > 0 and len(previous_way_ids) > max_prefix_n:
            previous_way_ids = previous_way_ids[-max_prefix_n:]


def _serialize_path_segments(
    path_segments: list[dict[str, object]],
    speed_kmh: float,
) -> list[dict[str, object]]:
    """Normalize detailed route segments for stable JSON reconciliation."""
    fallback_mps = speed_kmh / 3.6
    result: list[dict[str, object]] = []
    for segment in path_segments:
        distance_m = max(0.0, float(segment.get("distance_m", 0.0)))
        raw_duration_s = segment.get("duration_s")
        duration_s = (
            distance_m / fallback_mps
            if raw_duration_s is None
            else max(0.0, float(raw_duration_s))
        )
        raw_wait_s = segment.get("wait_s", 0.0)
        wait_s = max(0.0, float(raw_wait_s))
        result.append(
            {
                "from_node": str(segment.get("from_node", "")),
                "to_node": str(segment.get("to_node", "")),
                "way_id": str(segment.get("way_id", "")),
                "distance_m": round(distance_m, 2),
                "duration_s": round(duration_s, 2),
                "wait_s": round(wait_s, 2),
                "ferry": bool(segment.get("ferry", False)),
                "road_context": (
                    dict(segment.get("road_context", {}))
                    if isinstance(segment.get("road_context"), dict)
                    else _empty_road_context()
                ),
                "constraints": [
                    dict(constraint)
                    for constraint in segment.get("constraints", [])
                    if isinstance(constraint, dict)
                ],
                "conditional_rules": [
                    dict(rule)
                    for rule in segment.get("conditional_rules", [])
                    if isinstance(rule, dict)
                ],
                "transition_rules": [
                    dict(rule)
                    for rule in segment.get("transition_rules", [])
                    if isinstance(rule, dict)
                ],
            }
        )
    return result


def _portable_ferry_metrics(path_segments: list[dict[str, object]]) -> dict[str, object]:
    """Summarize ferry geometry for a portable route path."""
    ferry_segments = [
        segment for segment in path_segments if bool(segment.get("ferry"))
    ]
    return {
        "ferry_way_ids": sorted(
            {
                str(segment.get("way_id", ""))
                for segment in ferry_segments
                if str(segment.get("way_id", "")).strip()
            }
        ),
        "ferry_distance_m": round(
            sum(float(segment.get("distance_m", 0.0)) for segment in ferry_segments),
            2,
        ),
        "ferry_crossing_s": 0.0,
        "ferry_edge_n": len(ferry_segments),
    }


def _bearing_degrees(start: object, end: object) -> float | None:
    """Return the initial WGS84 bearing from one ``[lon, lat]`` point to another."""
    if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
        return None
    if len(start) < 2 or len(end) < 2:
        return None
    try:
        start_lon = math.radians(float(start[0]))
        start_lat = math.radians(float(start[1]))
        end_lon = math.radians(float(end[0]))
        end_lat = math.radians(float(end[1]))
    except (TypeError, ValueError):
        return None
    if not all(
        math.isfinite(value)
        for value in (start_lon, start_lat, end_lon, end_lat)
    ):
        return None
    delta_lon = end_lon - start_lon
    y = math.sin(delta_lon) * math.cos(end_lat)
    x = (
        math.cos(start_lat) * math.sin(end_lat)
        - math.sin(start_lat) * math.cos(end_lat) * math.cos(delta_lon)
    )
    if abs(x) < 1e-15 and abs(y) < 1e-15:
        return None
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _turn_angle_degrees(
    before: float | None,
    after: float | None,
) -> float | None:
    """Return a signed shortest turn angle; negative is left, positive is right."""
    if before is None or after is None:
        return None
    angle = (after - before + 180.0) % 360.0 - 180.0
    return round(angle, 1)


def _maneuver_road_context(segment: dict[str, object] | None) -> dict[str, str | None]:
    """Return the stable nullable road context attached to one route segment."""
    context = _empty_road_context()
    raw_context = segment.get("road_context") if isinstance(segment, dict) else None
    if isinstance(raw_context, dict):
        for field in ROAD_CONTEXT_FIELDS:
            context[field] = _road_context_value(raw_context.get(field))
    return context


def _road_context_changed(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> bool:
    """Detect a meaningful mapped-road identity change between two segments."""
    return any(
        before.get(field) != after.get(field)
        for field in ("name", "ref", "highway", "route")
        if before.get(field) is not None or after.get(field) is not None
    )


def _maneuver_kind(
    index: int,
    segment_n: int,
    previous: dict[str, object] | None,
    current: dict[str, object] | None,
    angle: float | None,
    context_changed: bool,
) -> str:
    """Classify a route transition using geometry and ferry state."""
    if index == 0:
        return "start"
    if index == segment_n:
        return "arrive"
    previous_ferry = bool(previous and previous.get("ferry"))
    current_ferry = bool(current and current.get("ferry"))
    if current_ferry and not previous_ferry:
        return "ferry_boarding"
    if previous_ferry and not current_ferry:
        return "ferry_landing"
    if angle is None:
        return "change_way" if context_changed else "continue"
    magnitude = abs(angle)
    if magnitude >= 135.0:
        return "u_turn"
    if angle <= -45.0:
        return "left"
    if angle >= 45.0:
        return "right"
    if angle < -15.0:
        return "slight_left"
    if angle > 15.0:
        return "slight_right"
    return "change_way" if context_changed else "continue"


def _build_route_maneuvers(
    path_nodes: list[str],
    path_coordinates: list[list[float]],
    path_segments: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Build stable, geometry-derived maneuver records for a detailed route."""
    segment_n = len(path_segments)
    if not path_segments or len(path_nodes) != segment_n + 1:
        return []
    if len(path_coordinates) != segment_n + 1:
        return []

    bearings = [
        _bearing_degrees(path_coordinates[index], path_coordinates[index + 1])
        for index in range(segment_n)
    ]
    transition_indices = [0]
    transition_details: dict[int, tuple[float | None, bool]] = {}
    for index in range(1, segment_n):
        previous = path_segments[index - 1]
        current = path_segments[index]
        previous_context = _maneuver_road_context(previous)
        current_context = _maneuver_road_context(current)
        context_changed = _road_context_changed(previous_context, current_context)
        angle = _turn_angle_degrees(bearings[index - 1], bearings[index])
        ferry_changed = bool(previous.get("ferry")) != bool(current.get("ferry"))
        if ferry_changed or context_changed or (angle is not None and abs(angle) >= 15.0):
            transition_indices.append(index)
            transition_details[index] = (angle, context_changed)
    transition_indices.append(segment_n)

    maneuvers: list[dict[str, object]] = []
    for sequence, index in enumerate(transition_indices):
        previous = path_segments[index - 1] if index > 0 else None
        current = path_segments[index] if index < segment_n else None
        angle, context_changed = transition_details.get(index, (None, False))
        next_index = transition_indices[sequence + 1] if sequence + 1 < len(transition_indices) else index
        leg_segments = path_segments[index:next_index] if index < segment_n else []
        distance_m = sum(float(segment.get("distance_m", 0.0)) for segment in leg_segments)
        duration_s = sum(float(segment.get("duration_s", 0.0)) for segment in leg_segments)
        wait_s = sum(float(segment.get("wait_s", 0.0)) for segment in leg_segments)
        context_segment = current if current is not None else previous
        incoming_bearing = bearings[index - 1] if index > 0 else None
        outgoing_bearing = bearings[index] if index < segment_n else None
        coordinate = path_coordinates[index]
        maneuvers.append(
            {
                "sequence": sequence,
                "kind": _maneuver_kind(
                    index,
                    segment_n,
                    previous,
                    current,
                    angle,
                    context_changed,
                ),
                "node_id": str(path_nodes[index]),
                "coordinate": [float(coordinate[0]), float(coordinate[1])],
                "from_way_id": (
                    str(previous.get("way_id", "")) if previous is not None else None
                ),
                "to_way_id": (
                    str(current.get("way_id", "")) if current is not None else None
                ),
                "road_context": _maneuver_road_context(context_segment),
                "bearing_before_deg": (
                    round(incoming_bearing, 1) if incoming_bearing is not None else None
                ),
                "bearing_after_deg": (
                    round(outgoing_bearing, 1) if outgoing_bearing is not None else None
                ),
                "turn_angle_deg": angle,
                "distance_m": round(max(0.0, distance_m), 2),
                "duration_s": round(max(0.0, duration_s), 2),
                "wait_s": round(max(0.0, wait_s), 2),
            }
        )
    return maneuvers


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
        if key not in {
            "path_node_ids",
            "path_coordinates",
            "path_way_ids",
            "path_segments",
            "path_segment_n",
            "path_segment_total_distance_m",
            "path_segment_total_duration_s",
            "path_segment_total_wait_s",
            "path_segment_source",
            "maneuver_n",
            "maneuvers",
        }
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
    objective: str = "distance",
    vehicle_weight_t: float | None = None,
    vehicle_rating_t: float | None = None,
    vehicle_height_m: float | None = None,
    vehicle_width_m: float | None = None,
    vehicle_length_m: float | None = None,
    vehicle_axleload_t: float | None = None,
    vehicle_class: str = "general",
    allow_hgv_destination: bool = False,
) -> dict[str, Any]:
    """Return a JSON-serializable route result for two WGS84 points."""
    objective = validate_route_objective(objective)
    vehicle_weight_t = validate_vehicle_weight_t(vehicle_weight_t)
    vehicle_rating_t = validate_vehicle_rating_t(vehicle_rating_t)
    vehicle_height_m = validate_vehicle_height_m(vehicle_height_m)
    vehicle_width_m = validate_vehicle_width_m(vehicle_width_m)
    vehicle_length_m = validate_vehicle_length_m(vehicle_length_m)
    vehicle_axleload_t = validate_vehicle_axleload_t(vehicle_axleload_t)
    vehicle_class = validate_vehicle_class(vehicle_class)
    if isinstance(graph, SQLiteRoadGraph):
        coordinates: Coordinates = {}
        graph_coordinates: Coordinates | SQLiteRoadGraph = graph
        node_index = None
    else:
        coordinates = graph.coordinates
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
        "objective": objective,
        "vehicle_weight_t": vehicle_weight_t,
        "vehicle_rating_t": vehicle_rating_t,
        "vehicle_height_m": vehicle_height_m,
        "vehicle_width_m": vehicle_width_m,
        "vehicle_length_m": vehicle_length_m,
        "vehicle_axleload_t": vehicle_axleload_t,
        "vehicle_class": vehicle_class,
        "allow_hgv_destination": bool(allow_hgv_destination),
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
        "ferry_wait_s": 0.0,
        "ferry_wait_n": 0,
        "ferry_way_ids": [],
        "ferry_distance_m": 0.0,
        "ferry_crossing_s": 0.0,
        "ferry_edge_n": 0,
        "departure": departure.isoformat() if departure else None,
        "arrival": None,
        "speed_kmh": speed_kmh,
        "method": _method(
            graph,
            departure,
            speed_kmh,
            include_ferries,
            objective,
            vehicle_weight_t,
            vehicle_rating_t,
            vehicle_height_m,
            vehicle_width_m,
            vehicle_length_m,
            vehicle_axleload_t,
            vehicle_class,
            bool(allow_hgv_destination),
        ),
        "graph": _graph_summary(graph, (coordinates if not isinstance(graph, SQLiteRoadGraph) else {})),
    }
    if include_path:
        result["path_node_n"] = 0
        result["path_node_ids"] = []
        result["path_coordinates"] = []
        result["path_segment_n"] = 0
        result["path_segment_total_distance_m"] = 0.0
        result["path_segment_total_duration_s"] = 0.0
        result["path_segment_total_wait_s"] = 0.0
        result["path_way_ids"] = []
        result["path_segments"] = []
        result["maneuver_n"] = 0
        result["maneuvers"] = []
        result["path_segment_source"] = (
            "sqlite_edges"
            if isinstance(graph, SQLiteRoadGraph) and graph.has_way_ids
            else (
                "portable_edges"
                if isinstance(graph, PortableRoadGraph) and graph.has_way_ids
                else "not_available"
            )
        )
    if not start_node or not goal_node:
        result["status"] = "provided_but_unreachable"
        return result

    adjacency = graph
    portable_metric_path = (
        isinstance(graph, PortableRoadGraph)
        and include_ferries
        and graph.ferry_edge_count > 0
        and not include_path
    )
    portable_metric_segments: list[dict[str, object]] = []
    if isinstance(graph, SQLiteRoadGraph):
        route_result = shortest_path_metrics(
            start_node,
            goal_node,
            adjacency,
            departure=departure,
            speed_kmh=speed_kmh,
            vehicle_weight_t=vehicle_weight_t,
            vehicle_rating_t=vehicle_rating_t,
            vehicle_height_m=vehicle_height_m,
            vehicle_width_m=vehicle_width_m,
            vehicle_length_m=vehicle_length_m,
            vehicle_axleload_t=vehicle_axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=allow_hgv_destination,
            return_path=include_path,
            return_path_details=include_path or portable_metric_path,
            include_ferries=include_ferries,
            objective=objective,
        )
    else:
        route_result = shortest_path(
            start_node,
            goal_node,
            adjacency,
            departure=departure,
            speed_kmh=speed_kmh,
            vehicle_weight_t=vehicle_weight_t,
            vehicle_rating_t=vehicle_rating_t,
            vehicle_height_m=vehicle_height_m,
            vehicle_width_m=vehicle_width_m,
            vehicle_length_m=vehicle_length_m,
            vehicle_axleload_t=vehicle_axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=allow_hgv_destination,
            return_path=include_path,
            return_path_details=include_path or portable_metric_path,
            include_ferries=include_ferries,
        )
    if route_result is None:
        result["status"] = "provided_but_unreachable"
        return result
    if include_path:
        if isinstance(graph, SQLiteRoadGraph):
            (
                route_distance,
                duration_s,
                path_nodes,
                path_way_ids,
                path_segments,
            ) = route_result
        else:
            route_distance, path_nodes, path_way_ids, path_segments = route_result
            duration_s = route_distance / (speed_kmh / 3.6)
        if result["path_segment_source"] == "not_available" or any(
            not str(segment.get("way_id", "")).strip() for segment in path_segments
        ):
            result["path_segment_source"] = "not_available"
            path_way_ids = []
            path_segments = []
        if result["path_segment_source"] == "sqlite_edges":
            _attach_path_constraints(
                graph,
                path_segments,
                vehicle_weight_t=vehicle_weight_t,
                vehicle_rating_t=vehicle_rating_t,
                vehicle_height_m=vehicle_height_m,
                vehicle_width_m=vehicle_width_m,
                vehicle_length_m=vehicle_length_m,
                vehicle_axleload_t=vehicle_axleload_t,
                vehicle_class=vehicle_class,
                allow_hgv_destination=bool(allow_hgv_destination),
            )
        path_segments = _serialize_path_segments(path_segments, speed_kmh)
        result["path_node_n"] = len(path_nodes)
        result["path_node_ids"] = path_nodes
        result["path_coordinates"] = _path_coordinates(graph, coordinates, path_nodes)
        result["path_segment_n"] = len(path_segments)
        result["path_segment_total_distance_m"] = round(
            sum(float(segment["distance_m"]) for segment in path_segments),
            2,
        )
        result["path_segment_total_duration_s"] = round(
            sum(float(segment["duration_s"]) for segment in path_segments),
            2,
        )
        result["path_segment_total_wait_s"] = round(
            sum(float(segment["wait_s"]) for segment in path_segments),
            2,
        )
        result["path_way_ids"] = path_way_ids
        result["path_segments"] = path_segments
        if isinstance(graph, PortableRoadGraph):
            result.update(_portable_ferry_metrics(path_segments))
        maneuvers = _build_route_maneuvers(
            path_nodes,
            result["path_coordinates"],
            path_segments,
        )
        result["maneuver_n"] = len(maneuvers)
        result["maneuvers"] = maneuvers
    else:
        if isinstance(graph, SQLiteRoadGraph):
            route_distance, duration_s = route_result
        elif portable_metric_path:
            route_distance, _path_nodes, _path_way_ids, raw_segments = route_result
            portable_metric_segments = _serialize_path_segments(raw_segments, speed_kmh)
            duration_s = route_distance / (speed_kmh / 3.6)
        else:
            route_distance = route_result
            duration_s = route_distance / (speed_kmh / 3.6)

    result["reachable"] = True
    result["route_distance_m"] = round(route_distance, 2)
    result["estimated_duration_s"] = round(duration_s, 2)
    if isinstance(graph, SQLiteRoadGraph):
        result["ferry_wait_s"] = round(graph.last_ferry_wait_s, 2)
        result["ferry_wait_n"] = graph.last_ferry_wait_n
        result["ferry_way_ids"] = list(graph.last_ferry_way_ids)
        result["ferry_distance_m"] = round(graph.last_ferry_distance_m, 2)
        result["ferry_crossing_s"] = round(graph.last_ferry_crossing_s, 2)
        result["ferry_edge_n"] = graph.last_ferry_edge_n
    elif portable_metric_path:
        result.update(_portable_ferry_metrics(portable_metric_segments))
    if departure:
        result["arrival"] = (departure + timedelta(seconds=duration_s)).isoformat()
    return result


def query_route_matrix(
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
    graph: Graph,
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    include_path: bool = False,
    include_ferries: bool = False,
    objective: str = "distance",
    vehicle_weight_t: float | None = None,
    vehicle_rating_t: float | None = None,
    vehicle_height_m: float | None = None,
    vehicle_width_m: float | None = None,
    vehicle_length_m: float | None = None,
    vehicle_axleload_t: float | None = None,
    vehicle_class: str = "general",
    allow_hgv_destination: bool = False,
) -> dict[str, Any]:
    """Return bounded all-pairs route summaries for two WGS84 point lists.

    The matrix deliberately keeps the point-to-point route contract as the
    source of truth. Each pair receives the same snapping, conditional-rule,
    ferry, and vehicle-profile semantics as :func:`query_route`; full route
    responses are included only when ``include_path`` is requested.
    """
    if not origins:
        raise ValueError("at least one origin is required")
    if not destinations:
        raise ValueError("at least one destination is required")
    pair_n = len(origins) * len(destinations)
    if pair_n > ROUTE_MATRIX_MAX_PAIRS:
        raise ValueError(
            f"origin × destination pair count must not exceed {ROUTE_MATRIX_MAX_PAIRS}"
        )
    objective = validate_route_objective(objective)
    vehicle_weight_t = validate_vehicle_weight_t(vehicle_weight_t)
    vehicle_rating_t = validate_vehicle_rating_t(vehicle_rating_t)
    vehicle_height_m = validate_vehicle_height_m(vehicle_height_m)
    vehicle_width_m = validate_vehicle_width_m(vehicle_width_m)
    vehicle_length_m = validate_vehicle_length_m(vehicle_length_m)
    vehicle_axleload_t = validate_vehicle_axleload_t(vehicle_axleload_t)
    vehicle_class = validate_vehicle_class(vehicle_class)
    try:
        speed_kmh = float(speed_kmh)
    except (TypeError, ValueError) as exc:
        raise ValueError("speed_kmh must be a finite positive number") from exc
    if not math.isfinite(speed_kmh) or speed_kmh <= 0:
        raise ValueError("speed_kmh must be a finite positive number")

    origin_records = [
        {"index": index, "lat": lat, "lon": lon}
        for index, (lat, lon) in enumerate(origins)
    ]
    destination_records = [
        {"index": index, "lat": lat, "lon": lon}
        for index, (lat, lon) in enumerate(destinations)
    ]
    pairs: list[dict[str, Any]] = []
    graph_summary: dict[str, Any] | None = None
    reachable_n = 0
    for origin_index, (origin_lat, origin_lon) in enumerate(origins):
        for destination_index, (destination_lat, destination_lon) in enumerate(destinations):
            route = query_route(
                origin_lat,
                origin_lon,
                destination_lat,
                destination_lon,
                graph,
                departure=departure,
                speed_kmh=speed_kmh,
                include_path=include_path,
                include_ferries=include_ferries,
                objective=objective,
                vehicle_weight_t=vehicle_weight_t,
                vehicle_rating_t=vehicle_rating_t,
                vehicle_height_m=vehicle_height_m,
                vehicle_width_m=vehicle_width_m,
                vehicle_length_m=vehicle_length_m,
                vehicle_axleload_t=vehicle_axleload_t,
                vehicle_class=vehicle_class,
                allow_hgv_destination=allow_hgv_destination,
            )
            if graph_summary is None:
                graph_summary = route.get("graph")
            if route["reachable"]:
                reachable_n += 1
            pair: dict[str, Any] = {
                "origin_index": origin_index,
                "destination_index": destination_index,
                "status": route["status"],
                "reachable": route["reachable"],
                "start": route["start"],
                "goal": route["goal"],
                "route_distance_m": route["route_distance_m"],
                "estimated_duration_s": route["estimated_duration_s"],
                "ferry_wait_s": route["ferry_wait_s"],
                "ferry_wait_n": route["ferry_wait_n"],
                "ferry_distance_m": route["ferry_distance_m"],
                "ferry_crossing_s": route["ferry_crossing_s"],
                "ferry_edge_n": route["ferry_edge_n"],
                "arrival": route["arrival"],
                "route": route if include_path else None,
            }
            pairs.append(pair)
    return {
        "contract": ROUTE_MATRIX_CONTRACT,
        "status": "provided",
        "objective": objective,
        "vehicle_weight_t": vehicle_weight_t,
        "vehicle_rating_t": vehicle_rating_t,
        "vehicle_height_m": vehicle_height_m,
        "vehicle_width_m": vehicle_width_m,
        "vehicle_length_m": vehicle_length_m,
        "vehicle_axleload_t": vehicle_axleload_t,
        "vehicle_class": vehicle_class,
        "allow_hgv_destination": bool(allow_hgv_destination),
        "departure": departure.isoformat() if departure else None,
        "speed_kmh": speed_kmh,
        "include_path": bool(include_path),
        "include_ferries": bool(include_ferries),
        "origin_n": len(origins),
        "destination_n": len(destinations),
        "pair_n": pair_n,
        "reachable_n": reachable_n,
        "unreachable_n": pair_n - reachable_n,
        "origins": origin_records,
        "destinations": destination_records,
        "pairs": pairs,
        "graph": graph_summary or {},
    }


def query_route_comparison(
    start_lat: float,
    start_lon: float,
    goal_lat: float,
    goal_lon: float,
    graph: Graph,
    profiles: list[dict[str, object]],
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    include_path: bool = False,
    include_ferries: bool = False,
    objective: str = "distance",
) -> dict[str, Any]:
    """Compare a bounded set of vehicle profiles over one origin/destination pair.

    Each profile is routed independently through :func:`query_route`, preserving
    the existing time-dependent restrictions and optional path evidence. The
    first profile is the baseline for metric deltas; an unreachable baseline
    leaves deltas null rather than implying a comparison between incomparable
    routes.
    """
    objective = validate_route_objective(objective)
    try:
        speed_kmh = float(speed_kmh)
    except (TypeError, ValueError) as exc:
        raise ValueError("speed_kmh must be a finite positive number") from exc
    if not math.isfinite(speed_kmh) or speed_kmh <= 0:
        raise ValueError("speed_kmh must be a finite positive number")
    normalized_profiles = _validated_route_profiles(profiles)
    route_results: list[dict[str, Any]] = []
    graph_summary: dict[str, Any] | None = None
    for profile in normalized_profiles:
        route = query_route(
            start_lat,
            start_lon,
            goal_lat,
            goal_lon,
            graph,
            departure=departure,
            speed_kmh=speed_kmh,
            include_path=include_path,
            include_ferries=include_ferries,
            objective=objective,
            **{
                field: profile[field]
                for field in ROUTE_PROFILE_FIELDS
            },
        )
        if graph_summary is None:
            graph_summary = route.get("graph")
        route_results.append(route)

    baseline = route_results[0]
    baseline_reachable = bool(baseline.get("reachable"))
    delta_fields = (
        "route_distance_m",
        "estimated_duration_s",
        "ferry_wait_s",
        "ferry_distance_m",
        "ferry_crossing_s",
    )
    profile_rows: list[dict[str, Any]] = []
    for profile, route in zip(normalized_profiles, route_results):
        deltas: dict[str, float | None] = {}
        for field in delta_fields:
            current = route.get(field)
            base = baseline.get(field)
            if (
                baseline_reachable
                and route.get("reachable")
                and isinstance(current, (int, float))
                and isinstance(base, (int, float))
            ):
                deltas[field] = round(float(current) - float(base), 2)
            else:
                deltas[field] = None
        row: dict[str, Any] = {
            "name": profile["name"],
            "vehicle_weight_t": profile["vehicle_weight_t"],
            "vehicle_rating_t": profile["vehicle_rating_t"],
            "vehicle_height_m": profile["vehicle_height_m"],
            "vehicle_width_m": profile["vehicle_width_m"],
            "vehicle_length_m": profile["vehicle_length_m"],
            "vehicle_axleload_t": profile["vehicle_axleload_t"],
            "vehicle_class": profile["vehicle_class"],
            "allow_hgv_destination": profile["allow_hgv_destination"],
            "status": route["status"],
            "reachable": route["reachable"],
            "start": route["start"],
            "goal": route["goal"],
            "route_distance_m": route["route_distance_m"],
            "estimated_duration_s": route["estimated_duration_s"],
            "ferry_wait_s": route["ferry_wait_s"],
            "ferry_wait_n": route["ferry_wait_n"],
            "ferry_distance_m": route["ferry_distance_m"],
            "ferry_crossing_s": route["ferry_crossing_s"],
            "ferry_edge_n": route["ferry_edge_n"],
            "arrival": route["arrival"],
            "delta_from_baseline": deltas,
            "method": route["method"],
            "route": route if include_path else None,
        }
        profile_rows.append(row)

    reachable_n = sum(1 for route in route_results if route["reachable"])
    return {
        "contract": ROUTE_COMPARISON_CONTRACT,
        "status": "provided",
        "start": {"lat": start_lat, "lon": start_lon},
        "goal": {"lat": goal_lat, "lon": goal_lon},
        "objective": objective,
        "departure": departure.isoformat() if departure else None,
        "speed_kmh": speed_kmh,
        "include_path": bool(include_path),
        "include_ferries": bool(include_ferries),
        "baseline_profile": normalized_profiles[0]["name"],
        "baseline_reachable": baseline_reachable,
        "profile_n": len(profile_rows),
        "reachable_n": reachable_n,
        "unreachable_n": len(profile_rows) - reachable_n,
        "profiles": profile_rows,
        "graph": graph_summary or {},
    }


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
        help="optional vehicle weight profile in metric tonnes for weight-based restrictions",
    )
    parser.add_argument(
        "--rating-t",
        type=float,
        default=None,
        help=(
            "optional HGV permitted gross-weight rating in metric tonnes for "
            "maxweightrating:hgv and Irish maxweightrating:goods restrictions"
        ),
    )
    parser.add_argument(
        "--height-m",
        type=float,
        default=None,
        help="optional vehicle height profile in metres for height-based restrictions",
    )
    parser.add_argument(
        "--width-m",
        type=float,
        default=None,
        help="optional vehicle width profile in metres for width-based restrictions",
    )
    parser.add_argument(
        "--length-m",
        type=float,
        default=None,
        help="optional vehicle length profile in metres for length-based restrictions",
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
        help="vehicle-class profile for class-specific conditional access (default: general)",
    )
    parser.add_argument(
        "--allow-hgv-destination",
        action="store_true",
        help=(
            "allow an explicit hgv profile to use destination-only ways and "
            "supported destination exceptions to HGV weight/rating limits"
        ),
    )
    parser.add_argument("--out", default=None, help="optional JSON output path")
    parser.add_argument(
        "--include-path",
        action="store_true",
        help=(
            "include graph node IDs, ordered way/segment records, and "
            "GeoJSON-order coordinates in the JSON result"
        ),
    )
    parser.add_argument(
        "--geojson-out",
        default=None,
        help="optional GeoJSON Feature output path; implies --include-path",
    )
    parser.add_argument(
        "--include-ferries",
        action="store_true",
        help=(
            "include persisted ferry geometry; SQLite graphs may also apply "
            "schedules, waits, and durations"
        ),
    )
    args = parser.parse_args(argv)
    _validate_coordinate(parser, "--start-lat", args.start_lat, -90.0, 90.0)
    _validate_coordinate(parser, "--start-lon", args.start_lon, -180.0, 180.0)
    _validate_coordinate(parser, "--goal-lat", args.goal_lat, -90.0, 90.0)
    _validate_coordinate(parser, "--goal-lon", args.goal_lon, -180.0, 180.0)
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    try:
        weight_t = validate_vehicle_weight_t(args.weight_t)
        rating_t = validate_vehicle_rating_t(args.rating_t)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        height_m = validate_vehicle_height_m(args.height_m)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        width_m = validate_vehicle_width_m(args.width_m)
        length_m = validate_vehicle_length_m(args.length_m)
        axleload_t = validate_vehicle_axleload_t(args.axleload_t)
    except ValueError as exc:
        parser.error(str(exc))
    try:
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
            objective=args.objective,
            vehicle_weight_t=weight_t,
            vehicle_rating_t=rating_t,
            vehicle_height_m=height_m,
            vehicle_width_m=width_m,
            vehicle_length_m=length_m,
            vehicle_axleload_t=axleload_t,
            vehicle_class=args.vehicle_class,
            allow_hgv_destination=args.allow_hgv_destination,
        )
    finally:
        if isinstance(graph, SQLiteRoadGraph):
            graph.close()
    if args.out:
        destination = project_output_file_path(
            args.out,
            str(Path(args.out)),
            label="route output",
        )
        atomic_write_json(destination, result, indent=2)
    if args.geojson_out:
        destination = project_output_file_path(
            args.geojson_out,
            str(Path(args.geojson_out)),
            label="route GeoJSON output",
        )
        atomic_write_json(destination, route_geojson(result), indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
