#!/usr/bin/env python3
"""Serve the generated Ireland geometry report from a local HTTP server.

The lazy report fetches ``report_data.json`` at runtime, which browsers may
block when the HTML is opened directly from ``file://``. This module provides
an intentionally localhost-first server using only the Python standard
library. It serves the selected report and generated output directory;
``/__health`` provides a small machine-readable smoke-test endpoint, and
``/api/capabilities`` provides versioned endpoint discovery for automation.
``/api/openapi.json`` provides the read-only API's versioned OpenAPI document.
``/api/route/matrix`` provides a bounded origin/destination matrix using the
same profile-aware routing semantics as the point-to-point route endpoint.
Its GET form accepts repeated query points and its read-only POST form accepts
a bounded structured JSON request.
``/api/route/compare`` compares a bounded set of named vehicle profiles over
one origin/destination pair using those same routing semantics.
``/api/interpretation`` provides compact data-derived findings without
downloading the full report data pack.
``/api/report/runtime`` provides the compact, conditionally cacheable analytical
readiness contract for automation and long-running dashboard sessions.
All read-only readiness responses recheck the current output-manifest and
input-source alignment contracts before reporting analytical readiness.
Every read-only API GET path also accepts HEAD for bodyless header inspection.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sqlite3
import sys
import threading
import time
import uuid
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

try:
    from query_data import (
        _cursor_for_row,
        _json_safe,
        probe_backend,
        query_rows,
    )
    from road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from route_query import (
        ROUTE_COMPARISON_CONTRACT,
        ROUTE_COMPARISON_MAX_PROFILES,
        ROUTE_COMPARISON_MIN_PROFILES,
        ROUTE_CONTRACT,
        ROUTE_MATRIX_CONTRACT,
        ROUTE_MATRIX_MAX_PAIRS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        parse_route_profile_spec,
        query_route,
        query_route_comparison,
        query_route_matrix,
        route_geojson,
        validate_route_objective,
        validate_vehicle_axleload_t,
        validate_vehicle_class,
        validate_vehicle_height_m,
        validate_vehicle_length_m,
        validate_vehicle_rating_t,
        validate_vehicle_weight_t,
        validate_vehicle_width_m,
    )
    from runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        SOURCE_ALIGNMENT_CONTRACT,
        SOURCE_FRESHNESS_CONTRACT,
        default_project_root,
        manifest_source_alignment,
        package_version,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
    )
except ImportError:
    from scripts.query_data import (
        _cursor_for_row,
        _json_safe,
        probe_backend,
        query_rows,
    )
    from scripts.road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from scripts.route_query import (
        ROUTE_COMPARISON_CONTRACT,
        ROUTE_COMPARISON_MAX_PROFILES,
        ROUTE_COMPARISON_MIN_PROFILES,
        ROUTE_CONTRACT,
        ROUTE_MATRIX_CONTRACT,
        ROUTE_MATRIX_MAX_PAIRS,
        ROUTE_OBJECTIVES,
        VEHICLE_CLASSES,
        parse_route_profile_spec,
        query_route,
        query_route_comparison,
        query_route_matrix,
        route_geojson,
        validate_route_objective,
        validate_vehicle_axleload_t,
        validate_vehicle_class,
        validate_vehicle_height_m,
        validate_vehicle_length_m,
        validate_vehicle_rating_t,
        validate_vehicle_weight_t,
        validate_vehicle_width_m,
    )
    from scripts.runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        SOURCE_ALIGNMENT_CONTRACT,
        SOURCE_FRESHNESS_CONTRACT,
        default_project_root,
        manifest_source_alignment,
        package_version,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
    )


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_REPORT = "report_lazy.html"
HEALTH_PATH = "/__health"
ROUTE_API_PATH = "/api/route"
ROUTE_MATRIX_API_PATH = "/api/route/matrix"
ROUTE_COMPARISON_API_PATH = "/api/route/compare"
QUERY_API_PATH = "/api/query"
METADATA_API_PATH = "/api/metadata"
CAPABILITIES_API_PATH = "/api/capabilities"
OPENAPI_PATH = "/api/openapi.json"
INTERPRETATION_API_PATH = "/api/interpretation"
REPORT_PAGE_API_PATH = "/api/report/page"
REPORT_EXPORT_API_PATH = "/api/report/export"
REPORT_RUNTIME_API_PATH = "/api/report/runtime"
SOURCE_ALIGNMENT_SURFACES = (
    HEALTH_PATH,
    CAPABILITIES_API_PATH,
    METADATA_API_PATH,
    INTERPRETATION_API_PATH,
    REPORT_PAGE_API_PATH,
    REPORT_RUNTIME_API_PATH,
    REPORT_EXPORT_API_PATH,
)
REPORT_PAGE_CONTRACT = "ireland-geometry.report-page.v1"
REPORT_EXPORT_CONTRACT = "ireland-geometry.report-export.v1"
REPORT_PAGE_DEFAULT_LIMIT = 50
REPORT_PAGE_MAX_LIMIT = 100
REPORT_FILTER_SORT_KEYS = ("name", "group", "area_m2", "score", "flags")
REPORT_PAGE_SORT_TIEBREAKER = "osm_id"
HEALTH_CONTRACT = "ireland-geometry.health.v1"
QUERY_CONTRACT = "ireland-geometry.query.v1"
METADATA_CONTRACT = "ireland-geometry.metadata.v1"
CAPABILITIES_CONTRACT = "ireland-geometry.capabilities.v1"
OPENAPI_CONTRACT = "ireland-geometry.openapi.v1"
INTERPRETATION_CONTRACT = "ireland-geometry.interpretation.v1"
MANIFEST_ALIGNMENT_CONTRACT = "ireland-geometry.manifest-alignment.v1"
REPORT_RUNTIME_CONTRACT = "ireland-geometry.report-runtime.v1"
API_ERROR_CONTRACT = "ireland-geometry.api-error.v1"
REQUEST_ID_HEADER = "X-Ireland-Geometry-Request-ID"
REQUEST_ID_INPUT_HEADERS = (REQUEST_ID_HEADER, "X-Request-ID")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECURITY_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}
REQUEST_TIMING_HEADER = "Server-Timing"
REQUEST_TIMING_METRIC = "ireland_geometry"
REQUEST_TIMING_LOG_FIELD = "duration_ms"
REPORT_RUNTIME_HEADER_NAMES = {
    "contract": "X-Ireland-Geometry-Runtime-Contract",
    "status": "X-Ireland-Geometry-Runtime-Status",
    "analysis_ready": "X-Ireland-Geometry-Analysis-Ready",
    "validation": "X-Ireland-Geometry-Validation",
    "manifest_alignment": "X-Ireland-Geometry-Manifest-Alignment",
    "source_alignment": "X-Ireland-Geometry-Source-Alignment",
}
CONDITIONAL_ENDPOINTS = (
    HEALTH_PATH,
    CAPABILITIES_API_PATH,
    OPENAPI_PATH,
    METADATA_API_PATH,
    INTERPRETATION_API_PATH,
    REPORT_PAGE_API_PATH,
    REPORT_RUNTIME_API_PATH,
    REPORT_EXPORT_API_PATH,
    QUERY_API_PATH,
    ROUTE_API_PATH,
    ROUTE_MATRIX_API_PATH,
    ROUTE_COMPARISON_API_PATH,
)
HEAD_ENDPOINTS = CONDITIONAL_ENDPOINTS
API_CONTRACTS = {
    "api_error": API_ERROR_CONTRACT,
    "health": HEALTH_CONTRACT,
    "capabilities": CAPABILITIES_CONTRACT,
    "metadata": METADATA_CONTRACT,
    "query": QUERY_CONTRACT,
    "route": ROUTE_CONTRACT,
    "route_matrix": ROUTE_MATRIX_CONTRACT,
    "route_comparison": ROUTE_COMPARISON_CONTRACT,
    "openapi": OPENAPI_CONTRACT,
    "interpretation": INTERPRETATION_CONTRACT,
    "report_page": REPORT_PAGE_CONTRACT,
    "report_export": REPORT_EXPORT_CONTRACT,
    "report_runtime": REPORT_RUNTIME_CONTRACT,
}
QUERY_MAX_LIMIT = 1000
QUERY_MAX_OFFSET = 10_000_000
ROUTE_JSON_MAX_BODY_BYTES = 128 * 1024
ROUTE_MATRIX_JSON_MAX_BODY_BYTES = 128 * 1024
ROUTE_COMPARISON_JSON_MAX_BODY_BYTES = 128 * 1024
QUERY_JSON_MAX_BODY_BYTES = 128 * 1024
REPORT_PAGE_JSON_MAX_BODY_BYTES = 128 * 1024
REPORT_EXPORT_JSON_MAX_BODY_BYTES = 128 * 1024
API_JSON_GZIP_MIN_BYTES = 1024
TRUE_QUERY_VALUES = {"1", "true", "yes", "on"}
QUERY_BACKEND_NAMES = ("duckdb", "parquet", "csv", "jsonl")
VALIDATION_RECORD_NAMES = ("verification", "schema_validation", "reproducibility")


def _openapi_query_parameter(
    name: str,
    schema: dict[str, object],
    *,
    description: str,
    required: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def _openapi_conditional_headers() -> dict[str, object]:
    return {
        "ETag": {
            "description": "Deterministic entity tag for conditional revalidation.",
            "schema": {"type": "string"},
        },
        "Cache-Control": {
            "description": "The representation may be revalidated with If-None-Match.",
            "schema": {"type": "string", "const": "no-cache"},
        },
        "Content-Encoding": {
            "description": (
                "Optional gzip encoding when the request accepts gzip and the "
                f"JSON representation is at least {API_JSON_GZIP_MIN_BYTES} bytes."
            ),
            "schema": {"type": "string", "const": "gzip"},
        },
        "Vary": {
            "description": "Present on negotiated gzip representations.",
            "schema": {"type": "string", "const": "Accept-Encoding"},
        },
    }


def _openapi_request_id_header() -> dict[str, object]:
    return {
        REQUEST_ID_HEADER: {
            "description": (
                "Request correlation identifier. A valid client-supplied identifier "
                "is echoed; otherwise the server generates one."
            ),
            "schema": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
            },
        }
    }


def _openapi_security_headers() -> dict[str, object]:
    return {
        name: {
            "description": "Baseline browser security response policy.",
            "schema": {"type": "string", "const": value},
        }
        for name, value in SECURITY_RESPONSE_HEADERS.items()
    }


def _openapi_request_timing_header() -> dict[str, object]:
    return {
        REQUEST_TIMING_HEADER: {
            "description": (
                "Server processing time before response headers, in milliseconds."
            ),
            "schema": {
                "type": "string",
                "pattern": rf"^{REQUEST_TIMING_METRIC};dur=[0-9]+(?:\.[0-9]+)?$",
            },
        }
    }


def _openapi_json_response(
    schema: str,
    description: str,
    *,
    conditional: bool = False,
) -> dict[str, object]:
    response: dict[str, object] = {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema}"}
            }
        },
    }
    response["headers"] = {
        **_openapi_request_id_header(),
        **_openapi_security_headers(),
        **_openapi_request_timing_header(),
    }
    if conditional:
        response["headers"].update(_openapi_conditional_headers())
    return response


def _openapi_error_response(description: str) -> dict[str, object]:
    return _openapi_json_response("ApiError", description)


def _query_first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


def _query_boolean(query: dict[str, list[str]], name: str) -> bool:
    return (_query_first(query, name) or "").strip().lower() in TRUE_QUERY_VALUES


def _parse_route_options(
    query: dict[str, list[str]], *, include_path: bool | None = None
) -> dict[str, object]:
    """Parse the shared profile options for point and matrix route endpoints."""
    speed_text = _query_first(query, "speed_kmh") or "50"
    try:
        speed_kmh = float(speed_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("speed_kmh must be a finite positive number") from exc
    if not math.isfinite(speed_kmh) or speed_kmh <= 0:
        raise ValueError("speed_kmh must be a finite positive number")
    parsed_include_path = _query_boolean(query, "include_path")
    if include_path is not None:
        parsed_include_path = include_path
    return {
        "departure": parse_departure(_query_first(query, "departure")),
        "speed_kmh": speed_kmh,
        "include_path": parsed_include_path,
        "include_ferries": _query_boolean(query, "include_ferries"),
        "objective": validate_route_objective(_query_first(query, "objective")),
        "vehicle_weight_t": validate_vehicle_weight_t(_query_first(query, "weight_t")),
        "vehicle_rating_t": validate_vehicle_rating_t(_query_first(query, "rating_t")),
        "vehicle_height_m": validate_vehicle_height_m(_query_first(query, "height_m")),
        "vehicle_width_m": validate_vehicle_width_m(_query_first(query, "width_m")),
        "vehicle_length_m": validate_vehicle_length_m(_query_first(query, "length_m")),
        "vehicle_axleload_t": validate_vehicle_axleload_t(_query_first(query, "axleload_t")),
        "vehicle_class": validate_vehicle_class(_query_first(query, "vehicle_class")),
        "allow_hgv_destination": _query_boolean(query, "allow_hgv_destination"),
    }


def _parse_matrix_point(value: str, name: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"{name} must use lat,lon")
    return (
        _coordinate(parts[0], f"{name} latitude", -90.0, 90.0),
        _coordinate(parts[1], f"{name} longitude", -180.0, 180.0),
    )


def _json_point(value: object, name: str) -> tuple[float, float]:
    """Parse one structured JSON point with exact lat/lon members."""
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object with lat and lon")
    if set(value) != {"lat", "lon"}:
        raise ValueError(f"{name} must contain only lat and lon")
    lat = value.get("lat")
    lon = value.get("lon")
    if isinstance(lat, bool) or not isinstance(lat, (int, float)):
        raise TypeError(f"{name}.lat must be a finite number")
    if isinstance(lon, bool) or not isinstance(lon, (int, float)):
        raise TypeError(f"{name}.lon must be a finite number")
    return (
        _coordinate(str(lat), f"{name}.lat", -90.0, 90.0),
        _coordinate(str(lon), f"{name}.lon", -180.0, 180.0),
    )


def _json_matrix_points(payload: dict[str, object], name: str) -> list[tuple[float, float]]:
    """Parse a structured matrix point list from a JSON request body."""
    value = payload.get(name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    if len(value) > ROUTE_MATRIX_MAX_PAIRS:
        raise ValueError(f"{name} must contain no more than {ROUTE_MATRIX_MAX_PAIRS} points")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        points.append(_json_point(point, f"{name}[{index}]"))
    return points


def _reject_nonfinite_json(value: str) -> object:
    """Reject the non-standard NaN and Infinity constants accepted by json.loads."""
    raise ValueError(f"JSON number {value} is not finite")


def _json_route_options(
    payload: dict[str, object],
    *,
    include_vehicle_fields: bool = True,
    extra_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    """Normalize structured JSON route options through the GET validators."""
    allowed = {
        "speed_kmh",
        "objective",
        "departure",
        "include_path",
        "include_ferries",
        *extra_fields,
    }
    if include_vehicle_fields:
        allowed.update(
            {
                "weight_t",
                "rating_t",
                "height_m",
                "width_m",
                "length_m",
                "axleload_t",
                "vehicle_class",
                "allow_hgv_destination",
            }
        )
    unknown = sorted(set(payload) - allowed)
    if unknown:
        endpoint_name = "matrix" if include_vehicle_fields else "comparison"
        raise ValueError(f"unsupported route {endpoint_name} field: {unknown[0]}")
    query: dict[str, list[str]] = {}
    numeric_names = ["speed_kmh"]
    if include_vehicle_fields:
        numeric_names.extend(
            ["weight_t", "rating_t", "height_m", "width_m", "length_m", "axleload_t"]
        )
    for name in numeric_names:
        value = payload.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a finite number")
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number")
        query[name] = [str(value)]
    string_names = ["objective", "departure"]
    if include_vehicle_fields:
        string_names.insert(0, "vehicle_class")
    for name in string_names:
        value = payload.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        query[name] = [value]
    bool_names = ["include_path", "include_ferries"]
    if include_vehicle_fields:
        bool_names.insert(0, "allow_hgv_destination")
    for name in bool_names:
        value = payload.get(name)
        if value is None:
            continue
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
        query[name] = ["1" if value else "0"]
    return _parse_route_options(query)


def _json_query_request(payload: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a structured analysis-query request body."""
    allowed = {
        "backend",
        "limit",
        "offset",
        "osm_id",
        "group",
        "is_control",
        "min_score",
        "after_score",
        "after_osm_id",
        "after_invalid_osm_id",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unsupported query field: {unknown[0]}")

    normalized: dict[str, object] = {}
    for name in ("backend", "osm_id", "group", "is_control", "after_osm_id", "after_invalid_osm_id"):
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        normalized[name] = value
    backend = normalized.get("backend")
    if backend is not None and backend not in {"auto", *QUERY_BACKEND_NAMES}:
        raise ValueError(
            f"backend must be auto or one of: {', '.join(QUERY_BACKEND_NAMES)}"
        )

    for name in ("limit", "offset"):
        if name not in payload:
            continue
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        normalized[name] = value
    for name in ("min_score", "after_score"):
        if name not in payload:
            continue
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a finite number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{name} must be finite")
        normalized[name] = number
    return normalized


def _json_report_request(
    payload: dict[str, object], *, kind: str
) -> dict[str, object]:
    """Validate and normalize a structured report page/export request body."""
    filter_fields = {
        "q",
        "group",
        "century",
        "rating",
        "type",
        "county",
        "review",
        "pattern",
        "culture",
        "score",
        "angle",
        "ratio",
        "circular",
        "multi",
        "sort",
        "desc",
    }
    page_fields = {"limit", "offset", "initial"}
    export_fields = {"format"}
    allowed = filter_fields | (page_fields if kind == "page" else export_fields)
    if kind not in {"page", "export"}:
        raise ValueError(f"unsupported report request kind: {kind}")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unsupported report {kind} field: {unknown[0]}")

    normalized: dict[str, object] = {}
    for name in (
        "q",
        "group",
        "century",
        "rating",
        "type",
        "county",
        "review",
        "pattern",
        "culture",
        "sort",
    ):
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        if name == "culture" and value not in {"named", "heritage", "pobal", "civic"}:
            raise ValueError("culture must be named, heritage, pobal, or civic")
        if name == "sort" and value not in REPORT_FILTER_SORT_KEYS:
            raise ValueError(f"sort must be one of: {', '.join(REPORT_FILTER_SORT_KEYS)}")
        normalized[name] = value
    if "score" in payload:
        value = payload["score"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("score must be a finite number")
        score = float(value)
        if not math.isfinite(score):
            raise ValueError("score must be finite")
        normalized["score"] = score
    for name in ("angle", "ratio", "circular", "multi", "desc"):
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
        normalized[name] = value
    if kind == "page":
        for name in ("limit", "offset"):
            if name not in payload:
                continue
            value = payload[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            normalized[name] = value
        if "initial" in payload:
            value = payload["initial"]
            if not isinstance(value, bool):
                raise TypeError("initial must be a boolean")
            normalized["initial"] = value
    else:
        format_value = payload.get("format")
        if not isinstance(format_value, str):
            raise TypeError("format must be a string")
        if format_value not in {"csv", "geojson"}:
            raise ValueError("format must be csv or geojson")
        normalized["format"] = format_value
    return normalized


def _json_comparison_request(
    payload: dict[str, object],
) -> tuple[float, float, float, float, list[object], dict[str, object]]:
    """Parse a structured route-comparison body."""
    start_lat, start_lon = _json_point(payload.get("start"), "start")
    goal_lat, goal_lon = _json_point(payload.get("goal"), "goal")
    profiles_value = payload.get("profiles")
    if not isinstance(profiles_value, list):
        raise TypeError("profiles must be an array")
    if len(profiles_value) < ROUTE_COMPARISON_MIN_PROFILES:
        raise ValueError(
            f"at least {ROUTE_COMPARISON_MIN_PROFILES} route profiles are required"
        )
    if len(profiles_value) > ROUTE_COMPARISON_MAX_PROFILES:
        raise ValueError(
            f"route profile count must not exceed {ROUTE_COMPARISON_MAX_PROFILES}"
        )
    profiles: list[object] = []
    for index, profile in enumerate(profiles_value):
        if not isinstance(profile, str):
            raise TypeError(f"profiles[{index}] must be a string")
        profiles.append(parse_route_profile_spec(profile))
    options = _json_route_options(
        payload,
        include_vehicle_fields=False,
        extra_fields=("start", "goal", "profiles"),
    )
    return start_lat, start_lon, goal_lat, goal_lon, profiles, options


def _json_route_request(
    payload: dict[str, object],
) -> tuple[float, float, float, float, str, dict[str, object]]:
    """Parse a structured single-trip route body."""
    start_lat, start_lon = _json_point(payload.get("start"), "start")
    goal_lat, goal_lon = _json_point(payload.get("goal"), "goal")
    format_value = payload.get("format", "json")
    if not isinstance(format_value, str):
        raise TypeError("format must be a string")
    if format_value not in {"json", "geojson"}:
        raise ValueError("format must be json or geojson")
    options = _json_route_options(payload, extra_fields=("start", "goal", "format"))
    if format_value == "geojson":
        options["include_path"] = True
    return start_lat, start_lon, goal_lat, goal_lon, format_value, options


def openapi_document() -> dict[str, object]:
    """Return the versioned, dependency-free schema for the local read API."""
    report_parameters = [
        _openapi_query_parameter("q", {"type": "string"}, description="Case-insensitive target search."),
        _openapi_query_parameter("group", {"type": "string"}, description="Exact target group."),
        _openapi_query_parameter("century", {"type": "string"}, description="Exact NIAH century filter."),
        _openapi_query_parameter("rating", {"type": "string"}, description="Exact NIAH rating filter."),
        _openapi_query_parameter("type", {"type": "string"}, description="Exact NIAH type filter."),
        _openapi_query_parameter("county", {"type": "string"}, description="Exact spatial or NIAH county context filter."),
        _openapi_query_parameter("review", {"type": "string"}, description="Review queue state."),
        _openapi_query_parameter("pattern", {"type": "string"}, description="Require a named geometric pattern flag."),
        _openapi_query_parameter(
            "culture",
            {"type": "string", "enum": ["named", "heritage", "pobal", "civic"]},
            description="Data-derived cultural lens: named places, heritage joins, shared life, or civic institutions.",
        ),
        _openapi_query_parameter("score", {"type": "number", "format": "double"}, description="Finite inclusive minimum score."),
        _openapi_query_parameter("angle", {"type": "boolean"}, description="Require a golden-angle flag."),
        _openapi_query_parameter("ratio", {"type": "boolean"}, description="Require a golden-ratio flag."),
        _openapi_query_parameter("circular", {"type": "boolean"}, description="Require a circularity flag."),
        _openapi_query_parameter("multi", {"type": "boolean"}, description="Require multipart or repaired geometry."),
        _openapi_query_parameter("sort", {"type": "string", "enum": list(REPORT_FILTER_SORT_KEYS)}, description="Stable sort key."),
        _openapi_query_parameter("desc", {"type": "boolean", "default": True}, description="Sort descending when true."),
    ]
    report_page_parameters = [
        *report_parameters,
        _openapi_query_parameter("limit", {"type": "integer", "minimum": 1, "maximum": REPORT_PAGE_MAX_LIMIT, "default": REPORT_PAGE_DEFAULT_LIMIT}, description="Rows in one page."),
        _openapi_query_parameter("offset", {"type": "integer", "minimum": 0, "maximum": QUERY_MAX_OFFSET, "default": 0}, description="Zero-based page offset."),
        _openapi_query_parameter("initial", {"type": "boolean"}, description="Include compact static report sections."),
    ]
    report_filter_properties = {
        "q": {"type": "string"},
        "group": {"type": "string"},
        "century": {"type": "string"},
        "rating": {"type": "string"},
        "type": {"type": "string"},
        "county": {"type": "string"},
        "review": {"type": "string"},
        "pattern": {"type": "string"},
        "culture": {
            "type": "string",
            "enum": ["named", "heritage", "pobal", "civic"],
        },
        "score": {"type": "number", "format": "double"},
        "angle": {"type": "boolean"},
        "ratio": {"type": "boolean"},
        "circular": {"type": "boolean"},
        "multi": {"type": "boolean"},
        "sort": {"type": "string", "enum": list(REPORT_FILTER_SORT_KEYS)},
        "desc": {"type": "boolean", "default": True},
    }
    report_page_request_schema = {
        "type": "object",
        "properties": {
            **report_filter_properties,
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": REPORT_PAGE_MAX_LIMIT,
                "default": REPORT_PAGE_DEFAULT_LIMIT,
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "maximum": QUERY_MAX_OFFSET,
                "default": 0,
            },
            "initial": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    report_export_request_schema = {
        "type": "object",
        "required": ["format"],
        "properties": {
            **report_filter_properties,
            "format": {"type": "string", "enum": ["csv", "geojson"]},
        },
        "additionalProperties": False,
    }
    document = {
        "openapi": "3.1.0",
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "info": {
            "title": "Ireland Geometry local report API",
            "version": package_version(),
            "description": (
                "Read-only local endpoints for report health, build metadata, "
                "analysis queries, and point-to-point routing."
                " A compact interpretation endpoint is available for automation."
            ),
        },
        "servers": [{"url": "/"}],
        "x-contract": OPENAPI_CONTRACT,
        "x-response-contracts": dict(API_CONTRACTS),
        "paths": {
            HEALTH_PATH: {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Return report and backend readiness",
                    "responses": {"200": _openapi_json_response("HealthResponse", "Health status", conditional=True)},
                }
            },
            CAPABILITIES_API_PATH: {
                "get": {
                    "operationId": "getCapabilities",
                    "summary": "Discover available local API capabilities",
                    "responses": {"200": _openapi_json_response("CapabilitiesResponse", "Capability inventory", conditional=True)},
                }
            },
            OPENAPI_PATH: {
                "get": {
                    "operationId": "getOpenApiDocument",
                    "summary": "Return this OpenAPI document",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document",
                            "headers": {
                                **_openapi_request_id_header(),
                                **_openapi_security_headers(),
                                **_openapi_request_timing_header(),
                                **_openapi_conditional_headers(),
                            },
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    },
                }
            },
            METADATA_API_PATH: {
                "get": {
                    "operationId": "getMetadata",
                    "summary": "Return compact build and export metadata",
                    "responses": {
                        "200": _openapi_json_response("MetadataResponse", "Build metadata", conditional=True),
                    },
                }
            },
            INTERPRETATION_API_PATH: {
                "get": {
                    "operationId": "getInterpretation",
                    "summary": "Return compact data-derived findings",
                    "responses": {
                        "200": _openapi_json_response(
                            "InterpretationResponse", "Compact interpretation sidecar", conditional=True
                        ),
                        "404": _openapi_error_response("Interpretation sidecar is not available"),
                        "500": _openapi_error_response("Interpretation sidecar is invalid"),
                    },
                }
            },
            REPORT_PAGE_API_PATH: {
                "get": {
                    "operationId": "getReportPage",
                    "summary": "Read a bounded, filtered report target page",
                    "parameters": report_page_parameters,
                    "responses": {
                        "200": _openapi_json_response("ReportPageResponse", "Report target page", conditional=True),
                        "400": _openapi_error_response("Invalid report page parameters"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "500": _openapi_error_response("Report data pack is invalid"),
                    },
                },
                "post": {
                    "operationId": "getReportPageJson",
                    "summary": "Read a bounded, filtered report target page from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ReportPageRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": _openapi_json_response("ReportPageResponse", "Report target page", conditional=True),
                        "400": _openapi_error_response("Invalid report page JSON body"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "413": _openapi_error_response("Report page JSON body is too large"),
                        "415": _openapi_error_response("Unsupported report page request media type"),
                        "500": _openapi_error_response("Report data pack is invalid"),
                    },
                },
            },
            REPORT_RUNTIME_API_PATH: {
                "get": {
                    "operationId": "getReportRuntime",
                    "summary": "Return current report analytical readiness",
                    "responses": {
                        "200": _openapi_json_response(
                            "ReportRuntime", "Current report runtime readiness", conditional=True
                        ),
                    },
                }
            },
            REPORT_EXPORT_API_PATH: {
                "get": {
                    "operationId": "exportFilteredReport",
                    "summary": "Export the current report filter as CSV or GeoJSON",
                    "parameters": [
                        *report_parameters,
                        _openapi_query_parameter("format", {"type": "string", "enum": ["csv", "geojson"]}, description="Export format.", required=True),
                    ],
                    "responses": {
                        "200": {
                            "description": "Filtered CSV or GeoJSON export",
                            "headers": {
                                **_openapi_request_id_header(),
                                **_openapi_security_headers(),
                                **_openapi_request_timing_header(),
                                **_openapi_conditional_headers(),
                                REPORT_RUNTIME_HEADER_NAMES["contract"]: {
                                    "description": "Runtime readiness contract for the exported report pack.",
                                    "schema": {
                                        "type": "string",
                                        "const": REPORT_RUNTIME_CONTRACT,
                                    },
                                },
                                REPORT_RUNTIME_HEADER_NAMES["status"]: {
                                    "description": "Current runtime status: pass, incomplete, or fail.",
                                    "schema": {
                                        "type": "string",
                                        "enum": ["pass", "incomplete", "fail"],
                                    },
                                },
                                REPORT_RUNTIME_HEADER_NAMES["analysis_ready"]: {
                                    "description": "Whether analytical validation and manifest alignment both pass.",
                                    "schema": {"type": "string", "enum": ["true", "false"]},
                                },
                                REPORT_RUNTIME_HEADER_NAMES["validation"]: {
                                    "description": "Aggregated validation-record status.",
                                    "schema": {
                                        "type": "string",
                                        "enum": ["pass", "incomplete", "fail", "not_provided"],
                                    },
                                },
                                REPORT_RUNTIME_HEADER_NAMES["manifest_alignment"]: {
                                    "description": "Current output-manifest byte/hash alignment status.",
                                    "schema": {
                                        "type": "string",
                                        "enum": ["pass", "incomplete", "fail", "not_provided"],
                                    },
                                },
                                REPORT_RUNTIME_HEADER_NAMES["source_alignment"]: {
                                    "description": "Current input-source metadata alignment status.",
                                    "schema": {
                                        "type": "string",
                                        "enum": ["pass", "incomplete", "fail", "not_provided"],
                                    },
                                },
                            },
                            "content": {
                                "text/csv": {"schema": {"type": "string"}},
                                "application/geo+json": {"schema": {"$ref": "#/components/schemas/GeoJSONFeatureCollection"}},
                            },
                        },
                        "400": _openapi_error_response("Invalid export parameters"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "500": _openapi_error_response("Report data pack is invalid"),
                    },
                },
                "post": {
                    "operationId": "exportFilteredReportJson",
                    "summary": "Export the current report filter from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ReportExportRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Filtered CSV or GeoJSON export",
                            "headers": {
                                **_openapi_request_id_header(),
                                **_openapi_security_headers(),
                                **_openapi_request_timing_header(),
                                **_openapi_conditional_headers(),
                                **{
                                    name: {"schema": {"type": "string"}}
                                    for name in REPORT_RUNTIME_HEADER_NAMES.values()
                                },
                            },
                            "content": {
                                "text/csv": {"schema": {"type": "string"}},
                                "application/geo+json": {
                                    "schema": {"$ref": "#/components/schemas/GeoJSONFeatureCollection"}
                                },
                            },
                        },
                        "400": _openapi_error_response("Invalid export JSON body"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "413": _openapi_error_response("Report export JSON body is too large"),
                        "415": _openapi_error_response("Unsupported report export request media type"),
                        "500": _openapi_error_response("Report data pack is invalid"),
                    },
                },
            },
            QUERY_API_PATH: {
                "get": {
                    "operationId": "queryAnalysisRows",
                    "summary": "Read a bounded, ordered analysis page",
                    "parameters": [
                        _openapi_query_parameter(
                            "backend",
                            {"type": "string", "enum": ["auto", *QUERY_BACKEND_NAMES]},
                            description="Preferred export backend; auto failover is supported.",
                        ),
                        _openapi_query_parameter(
                            "limit",
                            {"type": "integer", "minimum": 1, "maximum": QUERY_MAX_LIMIT, "default": 100},
                            description="Number of rows to return.",
                        ),
                        _openapi_query_parameter(
                            "offset",
                            {"type": "integer", "minimum": 0, "maximum": QUERY_MAX_OFFSET, "default": 0},
                            description="Bounded offset page; mutually exclusive with cursors.",
                        ),
                        _openapi_query_parameter("osm_id", {"type": "string"}, description="Exact OSM ID."),
                        _openapi_query_parameter("group", {"type": "string"}, description="Exact target group."),
                        _openapi_query_parameter(
                            "is_control",
                            {"type": "string", "enum": ["target", "control", "0", "1"]},
                            description="Select target or control rows.",
                        ),
                        _openapi_query_parameter(
                            "min_score",
                            {"type": "number", "format": "double"},
                            description="Finite inclusive minimum score.",
                        ),
                        _openapi_query_parameter(
                            "after_score",
                            {"type": "number", "format": "double"},
                            description="Finite-score cursor component.",
                        ),
                        _openapi_query_parameter(
                            "after_osm_id",
                            {"type": "string"},
                            description="OSM ID cursor component.",
                        ),
                        _openapi_query_parameter(
                            "after_invalid_osm_id",
                            {"type": "string"},
                            description="Cursor for malformed/non-finite score rows.",
                        ),
                    ],
                    "responses": {
                        "200": _openapi_json_response("QueryResponse", "Analysis rows", conditional=True),
                        "400": _openapi_error_response("Invalid query parameters"),
                        "503": _openapi_error_response("No readable query backend"),
                    },
                },
                "post": {
                    "operationId": "queryAnalysisRowsJson",
                    "summary": "Read a bounded, ordered analysis page from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/QueryRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": _openapi_json_response("QueryResponse", "Analysis rows", conditional=True),
                        "400": _openapi_error_response("Invalid query JSON body"),
                        "413": _openapi_error_response("Query JSON body is too large"),
                        "415": _openapi_error_response("Unsupported query request media type"),
                        "503": _openapi_error_response("No readable query backend"),
                    },
                },
            },
            ROUTE_API_PATH: {
                "get": {
                    "operationId": "queryRoute",
                    "summary": "Route between two WGS84 coordinates",
                    "parameters": [
                        _openapi_query_parameter(
                            "start_lat",
                            {"type": "number", "format": "double", "minimum": -90, "maximum": 90},
                            description="Start latitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "start_lon",
                            {"type": "number", "format": "double", "minimum": -180, "maximum": 180},
                            description="Start longitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "goal_lat",
                            {"type": "number", "format": "double", "minimum": -90, "maximum": 90},
                            description="Goal latitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "goal_lon",
                            {"type": "number", "format": "double", "minimum": -180, "maximum": 180},
                            description="Goal longitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "speed_kmh",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0, "default": 50},
                            description=(
                                "Positive fallback routing speed in km/h; supported numeric "
                                "OSM maxspeed ceilings are applied per way on v21 SQLite graphs; "
                                "safely supported maxspeed:conditional windows are evaluated "
                                "when departure is supplied; supported conditional one-way "
                                "windows use the same departure profile."
                            ),
                        ),
                        _openapi_query_parameter(
                            "weight_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional vehicle weight profile in metric tonnes; activates "
                                "weight-based conditional turn restrictions and generic "
                                "numeric maxweight limits on SQLite road graphs; the hgv "
                                "profile also applies numeric maxweight:hgv limits."
                            ),
                        ),
                        _openapi_query_parameter(
                            "rating_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional HGV permitted gross-weight rating in metric tonnes; "
                                "activates maxweightrating:hgv limits and the Irish "
                                "maxweightrating:goods alias when vehicle_class=hgv. "
                                "This is distinct from the actual weight_t profile."
                            ),
                        ),
                        _openapi_query_parameter(
                            "height_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional vehicle height profile in metres; activates numeric "
                                "legal and physical maxheight limits on SQLite road graphs."
                            ),
                        ),
                        _openapi_query_parameter(
                            "width_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional vehicle width profile in metres; activates numeric "
                                "maxwidth limits on SQLite road graphs."
                            ),
                        ),
                        _openapi_query_parameter(
                            "length_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional vehicle length profile in metres; activates numeric "
                                "maxlength limits on SQLite road graphs."
                            ),
                        ),
                        _openapi_query_parameter(
                            "axleload_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description=(
                                "Optional vehicle axle-load profile in metric tonnes; activates "
                                "numeric maxaxleload limits on SQLite road graphs."
                            ),
                        ),
                        _openapi_query_parameter(
                            "vehicle_class",
                            {"type": "string", "enum": list(VEHICLE_CLASSES), "default": "general"},
                            description=(
                                "Vehicle-class profile for conditional access; delivery activates "
                                "OSM delivery-only windows, psv activates public-service-vehicle "
                                "windows, hgv activates heavy-goods-vehicle limits/windows, and "
                                "taxi activates taxi-specific windows on SQLite road graphs."
                            ),
                        ),
                        _openapi_query_parameter(
                            "allow_hgv_destination",
                            {"type": "boolean", "default": False},
                            description=(
                                "For vehicle_class=hgv, explicitly allow static hgv=destination "
                                "destination-only ways and supported "
                                "ways and supported none @ destination exceptions to HGV "
                                "weight/rating limits. Use only when the route serves the destination."
                            ),
                        ),
                        _openapi_query_parameter(
                            "objective",
                            {"type": "string", "enum": list(ROUTE_OBJECTIVES), "default": "distance"},
                            description=(
                                "Optimize shortest physical distance (distance) or fastest "
                                "estimated duration (duration)."
                            ),
                        ),
                        _openapi_query_parameter(
                            "departure",
                            {"type": "string", "format": "date-time"},
                            description="Optional ISO-8601 departure for conditional profiles.",
                        ),
                        _openapi_query_parameter(
                            "include_path",
                            {"type": "boolean", "default": False},
                            description=(
                                "Include reconstructed path node IDs, GeoJSON-order "
                                "coordinates, ordered directed way/segment records, "
                                "per-segment metrics, persisted edge constraints, "
                                "and conditional-rule evaluations."
                            ),
                        ),
                        _openapi_query_parameter(
                            "include_ferries",
                            {"type": "boolean", "default": False},
                            description=(
                                "Include ferry geometry when available. Portable graphs expose "
                                "basic ferry edges without service windows, next-opening waits, or "
                                "crossing durations; "
                                "SQLite companion contracts apply those timing semantics when present."
                            ),
                        ),
                        _openapi_query_parameter(
                            "format",
                            {"type": "string", "enum": ["json", "geojson"], "default": "json"},
                            description="Return JSON route metadata or a GeoJSON Feature.",
                        ),
                    ],
                    "responses": {
                        "200": {
                            "description": "Route metadata or GeoJSON path",
                            "headers": {
                                **_openapi_request_id_header(),
                                **_openapi_security_headers(),
                                **_openapi_request_timing_header(),
                                **_openapi_conditional_headers(),
                            },
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RouteResponse"}
                                },
                                "application/geo+json": {
                                    "schema": {"$ref": "#/components/schemas/GeoJSONFeature"}
                                },
                            },
                        },
                        "400": _openapi_error_response("Invalid route parameters"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                },
                "post": {
                    "operationId": "queryRouteJson",
                    "summary": "Route between two WGS84 coordinates from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RouteRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Route metadata or GeoJSON path",
                            "headers": {
                                **_openapi_request_id_header(),
                                **_openapi_security_headers(),
                                **_openapi_request_timing_header(),
                                **_openapi_conditional_headers(),
                            },
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RouteResponse"}
                                },
                                "application/geo+json": {
                                    "schema": {"$ref": "#/components/schemas/GeoJSONFeature"}
                                },
                            },
                        },
                        "400": _openapi_error_response("Invalid route JSON body"),
                        "413": _openapi_error_response("Route JSON body is too large"),
                        "415": _openapi_error_response("Route requires application/json"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                },
            },
            ROUTE_MATRIX_API_PATH: {
                "get": {
                    "operationId": "queryRouteMatrix",
                    "summary": "Route a bounded origin–destination matrix",
                    "parameters": [
                        _openapi_query_parameter(
                            "origin",
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": ROUTE_MATRIX_MAX_PAIRS,
                                "items": {"type": "string", "pattern": "^[-+0-9.eE]+,[-+0-9.eE]+$"},
                            },
                            description="Repeated origin point encoded as latitude,longitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "destination",
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": ROUTE_MATRIX_MAX_PAIRS,
                                "items": {"type": "string", "pattern": "^[-+0-9.eE]+,[-+0-9.eE]+$"},
                            },
                            description=(
                                "Repeated destination point encoded as latitude,longitude; "
                                f"the origin × destination matrix is limited to {ROUTE_MATRIX_MAX_PAIRS} pairs."
                            ),
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "speed_kmh",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0, "default": 50},
                            description="Positive fallback routing speed in km/h.",
                        ),
                        _openapi_query_parameter(
                            "weight_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional vehicle weight profile in metric tonnes.",
                        ),
                        _openapi_query_parameter(
                            "rating_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional HGV permitted gross-weight rating in metric tonnes.",
                        ),
                        _openapi_query_parameter(
                            "height_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional vehicle height profile in metres.",
                        ),
                        _openapi_query_parameter(
                            "width_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional vehicle width profile in metres.",
                        ),
                        _openapi_query_parameter(
                            "length_m",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional vehicle length profile in metres.",
                        ),
                        _openapi_query_parameter(
                            "axleload_t",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0},
                            description="Optional vehicle axle-load profile in metric tonnes.",
                        ),
                        _openapi_query_parameter(
                            "vehicle_class",
                            {"type": "string", "enum": list(VEHICLE_CLASSES), "default": "general"},
                            description="Vehicle-class profile for conditional access and restrictions.",
                        ),
                        _openapi_query_parameter(
                            "allow_hgv_destination",
                            {"type": "boolean", "default": False},
                            description="Allow destination-qualified HGV access when explicitly true.",
                        ),
                        _openapi_query_parameter(
                            "objective",
                            {"type": "string", "enum": list(ROUTE_OBJECTIVES), "default": "distance"},
                            description="Optimize physical distance or estimated duration.",
                        ),
                        _openapi_query_parameter(
                            "departure",
                            {"type": "string", "format": "date-time"},
                            description="Optional ISO-8601 departure for conditional profiles.",
                        ),
                        _openapi_query_parameter(
                            "include_path",
                            {"type": "boolean", "default": False},
                            description="Include full point-to-point route responses with path evidence in each pair.",
                        ),
                        _openapi_query_parameter(
                            "include_ferries",
                            {"type": "boolean", "default": False},
                            description=(
                                "Include ferry geometry when available. Portable graphs provide "
                                "geometry only; SQLite graphs may provide schedule-aware timing."
                            ),
                        ),
                    ],
                    "responses": {
                        "200": _openapi_json_response("RouteMatrixResponse", "Route matrix summaries", conditional=True),
                        "400": _openapi_error_response("Invalid route matrix parameters"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                },
                "post": {
                    "operationId": "queryRouteMatrixJson",
                    "summary": "Route a bounded origin–destination matrix from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RouteMatrixRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": _openapi_json_response("RouteMatrixResponse", "Route matrix summaries", conditional=True),
                        "400": _openapi_error_response("Invalid route matrix JSON body"),
                        "413": _openapi_error_response("Route matrix JSON body is too large"),
                        "415": _openapi_error_response("Route matrix requires application/json"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                }
            },
            ROUTE_COMPARISON_API_PATH: {
                "get": {
                    "operationId": "compareRouteProfiles",
                    "summary": "Compare bounded vehicle profiles over one route",
                    "parameters": [
                        _openapi_query_parameter(
                            "start_lat",
                            {"type": "number", "format": "double", "minimum": -90, "maximum": 90},
                            description="Trip origin latitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "start_lon",
                            {"type": "number", "format": "double", "minimum": -180, "maximum": 180},
                            description="Trip origin longitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "goal_lat",
                            {"type": "number", "format": "double", "minimum": -90, "maximum": 90},
                            description="Trip destination latitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "goal_lon",
                            {"type": "number", "format": "double", "minimum": -180, "maximum": 180},
                            description="Trip destination longitude.",
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "profile",
                            {
                                "type": "array",
                                "minItems": ROUTE_COMPARISON_MIN_PROFILES,
                                "maxItems": ROUTE_COMPARISON_MAX_PROFILES,
                                "items": {"type": "string"},
                            },
                            description=(
                                "Repeated NAME;key=value vehicle profile. Supported keys are "
                                "vehicle_class, weight_t, rating_t, height_m, width_m, "
                                "length_m, axleload_t, and allow_hgv_destination; the first "
                                "profile is the baseline."
                            ),
                            required=True,
                        ),
                        _openapi_query_parameter(
                            "speed_kmh",
                            {"type": "number", "format": "double", "exclusiveMinimum": 0, "default": 50},
                            description="Positive fallback routing speed in km/h shared by all profiles.",
                        ),
                        _openapi_query_parameter(
                            "objective",
                            {"type": "string", "enum": list(ROUTE_OBJECTIVES), "default": "distance"},
                            description="Optimize physical distance or estimated duration for all profiles.",
                        ),
                        _openapi_query_parameter(
                            "departure",
                            {"type": "string", "format": "date-time"},
                            description="Optional ISO-8601 departure shared by all profiles.",
                        ),
                        _openapi_query_parameter(
                            "include_path",
                            {"type": "boolean", "default": False},
                            description="Include each full point-to-point route response with path evidence.",
                        ),
                        _openapi_query_parameter(
                            "include_ferries",
                            {"type": "boolean", "default": False},
                            description=(
                                "Include ferry geometry when available. Portable graphs provide "
                                "geometry only; SQLite graphs may provide schedule-aware timing."
                            ),
                        ),
                    ],
                    "responses": {
                        "200": _openapi_json_response(
                            "RouteComparisonResponse", "Compared route profile summaries", conditional=True
                        ),
                        "400": _openapi_error_response("Invalid route comparison parameters"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                },
                "post": {
                    "operationId": "compareRouteProfilesJson",
                    "summary": "Compare bounded vehicle profiles from a JSON body",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/RouteComparisonRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _openapi_json_response(
                            "RouteComparisonResponse", "Compared route profile summaries", conditional=True
                        ),
                        "400": _openapi_error_response("Invalid route comparison JSON body"),
                        "413": _openapi_error_response("Route comparison JSON body is too large"),
                        "415": _openapi_error_response("Route comparison requires application/json"),
                        "500": _openapi_error_response("Routing failure"),
                        "503": _openapi_error_response("No supported road graph"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "ApiError": {
                    "type": "object",
                    "required": ["contract", "status", "error", "error_code", "request_id"],
                    "properties": {
                        "contract": {"type": "string"},
                        "status": {"type": "string"},
                        "error": {"type": "string"},
                        "error_code": {"type": "string"},
                        "request_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                        },
                    },
                    "additionalProperties": True,
                },
                "RoutingGraphCapability": {
                    "type": "object",
                    "required": [
                        "available",
                        "backend",
                        "format",
                        "metadata_status",
                        "profile_semantics",
                        "path_segment_sources",
                        "counts",
                        "features",
                    ],
                    "properties": {
                        "available": {"type": "boolean"},
                        "backend": {
                            "type": "string",
                            "enum": ["sqlite", "portable", "not_available"],
                        },
                        "format": {"type": ["string", "null"]},
                        "metadata_status": {
                            "type": "string",
                            "enum": ["available", "not_provided", "invalid"],
                        },
                        "profile_semantics": {
                            "type": "string",
                            "enum": ["full_sqlite", "portable_basic", "not_available"],
                        },
                        "path_segment_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "counts": {
                            "type": "object",
                            "required": ["node_n", "edge_n", "ferry_edge_n", "way_context_n"],
                            "properties": {
                                field: {"type": ["integer", "null"], "minimum": 0}
                                for field in ("node_n", "edge_n", "ferry_edge_n", "way_context_n")
                            },
                            "additionalProperties": False,
                        },
                        "features": {
                            "type": "object",
                            "required": [
                                "path_segment_explainability",
                                "way_context",
                                "vehicle_profiles",
                                "conditional_rules",
                                "turn_restrictions",
                                "ferry_geometry",
                                "ferry_schedules",
                            ],
                            "properties": {
                                "path_segment_explainability": {"type": "boolean"},
                                "way_context": {"type": "boolean"},
                                "vehicle_profiles": {"type": "boolean"},
                                "conditional_rules": {"type": "boolean"},
                                "turn_restrictions": {"type": "boolean"},
                                "ferry_geometry": {"type": "boolean"},
                                "ferry_schedules": {"type": "boolean"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "HealthResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "ready",
                        "analysis_ready",
                        "manifest_alignment",
                        "source_alignment",
                        "validation",
                        "routing_graph",
                    ],
                    "properties": {
                        "contract": {"const": HEALTH_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "degraded"]},
                        "ready": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "output_symlink_guard": {"type": "boolean", "const": True},
                        "manifest_alignment": {
                            "$ref": "#/components/schemas/ManifestAlignment"
                        },
                        "source_alignment": {
                            "$ref": "#/components/schemas/SourceAlignment"
                        },
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
                        "routing_graph": {"$ref": "#/components/schemas/RoutingGraphCapability"},
                        "interpretation_available": {"type": "boolean"},
                        "interpretation_endpoint": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "CapabilitiesResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "ready",
                        "analysis_ready",
                        "project_root",
                        "manifest_alignment",
                        "source_alignment",
                        "source_alignment_surfaces",
                        "conditional_endpoints",
                        "head_endpoints",
                        "api_json_gzip",
                        "api_json_gzip_min_bytes",
                        "api_error_contract",
                        "api_error_request_ids",
                        "api_error_request_id_header",
                        "api_request_logging",
                        "api_request_log_field",
                        "request_id_all_responses",
                        "request_id_all_response_logging",
                        "security_response_headers",
                        "api_request_timing",
                        "api_request_timing_header",
                        "api_request_timing_metric",
                        "api_request_timing_log_field",
                        "validation",
                        "routing_graph",
                        "contracts",
                        "endpoints",
                        "source_freshness",
                    ],
                    "properties": {
                        "contract": {"const": CAPABILITIES_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "degraded"]},
                        "ready": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "project_root": {"type": "string"},
                        "output_symlink_guard": {"type": "boolean", "const": True},
                        "manifest_alignment": {
                            "$ref": "#/components/schemas/ManifestAlignment"
                        },
                        "source_alignment": {
                            "$ref": "#/components/schemas/SourceAlignment"
                        },
                        "source_alignment_surfaces": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "conditional_endpoints": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "head_endpoints": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "api_json_gzip": {"type": "boolean", "const": True},
                        "api_json_gzip_min_bytes": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "api_error_contract": {"type": "string", "const": API_ERROR_CONTRACT},
                        "api_error_request_ids": {"type": "boolean", "const": True},
                        "api_error_request_id_header": {"type": "string", "const": REQUEST_ID_HEADER},
                        "api_request_logging": {"type": "boolean", "const": True},
                        "api_request_log_field": {"type": "string", "const": "request_id"},
                        "request_id_all_responses": {"type": "boolean", "const": True},
                        "request_id_all_response_logging": {"type": "boolean", "const": True},
                        "security_response_headers": {
                            "type": "object",
                            "required": list(SECURITY_RESPONSE_HEADERS),
                            "properties": {
                                name: {"type": "string", "const": value}
                                for name, value in SECURITY_RESPONSE_HEADERS.items()
                            },
                            "additionalProperties": False,
                        },
                        "api_request_timing": {"type": "boolean", "const": True},
                        "api_request_timing_header": {
                            "type": "string",
                            "const": REQUEST_TIMING_HEADER,
                        },
                        "api_request_timing_metric": {
                            "type": "string",
                            "const": REQUEST_TIMING_METRIC,
                        },
                        "api_request_timing_log_field": {
                            "type": "string",
                            "const": REQUEST_TIMING_LOG_FIELD,
                        },
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
                        "routing_graph": {
                            "$ref": "#/components/schemas/RoutingGraphCapability"
                        },
                        "contracts": {"type": "object", "additionalProperties": {"type": "string"}},
                        "endpoints": {"type": "object", "additionalProperties": True},
                        "source_freshness": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/SourceFreshness"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "additionalProperties": True,
                },
                "ValidationStatus": {
                    "type": "object",
                    "required": ["status", "passed", "manifest_available", "records"],
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pass", "incomplete", "fail", "not_provided"],
                        },
                        "passed": {"type": "boolean"},
                        "manifest_available": {"type": "boolean"},
                        "records": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": True,
                },
                "ManifestAlignment": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "passed",
                        "checked_count",
                        "manifest_artifact_count",
                        "actual_file_count",
                        "symlink_count",
                        "unlisted_count",
                        "errors",
                    ],
                    "properties": {
                        "contract": {"const": MANIFEST_ALIGNMENT_CONTRACT},
                        "status": {
                            "type": "string",
                            "enum": ["pass", "fail", "incomplete", "not_provided"],
                        },
                        "passed": {"type": "boolean"},
                        "checked_count": {"type": "integer", "minimum": 0},
                        "manifest_artifact_count": {"type": "integer", "minimum": 0},
                        "actual_file_count": {"type": "integer", "minimum": 0},
                        "symlink_count": {"type": "integer", "minimum": 0},
                        "unlisted_count": {"type": "integer", "minimum": 0},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
                "SourceAlignment": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "passed",
                        "mode",
                        "checked_count",
                        "unavailable_count",
                        "unhashed_count",
                        "hash_checked_count",
                        "metadata_checked_count",
                        "symlink_count",
                        "errors",
                    ],
                    "properties": {
                        "contract": {"const": SOURCE_ALIGNMENT_CONTRACT},
                        "status": {
                            "type": "string",
                            "enum": ["pass", "fail", "not_provided"],
                        },
                        "passed": {"type": "boolean"},
                        "mode": {"type": "string", "enum": ["metadata", "hash"]},
                        "checked_count": {"type": "integer", "minimum": 0},
                        "unavailable_count": {"type": "integer", "minimum": 0},
                        "unhashed_count": {"type": "integer", "minimum": 0},
                        "hash_checked_count": {"type": "integer", "minimum": 0},
                        "metadata_checked_count": {"type": "integer", "minimum": 0},
                        "symlink_count": {"type": "integer", "minimum": 0},
                        "errors": {"type": "array", "items": {"type": "string"}},
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symlink_paths": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "metadata_checked": {"type": "boolean"},
                                    "expected_metadata_sha256": {
                                        "type": ["string", "null"],
                                        "pattern": "^[0-9a-f]{64}$",
                                    },
                                    "actual_metadata_sha256": {
                                        "type": ["string", "null"],
                                        "pattern": "^[0-9a-f]{64}$",
                                    },
                                },
                                "additionalProperties": True,
                            },
                        },
                    },
                    "additionalProperties": True,
                },
                "MetadataResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "manifest_alignment",
                        "source_alignment",
                        "source_freshness",
                    ],
                    "properties": {
                        "contract": {"const": METADATA_CONTRACT},
                        "status": {"type": "string"},
                        "manifest_alignment": {
                            "$ref": "#/components/schemas/ManifestAlignment"
                        },
                        "source_alignment": {
                            "$ref": "#/components/schemas/SourceAlignment"
                        },
                        "source_freshness": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/SourceFreshness"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "additionalProperties": True,
                },
                "SourceFreshness": {
                    "type": "object",
                    "required": ["contract", "observed_at", "sources"],
                    "properties": {
                        "contract": {"const": SOURCE_FRESHNESS_CONTRACT},
                        "observed_at": {"type": "string", "format": "date-time"},
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": [
                                    "source_kind",
                                    "path",
                                    "path_base",
                                    "modified_at",
                                    "age_seconds",
                                ],
                                "properties": {
                                    "source_kind": {"type": "string"},
                                    "path": {"type": "string"},
                                    "path_base": {
                                        "type": "string",
                                        "enum": ["project_root", "external"],
                                    },
                                    "relative_path": {"type": "string"},
                                    "modified_at": {"type": "string", "format": "date-time"},
                                    "age_seconds": {"type": "number", "minimum": 0},
                                },
                                "additionalProperties": True,
                            },
                        },
                    },
                    "additionalProperties": True,
                },
                "InterpretationResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "available",
                        "analysis_ready",
                        "manifest_alignment",
                        "source_alignment",
                        "validation",
                        "interpretation",
                    ],
                    "properties": {
                        "contract": {"const": INTERPRETATION_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "not_provided", "error"]},
                        "available": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "manifest_alignment": {
                            "$ref": "#/components/schemas/ManifestAlignment"
                        },
                        "source_alignment": {
                            "$ref": "#/components/schemas/SourceAlignment"
                        },
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
                        "summary": {"type": "object", "additionalProperties": True},
                        "interpretation": {"type": ["object", "null"]},
                        "source": {"type": "string"},
                        "error": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "ReportPageRequest": report_page_request_schema,
                "ReportExportRequest": report_export_request_schema,
                "ReportPagePagination": {
                    "type": "object",
                    "required": [
                        "limit",
                        "offset",
                        "count",
                        "total",
                        "has_more",
                        "next_offset",
                        "sort_tiebreaker",
                        "matching_golden_angle",
                        "matching_niah",
                    ],
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": REPORT_PAGE_MAX_LIMIT,
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": QUERY_MAX_OFFSET,
                        },
                        "count": {"type": "integer", "minimum": 0},
                        "total": {"type": "integer", "minimum": 0},
                        "has_more": {"type": "boolean"},
                        "next_offset": {
                            "type": ["integer", "null"],
                            "minimum": 0,
                            "maximum": QUERY_MAX_OFFSET,
                        },
                        "sort_tiebreaker": {"const": REPORT_PAGE_SORT_TIEBREAKER},
                        "matching_golden_angle": {"type": "integer", "minimum": 0},
                        "matching_niah": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "ReportPageResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "paged",
                        "targets",
                        "page",
                        "filter_options",
                        "runtime",
                    ],
                    "properties": {
                        "contract": {"const": REPORT_PAGE_CONTRACT},
                        "status": {"type": "string", "const": "ok"},
                        "paged": {"type": "boolean", "const": True},
                        "initial": {"type": "boolean"},
                        "targets": {"type": "array", "items": {"type": "object"}},
                        "page": {"$ref": "#/components/schemas/ReportPagePagination"},
                        "filters": {"type": "object", "additionalProperties": True},
                        "filter_options": {"type": "object", "additionalProperties": True},
                        "runtime": {"$ref": "#/components/schemas/ReportRuntime"},
                    },
                    "additionalProperties": True,
                },
                "ReportRuntime": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "analysis_ready",
                        "validation",
                        "manifest_alignment",
                        "source_alignment",
                        "snapshot",
                    ],
                    "properties": {
                        "contract": {"const": REPORT_RUNTIME_CONTRACT},
                        "status": {"type": "string", "enum": ["pass", "incomplete", "fail"]},
                        "analysis_ready": {"type": "boolean"},
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
                        "manifest_alignment": {
                            "$ref": "#/components/schemas/ManifestAlignment"
                        },
                        "source_alignment": {
                            "$ref": "#/components/schemas/SourceAlignment"
                        },
                        "snapshot": {
                            "$ref": "#/components/schemas/ReportRuntimeSnapshot"
                        },
                    },
                    "additionalProperties": True,
                },
                "ReportRuntimeSnapshot": {
                    "type": "object",
                    "required": [
                        "available",
                        "generated_at",
                        "git_revision",
                        "git_dirty",
                        "package_version",
                        "schema_version",
                        "manifest_sha256",
                    ],
                    "properties": {
                        "available": {"type": "boolean"},
                        "generated_at": {"type": ["string", "null"]},
                        "git_revision": {"type": ["string", "null"]},
                        "git_dirty": {"type": ["boolean", "null"]},
                        "package_version": {"type": ["string", "null"]},
                        "schema_version": {"type": ["integer", "null"]},
                        "manifest_sha256": {
                            "type": ["string", "null"],
                            "pattern": "^[0-9a-f]{64}$",
                        },
                        "error": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "GeoJSONFeatureCollection": {
                    "type": "object",
                    "required": ["type", "features"],
                    "properties": {
                        "type": {"const": "FeatureCollection"},
                        "features": {"type": "array", "items": {"type": "object"}},
                        "contract": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "QueryRequest": {
                    "type": "object",
                    "properties": {
                        "backend": {
                            "type": "string",
                            "enum": ["auto", *QUERY_BACKEND_NAMES],
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": QUERY_MAX_LIMIT,
                            "default": 100,
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": QUERY_MAX_OFFSET,
                            "default": 0,
                        },
                        "osm_id": {"type": "string"},
                        "group": {"type": "string"},
                        "is_control": {
                            "type": "string",
                            "enum": ["target", "control", "0", "1"],
                        },
                        "min_score": {"type": "number", "format": "double"},
                        "after_score": {"type": "number", "format": "double"},
                        "after_osm_id": {"type": "string"},
                        "after_invalid_osm_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "QueryResponse": {
                    "type": "object",
                    "required": ["contract", "status", "count", "limit", "offset", "has_more", "rows"],
                    "properties": {
                        "contract": {"const": QUERY_CONTRACT},
                        "status": {"type": "string"},
                        "backend": {"type": "string", "enum": list(QUERY_BACKEND_NAMES)},
                        "count": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": QUERY_MAX_LIMIT},
                        "offset": {"type": "integer", "minimum": 0},
                        "has_more": {"type": "boolean"},
                        "next_offset": {"type": ["integer", "null"]},
                        "next_cursor": {"type": ["object", "null"]},
                        "rows": {"type": "array", "items": {"type": "object"}},
                    },
                    "additionalProperties": True,
                },
                "RouteRequest": {
                    "type": "object",
                    "required": ["start", "goal"],
                    "properties": {
                        "start": {
                            "type": "object",
                            "required": ["lat", "lon"],
                            "properties": {
                                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            },
                            "additionalProperties": False,
                        },
                        "goal": {
                            "type": "object",
                            "required": ["lat", "lon"],
                            "properties": {
                                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            },
                            "additionalProperties": False,
                        },
                        "speed_kmh": {"type": "number", "exclusiveMinimum": 0, "default": 50},
                        "weight_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "rating_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "height_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "width_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "length_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "axleload_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_class": {
                            "type": "string",
                            "enum": list(VEHICLE_CLASSES),
                            "default": "general",
                        },
                        "allow_hgv_destination": {"type": "boolean", "default": False},
                        "objective": {
                            "type": "string",
                            "enum": list(ROUTE_OBJECTIVES),
                            "default": "distance",
                        },
                        "departure": {"type": ["string", "null"], "format": "date-time"},
                        "include_path": {"type": "boolean", "default": False},
                        "include_ferries": {"type": "boolean", "default": False},
                        "format": {"type": "string", "enum": ["json", "geojson"], "default": "json"},
                    },
                    "additionalProperties": False,
                },
                "RouteResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "reachable",
                        "objective",
                        "vehicle_weight_t",
                        "vehicle_rating_t",
                        "vehicle_height_m",
                        "vehicle_width_m",
                        "vehicle_length_m",
                        "vehicle_axleload_t",
                        "vehicle_class",
                        "allow_hgv_destination",
                        "ferry_wait_s",
                        "ferry_wait_n",
                        "ferry_way_ids",
                        "ferry_distance_m",
                        "ferry_crossing_s",
                        "ferry_edge_n",
                    ],
                    "properties": {
                        "contract": {"const": ROUTE_CONTRACT},
                        "status": {"type": "string"},
                        "reachable": {"type": "boolean"},
                        "objective": {"type": "string", "enum": list(ROUTE_OBJECTIVES)},
                        "vehicle_weight_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_rating_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_height_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_width_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_length_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_axleload_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_class": {"type": "string", "enum": list(VEHICLE_CLASSES)},
                        "allow_hgv_destination": {"type": "boolean"},
                        "route_distance_m": {"type": ["number", "null"]},
                        "estimated_duration_s": {"type": ["number", "null"]},
                        "ferry_wait_s": {"type": "number", "minimum": 0},
                        "ferry_wait_n": {"type": "integer", "minimum": 0},
                        "ferry_way_ids": {"type": "array", "items": {"type": "string"}},
                        "ferry_distance_m": {"type": "number", "minimum": 0},
                        "ferry_crossing_s": {"type": "number", "minimum": 0},
                        "ferry_edge_n": {"type": "integer", "minimum": 0},
                        "path_node_n": {"type": "integer", "minimum": 0},
                        "path_node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "path_coordinates": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        },
                        "path_segment_n": {"type": "integer", "minimum": 0},
                        "path_segment_total_distance_m": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Sum of the per-segment physical distances in metres.",
                        },
                        "path_segment_total_duration_s": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Sum of per-segment estimated durations, including waits.",
                        },
                        "path_segment_total_wait_s": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Sum of per-segment service-wait components in seconds.",
                        },
                        "path_segment_source": {
                            "type": "string",
                            "enum": ["sqlite_edges", "portable_edges", "not_available"],
                            "description": (
                                "Whether ordered segment records came from SQLite "
                                "edges, portable CSV/JSON edges, or are unavailable "
                                "because the graph supplied no way IDs."
                            ),
                        },
                        "maneuver_n": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Number of geometry-derived maneuver records in a "
                                "path-enabled response."
                            ),
                        },
                        "maneuvers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": [
                                    "sequence",
                                    "kind",
                                    "node_id",
                                    "coordinate",
                                    "from_way_id",
                                    "to_way_id",
                                    "road_context",
                                    "bearing_before_deg",
                                    "bearing_after_deg",
                                    "turn_angle_deg",
                                    "distance_m",
                                    "duration_s",
                                    "wait_s",
                                ],
                                "properties": {
                                    "sequence": {"type": "integer", "minimum": 0},
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "start",
                                            "arrive",
                                            "continue",
                                            "change_way",
                                            "slight_left",
                                            "left",
                                            "slight_right",
                                            "right",
                                            "u_turn",
                                            "ferry_boarding",
                                            "ferry_landing",
                                        ],
                                    },
                                    "node_id": {"type": "string"},
                                    "coordinate": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 2,
                                        "maxItems": 2,
                                    },
                                    "from_way_id": {"type": ["string", "null"]},
                                    "to_way_id": {"type": ["string", "null"]},
                                    "road_context": {
                                        "type": "object",
                                        "required": [
                                            "name",
                                            "ref",
                                            "highway",
                                            "route",
                                            "oneway",
                                        ],
                                        "properties": {
                                            "name": {"type": ["string", "null"]},
                                            "ref": {"type": ["string", "null"]},
                                            "highway": {"type": ["string", "null"]},
                                            "route": {"type": ["string", "null"]},
                                            "oneway": {"type": ["string", "null"]},
                                        },
                                        "additionalProperties": False,
                                    },
                                    "bearing_before_deg": {"type": ["number", "null"]},
                                    "bearing_after_deg": {"type": ["number", "null"]},
                                    "turn_angle_deg": {"type": ["number", "null"]},
                                    "distance_m": {"type": "number", "minimum": 0},
                                    "duration_s": {"type": "number", "minimum": 0},
                                    "wait_s": {"type": "number", "minimum": 0},
                                },
                                "additionalProperties": False,
                            },
                            "description": (
                                "Geometry-derived start, arrival, turn, way-change, "
                                "and ferry-transition records. Distances and durations "
                                "cover the outgoing leg up to the next maneuver."
                            ),
                        },
                        "path_way_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Ordered way IDs, one per directed graph segment; "
                                "IDs may repeat when a way spans multiple segments."
                            ),
                        },
                        "path_segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": [
                                    "from_node",
                                    "to_node",
                                    "way_id",
                                    "distance_m",
                                    "duration_s",
                                    "wait_s",
                                    "ferry",
                                    "road_context",
                                    "constraints",
                                    "conditional_rules",
                                    "transition_rules",
                                ],
                                "properties": {
                                    "from_node": {"type": "string"},
                                    "to_node": {"type": "string"},
                                    "way_id": {"type": "string"},
                                    "distance_m": {"type": "number", "minimum": 0},
                                    "duration_s": {"type": "number", "minimum": 0},
                                    "wait_s": {"type": "number", "minimum": 0},
                                    "ferry": {"type": "boolean"},
                                    "road_context": {
                                        "type": "object",
                                        "required": [
                                            "name",
                                            "ref",
                                            "highway",
                                            "route",
                                            "oneway",
                                        ],
                                        "properties": {
                                            "name": {"type": ["string", "null"]},
                                            "ref": {"type": ["string", "null"]},
                                            "highway": {"type": ["string", "null"]},
                                            "route": {"type": ["string", "null"]},
                                            "oneway": {"type": ["string", "null"]},
                                        },
                                        "additionalProperties": False,
                                        "description": (
                                            "Persisted human-readable OSM way context. "
                                            "Legacy graphs may return null fields when "
                                            "the source tags were not persisted."
                                        ),
                                    },
                                    "constraints": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": [
                                                "key",
                                                "value",
                                                "unit",
                                                "status",
                                                "evaluated",
                                                "profile",
                                            ],
                                            "properties": {
                                                "key": {"type": "string"},
                                                "value": {
                                                    "type": ["number", "string", "null"]
                                                },
                                                "unit": {"type": ["string", "null"]},
                                                "status": {
                                                    "type": "string",
                                                    "enum": [
                                                        "supported",
                                                        "unlimited",
                                                        "unsupported",
                                                    ],
                                                },
                                                "evaluated": {"type": "boolean"},
                                                "profile": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                        "description": (
                                            "Static normalized OSM edge constraints. "
                                            "Each record identifies the source key and "
                                            "value/unit, retains the parser status, "
                                            "and states whether the supplied route "
                                            "profile evaluated it. Conditional turns "
                                            "and access rules are reported separately."
                                        ),
                                    },
                                    "conditional_rules": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": [
                                                "key",
                                                "value",
                                                "unit",
                                                "condition",
                                                "mode",
                                                "direction",
                                                "vehicle_class",
                                                "status",
                                                "evaluated",
                                                "active",
                                                "applied",
                                                "profile",
                                            ],
                                            "properties": {
                                                "key": {"type": "string"},
                                                "value": {
                                                    "type": ["number", "string", "null"]
                                                },
                                                "unit": {"type": ["string", "null"]},
                                                "condition": {"type": "string"},
                                                "mode": {"type": "string"},
                                                "direction": {"type": "string"},
                                                "vehicle_class": {"type": "string"},
                                                "status": {
                                                    "type": "string",
                                                    "enum": [
                                                        "supported",
                                                        "unlimited",
                                                        "unsupported",
                                                    ],
                                                },
                                                "evaluated": {"type": "boolean"},
                                                "active": {"type": ["boolean", "null"]},
                                                "applied": {"type": "boolean"},
                                                "profile": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                        "description": (
                                            "Conditional speed, one-way, and access "
                                            "rules attached to this traversal. Active "
                                            "and applied distinguish a rule that was "
                                            "in force from one merely retained on the "
                                            "source edge."
                                        ),
                                    },
                                    "transition_rules": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": [
                                                "key",
                                                "relation_id",
                                                "via_node",
                                                "from_way",
                                                "to_way",
                                                "via_way_ids",
                                                "kind",
                                                "condition",
                                                "status",
                                                "evaluated",
                                                "active",
                                                "applied",
                                                "selected",
                                                "profile",
                                            ],
                                            "properties": {
                                                "key": {
                                                    "type": "string",
                                                    "enum": [
                                                        "turn_restriction",
                                                        "conditional_turn_restriction",
                                                    ],
                                                },
                                                "relation_id": {"type": "string"},
                                                "via_node": {"type": "string"},
                                                "from_way": {"type": "string"},
                                                "to_way": {"type": "string"},
                                                "via_way_ids": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                                "kind": {
                                                    "type": "string",
                                                    "enum": ["no", "only"],
                                                },
                                                "condition": {"type": "string"},
                                                "status": {
                                                    "type": "string",
                                                    "enum": [
                                                        "supported",
                                                        "unsupported",
                                                    ],
                                                },
                                                "evaluated": {"type": "boolean"},
                                                "active": {"type": ["boolean", "null"]},
                                                "applied": {"type": "boolean"},
                                                "selected": {"type": "boolean"},
                                                "profile": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                        "description": (
                                            "Turn restrictions matched by the path "
                                            "transition entering this segment's from_node. "
                                            "Records retain relation and via-way identity, "
                                            "conditional evaluation state, and whether the "
                                            "chosen outgoing way was the relation target."
                                        ),
                                    },
                                },
                                "additionalProperties": False,
                            },
                            "description": (
                                "Ordered directed segment records. The tuple "
                                "(from_node, to_node, way_id) identifies a traversed "
                                "SQLite edge because the graph has no standalone edge ID. "
                                "Each record also carries physical distance, estimated "
                                "duration including wait, wait seconds, and ferry status."
                                " The road_context object exposes persisted OSM way "
                                "identity such as name, reference, highway class, "
                                "route type, and oneway state."
                                " The constraints array records static normalized OSM "
                                "edge limits and destination-qualified HGV metadata. "
                                "The conditional_rules array records entry-time "
                                "speed, one-way, and access decisions. The "
                                "transition_rules array records relation-level "
                                "no/only turn decisions at the segment entry. "
                                "The maneuvers array provides geometry-derived "
                                "turn and ferry-transition guidance when path details "
                                "are requested."
                            ),
                        },
                        "arrival": {"type": ["string", "null"], "format": "date-time"},
                    },
                    "additionalProperties": True,
                },
                "RouteMatrixRequest": {
                    "type": "object",
                    "required": ["origins", "destinations"],
                    "properties": {
                        "origins": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": ROUTE_MATRIX_MAX_PAIRS,
                            "items": {
                                "type": "object",
                                "required": ["lat", "lon"],
                                "properties": {
                                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "destinations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": ROUTE_MATRIX_MAX_PAIRS,
                            "items": {
                                "type": "object",
                                "required": ["lat", "lon"],
                                "properties": {
                                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "speed_kmh": {"type": "number", "exclusiveMinimum": 0, "default": 50},
                        "weight_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "rating_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "height_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "width_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "length_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "axleload_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_class": {"type": "string", "enum": list(VEHICLE_CLASSES), "default": "general"},
                        "allow_hgv_destination": {"type": "boolean", "default": False},
                        "objective": {"type": "string", "enum": list(ROUTE_OBJECTIVES), "default": "distance"},
                        "departure": {"type": ["string", "null"], "format": "date-time"},
                        "include_path": {"type": "boolean", "default": False},
                        "include_ferries": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                "RouteComparisonRequest": {
                    "type": "object",
                    "required": ["start", "goal", "profiles"],
                    "properties": {
                        "start": {
                            "type": "object",
                            "required": ["lat", "lon"],
                            "properties": {
                                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            },
                            "additionalProperties": False,
                        },
                        "goal": {
                            "type": "object",
                            "required": ["lat", "lon"],
                            "properties": {
                                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            },
                            "additionalProperties": False,
                        },
                        "profiles": {
                            "type": "array",
                            "minItems": ROUTE_COMPARISON_MIN_PROFILES,
                            "maxItems": ROUTE_COMPARISON_MAX_PROFILES,
                            "items": {"type": "string"},
                        },
                        "speed_kmh": {"type": "number", "exclusiveMinimum": 0, "default": 50},
                        "objective": {
                            "type": "string",
                            "enum": list(ROUTE_OBJECTIVES),
                            "default": "distance",
                        },
                        "departure": {"type": ["string", "null"], "format": "date-time"},
                        "include_path": {"type": "boolean", "default": False},
                        "include_ferries": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                "RouteMatrixResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "objective",
                        "vehicle_weight_t",
                        "vehicle_rating_t",
                        "vehicle_height_m",
                        "vehicle_width_m",
                        "vehicle_length_m",
                        "vehicle_axleload_t",
                        "vehicle_class",
                        "allow_hgv_destination",
                        "departure",
                        "speed_kmh",
                        "include_path",
                        "include_ferries",
                        "origin_n",
                        "destination_n",
                        "pair_n",
                        "reachable_n",
                        "unreachable_n",
                        "origins",
                        "destinations",
                        "pairs",
                        "graph",
                    ],
                    "properties": {
                        "contract": {"const": ROUTE_MATRIX_CONTRACT},
                        "status": {"type": "string", "const": "provided"},
                        "objective": {"type": "string", "enum": list(ROUTE_OBJECTIVES)},
                        "vehicle_weight_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_rating_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_height_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_width_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_length_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_axleload_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_class": {"type": "string", "enum": list(VEHICLE_CLASSES)},
                        "allow_hgv_destination": {"type": "boolean"},
                        "departure": {"type": ["string", "null"], "format": "date-time"},
                        "speed_kmh": {"type": "number", "exclusiveMinimum": 0},
                        "include_path": {"type": "boolean"},
                        "include_ferries": {"type": "boolean"},
                        "origin_n": {"type": "integer", "minimum": 1},
                        "destination_n": {"type": "integer", "minimum": 1},
                        "pair_n": {"type": "integer", "minimum": 1, "maximum": ROUTE_MATRIX_MAX_PAIRS},
                        "reachable_n": {"type": "integer", "minimum": 0},
                        "unreachable_n": {"type": "integer", "minimum": 0},
                        "origins": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["index", "lat", "lon"],
                                "properties": {
                                    "index": {"type": "integer", "minimum": 0},
                                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "destinations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["index", "lat", "lon"],
                                "properties": {
                                    "index": {"type": "integer", "minimum": 0},
                                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "pairs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": [
                                    "origin_index",
                                    "destination_index",
                                    "status",
                                    "reachable",
                                    "start",
                                    "goal",
                                    "route_distance_m",
                                    "estimated_duration_s",
                                    "ferry_wait_s",
                                    "ferry_wait_n",
                                    "ferry_distance_m",
                                    "ferry_crossing_s",
                                    "ferry_edge_n",
                                    "arrival",
                                    "route",
                                ],
                                "properties": {
                                    "origin_index": {"type": "integer", "minimum": 0},
                                    "destination_index": {"type": "integer", "minimum": 0},
                                    "status": {"type": "string"},
                                    "reachable": {"type": "boolean"},
                                    "start": {"type": "object", "additionalProperties": True},
                                    "goal": {"type": "object", "additionalProperties": True},
                                    "route_distance_m": {"type": ["number", "null"], "minimum": 0},
                                    "estimated_duration_s": {"type": ["number", "null"], "minimum": 0},
                                    "ferry_wait_s": {"type": "number", "minimum": 0},
                                    "ferry_wait_n": {"type": "integer", "minimum": 0},
                                    "ferry_distance_m": {"type": "number", "minimum": 0},
                                    "ferry_crossing_s": {"type": "number", "minimum": 0},
                                    "ferry_edge_n": {"type": "integer", "minimum": 0},
                                    "arrival": {"type": ["string", "null"], "format": "date-time"},
                                    "route": {
                                        "anyOf": [
                                            {"$ref": "#/components/schemas/RouteResponse"},
                                            {"type": "null"},
                                        ]
                                    },
                                },
                                "additionalProperties": False,
                            },
                        },
                        "graph": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": True,
                },
                "RouteComparisonResponse": {
                    "type": "object",
                    "required": [
                        "contract",
                        "status",
                        "start",
                        "goal",
                        "objective",
                        "departure",
                        "speed_kmh",
                        "include_path",
                        "include_ferries",
                        "baseline_profile",
                        "baseline_reachable",
                        "profile_n",
                        "reachable_n",
                        "unreachable_n",
                        "profiles",
                        "graph",
                    ],
                    "properties": {
                        "contract": {"const": ROUTE_COMPARISON_CONTRACT},
                        "status": {"type": "string", "const": "provided"},
                        "start": {"type": "object", "additionalProperties": True},
                        "goal": {"type": "object", "additionalProperties": True},
                        "objective": {"type": "string", "enum": list(ROUTE_OBJECTIVES)},
                        "departure": {"type": ["string", "null"], "format": "date-time"},
                        "speed_kmh": {"type": "number", "exclusiveMinimum": 0},
                        "include_path": {"type": "boolean"},
                        "include_ferries": {"type": "boolean"},
                        "baseline_profile": {"type": "string"},
                        "baseline_reachable": {"type": "boolean"},
                        "profile_n": {
                            "type": "integer",
                            "minimum": ROUTE_COMPARISON_MIN_PROFILES,
                            "maximum": ROUTE_COMPARISON_MAX_PROFILES,
                        },
                        "reachable_n": {"type": "integer", "minimum": 0},
                        "unreachable_n": {"type": "integer", "minimum": 0},
                        "profiles": {
                            "type": "array",
                            "minItems": ROUTE_COMPARISON_MIN_PROFILES,
                            "maxItems": ROUTE_COMPARISON_MAX_PROFILES,
                            "items": {"$ref": "#/components/schemas/RouteComparisonProfile"},
                        },
                        "graph": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": True,
                },
                "RouteComparisonProfile": {
                    "type": "object",
                    "required": [
                        "name",
                        "vehicle_weight_t",
                        "vehicle_rating_t",
                        "vehicle_height_m",
                        "vehicle_width_m",
                        "vehicle_length_m",
                        "vehicle_axleload_t",
                        "vehicle_class",
                        "allow_hgv_destination",
                        "status",
                        "reachable",
                        "route_distance_m",
                        "estimated_duration_s",
                        "ferry_wait_s",
                        "ferry_wait_n",
                        "ferry_distance_m",
                        "ferry_crossing_s",
                        "ferry_edge_n",
                        "arrival",
                        "delta_from_baseline",
                        "route",
                    ],
                    "properties": {
                        "name": {"type": "string"},
                        "vehicle_weight_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_rating_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_height_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_width_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_length_m": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_axleload_t": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "vehicle_class": {"type": "string", "enum": list(VEHICLE_CLASSES)},
                        "allow_hgv_destination": {"type": "boolean"},
                        "status": {"type": "string"},
                        "reachable": {"type": "boolean"},
                        "start": {"type": "object", "additionalProperties": True},
                        "goal": {"type": "object", "additionalProperties": True},
                        "route_distance_m": {"type": ["number", "null"]},
                        "estimated_duration_s": {"type": ["number", "null"]},
                        "ferry_wait_s": {"type": "number", "minimum": 0},
                        "ferry_wait_n": {"type": "integer", "minimum": 0},
                        "ferry_distance_m": {"type": "number", "minimum": 0},
                        "ferry_crossing_s": {"type": "number", "minimum": 0},
                        "ferry_edge_n": {"type": "integer", "minimum": 0},
                        "arrival": {"type": ["string", "null"], "format": "date-time"},
                        "delta_from_baseline": {
                            "type": "object",
                            "required": [
                                "route_distance_m",
                                "estimated_duration_s",
                                "ferry_wait_s",
                                "ferry_distance_m",
                                "ferry_crossing_s",
                            ],
                            "properties": {
                                "route_distance_m": {"type": ["number", "null"]},
                                "estimated_duration_s": {"type": ["number", "null"]},
                                "ferry_wait_s": {"type": ["number", "null"]},
                                "ferry_distance_m": {"type": ["number", "null"]},
                                "ferry_crossing_s": {"type": ["number", "null"]},
                            },
                            "additionalProperties": False,
                        },
                        "method": {"type": "string"},
                        "route": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/RouteResponse"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "additionalProperties": False,
                },
                "GeoJSONFeature": {
                    "type": "object",
                    "required": ["type", "geometry", "properties"],
                    "properties": {
                        "type": {"const": "Feature"},
                        "geometry": {"type": ["object", "null"]},
                        "properties": {"type": "object"},
                    },
                    "additionalProperties": True,
                },
            }
        },
    }
    return _openapi_with_head_operations(document)


def _openapi_with_head_operations(document: dict[str, object]) -> dict[str, object]:
    """Add bodyless header-inspection operations for every documented GET path."""
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return document
    for path, operations in paths.items():
        if not isinstance(path, str) or not isinstance(operations, dict):
            continue
        get_operation = operations.get("get")
        if not isinstance(get_operation, dict):
            continue
        head_operation = dict(get_operation)
        operation_name = path.strip("/").replace("/", "_").replace("-", "_")
        head_operation["operationId"] = f"head_{operation_name or 'root'}"
        head_operation["summary"] = "Inspect response headers without downloading the body"
        head_operation["description"] = (
            "HEAD uses the same query parameters and response headers as GET "
            "but returns no response body."
        )
        operations["head"] = head_operation
    return document


def _etag(body: bytes) -> str:
    """Return a strong, deterministic entity tag for a response body."""
    return f'"{hashlib.sha256(body).hexdigest()}"'


def _etag_matches(header: str | None, etag: str) -> bool:
    """Apply the GET/HEAD weak comparison rules used by If-None-Match."""
    if not header:
        return False
    expected = etag.removeprefix("W/")
    for candidate in header.split(","):
        token = candidate.strip()
        if token == "*":
            return True
        token = token.removeprefix("W/")
        if token == expected:
            return True
    return False


def _read_json_object(path: Path) -> tuple[dict[str, object] | None, str | None]:
    """Read a small generated JSON record without allowing malformed files to crash the server."""
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "record must be a JSON object"
    return payload, None


def _sha256_file(path: Path) -> str | None:
    """Hash a regular output file without following symlinks."""
    if path.is_symlink() or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _manifest_artifact_alignment(directory: Path) -> dict[str, object]:
    """Recheck current output bytes against the manifest artifact inventory."""
    manifest_path = directory / "manifest.json"
    base: dict[str, object] = {
        "contract": MANIFEST_ALIGNMENT_CONTRACT,
        "checked_count": 0,
        "manifest_artifact_count": 0,
        "actual_file_count": 0,
        "symlink_count": 0,
        "unlisted_count": 0,
        "errors": [],
    }
    if manifest_path.is_symlink():
        return {
            **base,
            "status": "fail",
            "passed": False,
            "errors": ["manifest.json is a symlink"],
        }
    manifest, error = _read_json_object(manifest_path)
    if error:
        return {
            **base,
            "status": "fail",
            "passed": False,
            "errors": [f"manifest.json is invalid: {error}"],
        }
    if manifest is None:
        status = "not_provided" if not manifest_path.exists() else "fail"
        message = "manifest.json is unavailable" if status == "not_provided" else (
            "manifest.json must be a JSON object"
        )
        return {**base, "status": status, "passed": False, "errors": [message]}

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return {
            **base,
            "status": "incomplete",
            "passed": False,
            "errors": ["manifest.json has no artifact inventory"],
        }

    errors: list[str] = []
    seen: set[str] = set()
    output_root = directory.resolve()
    checked_count = 0
    for index, record in enumerate(artifacts, 1):
        if not isinstance(record, dict):
            errors.append(f"manifest artifact {index} is not an object")
            continue
        relative = record.get("relative_path")
        if (
            record.get("path_base") != "output_dir"
            or not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
        ):
            errors.append(f"manifest artifact {index} has an unsafe relative path")
            continue
        posix_path = PurePosixPath(relative)
        if (
            posix_path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in posix_path.parts)
        ):
            errors.append(f"manifest artifact has an unsafe relative path: {relative}")
            continue
        if relative in seen:
            errors.append(f"manifest has a duplicate artifact: {relative}")
            continue
        seen.add(relative)
        candidate = directory.joinpath(*posix_path.parts)
        try:
            candidate.resolve().relative_to(output_root)
        except ValueError:
            errors.append(f"manifest artifact escapes output directory: {relative}")
            continue
        expected_bytes = record.get("bytes")
        expected_hash = record.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 0:
            errors.append(f"manifest artifact has an invalid byte count: {relative}")
            continue
        if not _is_sha256(expected_hash):
            errors.append(f"manifest artifact has an invalid SHA-256: {relative}")
            continue
        if not candidate.is_file() or candidate.is_symlink():
            errors.append(f"current output artifact is missing: {relative}")
            continue
        checked_count += 1
        if candidate.stat().st_size != expected_bytes:
            errors.append(f"current output byte count differs from manifest: {relative}")
        if _sha256_file(candidate) != expected_hash:
            errors.append(f"current output hash differs from manifest: {relative}")

    required = ("verification.json", "schema_validation.json", "reproducibility.json")
    errors.extend(
        f"manifest is missing a required validation artifact record: {name}"
        for name in required
        if name not in seen
    )
    symlinks = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_symlink()
    )
    errors.extend(f"current output contains a symlink: {relative}" for relative in symlinks)
    actual_files = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    allowed_unlisted = {"manifest.json"} | set(MANIFEST_EXCLUDED_OUTPUTS)
    unlisted = sorted(actual_files - seen - allowed_unlisted)
    errors.extend(f"current output contains an unlisted artifact: {relative}" for relative in unlisted)
    base.update(
        {
            "status": "pass" if not errors else "fail",
            "passed": not errors,
            "checked_count": checked_count,
            "manifest_artifact_count": len(artifacts),
            "actual_file_count": len(actual_files),
            "symlink_count": len(symlinks),
            "unlisted_count": len(unlisted),
            "errors": errors,
        }
    )
    return base


def _relative_file(out_dir: Path, value: str) -> Path:
    """Resolve a report path and reject paths outside the output directory."""
    candidate = (out_dir / value).resolve()
    try:
        candidate.relative_to(out_dir.resolve())
    except ValueError as exc:
        raise ValueError("--report must name a file inside --out-dir") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Report file does not exist: {candidate}")
    return candidate


def _output_symlink_paths(output: Path) -> list[Path]:
    """Return output-root or nested symlinks without following them."""
    if output.is_symlink():
        return [output]
    if not output.is_dir():
        return []
    return sorted(
        (path for path in output.rglob("*") if path.is_symlink()),
        key=lambda path: path.as_posix(),
    )


def _require_symlink_free_output(output: Path) -> None:
    """Fail closed before static serving can follow an output symlink."""
    symlinks = _output_symlink_paths(output)
    if not symlinks:
        return
    details = []
    for path in symlinks:
        try:
            details.append(str(path.relative_to(output)))
        except ValueError:
            details.append(str(path))
    raise ValueError("Output directory contains a symlink: " + ", ".join(details))


def _display_host(host: str) -> str:
    """Return a browser-friendly host for a listening address."""
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def report_url(server: ThreadingHTTPServer, report_name: str) -> str:
    """Build the URL for the selected report from a bound server."""
    host, port = server.server_address[:2]
    host_text = _display_host(str(host))
    if ":" in host_text and not host_text.startswith("["):
        host_text = f"[{host_text}]"
    return f"http://{host_text}:{port}/{quote(report_name)}"


def _query_backend_health(directory: Path) -> dict[str, dict[str, object]]:
    """Probe every supported query export without loading a result page."""
    return {name: probe_backend(directory, name) for name in QUERY_BACKEND_NAMES}


def _validation_status(directory: Path) -> dict[str, object]:
    """Summarize analytical validation independently from HTTP serving readiness."""
    records: dict[str, dict[str, object]] = {}
    for name in VALIDATION_RECORD_NAMES:
        payload, error = _read_json_object(directory / f"{name}.json")
        if error:
            status = "fail"
            passed = False
        elif payload is None:
            status = "not_provided"
            passed = False
        else:
            passed = payload.get("passed") is True
            status = "pass" if passed else "fail"
        records[name] = {
            "available": payload is not None,
            "status": status,
            "passed": passed,
            "error": error,
        }
    statuses = {record["status"] for record in records.values()}
    if statuses == {"pass"}:
        status = "pass"
    elif statuses & {"fail"}:
        status = "fail"
    elif "pass" in statuses:
        status = "incomplete"
    else:
        status = "not_provided"
    return {
        "status": status,
        "passed": status == "pass",
        "manifest_available": (directory / "manifest.json").is_file(),
        "records": records,
    }


def _report_runtime_snapshot(directory: Path) -> dict[str, object]:
    """Return stable identity metadata for the currently served build."""
    path = directory / "manifest.json"
    if path.is_symlink():
        return {
            "available": False,
            "generated_at": None,
            "git_revision": None,
            "git_dirty": None,
            "package_version": None,
            "schema_version": None,
            "manifest_sha256": None,
            "error": "manifest.json is a symlink",
        }
    payload, error = _read_json_object(path)
    if error:
        return {
            "available": False,
            "generated_at": None,
            "git_revision": None,
            "git_dirty": None,
            "package_version": None,
            "schema_version": None,
            "manifest_sha256": None,
            "error": f"manifest.json: {error}",
        }
    if payload is None:
        return {
            "available": False,
            "generated_at": None,
            "git_revision": None,
            "git_dirty": None,
            "package_version": None,
            "schema_version": None,
            "manifest_sha256": None,
            "error": "manifest.json is unavailable",
        }
    manifest_sha256 = _sha256_file(path)
    if manifest_sha256 is None:
        return {
            "available": False,
            "generated_at": payload.get("generated_at"),
            "git_revision": payload.get("git_revision"),
            "git_dirty": payload.get("git_dirty"),
            "package_version": payload.get("package_version"),
            "schema_version": payload.get("schema_version"),
            "manifest_sha256": None,
            "error": "manifest.json could not be hashed",
        }
    return {
        "available": True,
        "generated_at": payload.get("generated_at"),
        "git_revision": payload.get("git_revision"),
        "git_dirty": payload.get("git_dirty"),
        "package_version": payload.get("package_version"),
        "schema_version": payload.get("schema_version"),
        "manifest_sha256": manifest_sha256,
    }


def _report_source_alignment(
    directory: Path,
    data_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Return current input-source alignment without reading source payloads."""
    active_project_root = project_root or directory.parent
    manifest, _error = _read_json_object(directory / "manifest.json")
    if not isinstance(manifest, dict):
        return manifest_source_alignment(
            {},
            project_root=active_project_root,
            data_root=data_root or active_project_root / "data",
            out_dir=directory,
        )
    return manifest_source_alignment(
        manifest,
        project_root=active_project_root,
        data_root=data_root or active_project_root / "data",
        out_dir=directory,
    )


def _report_runtime_state(
    directory: Path,
    *,
    data_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Return the current analytical gate for server-backed report clients."""
    validation = _validation_status(directory)
    manifest_alignment = _manifest_artifact_alignment(directory)
    source_alignment = _report_source_alignment(directory, data_root, project_root)
    snapshot = _report_runtime_snapshot(directory)
    source_passed = source_alignment["status"] != "fail"
    analysis_ready = bool(validation["passed"] and manifest_alignment["passed"] and source_passed)
    if analysis_ready:
        status = "pass"
    elif (
        validation["status"] == "fail"
        or manifest_alignment["status"] == "fail"
        or source_alignment["status"] == "fail"
    ):
        status = "fail"
    else:
        status = "incomplete"
    return {
        "contract": REPORT_RUNTIME_CONTRACT,
        "status": status,
        "analysis_ready": analysis_ready,
        "validation": validation,
        "manifest_alignment": manifest_alignment,
        "source_alignment": source_alignment,
        "snapshot": snapshot,
    }


def _report_runtime_headers(runtime: dict[str, object]) -> dict[str, str]:
    """Flatten report runtime readiness into safe headers for binary exports."""
    validation = runtime.get("validation")
    manifest_alignment = runtime.get("manifest_alignment")
    source_alignment = runtime.get("source_alignment")
    validation_status = (
        validation.get("status", "incomplete")
        if isinstance(validation, dict)
        else "incomplete"
    )
    alignment_status = (
        manifest_alignment.get("status", "incomplete")
        if isinstance(manifest_alignment, dict)
        else "incomplete"
    )
    source_status = (
        source_alignment.get("status", "incomplete")
        if isinstance(source_alignment, dict)
        else "incomplete"
    )
    status = runtime.get("status", "incomplete")
    return {
        REPORT_RUNTIME_HEADER_NAMES["contract"]: REPORT_RUNTIME_CONTRACT,
        REPORT_RUNTIME_HEADER_NAMES["status"]: str(status),
        REPORT_RUNTIME_HEADER_NAMES["analysis_ready"]: (
            "true" if runtime.get("analysis_ready") is True else "false"
        ),
        REPORT_RUNTIME_HEADER_NAMES["validation"]: str(validation_status),
        REPORT_RUNTIME_HEADER_NAMES["manifest_alignment"]: str(alignment_status),
        REPORT_RUNTIME_HEADER_NAMES["source_alignment"]: str(source_status),
    }


def _apply_report_runtime(
    payload: dict[str, object],
    runtime: dict[str, object],
    *,
    initial: bool,
    attach_runtime: bool = True,
) -> None:
    """Attach runtime readiness and prevent stale initial dashboard claims."""
    if attach_runtime:
        payload["runtime"] = runtime
    if not initial:
        return
    summary = payload.get("summary")
    if isinstance(summary, dict):
        summary = dict(summary)
        summary["analysis_ready"] = runtime["analysis_ready"]
        summary["manifest_alignment"] = runtime["manifest_alignment"]
        summary["source_alignment"] = runtime["source_alignment"]
        payload["summary"] = summary

    if runtime["analysis_ready"]:
        return
    interpretation = payload.get("interpretation")
    if not isinstance(interpretation, dict):
        return
    interpretation = dict(interpretation)
    findings = []
    validation = runtime["validation"]
    manifest_alignment = runtime["manifest_alignment"]
    source_alignment = runtime["source_alignment"]
    validation_status = (
        validation.get("status", "incomplete")
        if isinstance(validation, dict)
        else "incomplete"
    )
    alignment_status = (
        manifest_alignment.get("status", "incomplete")
        if isinstance(manifest_alignment, dict)
        else "incomplete"
    )
    source_status = (
        source_alignment.get("status", "incomplete")
        if isinstance(source_alignment, dict)
        else "incomplete"
    )
    validation_text = (
        f"Current input source alignment is {source_status}; numerical findings should be treated as provisional."
        if source_status == "fail"
        else f"Current output artifact alignment is {alignment_status}; numerical findings should be treated as provisional."
        if alignment_status != "pass"
        else f"Analytical validation is {validation_status}; numerical findings should be treated as provisional."
    )
    for finding in interpretation.get("findings", []):
        if not isinstance(finding, dict):
            findings.append(finding)
            continue
        finding = dict(finding)
        if finding.get("id") == "validation":
            finding["status"] = (
                source_status
                if source_status == "fail"
                else alignment_status
                if alignment_status != "pass"
                else validation_status
            )
            finding["text"] = validation_text
        findings.append(finding)
    interpretation["findings"] = findings
    caveats = list(interpretation.get("caveats", []))
    if alignment_status != "pass":
        caveat = "Current output artifacts are not aligned with the manifest; numerical findings should be treated as provisional."
        if caveat not in caveats:
            caveats.append(caveat)
    if source_status == "fail":
        caveat = "Current input sources are not aligned with the output manifest; numerical findings should be treated as provisional."
        if caveat not in caveats:
            caveats.append(caveat)
    interpretation["caveats"] = caveats
    payload["interpretation"] = interpretation


class ReportRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler with health and local route-query endpoints."""

    server_version = "IrelandGeometryReport/1"

    def __init__(self, *args, report_name: str, **kwargs):
        self.report_name = report_name
        self._head_only = False
        self._request_id_value: str | None = None
        self._request_started_at = time.perf_counter()
        super().__init__(*args, **kwargs)

    def do_HEAD(self) -> None:
        """Return API or gzip-pack headers without transferring a response body."""
        path = urlsplit(self.path).path
        if path in HEAD_ENDPOINTS or (
            path == "/report_data.json" and self._accepts_gzip()
        ):
            self._head_only = True
            try:
                self.do_GET()
            finally:
                self._head_only = False
            return
        try:
            _require_symlink_free_output(Path(self.directory or "."))
        except ValueError:
            self.send_error(404, "File not found")
            return
        if path == HEALTH_PATH or path.startswith("/api/"):
            self._head_only = True
            try:
                super().do_HEAD()
            finally:
                self._head_only = False
            return
        super().do_HEAD()

    def do_GET(self) -> None:
        try:
            _require_symlink_free_output(Path(self.directory or "."))
        except ValueError:
            self.send_error(404, "File not found")
            return
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            self._write_health()
            return
        if path == ROUTE_API_PATH:
            self._write_route()
            return
        if path == ROUTE_MATRIX_API_PATH:
            self._write_route_matrix()
            return
        if path == ROUTE_COMPARISON_API_PATH:
            self._write_route_comparison()
            return
        if path == QUERY_API_PATH:
            self._write_query()
            return
        if path == METADATA_API_PATH:
            self._write_metadata()
            return
        if path == INTERPRETATION_API_PATH:
            self._write_interpretation()
            return
        if path == REPORT_PAGE_API_PATH:
            self._write_report_page()
            return
        if path == REPORT_RUNTIME_API_PATH:
            self._write_report_runtime()
            return
        if path == REPORT_EXPORT_API_PATH:
            self._write_report_export()
            return
        if path == CAPABILITIES_API_PATH:
            self._write_capabilities()
            return
        if path == OPENAPI_PATH:
            self._write_openapi()
            return
        if path == "/report_data.json" and self._accepts_gzip():
            self._write_gzipped_data_pack()
            return
        super().do_GET()

    def do_POST(self) -> None:
        """Accept structured JSON for the read-only route query endpoints."""
        try:
            _require_symlink_free_output(Path(self.directory or "."))
        except ValueError:
            self.send_error(404, "File not found")
            return
        path = urlsplit(self.path).path
        if path not in {
            QUERY_API_PATH,
            ROUTE_API_PATH,
            ROUTE_MATRIX_API_PATH,
            ROUTE_COMPARISON_API_PATH,
            REPORT_PAGE_API_PATH,
            REPORT_EXPORT_API_PATH,
        }:
            self.send_error(405, "POST is only supported for structured API endpoints")
            return
        if path == QUERY_API_PATH:
            payload = self._read_json_object_body(
                contract=QUERY_CONTRACT,
                label="query",
                max_body_bytes=QUERY_JSON_MAX_BODY_BYTES,
            )
            if payload is not None:
                self._write_query(json_body=payload)
            return
        if path == ROUTE_API_PATH:
            payload = self._read_json_object_body(
                contract=ROUTE_CONTRACT,
                label="route",
                max_body_bytes=ROUTE_JSON_MAX_BODY_BYTES,
            )
            if payload is not None:
                self._write_route(json_body=payload)
            return
        if path == REPORT_PAGE_API_PATH:
            payload = self._read_json_object_body(
                contract=REPORT_PAGE_CONTRACT,
                label="report page",
                max_body_bytes=REPORT_PAGE_JSON_MAX_BODY_BYTES,
            )
            if payload is not None:
                self._write_report_page(json_body=payload)
            return
        if path == REPORT_EXPORT_API_PATH:
            payload = self._read_json_object_body(
                contract=REPORT_EXPORT_CONTRACT,
                label="report export",
                max_body_bytes=REPORT_EXPORT_JSON_MAX_BODY_BYTES,
            )
            if payload is not None:
                self._write_report_export(json_body=payload)
            return
        if path == ROUTE_MATRIX_API_PATH:
            payload = self._read_json_object_body(
                contract=ROUTE_MATRIX_CONTRACT,
                label="route matrix",
                max_body_bytes=ROUTE_MATRIX_JSON_MAX_BODY_BYTES,
            )
            if payload is not None:
                self._write_route_matrix(json_body=payload)
            return
        payload = self._read_json_object_body(
            contract=ROUTE_COMPARISON_CONTRACT,
            label="route comparison",
            max_body_bytes=ROUTE_COMPARISON_JSON_MAX_BODY_BYTES,
        )
        if payload is not None:
            self._write_route_comparison(json_body=payload)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Return JSON errors for API paths while retaining HTML for static files."""
        path = urlsplit(self.path).path
        if path == HEALTH_PATH or path.startswith("/api/"):
            phrase = self.responses.get(code, ("Error",))[0]
            self._write_json(
                {
                    "contract": API_ERROR_CONTRACT,
                    "status": "error",
                    "error_code": f"http_{code}",
                    "error": message or phrase,
                },
                status=code,
            )
            return
        super().send_error(code, message, explain)

    def log_message(self, format: str, *args: object) -> None:
        """Include correlation and processing fields in every access log."""
        format = (
            f"{format} [request_id={self._request_id()}]"
            f" [{REQUEST_TIMING_LOG_FIELD}={self._request_duration_ms()}]"
        )
        super().log_message(format, *args)

    def end_headers(self) -> None:
        """Attach correlation, timing, and browser security to every response."""
        self.send_header(REQUEST_ID_HEADER, self._request_id())
        for name, value in SECURITY_RESPONSE_HEADERS.items():
            self.send_header(name, value)
        self.send_header(
            REQUEST_TIMING_HEADER,
            f"{REQUEST_TIMING_METRIC};dur={self._request_duration_ms()}",
        )
        super().end_headers()

    def _request_duration_ms(self) -> str:
        """Return bounded server processing time before response headers."""
        elapsed_ms = max(0.0, (time.perf_counter() - self._request_started_at) * 1000)
        return f"{elapsed_ms:.3f}"

    def _request_id(self) -> str:
        """Return a validated client correlation ID or generate one per request."""
        if self._request_id_value is not None:
            return self._request_id_value
        candidate = ""
        for header in REQUEST_ID_INPUT_HEADERS:
            value = self.headers.get(header, "").strip()
            if value:
                candidate = value
                break
        self._request_id_value = (
            candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex
        )
        return self._request_id_value

    def _request_id_headers(self) -> dict[str, str]:
        return {REQUEST_ID_HEADER: self._request_id()}

    def _read_json_object_body(
        self,
        *,
        contract: str,
        label: str,
        max_body_bytes: int,
    ) -> dict[str, object] | None:
        """Read and validate one bounded JSON object request body."""
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"{label} POST requires Content-Type: application/json",
                },
                status=415,
            )
            return None
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"{label} POST requires a valid Content-Length header",
                },
                status=411,
            )
            return None
        if length > max_body_bytes:
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"{label} JSON body must not exceed {max_body_bytes} bytes",
                },
                status=413,
            )
            return None
        raw = self.rfile.read(length)
        if len(raw) != length:
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"{label} JSON body ended before Content-Length",
                },
                status=400,
            )
            return None
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_nonfinite_json,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"invalid {label} JSON body: {exc}",
                },
                status=400,
            )
            return None
        if not isinstance(payload, dict):
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"{label} JSON body must be an object",
                },
                status=400,
            )
            return None
        return payload

    def copyfile(self, source, outputfile) -> None:
        """Ignore a client closing a download before the file is complete."""
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_head(self):
        """Refuse static paths whose components are output-directory symlinks."""
        candidate = Path(super().translate_path(self.path))
        directory = Path(self.directory or ".").resolve()
        try:
            relative = candidate.relative_to(directory)
        except ValueError:
            self.send_error(404, "File not found")
            return None
        current = directory
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                self.send_error(404, "File not found")
                return None
        return super().send_head()

    def _write_health(self) -> None:
        directory = Path(self.directory or ".").resolve()
        report_path = directory / self.report_name
        data_path = directory / "report_data.json"
        graph_path = getattr(self.server, "routing_graph_path", None)
        routing_graph = _routing_graph_inventory(graph_path)
        report_exists = report_path.is_file()
        data_pack_exists = data_path.is_file()
        data_pack_required = self.report_name == DEFAULT_REPORT
        ready = report_exists and (not data_pack_required or data_pack_exists)
        validation = _validation_status(directory)
        manifest_alignment = _manifest_artifact_alignment(directory)
        source_alignment = _report_source_alignment(
            directory,
            data_root=getattr(self.server, "data_root", None),
            project_root=getattr(self.server, "project_root", directory.parent),
        )
        query_backend_health = _query_backend_health(directory)
        query_readable_backends = [
            name for name, status in query_backend_health.items() if status["readable"]
        ]
        payload = {
            "contract": HEALTH_CONTRACT,
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "analysis_ready": (
                ready
                and validation["passed"]
                and manifest_alignment["passed"]
                and source_alignment["status"] != "fail"
            ),
            "manifest_alignment": manifest_alignment,
            "source_alignment": source_alignment,
            "validation": validation,
            "report": self.report_name,
            "report_exists": report_exists,
            "data_pack_exists": data_pack_exists,
            "data_pack_required": data_pack_required,
            "data_pack_gzip": data_pack_exists,
            "report_page_endpoint": REPORT_PAGE_API_PATH,
            "report_page_json_post": True,
            "report_page_json_max_body_bytes": REPORT_PAGE_JSON_MAX_BODY_BYTES,
            "report_export_endpoint": REPORT_EXPORT_API_PATH,
            "report_export_json_post": True,
            "report_export_json_max_body_bytes": REPORT_EXPORT_JSON_MAX_BODY_BYTES,
            "output_symlink_guard": True,
            "routing_available": _graph_available(graph_path),
            "route_endpoint": ROUTE_API_PATH,
            "route_json_post": True,
            "route_json_max_body_bytes": ROUTE_JSON_MAX_BODY_BYTES,
            "route_matrix_available": _graph_available(graph_path),
            "route_matrix_endpoint": ROUTE_MATRIX_API_PATH,
            "route_matrix_max_pairs": ROUTE_MATRIX_MAX_PAIRS,
            "route_matrix_json_post": True,
            "route_matrix_json_max_body_bytes": ROUTE_MATRIX_JSON_MAX_BODY_BYTES,
            "route_comparison_available": _graph_available(graph_path),
            "route_comparison_endpoint": ROUTE_COMPARISON_API_PATH,
            "route_comparison_min_profiles": ROUTE_COMPARISON_MIN_PROFILES,
            "route_comparison_max_profiles": ROUTE_COMPARISON_MAX_PROFILES,
            "route_comparison_json_post": True,
            "route_comparison_json_max_body_bytes": ROUTE_COMPARISON_JSON_MAX_BODY_BYTES,
            "query_available": bool(query_readable_backends),
            "query_endpoint": QUERY_API_PATH,
            "query_json_post": True,
            "query_json_max_body_bytes": QUERY_JSON_MAX_BODY_BYTES,
            "query_backend_health": query_backend_health,
            "query_readable_backends": query_readable_backends,
            "query_pagination": True,
            "query_max_limit": QUERY_MAX_LIMIT,
            "query_max_offset": QUERY_MAX_OFFSET,
            "query_min_score_finite": True,
            "query_cursor": True,
            "query_invalid_score_cursor": True,
            "query_auto_backend_failover": True,
            "metadata_available": (directory / "manifest.json").is_file(),
            "metadata_endpoint": METADATA_API_PATH,
            "interpretation_available": (directory / "interpretation.json").is_file(),
            "interpretation_endpoint": INTERPRETATION_API_PATH,
            "capabilities_endpoint": CAPABILITIES_API_PATH,
            "routing_graph": routing_graph,
        }
        self._write_json(payload, cache_control="no-cache")

    def _write_json(
        self,
        payload: dict,
        *,
        status: int = 200,
        content_type: str = "application/json",
        cache_control: str = "no-store",
    ) -> None:
        payload = dict(payload)
        if status >= 400:
            payload.setdefault("error_code", f"http_{status}")
            payload.setdefault("request_id", self._request_id())
            cache_control = "no-store"
        body = json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode("utf-8")
        response_body = body
        content_encoding = None
        vary = None
        if (
            cache_control != "no-store"
            and len(body) >= API_JSON_GZIP_MIN_BYTES
            and self._accepts_gzip()
        ):
            response_body = gzip.compress(body, compresslevel=6, mtime=0)
            content_encoding = "gzip"
            vary = "Accept-Encoding"
        response_etag = _etag(response_body) if cache_control != "no-store" else None
        if response_etag and _etag_matches(self.headers.get("If-None-Match"), response_etag):
            self._write_not_modified(
                response_etag,
                cache_control=cache_control,
                content_encoding=content_encoding,
                vary=vary,
            )
            return
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        if vary:
            self.send_header("Vary", vary)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Cache-Control", cache_control)
        if response_etag:
            self.send_header("ETag", response_etag)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(response_body)

    def _write_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        filename: str | None = None,
        cache_control: str = "no-cache",
        headers: dict[str, str] | None = None,
    ) -> None:
        response_etag = _etag(body) if cache_control != "no-store" else None
        if response_etag and _etag_matches(self.headers.get("If-None-Match"), response_etag):
            self._write_not_modified(
                response_etag,
                cache_control=cache_control,
                headers=headers,
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if response_etag:
            self.send_header("ETag", response_etag)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def _write_not_modified(
        self,
        response_etag: str,
        *,
        cache_control: str,
        content_encoding: str | None = None,
        vary: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(304)
        self.send_header("ETag", response_etag)
        self.send_header("Cache-Control", cache_control)
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        if vary:
            self.send_header("Vary", vary)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_query(self, *, json_body: dict[str, object] | None = None) -> None:
        try:
            if json_body is None:
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

                def value(name: str) -> str | None:
                    values = query.get(name)
                    return values[0] if values else None

            else:
                request = _json_query_request(json_body)

                def value(name: str) -> str | None:
                    raw = request.get(name)
                    return str(raw) if raw is not None else None

            limit_text = value("limit") or "100"
            limit = int(limit_text)
            if not 1 <= limit <= QUERY_MAX_LIMIT:
                raise ValueError(f"limit must be between 1 and {QUERY_MAX_LIMIT}")
            offset_text = value("offset") or "0"
            offset = int(offset_text)
            if not 0 <= offset <= QUERY_MAX_OFFSET:
                raise ValueError(f"offset must be between 0 and {QUERY_MAX_OFFSET}")
            min_score_text = value("min_score")
            min_score = float(min_score_text) if min_score_text not in {None, ""} else None
            if min_score is not None and not math.isfinite(min_score):
                raise ValueError("min_score must be finite")
            after_score_text = value("after_score")
            after_score = (
                float(after_score_text) if after_score_text not in {None, ""} else None
            )
            after_osm_id = value("after_osm_id") or None
            after_invalid_osm_id = value("after_invalid_osm_id") or None
            requested_backend = value("backend") or "auto"
            backend_errors: list[str] = []
            backend, rows = query_rows(
                Path(self.directory or ".").resolve(),
                backend=requested_backend,
                backend_errors=backend_errors,
                osm_id=value("osm_id") or None,
                group=value("group") or None,
                is_control=value("is_control") or None,
                min_score=min_score,
                after_score=after_score,
                after_osm_id=after_osm_id,
                after_invalid_osm_id=after_invalid_osm_id,
                limit=limit + 1,
                offset=offset,
            )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": QUERY_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
            )
            return
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            self._write_json(
                {
                    "contract": QUERY_CONTRACT,
                    "status": "query_unavailable",
                    "error": str(exc),
                },
                status=503,
            )
            return
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = _cursor_for_row(rows[-1]) if has_more and rows else None
        self._write_json(
            {
                "contract": QUERY_CONTRACT,
                "status": "ok",
                "backend": backend,
                "backend_fallbacks": backend_errors or None,
                "count": len(rows),
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "next_offset": (
                    offset + limit
                    if has_more and after_score is None and after_invalid_osm_id is None
                    else None
                ),
                "cursor": (
                    {"after_invalid_osm_id": after_invalid_osm_id}
                    if after_invalid_osm_id is not None
                    else (
                        {"after_score": after_score, "after_osm_id": after_osm_id}
                        if after_score is not None
                        else None
                    )
                ),
                "next_cursor": next_cursor,
                "filters": {
                    "osm_id": value("osm_id"),
                    "group": value("group"),
                    "is_control": value("is_control"),
                    "min_score": min_score,
                },
                "rows": rows,
            },
            cache_control="no-cache",
        )

    def _report_filters(
        self,
        *,
        json_body: dict[str, object] | None = None,
        request_kind: str = "page",
    ) -> tuple[dict[str, object], int, int, bool]:
        if json_body is None:
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

            def value(name: str) -> str | None:
                values = query.get(name)
                return values[0] if values else None

        else:
            request = _json_report_request(json_body, kind=request_kind)

            def value(name: str) -> str | None:
                raw = request.get(name)
                if raw is None:
                    return None
                if isinstance(raw, bool):
                    return "1" if raw else "0"
                return str(raw)

        def boolean(name: str, default: bool = False) -> bool:
            raw = value(name)
            if raw in {None, ""}:
                return default
            normalized = raw.strip().lower()
            if normalized in TRUE_QUERY_VALUES:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be a boolean")

        limit = int(value("limit") or REPORT_PAGE_DEFAULT_LIMIT)
        if not 1 <= limit <= REPORT_PAGE_MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {REPORT_PAGE_MAX_LIMIT}")
        offset = int(value("offset") or "0")
        if not 0 <= offset <= QUERY_MAX_OFFSET:
            raise ValueError(f"offset must be between 0 and {QUERY_MAX_OFFSET}")
        score_text = value("score")
        min_score = float(score_text) if score_text not in {None, ""} else 0.0
        if not math.isfinite(min_score):
            raise ValueError("score must be finite")
        sort_key = value("sort") or "score"
        sort_desc = boolean("desc", True)
        filters = {
            "query": value("q") or "",
            "group": value("group") or "",
            "century": value("century") or "",
            "rating": value("rating") or "",
            "niah_type": value("type") or "",
            "county": value("county") or "",
            "review_state": value("review") or "",
            "pattern": value("pattern") or "",
            "culture": value("culture") or "",
            "min_score": min_score,
            "only_angle": boolean("angle"),
            "only_ratio": boolean("ratio"),
            "only_circular": boolean("circular"),
            "only_multi": boolean("multi"),
            "sort_key": sort_key,
            "sort_desc": sort_desc,
        }
        return filters, limit, offset, boolean("initial")

    def _report_data_or_error(self, contract: str = REPORT_PAGE_CONTRACT) -> dict[str, object] | None:
        path = Path(self.directory or ".").resolve() / "report_data.json"
        try:
            return self.server.report_data(path)  # type: ignore[attr-defined]
        except FileNotFoundError:
            self._write_json(
                {
                    "contract": contract,
                    "status": "not_provided",
                    "error": "report_data.json is not available",
                },
                status=404,
                cache_control="no-cache",
            )
        except (OSError, ValueError) as exc:
            self._write_json(
                {
                    "contract": contract,
                    "status": "error",
                    "error": f"report_data.json is invalid: {exc}",
                },
                status=500,
                cache_control="no-cache",
            )
        return None

    def _write_report_page(self, *, json_body: dict[str, object] | None = None) -> None:
        try:
            filters, limit, offset, initial = self._report_filters(json_body=json_body)
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": REPORT_PAGE_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
                cache_control="no-cache",
            )
            return
        data = self._report_data_or_error(REPORT_PAGE_CONTRACT)
        if data is None:
            return
        directory = Path(self.directory or ".").resolve()
        try:
            try:
                from report import report_page_payload
            except ImportError:
                from scripts.report import report_page_payload

            payload = report_page_payload(
                data,
                limit=limit,
                offset=offset,
                initial=initial,
                include_static=initial,
                **filters,
            )
            _apply_report_runtime(
                payload,
                _report_runtime_state(
                    directory,
                    data_root=getattr(self.server, "data_root", None),
                    project_root=getattr(self.server, "project_root", directory.parent),
                ),
                initial=initial,
            )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": REPORT_PAGE_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
                cache_control="no-cache",
            )
            return
        self._write_json(payload, cache_control="no-cache")

    def _write_report_runtime(self) -> None:
        """Return the current report runtime contract with deterministic ETags."""
        directory = Path(self.directory or ".").resolve()
        self._write_json(
            _report_runtime_state(
                directory,
                data_root=getattr(self.server, "data_root", None),
                project_root=getattr(self.server, "project_root", directory.parent),
            ),
            cache_control="no-cache",
        )

    def _write_report_export(self, *, json_body: dict[str, object] | None = None) -> None:
        try:
            if json_body is None:
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                format_name = (query.get("format") or [""])[0].strip().lower()
                if format_name not in {"csv", "geojson"}:
                    raise ValueError("format must be csv or geojson")
            else:
                request = _json_report_request(json_body, kind="export")
                format_name = str(request["format"])
            filters, _limit, _offset, _initial = self._report_filters(
                json_body=json_body,
                request_kind="export",
            )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": REPORT_EXPORT_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
                cache_control="no-cache",
            )
            return
        data = self._report_data_or_error(REPORT_EXPORT_CONTRACT)
        if data is None:
            return
        runtime_headers = _report_runtime_headers(
            _report_runtime_state(
                Path(self.directory or ".").resolve(),
                data_root=getattr(self.server, "data_root", None),
                project_root=getattr(
                    self.server,
                    "project_root",
                    Path(self.directory or ".").resolve().parent,
                ),
            )
        )
        try:
            try:
                from report import report_csv, report_geojson
            except ImportError:
                from scripts.report import report_csv, report_geojson

            if format_name == "csv":
                body = report_csv(data, **filters).encode("utf-8")
                self._write_bytes(
                    body,
                    content_type="text/csv; charset=utf-8",
                    filename="ireland-geometry-filtered.csv",
                    headers=runtime_headers,
                )
            else:
                body = json.dumps(
                    _json_safe(report_geojson(data, **filters)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                self._write_bytes(
                    body,
                    content_type="application/geo+json; charset=utf-8",
                    filename="ireland-geometry-filtered.geojson",
                    headers=runtime_headers,
                )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": REPORT_EXPORT_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
                cache_control="no-cache",
            )

    def _write_metadata(self) -> None:
        """Return compact provenance, verification, schema, and export metadata."""
        directory = Path(self.directory or ".").resolve()
        records: dict[str, dict[str, object] | None] = {}
        errors: list[str] = []
        for name in (
            "manifest",
            "verification",
            "columnar_status",
            "schema_validation",
            "reproducibility",
        ):
            record, error = _read_json_object(directory / f"{name}.json")
            records[name] = record
            if error:
                errors.append(f"{name}.json: {error}")

        manifest = records["manifest"] or {}
        verification = records["verification"] or {}
        columnar = records["columnar_status"] or {}
        schema = records["schema_validation"] or {}
        reproducibility = records["reproducibility"] or {}
        sources = manifest.get("sources")
        artifacts = manifest.get("artifacts")
        verification_errors = verification.get("errors")
        verification_warnings = verification.get("warnings")
        schema_artifacts = schema.get("artifacts")
        portable_artifact_count = sum(
            1
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("relative_path")
        ) if isinstance(artifacts, list) else 0
        portable_source_count = sum(
            1
            for source in sources
            if isinstance(source, dict) and source.get("relative_path")
        ) if isinstance(sources, list) else 0
        external_source_count = sum(
            1
            for source in sources
            if isinstance(source, dict) and source.get("path_base") == "external"
        ) if isinstance(sources, list) else 0

        query_backend_health = _query_backend_health(directory)
        query_readable_backends = [
            name for name, status in query_backend_health.items() if status["readable"]
        ]
        backends: dict[str, dict[str, object]] = {}
        for name in QUERY_BACKEND_NAMES:
            info = columnar.get(name)
            probe = query_backend_health[name]
            backends[name] = {
                "status": info.get("status") if isinstance(info, dict) else None,
                "engine": info.get("engine") if isinstance(info, dict) else None,
                "rows": columnar.get("rows"),
                "available": probe["available"],
                "readable": probe["readable"],
                "error": probe["error"],
            }

        manifest_alignment = _manifest_artifact_alignment(directory)
        source_alignment = _report_source_alignment(
            directory,
            data_root=getattr(self.server, "data_root", None),
            project_root=getattr(self.server, "project_root", directory.parent),
        )
        status = (
            "degraded"
            if (
                errors
                or manifest_alignment["status"] in {"fail", "incomplete"}
                or source_alignment["status"] == "fail"
            )
            else ("ok" if records["manifest"] else "not_provided")
        )
        self._write_json(
            {
                "contract": METADATA_CONTRACT,
                "status": status,
                "errors": errors,
                "manifest_alignment": manifest_alignment,
                "source_alignment": source_alignment,
                "manifest": {
                    "available": records["manifest"] is not None,
                    "url": "/manifest.json",
                    "package_version": manifest.get("package_version"),
                    "runtime": manifest.get("runtime"),
                    "schema_version": manifest.get("schema_version"),
                    "generated_at": manifest.get("generated_at"),
                    "git_revision": manifest.get("git_revision"),
                    "git_dirty": manifest.get("git_dirty"),
                    "path_contract": manifest.get("path_contract"),
                    "data_root_relative": manifest.get("data_root_relative"),
                    "output_dir_relative": manifest.get("output_dir_relative"),
                    "parameters": manifest.get("parameters", {}),
                    "counts": manifest.get("counts", {}),
                    "source_count": len(sources) if isinstance(sources, list) else 0,
                    "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
                    "portable_artifact_count": portable_artifact_count,
                    "portable_source_count": portable_source_count,
                    "external_source_count": external_source_count,
                },
                "source_freshness": manifest.get("source_freshness"),
                "verification": {
                    "available": records["verification"] is not None,
                    "url": "/verification.json",
                    "status": (
                        "pass"
                        if verification.get("passed") is True
                        else ("fail" if records["verification"] is not None else "not_provided")
                    ),
                    "analysis_rows": verification.get("analysis_rows"),
                    "target_rows": verification.get("target_rows"),
                    "control_rows": verification.get("control_rows"),
                    "error_count": len(verification_errors) if isinstance(verification_errors, list) else 0,
                    "warning_count": len(verification_warnings) if isinstance(verification_warnings, list) else 0,
                },
                "exports": {
                    "available": records["columnar_status"] is not None,
                    "url": "/columnar_status.json",
                    "rows": columnar.get("rows"),
                    "contract": columnar.get("contract"),
                    "schema_sha256": columnar.get("schema_sha256"),
                    "row_digest": columnar.get("row_digest"),
                    "parity": columnar.get("parity"),
                    "backends": backends,
                    "readable_backends": query_readable_backends,
                },
                "schema_validation": {
                    "available": records["schema_validation"] is not None,
                    "url": "/schema_validation.json",
                    "status": (
                        "pass"
                        if schema.get("passed") is True
                        else ("fail" if records["schema_validation"] is not None else "not_provided")
                    ),
                    "schema_version": schema.get("schema_version"),
                    "artifact_count": len(schema_artifacts) if isinstance(schema_artifacts, dict) else 0,
                },
                "reproducibility": {
                    "available": records["reproducibility"] is not None,
                    "url": "/reproducibility.json",
                    "status": (
                        "pass"
                        if reproducibility.get("passed") is True
                        else (
                            "fail"
                            if records["reproducibility"] is not None
                            else "not_provided"
                        )
                    ),
                    "artifact_count": reproducibility.get("artifact_count"),
                },
                "links": {
                    "health": HEALTH_PATH,
                    "report_data": "/report_data.json",
                    "manifest": "/manifest.json",
                    "verification": "/verification.json",
                    "columnar_status": "/columnar_status.json",
                    "schema_validation": "/schema_validation.json",
                    "reproducibility": "/reproducibility.json",
                },
            },
            cache_control="no-cache",
        )

    def _write_interpretation(self) -> None:
        """Return the compact generated interpretation sidecar."""
        directory = Path(self.directory or ".").resolve()
        path = directory / "interpretation.json"
        payload, error = _read_json_object(path)
        runtime = _report_runtime_state(
            directory,
            data_root=getattr(self.server, "data_root", None),
            project_root=getattr(self.server, "project_root", directory.parent),
        )
        validation = runtime["validation"]
        manifest_alignment = runtime["manifest_alignment"]
        source_alignment = runtime["source_alignment"]
        if error:
            self._write_json(
                {
                    "contract": INTERPRETATION_CONTRACT,
                    "status": "error",
                    "available": False,
                    "analysis_ready": False,
                    "manifest_alignment": manifest_alignment,
                    "source_alignment": source_alignment,
                    "validation": validation,
                    "interpretation": None,
                    "error": f"interpretation.json: {error}",
                },
                status=500,
                cache_control="no-cache",
            )
            return
        if payload is None:
            self._write_json(
                {
                    "contract": INTERPRETATION_CONTRACT,
                    "status": "not_provided",
                    "available": False,
                    "analysis_ready": False,
                    "manifest_alignment": manifest_alignment,
                    "source_alignment": source_alignment,
                    "validation": validation,
                    "interpretation": None,
                    "source": "/interpretation.json",
                    "error": "interpretation.json is not available",
                },
                status=404,
                cache_control="no-cache",
            )
            return
        response = dict(payload)
        response["contract"] = INTERPRETATION_CONTRACT
        response["validation"] = validation
        response["manifest_alignment"] = manifest_alignment
        response["source_alignment"] = source_alignment
        response["analysis_ready"] = runtime["analysis_ready"]
        _apply_report_runtime(response, runtime, initial=True, attach_runtime=False)
        response.setdefault("source", "/interpretation.json")
        self._write_json(response, cache_control="no-cache")

    def _write_capabilities(self) -> None:
        """Return a machine-readable inventory of the local server contracts."""
        directory = Path(self.directory or ".").resolve()
        report_exists = (directory / self.report_name).is_file()
        data_pack_exists = (directory / "report_data.json").is_file()
        data_pack_required = self.report_name == DEFAULT_REPORT
        ready = report_exists and (not data_pack_required or data_pack_exists)
        validation = _validation_status(directory)
        manifest_alignment = _manifest_artifact_alignment(directory)
        source_alignment = _report_source_alignment(
            directory,
            data_root=getattr(self.server, "data_root", None),
            project_root=getattr(self.server, "project_root", directory.parent),
        )
        graph_path = getattr(self.server, "routing_graph_path", None)
        routing_graph = _routing_graph_inventory(graph_path)
        query_backend_health = _query_backend_health(directory)
        query_readable_backends = [
            name for name, status in query_backend_health.items() if status["readable"]
        ]
        manifest_available = (directory / "manifest.json").is_file()
        interpretation_available = (directory / "interpretation.json").is_file()
        manifest_record, _manifest_error = _read_json_object(directory / "manifest.json")
        columnar, _columnar_error = _read_json_object(directory / "columnar_status.json")
        self._write_json(
            {
                "contract": CAPABILITIES_CONTRACT,
                "status": "ok" if ready else "degraded",
                "ready": ready,
                "analysis_ready": (
                    ready
                    and validation["passed"]
                    and manifest_alignment["passed"]
                    and source_alignment["status"] != "fail"
                ),
                "output_symlink_guard": True,
                "project_root": str(
                    getattr(self.server, "project_root", directory.parent)
                ),
                "manifest_alignment": manifest_alignment,
                "source_alignment": source_alignment,
                "source_alignment_surfaces": list(SOURCE_ALIGNMENT_SURFACES),
                "conditional_endpoints": list(CONDITIONAL_ENDPOINTS),
                "head_endpoints": list(HEAD_ENDPOINTS),
                "api_json_gzip": True,
                "api_json_gzip_min_bytes": API_JSON_GZIP_MIN_BYTES,
                "api_error_contract": API_ERROR_CONTRACT,
                "api_error_request_ids": True,
                "api_error_request_id_header": REQUEST_ID_HEADER,
                "api_request_logging": True,
                "api_request_log_field": "request_id",
                "request_id_all_responses": True,
                "request_id_all_response_logging": True,
                "security_response_headers": dict(SECURITY_RESPONSE_HEADERS),
                "api_request_timing": True,
                "api_request_timing_header": REQUEST_TIMING_HEADER,
                "api_request_timing_metric": REQUEST_TIMING_METRIC,
                "api_request_timing_log_field": REQUEST_TIMING_LOG_FIELD,
                "validation": validation,
                "routing_graph": routing_graph,
                "package_version": package_version(),
                "contracts": dict(API_CONTRACTS),
                "report": {
                    "name": self.report_name,
                    "exists": report_exists,
                    "data_pack_exists": data_pack_exists,
                    "data_pack_required": data_pack_required,
                    "data_pack_gzip": data_pack_exists,
                },
                "endpoints": {
                    "health": {
                        "path": HEALTH_PATH,
                        "method": "GET",
                        "contract": HEALTH_CONTRACT,
                        "available": True,
                    },
                    "capabilities": {
                        "path": CAPABILITIES_API_PATH,
                        "method": "GET",
                        "contract": CAPABILITIES_CONTRACT,
                        "available": True,
                    },
                    "metadata": {
                        "path": METADATA_API_PATH,
                        "method": "GET",
                        "contract": METADATA_CONTRACT,
                        "available": manifest_available,
                    },
                    "interpretation": {
                        "path": INTERPRETATION_API_PATH,
                        "method": "GET",
                        "contract": INTERPRETATION_CONTRACT,
                        "available": interpretation_available,
                    },
                    "report_page": {
                        "path": REPORT_PAGE_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": REPORT_PAGE_CONTRACT,
                        "available": data_pack_exists,
                        "pagination": True,
                        "pagination_continuation": "next_offset",
                        "sort_tiebreaker": REPORT_PAGE_SORT_TIEBREAKER,
                        "max_limit": REPORT_PAGE_MAX_LIMIT,
                        "json_request": True,
                        "json_max_body_bytes": REPORT_PAGE_JSON_MAX_BODY_BYTES,
                    },
                    "report_runtime": {
                        "path": REPORT_RUNTIME_API_PATH,
                        "method": "GET",
                        "contract": REPORT_RUNTIME_CONTRACT,
                        "available": True,
                        "conditional": True,
                    },
                    "report_export": {
                        "path": REPORT_EXPORT_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": REPORT_EXPORT_CONTRACT,
                        "available": data_pack_exists,
                        "formats": ["csv", "geojson"],
                        "json_request": True,
                        "json_max_body_bytes": REPORT_EXPORT_JSON_MAX_BODY_BYTES,
                        "runtime_contract": REPORT_RUNTIME_CONTRACT,
                        "runtime_headers": dict(REPORT_RUNTIME_HEADER_NAMES),
                    },
                    "query": {
                        "path": QUERY_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": QUERY_CONTRACT,
                        "available": bool(query_readable_backends),
                        "backends": list(QUERY_BACKEND_NAMES),
                        "readable_backends": query_readable_backends,
                        "max_limit": QUERY_MAX_LIMIT,
                        "max_offset": QUERY_MAX_OFFSET,
                        "pagination": True,
                        "cursor": True,
                        "invalid_score_cursor": True,
                        "auto_backend_failover": True,
                        "json_request": True,
                        "json_max_body_bytes": QUERY_JSON_MAX_BODY_BYTES,
                    },
                    "route": {
                        "path": ROUTE_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": ROUTE_CONTRACT,
                        "available": _graph_available(graph_path),
                        "graph_backend": routing_graph["backend"],
                        "profile_semantics": routing_graph["profile_semantics"],
                        "active_graph_metadata_status": routing_graph["metadata_status"],
                        "active_graph_features": dict(routing_graph["features"]),
                        "active_graph_counts": dict(routing_graph["counts"]),
                        "active_path_segment_sources": list(
                            routing_graph["path_segment_sources"]
                        ),
                        "json_request": True,
                        "json_max_body_bytes": ROUTE_JSON_MAX_BODY_BYTES,
                            "objectives": list(ROUTE_OBJECTIVES),
                            "vehicle_weight_profiles": True,
                            "vehicle_rating_profiles": True,
                            "vehicle_height_profiles": True,
                            "vehicle_width_profiles": True,
                            "vehicle_length_profiles": True,
                            "vehicle_axleload_profiles": True,
                            "maxweight_profiles": True,
                            "maxweightrating_hgv_profiles": True,
                            "hgv_destination_profiles": True,
                            "hgv_destination_delivery_profiles": True,
                            "maxheight_profiles": True,
                            "maxwidth_profiles": True,
                            "maxlength_profiles": True,
                            "maxaxleload_profiles": True,
                            "maxspeed_profiles": True,
                            "maxspeed_conditional_profiles": True,
                            "oneway_conditional_profiles": True,
                            "path_segment_explainability": True,
                            "path_segment_constraints": True,
                            "path_segment_conditional_rules": True,
                            "path_segment_transition_rules": True,
                            "path_segment_way_context": True,
                            "portable_path_segments": True,
                            "path_segment_sources": [
                                "sqlite_edges",
                                "portable_edges",
                                "not_available",
                            ],
                            "path_maneuvers": True,
                            "vehicle_weight_conditional_access_profiles": True,
                            "conditional_access_profiles": True,
                            "directional_conditional_access_profiles": True,
                            "multi_clause_conditional_access_profiles": True,
                            "vehicle_class_profiles": True,
                            "vehicle_classes": list(VEHICLE_CLASSES),
                    },
                    "route_matrix": {
                        "path": ROUTE_MATRIX_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": ROUTE_MATRIX_CONTRACT,
                        "available": _graph_available(graph_path),
                        "graph_backend": routing_graph["backend"],
                        "profile_semantics": routing_graph["profile_semantics"],
                        "active_graph_metadata_status": routing_graph["metadata_status"],
                        "active_graph_features": dict(routing_graph["features"]),
                        "active_graph_counts": dict(routing_graph["counts"]),
                        "active_path_segment_sources": list(
                            routing_graph["path_segment_sources"]
                        ),
                        "max_pairs": ROUTE_MATRIX_MAX_PAIRS,
                        "json_request": True,
                        "json_max_body_bytes": ROUTE_MATRIX_JSON_MAX_BODY_BYTES,
                        "repeated_points": True,
                        "compact_pair_summaries": True,
                        "full_pair_routes": True,
                        "objectives": list(ROUTE_OBJECTIVES),
                        "vehicle_class_profiles": True,
                        "vehicle_classes": list(VEHICLE_CLASSES),
                    },
                    "route_comparison": {
                        "path": ROUTE_COMPARISON_API_PATH,
                        "method": "GET",
                        "methods": ["GET", "POST"],
                        "contract": ROUTE_COMPARISON_CONTRACT,
                        "available": _graph_available(graph_path),
                        "graph_backend": routing_graph["backend"],
                        "profile_semantics": routing_graph["profile_semantics"],
                        "active_graph_metadata_status": routing_graph["metadata_status"],
                        "active_graph_features": dict(routing_graph["features"]),
                        "active_graph_counts": dict(routing_graph["counts"]),
                        "active_path_segment_sources": list(
                            routing_graph["path_segment_sources"]
                        ),
                        "min_profiles": ROUTE_COMPARISON_MIN_PROFILES,
                        "max_profiles": ROUTE_COMPARISON_MAX_PROFILES,
                        "json_request": True,
                        "json_max_body_bytes": ROUTE_COMPARISON_JSON_MAX_BODY_BYTES,
                        "repeated_profiles": True,
                        "baseline_deltas": True,
                        "full_profile_routes": True,
                        "vehicle_classes": list(VEHICLE_CLASSES),
                    },
                    "openapi": {
                        "path": OPENAPI_PATH,
                        "method": "GET",
                        "contract": OPENAPI_CONTRACT,
                        "available": True,
                    },
                },
                "query_backend_health": query_backend_health,
                "exports": {
                    "available": columnar is not None,
                    "contract": columnar.get("contract") if columnar else None,
                    "parity": columnar.get("parity") if columnar else None,
                    "rows": columnar.get("rows") if columnar else None,
                },
                "source_freshness": (
                    manifest_record.get("source_freshness") if manifest_record else None
                ),
            },
            cache_control="no-cache",
        )

    def _write_openapi(self) -> None:
        """Return the stable schema for the local read-only endpoints."""
        self._write_json(openapi_document(), cache_control="no-cache")

    def _write_route(self, *, json_body: dict[str, object] | None = None) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

        try:
            if json_body is None:
                start_lat = _coordinate(
                    _query_first(query, "start_lat"), "start_lat", -90.0, 90.0
                )
                start_lon = _coordinate(
                    _query_first(query, "start_lon"), "start_lon", -180.0, 180.0
                )
                goal_lat = _coordinate(
                    _query_first(query, "goal_lat"), "goal_lat", -90.0, 90.0
                )
                goal_lon = _coordinate(
                    _query_first(query, "goal_lon"), "goal_lon", -180.0, 180.0
                )
                response_format = _query_first(query, "format")
                options = _parse_route_options(
                    query,
                    include_path=_query_boolean(query, "include_path")
                    or response_format == "geojson",
                )
            else:
                (
                    start_lat,
                    start_lon,
                    goal_lat,
                    goal_lon,
                    response_format,
                    options,
                ) = _json_route_request(json_body)
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": ROUTE_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
            )
            return

        graph_path = getattr(self.server, "routing_graph_path", None)
        if graph_path is None or not _graph_available(graph_path):
            self._write_json(
                {
                    "contract": ROUTE_CONTRACT,
                    "status": "routing_not_provided",
                    "error": "no supported road graph is available",
                },
                status=503,
            )
            return
        try:
            graph = load_graph(graph_path)
            if isinstance(graph, SQLiteRoadGraph):
                empty = graph.node_count == 0 or (
                    graph.edge_count == 0 and graph.ferry_edge_count == 0
                )
            else:
                coordinates, adjacency = graph
                empty = not coordinates or not adjacency
            if empty:
                self._write_json(
                    {
                        "contract": ROUTE_CONTRACT,
                        "status": "routing_not_provided",
                        "error": "the road graph is empty",
                    },
                    status=503,
                )
                return
            try:
                result = query_route(
                    start_lat,
                    start_lon,
                    goal_lat,
                    goal_lon,
                    graph,
                    **options,
                )
            finally:
                if isinstance(graph, SQLiteRoadGraph):
                    graph.close()
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self._write_json(
                {"contract": ROUTE_CONTRACT, "status": "routing_error", "error": str(exc)},
                status=500,
            )
            return
        if response_format == "geojson":
            self._write_json(
                route_geojson(result),
                content_type="application/geo+json",
                cache_control="no-cache",
            )
        else:
            result["contract"] = ROUTE_CONTRACT
            self._write_json(result, cache_control="no-cache")

    def _write_route_matrix(self, *, json_body: dict[str, object] | None = None) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        try:
            if json_body is None:
                origin_values = query.get("origin", [])
                destination_values = query.get("destination", [])
                if not origin_values:
                    raise ValueError("at least one origin is required")
                if not destination_values:
                    raise ValueError("at least one destination is required")
                origins = [
                    _parse_matrix_point(value, f"origin[{index}]")
                    for index, value in enumerate(origin_values)
                ]
                destinations = [
                    _parse_matrix_point(value, f"destination[{index}]")
                    for index, value in enumerate(destination_values)
                ]
                options = _parse_route_options(query)
            else:
                origins = _json_matrix_points(json_body, "origins")
                destinations = _json_matrix_points(json_body, "destinations")
                options = _json_route_options(json_body, extra_fields=("origins", "destinations"))
            pair_n = len(origins) * len(destinations)
            if pair_n > ROUTE_MATRIX_MAX_PAIRS:
                raise ValueError(
                    "origin × destination pair count must not exceed "
                    f"{ROUTE_MATRIX_MAX_PAIRS}"
                )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": ROUTE_MATRIX_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
            )
            return

        graph_path = getattr(self.server, "routing_graph_path", None)
        if graph_path is None or not _graph_available(graph_path):
            self._write_json(
                {
                    "contract": ROUTE_MATRIX_CONTRACT,
                    "status": "routing_not_provided",
                    "error": "no supported road graph is available",
                },
                status=503,
            )
            return
        try:
            graph = load_graph(graph_path)
            if isinstance(graph, SQLiteRoadGraph):
                empty = graph.node_count == 0 or (
                    graph.edge_count == 0 and graph.ferry_edge_count == 0
                )
            else:
                coordinates, adjacency = graph
                empty = not coordinates or not adjacency
            if empty:
                self._write_json(
                    {
                        "contract": ROUTE_MATRIX_CONTRACT,
                        "status": "routing_not_provided",
                        "error": "the road graph is empty",
                    },
                    status=503,
                )
                return
            try:
                result = query_route_matrix(origins, destinations, graph, **options)
            finally:
                if isinstance(graph, SQLiteRoadGraph):
                    graph.close()
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self._write_json(
                {
                    "contract": ROUTE_MATRIX_CONTRACT,
                    "status": "routing_error",
                    "error": str(exc),
                },
                status=500,
            )
            return
        self._write_json(result, cache_control="no-cache")

    def _write_route_comparison(self, *, json_body: dict[str, object] | None = None) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        try:
            if json_body is None:
                start_lat = _coordinate(
                    _query_first(query, "start_lat"), "start_lat", -90.0, 90.0
                )
                start_lon = _coordinate(
                    _query_first(query, "start_lon"), "start_lon", -180.0, 180.0
                )
                goal_lat = _coordinate(
                    _query_first(query, "goal_lat"), "goal_lat", -90.0, 90.0
                )
                goal_lon = _coordinate(
                    _query_first(query, "goal_lon"), "goal_lon", -180.0, 180.0
                )
                profile_values = query.get("profile", [])
                if len(profile_values) < ROUTE_COMPARISON_MIN_PROFILES:
                    raise ValueError(
                        f"at least {ROUTE_COMPARISON_MIN_PROFILES} route profiles are required"
                    )
                if len(profile_values) > ROUTE_COMPARISON_MAX_PROFILES:
                    raise ValueError(
                        f"route profile count must not exceed {ROUTE_COMPARISON_MAX_PROFILES}"
                    )
                profiles = [parse_route_profile_spec(value) for value in profile_values]
                parsed_options = _parse_route_options(query)
            else:
                (
                    start_lat,
                    start_lon,
                    goal_lat,
                    goal_lon,
                    profiles,
                    parsed_options,
                ) = _json_comparison_request(json_body)
            options = {
                key: parsed_options[key]
                for key in (
                    "departure",
                    "speed_kmh",
                    "include_path",
                    "include_ferries",
                    "objective",
                )
            }
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": ROUTE_COMPARISON_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
            )
            return

        graph_path = getattr(self.server, "routing_graph_path", None)
        if graph_path is None or not _graph_available(graph_path):
            self._write_json(
                {
                    "contract": ROUTE_COMPARISON_CONTRACT,
                    "status": "routing_not_provided",
                    "error": "no supported road graph is available",
                },
                status=503,
            )
            return
        try:
            graph = load_graph(graph_path)
            if isinstance(graph, SQLiteRoadGraph):
                empty = graph.node_count == 0 or (
                    graph.edge_count == 0 and graph.ferry_edge_count == 0
                )
            else:
                coordinates, adjacency = graph
                empty = not coordinates or not adjacency
            if empty:
                self._write_json(
                    {
                        "contract": ROUTE_COMPARISON_CONTRACT,
                        "status": "routing_not_provided",
                        "error": "the road graph is empty",
                    },
                    status=503,
                )
                return
            try:
                result = query_route_comparison(
                    start_lat,
                    start_lon,
                    goal_lat,
                    goal_lon,
                    graph,
                    profiles,
                    **options,
                )
            finally:
                if isinstance(graph, SQLiteRoadGraph):
                    graph.close()
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self._write_json(
                {
                    "contract": ROUTE_COMPARISON_CONTRACT,
                    "status": "routing_error",
                    "error": str(exc),
                },
                status=500,
            )
            return
        self._write_json(result, cache_control="no-cache")

    def _accepts_gzip(self) -> bool:
        """Return whether the client explicitly accepts gzip content."""
        gzip_quality: float | None = None
        wildcard_quality: float | None = None
        for value in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = [part.strip() for part in value.split(";")]
            if not parts or not parts[0]:
                continue
            quality = next((part[2:] for part in parts[1:] if part.startswith("q=")), "1")
            try:
                parsed_quality = float(quality)
            except ValueError:
                parsed_quality = 0.0
            if parts[0] == "gzip":
                gzip_quality = parsed_quality
            elif parts[0] == "*":
                wildcard_quality = parsed_quality
        if gzip_quality is not None:
            return gzip_quality > 0
        return wildcard_quality is not None and wildcard_quality > 0

    def _write_gzipped_data_pack(self) -> None:
        data_path = Path(self.directory or ".").resolve() / "report_data.json"
        if not data_path.is_file():
            if self._head_only:
                super().do_HEAD()
            else:
                super().do_GET()
            return
        body, response_etag = self.server.compressed_data_info(data_path)  # type: ignore[attr-defined]
        if _etag_matches(self.headers.get("If-None-Match"), response_etag):
            self._write_not_modified(
                response_etag,
                cache_control="no-cache",
                content_encoding="gzip",
                vary="Accept-Encoding",
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", response_etag)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)


class ReportHTTPServer(ThreadingHTTPServer):
    """Threaded server with address reuse and daemon request threads."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._gzip_cache_key: tuple[int, int] | None = None
        self._gzip_cache: bytes | None = None
        self._gzip_cache_etag: str | None = None
        self._gzip_lock = threading.Lock()
        self._report_data_cache_key: tuple[int, int] | None = None
        self._report_data_cache: dict[str, object] | None = None
        self._report_data_lock = threading.Lock()
        self.routing_graph_path: Path | None = None
        self.data_root: Path | None = None

    def compressed_data(self, path: Path) -> bytes:
        """Return deterministic gzip bytes, recompressing only after changes."""
        return self.compressed_data_info(path)[0]

    def compressed_data_info(self, path: Path) -> tuple[bytes, str]:
        """Return deterministic gzip bytes and their representation ETag."""
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        with self._gzip_lock:
            if (
                self._gzip_cache_key != key
                or self._gzip_cache is None
                or self._gzip_cache_etag is None
            ):
                self._gzip_cache = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
                self._gzip_cache_key = key
                self._gzip_cache_etag = _etag(self._gzip_cache)
            return self._gzip_cache, self._gzip_cache_etag

    def report_data(self, path: Path) -> dict[str, object]:
        """Load and cache the full report pack for server-side filtering."""
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        with self._report_data_lock:
            if self._report_data_cache_key == key and self._report_data_cache is not None:
                return self._report_data_cache
            payload, error = _read_json_object(path)
            if error:
                raise ValueError(error)
            if payload is None:
                raise FileNotFoundError(path)
            self._report_data_cache_key = key
            self._report_data_cache = payload
            return payload


def create_server(
    out_dir: str | Path | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    report: str = DEFAULT_REPORT,
    project_root: str | Path | None = None,
    data_root: str | Path | None = None,
    road_graph: str | Path | None = None,
) -> ReportHTTPServer:
    """Create, but do not start, a report server.

    ``port=0`` is supported for tests and callers that want the OS to select a
    free port. The default binds only to localhost so serving the output
    directory does not accidentally expose it to the network.
    """
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    configured_project_root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None
        else None
    )
    resolution_root = configured_project_root or default_project_root()

    def resolve_path(value: str | Path | None, default: str | Path) -> Path:
        candidate = Path(value) if value is not None else Path(default)
        return candidate if candidate.is_absolute() else resolution_root / candidate

    output_input = reject_symlink_root(
        resolve_path(out_dir, "output").expanduser(),
        label="Output directory",
    )
    output = output_input.resolve()
    if not output.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output}")
    _require_symlink_free_output(output)
    report_path = _relative_file(output, report)
    report_name = str(report_path.relative_to(output))
    active_project_root = configured_project_root or output.parent
    handler = partial(
        ReportRequestHandler,
        directory=str(output),
        report_name=report_name,
    )
    server = ReportHTTPServer((host, port), handler)
    server.report_name = report_name  # type: ignore[attr-defined]
    server.output_dir = output  # type: ignore[attr-defined]
    default_data = active_project_root / "data"
    data_input = reject_symlink_root(
        resolve_path(data_root, default_data).expanduser(),
        label="Data directory",
    )
    data_input = reject_symlink_tree(data_input, label="Data directory")
    graph_input = resolve_path(road_graph, data_input / "roads").expanduser()
    reject_symlink_path(graph_input, label="Road graph input")
    if graph_input.exists() and graph_input.is_dir():
        reject_symlink_tree(graph_input, label="Road graph input")
    data = data_input.resolve()
    server.project_root = active_project_root  # type: ignore[attr-defined]
    server.data_root = data
    server.routing_graph_path = graph_input.resolve()
    return server


def _graph_available(path: Path | None) -> bool:
    if path is None:
        return False
    if path.is_file():
        return True
    return path.is_dir() and any(
        (path / name).is_file() for name in ("road_graph.sqlite", "road_nodes.csv", "road_edges.csv")
    )


def _routing_graph_inventory(path: Path | None) -> dict[str, object]:
    """Describe the backend and semantics of the graph loaded by this server.

    The project-level Doctor inventory describes every routing feature present in
    the installed code.  A running server, however, may be pointed at either a
    full SQLite graph or the deliberately smaller portable CSV/JSON graph.  The
    latter does not implement SQLite vehicle profiles, conditional rules, turn
    restrictions, or ferry schedules, so those capabilities must not be inferred
    from the static project inventory.
    """
    empty_counts = {
        "node_n": None,
        "edge_n": None,
        "ferry_edge_n": None,
        "way_context_n": None,
    }
    empty_features = {
        "path_segment_explainability": False,
        "way_context": False,
        "vehicle_profiles": False,
        "conditional_rules": False,
        "turn_restrictions": False,
        "ferry_geometry": False,
        "ferry_schedules": False,
    }
    unavailable = {
        "available": False,
        "backend": "not_available",
        "format": None,
        "metadata_status": "not_provided",
        "profile_semantics": "not_available",
        "path_segment_sources": [],
        "counts": empty_counts,
        "features": empty_features,
    }
    if path is None:
        return unavailable

    candidate = Path(path)
    backend = "not_available"
    metadata_path: Path | None = None
    if candidate.is_dir():
        sqlite_path = candidate / "road_graph.sqlite"
        if sqlite_path.is_file():
            backend = "sqlite"
            metadata_path = candidate / "road_graph_metadata.json"
        elif (candidate / "road_nodes.csv").is_file() and (candidate / "road_edges.csv").is_file():
            backend = "portable"
            metadata_path = candidate / "road_graph_metadata.json"
    elif candidate.is_file():
        if candidate.suffix.lower() in {".sqlite", ".db"}:
            backend = "sqlite"
            metadata_path = candidate.parent / "road_graph_metadata.json"
        elif candidate.suffix.lower() == ".json":
            backend = "portable"
            metadata_path = candidate.parent / "road_graph_metadata.json"

    if backend == "not_available":
        return unavailable

    metadata: dict[str, object] = {}
    metadata_status = "not_provided"
    if metadata_path is not None:
        metadata, error = _read_json_object(metadata_path)
        if error is not None:
            metadata_status = "invalid"
        elif metadata is not None:
            metadata_status = "available"
        metadata = metadata or {}
    counts: dict[str, int | None] = {}
    for field in ("node_n", "edge_n", "ferry_edge_n", "way_context_n"):
        value = metadata.get(field)
        if isinstance(value, bool):
            counts[field] = None
            continue
        try:
            count = int(value) if value is not None else None
        except (TypeError, ValueError):
            count = None
        counts[field] = count if count is None or count >= 0 else None
    if backend == "sqlite":
        features = {
            "path_segment_explainability": True,
            "way_context": True,
            "vehicle_profiles": True,
            "conditional_rules": True,
            "turn_restrictions": True,
            "ferry_geometry": True,
            "ferry_schedules": True,
        }
        profile_semantics = "full_sqlite"
        path_segment_sources = ["sqlite_edges", "not_available"]
    else:
        features = {
            "path_segment_explainability": True,
            "way_context": True,
            "vehicle_profiles": False,
            "conditional_rules": False,
            "turn_restrictions": False,
            "ferry_geometry": True,
            "ferry_schedules": False,
        }
        profile_semantics = "portable_basic"
        path_segment_sources = ["portable_edges", "not_available"]
    return {
        "available": True,
        "backend": backend,
        "format": metadata.get("format") or ("sqlite" if backend == "sqlite" else "json_or_csv"),
        "metadata_status": metadata_status,
        "profile_semantics": profile_semantics,
        "path_segment_sources": path_segment_sources,
        "counts": counts,
        "features": features,
    }


def _coordinate(value: str | None, name: str, minimum: float, maximum: float) -> float:
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be finite and within [{minimum}, {maximum}]")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--out-dir", default=None, help="report output directory; defaults to output/")
    parser.add_argument(
        "--project-root",
        default=None,
        help="active project root for portable manifest source paths",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address; defaults to localhost")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port; 0 selects a free port")
    parser.add_argument("--data-root", default=None, help="routing data directory; defaults beside --out-dir")
    parser.add_argument("--road-graph", default=None, help="routing graph directory/file; defaults to data/roads")
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"report file inside --out-dir (default: {DEFAULT_REPORT})",
    )
    parser.add_argument("--open", action="store_true", help="open the report in the default browser")
    args = parser.parse_args(argv)

    try:
        server = create_server(
            args.out_dir,
            host=args.host,
            port=args.port,
            report=args.report,
            project_root=args.project_root,
            data_root=args.data_root,
            road_graph=args.road_graph,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    url = report_url(server, server.report_name)  # type: ignore[attr-defined]
    health_url = f"{url.rsplit('/', 1)[0]}{HEALTH_PATH}"
    print(f"Serving {server.output_dir} on {url}", flush=True)  # type: ignore[attr-defined]
    print(f"Health: {health_url}", flush=True)
    print(f"Route API: {health_url.rsplit('/', 1)[0]}{ROUTE_API_PATH}", flush=True)
    print(f"Query API: {health_url.rsplit('/', 1)[0]}{QUERY_API_PATH}", flush=True)
    print(f"Metadata API: {health_url.rsplit('/', 1)[0]}{METADATA_API_PATH}", flush=True)
    print(f"Capabilities API: {health_url.rsplit('/', 1)[0]}{CAPABILITIES_API_PATH}", flush=True)
    print(f"OpenAPI: {health_url.rsplit('/', 1)[0]}{OPENAPI_PATH}", flush=True)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: the output directory is reachable beyond localhost.", file=sys.stderr)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping report server.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
