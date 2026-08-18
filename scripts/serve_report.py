#!/usr/bin/env python3
"""Serve the generated Ireland geometry report from a local HTTP server.

The lazy report fetches ``report_data.json`` at runtime, which browsers may
block when the HTML is opened directly from ``file://``. This module provides
an intentionally localhost-first server using only the Python standard
library. It serves the selected report and generated output directory;
``/__health`` provides a small machine-readable smoke-test endpoint, and
``/api/capabilities`` provides versioned endpoint discovery for automation.
``/api/openapi.json`` provides the read-only API's versioned OpenAPI document.
``/api/interpretation`` provides compact data-derived findings without
downloading the full report data pack.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

try:
    from query_data import (
        _cursor_for_row,
        _json_safe,
        probe_backend,
        query_rows,
    )
    from report import (
        REPORT_EXPORT_CONTRACT,
        REPORT_FILTER_SORT_KEYS,
        REPORT_PAGE_CONTRACT,
        REPORT_PAGE_DEFAULT_LIMIT,
        REPORT_PAGE_MAX_LIMIT,
        report_csv,
        report_geojson,
        report_page_payload,
    )
    from road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from route_query import ROUTE_CONTRACT, query_route, route_geojson
    from runtime import SOURCE_FRESHNESS_CONTRACT, package_version, project_path
except ImportError:
    from scripts.query_data import (
        _cursor_for_row,
        _json_safe,
        probe_backend,
        query_rows,
    )
    from scripts.report import (
        REPORT_EXPORT_CONTRACT,
        REPORT_FILTER_SORT_KEYS,
        REPORT_PAGE_CONTRACT,
        REPORT_PAGE_DEFAULT_LIMIT,
        REPORT_PAGE_MAX_LIMIT,
        report_csv,
        report_geojson,
        report_page_payload,
    )
    from scripts.road_routing import SQLiteRoadGraph, load_graph, parse_departure
    from scripts.route_query import ROUTE_CONTRACT, query_route, route_geojson
    from scripts.runtime import SOURCE_FRESHNESS_CONTRACT, package_version, project_path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_REPORT = "report_lazy.html"
HEALTH_PATH = "/__health"
ROUTE_API_PATH = "/api/route"
QUERY_API_PATH = "/api/query"
METADATA_API_PATH = "/api/metadata"
CAPABILITIES_API_PATH = "/api/capabilities"
OPENAPI_PATH = "/api/openapi.json"
INTERPRETATION_API_PATH = "/api/interpretation"
REPORT_PAGE_API_PATH = "/api/report/page"
REPORT_EXPORT_API_PATH = "/api/report/export"
HEALTH_CONTRACT = "ireland-geometry.health.v1"
QUERY_CONTRACT = "ireland-geometry.query.v1"
METADATA_CONTRACT = "ireland-geometry.metadata.v1"
CAPABILITIES_CONTRACT = "ireland-geometry.capabilities.v1"
OPENAPI_CONTRACT = "ireland-geometry.openapi.v1"
INTERPRETATION_CONTRACT = "ireland-geometry.interpretation.v1"
API_CONTRACTS = {
    "health": HEALTH_CONTRACT,
    "capabilities": CAPABILITIES_CONTRACT,
    "metadata": METADATA_CONTRACT,
    "query": QUERY_CONTRACT,
    "route": ROUTE_CONTRACT,
    "openapi": OPENAPI_CONTRACT,
    "interpretation": INTERPRETATION_CONTRACT,
    "report_page": REPORT_PAGE_CONTRACT,
    "report_export": REPORT_EXPORT_CONTRACT,
}
QUERY_MAX_LIMIT = 1000
QUERY_MAX_OFFSET = 10_000_000
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


def _openapi_json_response(schema: str, description: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema}"}
            }
        },
    }


def _openapi_error_response(description: str) -> dict[str, object]:
    return _openapi_json_response("ApiError", description)


def openapi_document() -> dict[str, object]:
    """Return the versioned, dependency-free schema for the local read API."""
    report_parameters = [
        _openapi_query_parameter("q", {"type": "string"}, description="Case-insensitive target search."),
        _openapi_query_parameter("group", {"type": "string"}, description="Exact target group."),
        _openapi_query_parameter("century", {"type": "string"}, description="Exact NIAH century filter."),
        _openapi_query_parameter("rating", {"type": "string"}, description="Exact NIAH rating filter."),
        _openapi_query_parameter("type", {"type": "string"}, description="Exact NIAH type filter."),
        _openapi_query_parameter("review", {"type": "string"}, description="Review queue state."),
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
    return {
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
                    "responses": {"200": _openapi_json_response("HealthResponse", "Health status")},
                }
            },
            CAPABILITIES_API_PATH: {
                "get": {
                    "operationId": "getCapabilities",
                    "summary": "Discover available local API capabilities",
                    "responses": {"200": _openapi_json_response("CapabilitiesResponse", "Capability inventory")},
                }
            },
            OPENAPI_PATH: {
                "get": {
                    "operationId": "getOpenApiDocument",
                    "summary": "Return this OpenAPI document",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document",
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
                        "200": _openapi_json_response("MetadataResponse", "Build metadata"),
                    },
                }
            },
            INTERPRETATION_API_PATH: {
                "get": {
                    "operationId": "getInterpretation",
                    "summary": "Return compact data-derived findings",
                    "responses": {
                        "200": _openapi_json_response(
                            "InterpretationResponse", "Compact interpretation sidecar"
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
                        "200": _openapi_json_response("ReportPageResponse", "Report target page"),
                        "400": _openapi_error_response("Invalid report page parameters"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "500": _openapi_error_response("Report data pack is invalid"),
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
                            "content": {
                                "text/csv": {"schema": {"type": "string"}},
                                "application/geo+json": {"schema": {"$ref": "#/components/schemas/GeoJSONFeatureCollection"}},
                            },
                        },
                        "400": _openapi_error_response("Invalid export parameters"),
                        "404": _openapi_error_response("Report data pack is not available"),
                        "500": _openapi_error_response("Report data pack is invalid"),
                    },
                }
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
                        "200": _openapi_json_response("QueryResponse", "Analysis rows"),
                        "400": _openapi_error_response("Invalid query parameters"),
                        "503": _openapi_error_response("No readable query backend"),
                    },
                }
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
                            description="Positive routing speed in km/h.",
                        ),
                        _openapi_query_parameter(
                            "departure",
                            {"type": "string", "format": "date-time"},
                            description="Optional ISO-8601 departure for conditional profiles.",
                        ),
                        _openapi_query_parameter(
                            "include_path",
                            {"type": "boolean", "default": False},
                            description="Include reconstructed path node IDs and coordinates.",
                        ),
                        _openapi_query_parameter(
                            "include_ferries",
                            {"type": "boolean", "default": False},
                            description="Include static ferry geometry when available.",
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
                }
            },
        },
        "components": {
            "schemas": {
                "ApiError": {
                    "type": "object",
                    "required": ["contract", "status", "error"],
                    "properties": {
                        "contract": {"type": "string"},
                        "status": {"type": "string"},
                        "error": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "HealthResponse": {
                    "type": "object",
                    "required": ["contract", "status", "ready", "analysis_ready", "validation"],
                    "properties": {
                        "contract": {"const": HEALTH_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "degraded"]},
                        "ready": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
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
                        "validation",
                        "contracts",
                        "endpoints",
                        "source_freshness",
                    ],
                    "properties": {
                        "contract": {"const": CAPABILITIES_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "degraded"]},
                        "ready": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
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
                "MetadataResponse": {
                    "type": "object",
                    "required": ["contract", "status", "source_freshness"],
                    "properties": {
                        "contract": {"const": METADATA_CONTRACT},
                        "status": {"type": "string"},
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
                        "validation",
                        "interpretation",
                    ],
                    "properties": {
                        "contract": {"const": INTERPRETATION_CONTRACT},
                        "status": {"type": "string", "enum": ["ok", "not_provided", "error"]},
                        "available": {"type": "boolean"},
                        "analysis_ready": {"type": "boolean"},
                        "validation": {"$ref": "#/components/schemas/ValidationStatus"},
                        "summary": {"type": "object", "additionalProperties": True},
                        "interpretation": {"type": ["object", "null"]},
                        "source": {"type": "string"},
                        "error": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "ReportPageResponse": {
                    "type": "object",
                    "required": ["contract", "status", "paged", "targets", "page", "filter_options"],
                    "properties": {
                        "contract": {"const": REPORT_PAGE_CONTRACT},
                        "status": {"type": "string", "const": "ok"},
                        "paged": {"type": "boolean", "const": True},
                        "initial": {"type": "boolean"},
                        "targets": {"type": "array", "items": {"type": "object"}},
                        "page": {"type": "object", "additionalProperties": True},
                        "filters": {"type": "object", "additionalProperties": True},
                        "filter_options": {"type": "object", "additionalProperties": True},
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
                "RouteResponse": {
                    "type": "object",
                    "required": ["contract", "reachable"],
                    "properties": {
                        "contract": {"const": ROUTE_CONTRACT},
                        "status": {"type": "string"},
                        "reachable": {"type": "boolean"},
                        "route_distance_m": {"type": ["number", "null"]},
                        "estimated_duration_s": {"type": ["number", "null"]},
                        "arrival": {"type": ["string", "null"], "format": "date-time"},
                    },
                    "additionalProperties": True,
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


class ReportRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler with health and local route-query endpoints."""

    server_version = "IrelandGeometryReport/1"

    def __init__(self, *args, report_name: str, **kwargs):
        self.report_name = report_name
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            self._write_health()
            return
        if path == ROUTE_API_PATH:
            self._write_route()
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

    def copyfile(self, source, outputfile) -> None:
        """Ignore a client closing a download before the file is complete."""
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _write_health(self) -> None:
        directory = Path(self.directory or ".").resolve()
        report_path = directory / self.report_name
        data_path = directory / "report_data.json"
        graph_path = getattr(self.server, "routing_graph_path", None)
        report_exists = report_path.is_file()
        data_pack_exists = data_path.is_file()
        data_pack_required = self.report_name == DEFAULT_REPORT
        ready = report_exists and (not data_pack_required or data_pack_exists)
        validation = _validation_status(directory)
        query_backend_health = _query_backend_health(directory)
        query_readable_backends = [
            name for name, status in query_backend_health.items() if status["readable"]
        ]
        payload = {
            "contract": HEALTH_CONTRACT,
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "analysis_ready": ready and validation["passed"] and validation["manifest_available"],
            "validation": validation,
            "report": self.report_name,
            "report_exists": report_exists,
            "data_pack_exists": data_pack_exists,
            "data_pack_required": data_pack_required,
            "data_pack_gzip": data_pack_exists,
            "routing_available": _graph_available(graph_path),
            "route_endpoint": ROUTE_API_PATH,
            "query_available": bool(query_readable_backends),
            "query_endpoint": QUERY_API_PATH,
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
        }
        self._write_json(payload)

    def _write_json(
        self,
        payload: dict,
        *,
        status: int = 200,
        content_type: str = "application/json",
        cache_control: str = "no-store",
    ) -> None:
        body = json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode("utf-8")
        response_etag = _etag(body) if cache_control != "no-store" else None
        if response_etag and _etag_matches(self.headers.get("If-None-Match"), response_etag):
            self._write_not_modified(response_etag, cache_control=cache_control)
            return
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if response_etag:
            self.send_header("ETag", response_etag)
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        filename: str | None = None,
        cache_control: str = "no-cache",
    ) -> None:
        response_etag = _etag(body) if cache_control != "no-store" else None
        if response_etag and _etag_matches(self.headers.get("If-None-Match"), response_etag):
            self._write_not_modified(response_etag, cache_control=cache_control)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if response_etag:
            self.send_header("ETag", response_etag)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _write_not_modified(
        self,
        response_etag: str,
        *,
        cache_control: str,
        content_encoding: str | None = None,
        vary: str | None = None,
    ) -> None:
        self.send_response(304)
        self.send_header("ETag", response_etag)
        self.send_header("Cache-Control", cache_control)
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        if vary:
            self.send_header("Vary", vary)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_query(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

        def value(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

        try:
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
        except ValueError as exc:
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
        )

    def _report_filters(self) -> tuple[dict[str, object], int, int, bool]:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

        def value(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

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
            "review_state": value("review") or "",
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

    def _write_report_page(self) -> None:
        try:
            filters, limit, offset, initial = self._report_filters()
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
        try:
            payload = report_page_payload(
                data,
                limit=limit,
                offset=offset,
                initial=initial,
                include_static=initial,
                **filters,
            )
        except (TypeError, ValueError) as exc:
            self._write_json(
                {"contract": REPORT_PAGE_CONTRACT, "status": "error", "error": str(exc)},
                status=400,
                cache_control="no-cache",
            )
            return
        self._write_json(payload, cache_control="no-cache")

    def _write_report_export(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        format_name = (query.get("format") or [""])[0].strip().lower()
        if format_name not in {"csv", "geojson"}:
            self._write_json(
                {
                    "contract": REPORT_EXPORT_CONTRACT,
                    "status": "error",
                    "error": "format must be csv or geojson",
                },
                status=400,
                cache_control="no-cache",
            )
            return
        try:
            filters, _limit, _offset, _initial = self._report_filters()
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
        try:
            if format_name == "csv":
                body = report_csv(data, **filters).encode("utf-8")
                self._write_bytes(body, content_type="text/csv; charset=utf-8", filename="ireland-geometry-filtered.csv")
            else:
                body = json.dumps(
                    _json_safe(report_geojson(data, **filters)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                self._write_bytes(body, content_type="application/geo+json; charset=utf-8", filename="ireland-geometry-filtered.geojson")
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

        status = "degraded" if errors else ("ok" if records["manifest"] else "not_provided")
        self._write_json(
            {
                "contract": METADATA_CONTRACT,
                "status": status,
                "errors": errors,
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
        validation = _validation_status(directory)
        if error:
            self._write_json(
                {
                    "contract": INTERPRETATION_CONTRACT,
                    "status": "error",
                    "available": False,
                    "analysis_ready": False,
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
        response["analysis_ready"] = bool(
            validation["passed"] and validation["manifest_available"]
        )
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
        graph_path = getattr(self.server, "routing_graph_path", None)
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
                "analysis_ready": ready and validation["passed"] and validation["manifest_available"],
                "validation": validation,
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
                        "contract": REPORT_PAGE_CONTRACT,
                        "available": data_pack_exists,
                        "pagination": True,
                        "max_limit": REPORT_PAGE_MAX_LIMIT,
                    },
                    "report_export": {
                        "path": REPORT_EXPORT_API_PATH,
                        "method": "GET",
                        "contract": REPORT_EXPORT_CONTRACT,
                        "available": data_pack_exists,
                        "formats": ["csv", "geojson"],
                    },
                    "query": {
                        "path": QUERY_API_PATH,
                        "method": "GET",
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
                    },
                    "route": {
                        "path": ROUTE_API_PATH,
                        "method": "GET",
                        "contract": ROUTE_CONTRACT,
                        "available": _graph_available(graph_path),
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

    def _write_route(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

        def value(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

        def boolean(name: str) -> bool:
            return (value(name) or "").strip().lower() in TRUE_QUERY_VALUES

        try:
            start_lat = _coordinate(value("start_lat"), "start_lat", -90.0, 90.0)
            start_lon = _coordinate(value("start_lon"), "start_lon", -180.0, 180.0)
            goal_lat = _coordinate(value("goal_lat"), "goal_lat", -90.0, 90.0)
            goal_lon = _coordinate(value("goal_lon"), "goal_lon", -180.0, 180.0)
            speed_text = value("speed_kmh") or "50"
            speed_kmh = float(speed_text)
            if not math.isfinite(speed_kmh) or speed_kmh <= 0:
                raise ValueError("speed_kmh must be a finite positive number")
            departure = parse_departure(value("departure"))
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
                    departure=departure,
                    speed_kmh=speed_kmh,
                    include_path=boolean("include_path") or value("format") == "geojson",
                    include_ferries=boolean("include_ferries"),
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
        if value("format") == "geojson":
            self._write_json(route_geojson(result), content_type="application/geo+json")
        else:
            result["contract"] = ROUTE_CONTRACT
            self._write_json(result)

    def _accepts_gzip(self) -> bool:
        """Return whether the client explicitly accepts gzip content."""
        for value in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = [part.strip() for part in value.split(";")]
            if parts[0] != "gzip":
                continue
            quality = next((part[2:] for part in parts[1:] if part.startswith("q=")), "1")
            try:
                return float(quality) > 0
            except ValueError:
                return False
        return False

    def _write_gzipped_data_pack(self) -> None:
        data_path = Path(self.directory or ".").resolve() / "report_data.json"
        if not data_path.is_file():
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
    output = project_path(out_dir, "output").resolve()
    if not output.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output}")
    report_path = _relative_file(output, report)
    report_name = str(report_path.relative_to(output))
    handler = partial(
        ReportRequestHandler,
        directory=str(output),
        report_name=report_name,
    )
    server = ReportHTTPServer((host, port), handler)
    server.report_name = report_name  # type: ignore[attr-defined]
    server.output_dir = output  # type: ignore[attr-defined]
    default_data = output.parent / "data"
    data = project_path(data_root, str(default_data)).resolve()
    server.routing_graph_path = project_path(road_graph, str(data / "roads")).resolve()
    return server


def _graph_available(path: Path | None) -> bool:
    if path is None:
        return False
    if path.is_file():
        return True
    return path.is_dir() and any(
        (path / name).is_file() for name in ("road_graph.sqlite", "road_nodes.csv", "road_edges.csv")
    )


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
