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
Path-enabled output preserves ordered SQLite way/segment IDs and per-segment
distance, duration, ferry-wait, and ferry-status metrics; portable fallback
graphs report that segment IDs are unavailable.
SQLite-backed path segments also expose normalized static edge constraints,
including their source key, value/unit, persistence status, and whether the
active vehicle profile evaluated the constraint. They also expose conditional
speed, one-way, and access rule records with entry-time active/applied state.
They also expose nullable human-readable OSM way context such as name, ref,
highway class, route type, and oneway state.
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
        "node_n": len(coordinates),
        "edge_n": sum(len(edges) for edges in graph[1].values()),
        "way_context": False,
        "way_context_n": 0,
        "ferry_edge_n": 0,
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
        method = f"Dijkstra on supplied road graph; nearest graph node snap; route objective={objective}"
        if include_ferries:
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
        result["path_segment_source"] = (
            "sqlite_edges"
            if isinstance(graph, SQLiteRoadGraph) and graph.has_way_ids
            else "not_available"
        )
    if not start_node or not goal_node:
        result["status"] = "provided_but_unreachable"
        return result

    adjacency = graph if isinstance(graph, SQLiteRoadGraph) else graph[1]
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
            return_path_details=include_path,
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
            return_path_details=include_path,
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
        if result["path_segment_source"] != "not_available":
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
    else:
        if isinstance(graph, SQLiteRoadGraph):
            route_distance, duration_s = route_result
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
        help="include persisted ferry geometry, schedules, waits, and durations",
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
