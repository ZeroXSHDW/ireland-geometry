#!/usr/bin/env python3
"""Inspect the runtime, cached inputs, optional sources, and generated outputs."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from runtime import (
        PACKAGE_NAME,
        PACKAGE_ROOT,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        git_dirty,
        git_revision,
        package_version,
        path_age_seconds,
        path_modified_at,
        runtime_signature,
        sha256_file,
        utc_now,
    )
    from runtime import ROOT as DEFAULT_PROJECT_ROOT
except ImportError:
    from scripts.runtime import (
        PACKAGE_NAME,
        PACKAGE_ROOT,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        git_dirty,
        git_revision,
        package_version,
        path_age_seconds,
        path_modified_at,
        runtime_signature,
        sha256_file,
        utc_now,
    )
    from scripts.runtime import ROOT as DEFAULT_PROJECT_ROOT


MIN_PYTHON = (3, 10)
REQUIRED_DEPENDENCIES = ("numpy", "shapely", "requests", "osmium")
OPTIONAL_DEPENDENCIES = ("pyarrow", "duckdb", "rasterio", "laspy")
STRICT_OUTPUT_NAMES = (
    "analysis_results.csv",
    "report.html",
    "interpretation.json",
    "verification.json",
    "manifest.json",
)
NON_CACHEABLE_STAGES = {"report", "repro-check", "verify"}


def _file_contains(path: Path, needle: str) -> bool:
    """Search a text artifact without loading a large standalone report at once."""
    if not path.is_file():
        return False
    overlap = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            while chunk := handle.read(1 << 20):
                text = overlap + chunk
                if needle in text:
                    return True
                overlap = text[-max(0, len(needle) - 1) :]
    except OSError:
        return False
    return False


def pipeline_status(root: Path) -> dict[str, Any]:
    """Describe the available stages and the incremental execution contract."""
    module_path = root / "run_pipeline.py"
    result: dict[str, Any] = {
        "label": "pipeline stages",
        "module": str(module_path),
        "command": "ireland-geometry",
        "supports_no_network": True,
        "supports_incremental": True,
        "supports_dry_run": True,
        "supports_dry_run_json": False,
        "supports_dry_run_cache_explanations": False,
        "post_validation_report_refresh": False,
        "preserves_diagnostic_manifest_context": False,
        "diagnostic_json_outputs": False,
        "parameter_validation": False,
        "cache_version": None,
        "dependency_aware_cache": False,
        "cache_explanations": False,
        "status": "not_installed",
        "stage_count": 0,
        "stages": [],
    }
    if not module_path.is_file():
        return result
    try:
        spec = importlib.util.spec_from_file_location("_ireland_geometry_pipeline_contract", module_path)
        if spec is None or spec.loader is None:
            raise ImportError("could not load pipeline module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stages = list(getattr(module, "STAGES", ()))
        scripts = dict(getattr(module, "SCRIPTS", {}))
    except (ImportError, OSError, AttributeError, TypeError, ValueError) as exc:
        result["status"] = "invalid"
        result["error"] = str(exc)
        return result
    stage_rows = []
    for stage in stages:
        script = root / "scripts" / str(scripts.get(stage, ""))
        stage_rows.append(
            {
                "name": stage,
                "script": str(script),
                "exists": script.is_file(),
                "cacheable": stage not in NON_CACHEABLE_STAGES,
            }
        )
    result["stage_count"] = len(stage_rows)
    result["parameter_validation"] = all(
        _file_contains(module_path, token)
        for token in (
            "--mc must be positive",
            "--bootstrap-iterations must be positive",
            "--holdout-fraction must be finite",
            "At least one pipeline stage must be selected",
        )
    )
    result["supports_dry_run_json"] = all(
        _file_contains(module_path, token)
        for token in ("--dry-run-json", '"ireland-geometry.dry-run.v1"')
    )
    result["supports_dry_run_cache_explanations"] = result["supports_dry_run_json"] and all(
        _file_contains(module_path, token)
        for token in ('"cache_explanations": True', '"cache": cache_info')
    )
    result["post_validation_report_refresh"] = all(
        _file_contains(module_path, token)
        for token in (
            'post_validation_refresh = verify_requested and "report" in stages',
            '"post_validation_refresh":',
            'phase": "post_validation_refresh"',
            "if post_validation_refresh:",
        )
    )
    result["preserves_diagnostic_manifest_context"] = all(
        _file_contains(module_path, token)
        for token in ("DIAGNOSTIC_ONLY_STAGES", "provenance_context_preserved", "last_invocation")
    )
    diagnostic_scripts = (
        root / "scripts" / "schema_audit.py",
        root / "scripts" / "repro_check.py",
    )
    result["diagnostic_json_outputs"] = all(
        script.is_file() and _file_contains(script, "--json") for script in diagnostic_scripts
    )
    cache_module = root / "scripts" / "stage_cache.py"
    result["cache_version"] = 5 if _file_contains(cache_module, "CACHE_VERSION = 5") else None
    result["dependency_aware_cache"] = cache_module.is_file() and all(
        _file_contains(cache_module, token)
        for token in ("def local_module_paths(", "def local_module_signatures(", '"local_module_sha256"')
    )
    result["cache_explanations"] = cache_module.is_file() and all(
        _file_contains(cache_module, token)
        for token in ("def reuse_diagnostics(", '"fingerprint_inputs"', '"cache_status"')
    )
    result["stages"] = stage_rows
    result["status"] = (
        "available"
        if (
            stage_rows
            and all(row["exists"] for row in stage_rows)
            and result["dependency_aware_cache"]
            and result["cache_explanations"]
            and result["diagnostic_json_outputs"]
            and result["post_validation_report_refresh"]
        )
        else "incomplete"
    )
    return result


def console_commands_status(root: Path) -> dict[str, Any]:
    """Inventory console commands, module targets, and installed wrappers."""
    path = root / "pyproject.toml"
    result: dict[str, Any] = {
        "label": "console commands",
        "path": str(path),
        "commands": {},
        "command_count": 0,
        "installed_count": 0,
        "installation_status": "not_installed",
        "source": None,
        "status": "not_installed",
    }

    declared: list[tuple[str, str]] = []
    if path.is_file():
        result["source"] = "pyproject"
        in_scripts = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            result["status"] = "invalid"
            result["error"] = str(exc)
            return result
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                in_scripts = stripped == "[project.scripts]"
                continue
            if not in_scripts or "=" not in stripped:
                continue
            name, _, raw_target = stripped.partition("=")
            command = name.strip()
            target = raw_target.strip().split("#", 1)[0].strip().strip('"\'')
            if command and target:
                declared.append((command, target))
    else:
        try:
            distribution = importlib.metadata.distribution(PACKAGE_NAME)
        except importlib.metadata.PackageNotFoundError:
            distribution = None
        if distribution is not None:
            result["source"] = "installed_distribution"
            declared = [
                (entry_point.name, entry_point.value)
                for entry_point in distribution.entry_points
                if entry_point.group == "console_scripts"
            ]

    commands: dict[str, dict[str, Any]] = {}
    for command, target in declared:
        module, separator, function = target.partition(":")
        if not command or not separator or not module or not function:
            continue
        module_path = root / (module.replace(".", "/") + ".py")
        if not module_path.is_file():
            try:
                spec = importlib.util.find_spec(module)
            except (ImportError, ModuleNotFoundError, ValueError):
                spec = None
            if spec is not None and spec.origin and spec.origin not in {"built-in", "frozen"}:
                module_path = Path(spec.origin)
        executable_path: Path | None = None
        sibling = Path(sys.executable).with_name(command)
        if sibling.is_file() and os.access(sibling, os.X_OK):
            executable_path = sibling
        else:
            located = shutil.which(command)
            if located:
                executable_path = Path(located)
        commands[command] = {
            "target": target,
            "module": module,
            "function": function,
            "module_path": str(module_path),
            "exists": module_path.is_file(),
            "entrypoint": module_path.is_file() and _file_contains(module_path, f"def {function}("),
            "installed": executable_path is not None,
            "executable_path": str(executable_path) if executable_path else None,
        }
    result["commands"] = commands
    result["command_count"] = len(commands)
    result["installed_count"] = sum(item["installed"] for item in commands.values())
    result["installation_status"] = (
        "available"
        if commands and result["installed_count"] == len(commands)
        else ("partial" if result["installed_count"] else "not_installed")
    )
    result["status"] = (
        "available"
        if commands and all(item["exists"] and item["entrypoint"] for item in commands.values())
        else ("incomplete" if commands else "invalid")
    )
    return result


def report_server_status(
    root: Path, output: Path, *, data_root: Path | None = None
) -> dict[str, Any]:
    """Describe the packaged local report server and its current data pack."""
    module = root / "scripts" / "serve_report.py"
    lazy_report = output / "report_lazy.html"
    data_pack = output / "report_data.json"
    server_available = module.is_file()
    route_api = server_available and _file_contains(module, "ROUTE_API_PATH")
    query_module = root / "scripts" / "query_data.py"
    query_api = (
        server_available
        and query_module.is_file()
        and _file_contains(module, "QUERY_API_PATH")
    )
    metadata_api = server_available and _file_contains(module, "METADATA_API_PATH")
    capabilities_api = server_available and all(
        _file_contains(module, token)
        for token in ("CAPABILITIES_API_PATH", "_write_capabilities")
    )
    openapi_api = server_available and all(
        _file_contains(module, token)
        for token in ("OPENAPI_PATH", "OPENAPI_CONTRACT", "openapi_document", "_write_openapi")
    )
    interpretation_api = server_available and all(
        _file_contains(module, token)
        for token in (
            "INTERPRETATION_API_PATH",
            "INTERPRETATION_CONTRACT",
            "_write_interpretation",
        )
    )
    conditional_data_pack = server_available and all(
        _file_contains(module, token)
        for token in ("If-None-Match", "compressed_data_info", "ETag")
    )
    graph = (data_root or root / "data") / "roads"
    routing_available = any(
        (graph / name).is_file() for name in ("road_graph.sqlite", "road_nodes.csv", "road_edges.csv")
    )
    query_status = query_data_status(root, output)
    query_pagination = (
        query_api
        and query_status["supports_pagination"]
        and _file_contains(module, "QUERY_MAX_OFFSET")
    )
    query_cursor = (
        query_api
        and query_status["supports_cursor"]
        and _file_contains(module, "next_cursor")
    )
    query_invalid_score_cursor = (
        query_api
        and query_status["supports_invalid_score_cursor"]
        and _file_contains(module, "query_invalid_score_cursor")
    )
    query_auto_backend_failover = (
        query_api
        and query_status["auto_backend_failover"]
        and _file_contains(module, "query_auto_backend_failover")
    )
    query_backend_health = (
        query_api
        and query_status["readable_backends"]
        and _file_contains(module, "query_backend_health")
        and _file_contains(module, "query_readable_backends")
    )
    query_min_score_finite = query_api and query_status["min_score_finite"]
    health_readiness = server_available and all(
        _file_contains(module, token) for token in ('"ready": ready', 'else "degraded"')
    )
    contract_complete = (
        server_available
        and route_api
        and query_api
        and query_pagination
        and query_cursor
        and query_invalid_score_cursor
        and query_auto_backend_failover
        and query_backend_health
        and query_min_score_finite
        and health_readiness
        and metadata_api
        and capabilities_api
        and openapi_api
        and interpretation_api
        and conditional_data_pack
    )
    return {
        "label": "local report server",
        "module": str(module),
        "command": "ireland-geometry-serve",
        "default_bind": "127.0.0.1",
        "default_port": 8000,
        "default_report": "report_lazy.html",
        "gzip_data_pack": server_available and _file_contains(module, "_write_gzipped_data_pack"),
        "conditional_data_pack": conditional_data_pack,
        "route_api": route_api,
        "route_endpoint": "/api/route",
        "query_api": query_api,
        "query_endpoint": "/api/query",
        "query_pagination": query_pagination,
        "query_cursor": query_cursor,
        "query_invalid_score_cursor": query_invalid_score_cursor,
        "query_auto_backend_failover": query_auto_backend_failover,
        "query_backend_health": query_backend_health,
        "query_max_limit": 1000 if query_pagination else None,
        "query_max_offset": 10_000_000 if query_pagination else None,
        "query_min_score_finite": query_min_score_finite,
        "health_readiness": health_readiness,
        "metadata_api": metadata_api,
        "metadata_endpoint": "/api/metadata",
        "metadata_available": (output / "manifest.json").is_file(),
        "capabilities_api": capabilities_api,
        "capabilities_endpoint": "/api/capabilities",
        "openapi_api": openapi_api,
        "openapi_endpoint": "/api/openapi.json",
        "interpretation_api": interpretation_api,
        "interpretation_endpoint": "/api/interpretation",
        "interpretation_available": (output / "interpretation.json").is_file(),
        "query_available": query_api and query_status["status"] == "available",
        "routing_graph": str(graph),
        "routing_available": routing_available,
        "available": server_available,
        "report_exists": lazy_report.is_file(),
        "data_pack_exists": data_pack.is_file(),
        "status": "available"
        if contract_complete
        else ("incomplete" if server_available else "not_installed"),
    }


def route_query_status(root: Path) -> dict[str, Any]:
    """Describe the standalone coordinate-based route query command."""
    module = root / "scripts" / "route_query.py"
    tokens = {
        "supports_snap_metadata": "snap_distance_m",
        "supports_duration_and_arrival": "estimated_duration_s",
        "supports_departure_profiles": "parse_departure",
        "supports_path_reconstruction": "path_node_ids",
        "supports_geojson": "def route_geojson(",
        "supports_ferry_geometry": "include_ferries",
    }
    features = {
        name: module.is_file() and _file_contains(module, token)
        for name, token in tokens.items()
    }
    ferry_schedule_token = "ferry schedules are not modeled"
    ferry_schedules_modeled = (
        False if module.is_file() and _file_contains(module, ferry_schedule_token) else None
    )
    return {
        "label": "point-to-point route query",
        "module": str(module),
        "command": "ireland-geometry-route",
        "coordinate_system": "WGS84 latitude/longitude",
        **features,
        "ferry_schedules_modeled": ferry_schedules_modeled,
        "status": "available"
        if module.is_file() and all(features.values())
        else ("incomplete" if module.is_file() else "not_installed"),
    }


def geo3d_capability_status(root: Path) -> dict[str, Any]:
    """Describe normalized, raster, and point-cloud LiDAR adapters."""
    module = root / "scripts" / "building_parts.py"
    tokens = {
        "normalized": "def parse_lidar(",
        "geotiff": "def parse_raster_lidar(",
        "las_laz": "def parse_las_lidar(",
    }
    adapters = {name: module.is_file() and _file_contains(module, token) for name, token in tokens.items()}
    dependencies = {
        name: dependency_status(name, required=False) for name in ("rasterio", "laspy")
    }
    adapters["geotiff"] = adapters["geotiff"] and dependencies["rasterio"]["available"]
    adapters["las_laz"] = adapters["las_laz"] and dependencies["laspy"]["available"]
    available = module.is_file() and all(adapters.values())
    return {
        "label": "LiDAR/3-D adapters",
        "module": str(module),
        "accepted_sources": [
            "normalized CSV/JSON/GeoJSON",
            "GeoTIFF DSM/DTM",
            "geographic LAS/LAZ",
        ],
        "adapters": adapters,
        "dependencies": dependencies,
        "status": "available" if available else ("incomplete" if module.is_file() else "not_installed"),
    }


def query_data_status(root: Path, output: Path) -> dict[str, Any]:
    """Describe the read-only analysis-export query command and its backends."""
    module = root / "scripts" / "query_data.py"
    supports_pagination = module.is_file() and all(
        _file_contains(module, token)
        for token in ("offset: int = 0", "result[offset:]")
    )
    supports_cursor = module.is_file() and all(
        _file_contains(module, token)
        for token in (
            "after_score: float | None = None",
            "after_osm_id: str | None = None",
            "def _cursor_for_row(",
        )
    )
    supports_invalid_score_cursor = module.is_file() and all(
        _file_contains(module, token)
        for token in (
            "after_invalid_osm_id: str | None = None",
            '"after_invalid_osm_id"',
            "not math.isfinite(score)",
        )
    )
    auto_backend_failover = module.is_file() and all(
        _file_contains(module, token)
        for token in (
            'if backend == "auto":',
            "_query_backend_rows(",
            "Automatic query backend selection failed",
        )
    )
    min_score_finite = module.is_file() and _file_contains(
        module, "if min_score is not None and not math.isfinite(min_score):"
    )
    bounded_fallback = module.is_file() and all(
        _file_contains(module, token)
        for token in ("class _WorstFirst:", "heapq.heappush", "if limit == 0", "result[offset:]")
    )
    try:
        duckdb_available = importlib.util.find_spec("duckdb") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        duckdb_available = False
    try:
        pyarrow_available = importlib.util.find_spec("pyarrow") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        pyarrow_available = False
    backends = {
        "duckdb": (output / "analysis.duckdb").is_file() and duckdb_available,
        "parquet": (output / "analysis_results.parquet").is_file()
        and (pyarrow_available or duckdb_available),
        "csv": (output / "analysis_results.csv").is_file(),
        "jsonl": (output / "analysis_results.jsonl").is_file(),
    }
    try:
        from query_data import probe_backend
    except ImportError:
        try:
            from scripts.query_data import probe_backend
        except ImportError:
            probe_backend = None
    backend_health: dict[str, dict[str, Any]] = {}
    for backend in ("duckdb", "parquet", "csv", "jsonl"):
        if probe_backend is None:
            backend_health[backend] = {
                "available": backends[backend],
                "readable": False,
                "error": "query backend probe is unavailable",
            }
            continue
        try:
            backend_health[backend] = probe_backend(output, backend)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            backend_health[backend] = {
                "available": backends[backend],
                "readable": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    readable_backends = any(item["readable"] for item in backend_health.values())
    return {
        "label": "analysis export query",
        "module": str(module),
        "command": "ireland-geometry-query",
        "filters": [
            "osm_id",
            "group",
            "is_control",
            "min_score",
            "offset",
            "after_score",
            "after_osm_id",
            "after_invalid_osm_id",
        ],
        "formats": ["json", "jsonl", "csv"],
        "default_limit": 100,
        "supports_pagination": supports_pagination,
        "supports_cursor": supports_cursor,
        "supports_invalid_score_cursor": supports_invalid_score_cursor,
        "auto_backend_failover": auto_backend_failover,
        "max_page_size": 1000,
        "min_score_finite": min_score_finite,
        "bounded_fallback": bounded_fallback,
        "backends": backends,
        "backend_health": backend_health,
        "readable_backends": readable_backends,
        "status": "available"
        if (
            module.is_file()
            and readable_backends
            and supports_pagination
            and supports_cursor
            and supports_invalid_score_cursor
            and auto_backend_failover
            and min_score_finite
        )
        else ("incomplete" if module.is_file() else "not_installed"),
    }


def review_workflow_status(root: Path, output: Path) -> dict[str, Any]:
    """Describe the generated expert-review page and its annotation affordances."""
    module = root / "scripts" / "review.py"
    ui_module = root / "scripts" / "review_ui.py"
    queue = output / "review_queue.csv"
    page = output / "review.html"
    tokens = {
        "search_filter": 'id="search"',
        "state_filter": 'id="statusFilter"',
        "view_state_restore": "restoreViewState",
        "shareable_view_state": "history.replaceState",
        "local_resume": "localStorage",
        "evidence_source_field": 'data-edit="evidence_source"',
        "label_export": "expert-labels.csv",
        "evidence_detail": "Evidence detail",
        "source_links": "Source links",
        "target_notice": 'id="targetNotice"',
        "queue_scope_notice": "not in the current",
        "accessible_search": 'aria-label="Search review queue"',
        "accessible_edit_labels": 'aria-label="Review label for',
        "live_progress": 'aria-live="polite"',
        "json_export": "function downloadJson",
        "json_import": "async function importJson",
        "review_backup_input": 'aria-label="Import review labels JSON"',
        "json_schema_guard": "Unsupported JSON backup schema",
        "json_queue_guard": "different review queue",
        "json_invalid_record_guard": "invalid label records",
    }
    affordances = {name: _file_contains(page, token) for name, token in tokens.items()}
    affordances["label_input_validation"] = module.is_file() and _file_contains(
        module, "def validate_labels("
    )
    queue_rows = None
    evidence_fields = {
        field: False
        for field in ("niah_name", "architect", "reference_urls", "review_warnings")
    }
    if queue.is_file():
        try:
            with queue.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                evidence_fields = {field: field in (reader.fieldnames or []) for field in evidence_fields}
                queue_rows = sum(1 for _ in reader)
        except OSError:
            queue_rows = None
    available = module.is_file() and ui_module.is_file() and queue.is_file() and page.is_file()
    return {
        "label": "expert review workflow",
        "module": str(module),
        "ui_module": str(ui_module),
        "queue": str(queue),
        "page": str(page),
        "queue_rows": queue_rows,
        "affordances": affordances,
        "queue_evidence_fields": evidence_fields,
        "status": "available"
        if available and all(affordances.values()) and all(evidence_fields.values())
        else "incomplete",
    }


def report_capability_status(
    root: Path, output: Path, *, data_root: Path | None = None
) -> dict[str, Any]:
    """Describe standalone, lazy, and dependency-free dashboard modes."""
    standalone = output / "report.html"
    lazy = output / "report_lazy.html"
    data_pack = output / "report_data.json"
    fallback_tokens = (
        "function renderOfflineMap()",
        "OFFLINE_REQUESTED",
        "function loadMapAssets()",
    )
    fallback_available = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in fallback_tokens
    )
    route_panel_tokens = ("id=\"routeRun\"", "function runRoute()", "routeCoordinates(payload)")
    route_panel_available = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in route_panel_tokens
    )
    route_overlay_live = all(
        _file_contains(path, "routeLine=L.polyline") for path in (standalone, lazy)
    )
    route_overlay_offline = all(
        _file_contains(path, "class=\"offline-route\"") for path in (standalone, lazy)
    )
    review_link = all(_file_contains(path, 'href="review.html"') for path in (standalone, lazy))
    review_filter = all(_file_contains(path, 'id="reviewState"') for path in (standalone, lazy))
    review_queue_membership = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("not_queued", "review_queue_targets", "Not in review queue")
    )
    quality_panel = all(_file_contains(path, 'id="quality"') for path in (standalone, lazy))
    quality_findings = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ('id="qualityFindings"', 'data-quality-focus', 'Inspect duplicate-centroid groups')
    )
    quality_audit = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ('id="qualityAudit"', 'id="qualityAuditTable"', 'Download audit CSV')
    )
    interpretation_panel = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ('id="interpretation"', "const INTERPRETATION", "function renderInterpretation()")
    )
    accessibility = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'class="sort-button"',
            "function updateSortHeaders",
            "document.addEventListener('keydown'",
            'tabindex="0"',
            'aria-label="Search analyzed targets"',
        )
    )
    review_page = output / "review.html"
    server = report_server_status(root, output, data_root=data_root)
    return {
        "label": "dashboard reports",
        "standalone": standalone.is_file(),
        "lazy": lazy.is_file(),
        "data_pack": data_pack.is_file(),
        "offline_svg_fallback": fallback_available,
        "external_basemap_when_online": True,
        "local_server": server["available"],
        "gzip_data_pack": server["gzip_data_pack"],
        "conditional_data_pack": server["conditional_data_pack"],
        "openapi_api": server["openapi_api"],
        "openapi_endpoint": server["openapi_endpoint"],
        "route_panel": route_panel_available,
        "route_overlay_live": route_overlay_live,
        "route_overlay_offline": route_overlay_offline,
        "review_link": review_link,
        "review_filter": review_filter,
        "review_queue_membership": review_queue_membership,
        "quality_panel": quality_panel,
        "quality_findings": quality_findings,
        "quality_audit": quality_audit,
        "interpretation_panel": interpretation_panel,
        "accessibility": accessibility,
        "review_page": review_page.is_file(),
        "status": "available"
        if standalone.is_file()
        and lazy.is_file()
        and data_pack.is_file()
        and fallback_available
        and route_panel_available
        and route_overlay_live
        and route_overlay_offline
        and review_filter
        and review_queue_membership
        and quality_panel
        and quality_findings
        and quality_audit
        and interpretation_panel
        and accessibility
        else "incomplete",
    }


def export_capability_status(output: Path) -> dict[str, Any]:
    """Describe the row-oriented and optional queryable export backends."""
    status_path = output / "columnar_status.json"
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = {}
    backends = {}
    for name, filename in (
        ("csv", "analysis_results.csv"),
        ("jsonl", "analysis_results.jsonl"),
        ("parquet", "analysis_results.parquet"),
        ("duckdb", "analysis.duckdb"),
    ):
        path = output / filename
        advertised = payload.get(name, {}).get("status") if isinstance(payload, dict) else None
        backends[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "status": "available" if path.is_file() else (advertised or "not_provided"),
            "bytes": path.stat().st_size if path.is_file() else None,
            "engine": payload.get(name, {}).get("engine") if isinstance(payload, dict) else None,
        }
    available = [item for item in backends.values() if item["exists"]]
    parity = payload.get("parity") if isinstance(payload, dict) else None
    parity_contract = payload.get("contract") if isinstance(payload, dict) else None
    parity_status = parity.get("status") if isinstance(parity, dict) else "not_provided"
    parity_coverage = parity.get("coverage") if isinstance(parity, dict) else None
    parity_mismatches = parity.get("mismatches", []) if isinstance(parity, dict) else []
    return {
        "label": "analysis exports",
        "rows": payload.get("rows") if isinstance(payload, dict) else None,
        "backends": backends,
        "contract": parity_contract,
        "parity": {
            "contract": parity_contract,
            "status": parity_status,
            "coverage": parity_coverage,
            "checked_backends": parity.get("checked_backends", []) if isinstance(parity, dict) else [],
            "mismatches": parity_mismatches,
        },
        "status": (
            "available"
            if available and parity_status in {"pass", "partial", "not_provided"}
            else ("not_provided" if not available else "incomplete")
        ),
    }


def bundle_capability_status(root: Path, output: Path) -> dict[str, Any]:
    """Describe the deterministic portable-artifact bundle command."""
    module = root / "scripts" / "bundle.py"
    implementation = module.is_file() and all(
        _file_contains(module, token)
        for token in (
            "BUNDLE_CONTRACT",
            "def build_bundle(",
            "def verify_bundle(",
            "def extract_bundle(",
            "ZIP_EPOCH",
            "DEFAULT_EXCLUDES",
        )
    )
    manifest = output / "manifest.json"
    return {
        "label": "portable artifact bundle",
        "module": str(module),
        "command": "ireland-geometry-bundle",
        "contract": "ireland-geometry.bundle.v1" if implementation else None,
        "deterministic_zip": implementation,
        "verifies_archives": implementation,
        "extracts_archives": implementation,
        "requires_verified_guard": implementation and _file_contains(module, "--require-verified"),
        "json_output": implementation and _file_contains(module, "--json"),
        "default_archive": str(output.parent / f"{output.name}.bundle.zip"),
        "manifest_exists": manifest.is_file(),
        "status": "available" if implementation else ("incomplete" if module.is_file() else "not_installed"),
    }


def validation_capability_status(output: Path) -> dict[str, Any]:
    """Summarize the independent artifact, schema, and reproducibility gates."""
    records = {}
    for name in ("verification", "schema_validation", "reproducibility"):
        path = output / f"{name}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        records[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "passed": payload.get("passed") is True if isinstance(payload, dict) else False,
            "artifact_count": payload.get("artifact_count") if isinstance(payload, dict) else None,
        }
    return {
        "label": "validation gates",
        "records": records,
        "status": "pass" if all(item["passed"] for item in records.values()) else "incomplete",
    }


def schema_registry_status(root: Path) -> dict[str, Any]:
    """Confirm that the schema registry is present in source and wheel installs."""
    path = root / "schemas" / "artifacts.json"
    result: dict[str, Any] = {
        "label": "packaged schema registry",
        "path": str(path),
        "exists": path.is_file(),
        "schema_version": None,
        "artifact_count": 0,
        "status": "not_installed",
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["status"] = "incomplete"
        result["error"] = str(exc)
        return result
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), dict):
        result["status"] = "incomplete"
        result["error"] = "schema registry must contain an artifacts object"
        return result
    result["schema_version"] = payload.get("schema_version")
    result["artifact_count"] = len(payload["artifacts"])
    result["status"] = "available" if result["artifact_count"] else "incomplete"
    return result


def analysis_plan_status(project_root: Path, package_root: Path) -> dict[str, Any]:
    """Report the project override or packaged default analysis plan."""
    project_path = project_root / "analysis_plan.json"
    packaged_path = package_root / "schemas" / "analysis_plan.json"
    selected = default_analysis_plan_path(project_root, package_root=package_root)
    if project_path.is_file():
        source = "project"
    elif packaged_path.is_file():
        source = "packaged"
    else:
        source = None
    result: dict[str, Any] = {
        "label": "default analysis plan",
        "path": str(selected),
        "project_path": str(project_path),
        "packaged_path": str(packaged_path),
        "source": source,
        "exists": selected.is_file(),
        "sha256": sha256_file(selected),
        "plan_id": None,
        "plan_version": None,
        "scoring_contract": None,
        "scoring_version": None,
        "status": "not_installed",
    }
    if not selected.is_file():
        return result
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["status"] = "incomplete"
        result["error"] = str(exc)
        return result
    if not isinstance(payload, dict):
        result["status"] = "incomplete"
        result["error"] = "analysis plan must be a JSON object"
        return result
    result["plan_id"] = payload.get("plan_id")
    result["plan_version"] = payload.get("version")
    scoring = payload.get("scoring")
    if isinstance(scoring, dict):
        result["scoring_contract"] = scoring.get("contract")
        result["scoring_version"] = scoring.get("version")
        if scoring.get("contract") != "ireland-geometry.exploratory-score.v1" or scoring.get("version") != 1:
            result["status"] = "incomplete"
            result["error"] = "analysis plan scoring contract/version is unsupported"
            return result
    required = ("plan_id", "version", "primary_signals", "holdout_fraction")
    missing = [key for key in required if key not in payload]
    if missing:
        result["status"] = "incomplete"
        result["error"] = "analysis plan is missing: " + ", ".join(missing)
        return result
    result["status"] = "available"
    return result


def dependency_status(name: str, *, required: bool) -> dict[str, Any]:
    """Return an import/distribution check without importing heavy packages."""
    module_name = name
    try:
        available = importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "name": name,
        "module": module_name,
        "required": required,
        "available": available,
        "version": version,
        "status": "available"
        if available
        else ("missing_required" if required else "not_installed"),
    }


def path_status(
    path: Path,
    *,
    required: bool,
    label: str,
    max_age_days: float | None = None,
) -> dict[str, Any]:
    exists = path.exists()
    result: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "required": required,
        "exists": exists,
        "status": "available" if exists else ("missing_required" if required else "not_provided"),
    }
    if path.is_file():
        result["bytes"] = path.stat().st_size
        modified_at = path_modified_at(path)
        age_seconds = path_age_seconds(path)
        if modified_at is not None:
            result["modified_at"] = modified_at
        if age_seconds is not None:
            result["age_seconds"] = age_seconds
            result["age_days"] = round(age_seconds / 86_400, 3)
        if max_age_days is not None:
            result["max_age_days"] = max_age_days
            result["freshness_status"] = (
                "fresh"
                if age_seconds is not None and age_seconds <= max_age_days * 86_400
                else ("stale" if age_seconds is not None else "unknown")
            )
    return result


def routing_graph_status(data: Path) -> dict[str, Any]:
    """Report portable CSV or disk-backed SQLite routing graphs."""
    nodes = data / "roads" / "road_nodes.csv"
    edges = data / "roads" / "road_edges.csv"
    sqlite_graph = data / "roads" / "road_graph.sqlite"
    metadata_path = data / "roads" / "road_graph_metadata.json"
    node_exists = nodes.is_file()
    edge_exists = edges.is_file()
    sqlite_exists = sqlite_graph.is_file()
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            metadata = {}
    if sqlite_exists:
        return {
            "label": "routing graph",
            "path": str(data / "roads"),
            "required": False,
            "exists": True,
            "status": "available",
            "backend": "sqlite",
            "complete": metadata.get("complete"),
            "nodes": metadata.get("node_n"),
            "edges": metadata.get("edge_n"),
            "ways": metadata.get("way_n"),
            "include_restricted": metadata.get("include_restricted"),
            "excluded_access_way_n": metadata.get("excluded_access_way_n"),
            "turn_restriction_n": metadata.get("turn_restriction_n"),
            "restriction_relation_n": metadata.get("restriction_relation_n"),
            "restriction_applied_n": metadata.get("restriction_applied_n"),
            "restriction_unresolved_n": metadata.get("restriction_unresolved_n"),
            "restriction_conditional_n": metadata.get("restriction_conditional_n"),
            "restriction_conditional_supported_n": metadata.get("restriction_conditional_supported_n"),
            "restriction_conditional_unsupported_n": metadata.get("restriction_conditional_unsupported_n"),
            "restriction_conditional_stored_n": metadata.get("restriction_conditional_stored_n"),
            "restriction_conditional_unresolved_n": metadata.get("restriction_conditional_unresolved_n"),
            "restriction_conditional_geometry_unsupported_n": metadata.get("restriction_conditional_geometry_unsupported_n"),
            "restriction_via_way_n": metadata.get("restriction_via_way_n"),
            "restriction_via_way_applied_n": metadata.get("restriction_via_way_applied_n"),
            "restriction_via_way_unresolved_n": metadata.get("restriction_via_way_unresolved_n"),
            "restriction_via_way_unsupported_n": metadata.get("restriction_via_way_unsupported_n"),
            "restriction_unsupported_n": metadata.get("restriction_unsupported_n"),
            "ferry_way_n": metadata.get("ferry_way_n"),
            "ferry_segment_n": metadata.get("ferry_segment_n"),
            "ferry_edge_n": metadata.get("ferry_edge_n"),
            "ferry_relation_n": metadata.get("ferry_relation_n"),
            "ferry_schedules_modeled": metadata.get("ferry_schedules_modeled"),
            "turn_restrictions_supported": metadata.get("turn_restrictions_supported"),
            "metadata": str(metadata_path),
        }
    complete = node_exists and edge_exists
    return {
        "label": "routing graph",
        "path": str(data / "roads"),
        "required": False,
        "exists": complete,
        "status": "available" if complete else ("incomplete" if node_exists or edge_exists else "not_provided"),
        "backend": "csv",
        "complete": metadata.get("complete"),
        "nodes": {"path": str(nodes), "exists": node_exists},
        "edges": {"path": str(edges), "exists": edge_exists},
    }


def candidate_status(directory: Path, patterns: tuple[str, ...], *, label: str) -> dict[str, Any]:
    """Report supported optional source files while ignoring templates."""
    matches = sorted(
        {
            path
            for pattern in patterns
            for path in directory.glob(pattern)
            if path.is_file() and ".template." not in path.name
        }
    )
    result: dict[str, Any] = {
        "label": label,
        "path": str(directory),
        "required": False,
        "exists": bool(matches),
        "status": "available" if matches else "not_provided",
        "matches": [str(path) for path in matches],
    }
    if matches:
        result["selected"] = str(matches[0])
        result["bytes"] = matches[0].stat().st_size
    return result


def verification_status(output: Path) -> dict[str, Any]:
    """Report whether the generated artifact contract has passed."""
    path = output / "verification.json"
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "status": "not_provided",
        "passed": None,
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["status"] = "invalid"
        result["error"] = str(exc)
        return result
    if not isinstance(payload, dict):
        result["status"] = "invalid"
        result["error"] = "verification record must be a JSON object"
        return result
    passed = payload.get("passed") is True
    result["passed"] = passed
    result["status"] = "pass" if passed else "fail"
    if isinstance(payload.get("errors"), list):
        result["error_n"] = len(payload["errors"])
    return result


def manifest_path_status(output: Path) -> dict[str, Any]:
    """Report whether the output manifest exposes relocation-safe references."""
    path = output / "manifest.json"
    result: dict[str, Any] = {
        "label": "portable manifest paths",
        "path": str(path),
        "exists": path.is_file(),
        "status": "not_provided",
        "contract_version": None,
        "artifact_count": 0,
        "portable_artifact_count": 0,
        "source_count": 0,
        "portable_source_count": 0,
        "external_source_count": 0,
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["status"] = "invalid"
        result["error"] = str(exc)
        return result
    if not isinstance(payload, dict):
        result["status"] = "invalid"
        result["error"] = "manifest record must be a JSON object"
        return result
    contract = payload.get("path_contract")
    if not isinstance(contract, dict):
        result["status"] = "legacy"
        return result
    result["contract_version"] = contract.get("version")
    artifacts = payload.get("artifacts")
    sources = payload.get("sources")
    if isinstance(artifacts, list):
        result["artifact_count"] = len(artifacts)
        result["portable_artifact_count"] = sum(
            1
            for artifact in artifacts
            if isinstance(artifact, dict)
            and artifact.get("path_base") == "output_dir"
            and artifact.get("relative_path")
        )
    if isinstance(sources, list):
        result["source_count"] = len(sources)
        result["portable_source_count"] = sum(
            1
            for source in sources
            if isinstance(source, dict)
            and source.get("path_base") == "project_root"
            and source.get("relative_path")
        )
        result["external_source_count"] = sum(
            1
            for source in sources
            if isinstance(source, dict) and source.get("path_base") == "external"
        )
    result["status"] = "available" if contract.get("version") == 1 else "invalid"
    return result


def inspect_project(
    project_root: str | Path | None = None,
    *,
    data_root: str | Path | None = None,
    out_dir: str | Path | None = None,
    package_root: str | Path | None = None,
    max_input_age_days: float | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable project health snapshot.

    Missing cached inputs are reported as warnings rather than hard failures:
    a fresh checkout is expected to download them. Missing required Python
    dependencies and an unsupported interpreter remain hard runtime errors.
    """
    root = Path(project_root).resolve() if project_root else DEFAULT_PROJECT_ROOT.resolve()
    code_root = Path(package_root).resolve() if package_root else PACKAGE_ROOT.resolve()
    data = Path(data_root) if data_root else root / "data"
    output = Path(out_dir) if out_dir else root / "output"
    if not data.is_absolute():
        data = root / data
    if not output.is_absolute():
        output = root / output

    python_ok = sys.version_info[:2] >= MIN_PYTHON
    python_check = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "required": f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        "supported": python_ok,
        "status": "available" if python_ok else "unsupported",
    }
    dependencies = {
        "required": [dependency_status(name, required=True) for name in REQUIRED_DEPENDENCIES],
        "optional": [dependency_status(name, required=False) for name in OPTIONAL_DEPENDENCIES],
    }

    required_inputs = [
        path_status(
            data / "raw" / "ireland-latest.osm.pbf",
            required=True,
            label="Geofabrik OSM PBF",
            max_age_days=max_input_age_days,
        ),
        path_status(
            data / "combined.json",
            required=True,
            label="normalized OSM JSON",
            max_age_days=max_input_age_days,
        ),
        path_status(
            data / "niah" / "niah.json",
            required=True,
            label="normalized NIAH JSON",
            max_age_days=max_input_age_days,
        ),
    ]
    optional_inputs = [
        candidate_status(
            data / "lidar",
            ("*.csv", "*.json", "*.geojson", "*.tif", "*.tiff", "*.las", "*.laz"),
            label="LiDAR/DSM source",
        ),
        candidate_status(data / "history", ("*.csv", "*.json"), label="OSM edit history"),
        path_status(data / "boundaries" / "admin.geojson", required=False, label="administrative boundaries"),
        path_status(data / "boundaries" / "settlements.geojson", required=False, label="settlement boundaries"),
        routing_graph_status(data),
        candidate_status(data / "historical", ("*.csv", "*.json"), label="historical references"),
        candidate_status(data / "review", ("*.csv", "*.json"), label="expert review labels"),
    ]
    outputs = [
        path_status(output / "analysis_results.csv", required=False, label="analysis results"),
        path_status(output / "report.html", required=False, label="standalone report"),
        path_status(output / "interpretation.json", required=False, label="interpretation sidecar"),
        path_status(output / "review_queue.csv", required=False, label="expert review queue"),
        path_status(output / "review.html", required=False, label="expert review page"),
        path_status(output / "verification.json", required=False, label="verification record"),
        path_status(output / "manifest.json", required=False, label="provenance manifest"),
        path_status(output / "columnar_status.json", required=False, label="columnar export status"),
        path_status(output / "analysis_results.jsonl", required=False, label="JSONL analysis export"),
        path_status(output / "analysis_results.parquet", required=False, label="Parquet analysis export"),
        path_status(output / "analysis.duckdb", required=False, label="DuckDB analysis export"),
    ]
    verification = verification_status(output)
    schema_registry = schema_registry_status(code_root)
    analysis_plan = analysis_plan_status(root, code_root)
    commands = console_commands_status(code_root)

    errors: list[str] = []
    warnings: list[str] = []
    if not python_ok:
        errors.append(
            f"Python {platform.python_version()} is below the supported {python_check['required']}."
        )
    for dependency in dependencies["required"]:
        if not dependency["available"]:
            errors.append(f"Required dependency is unavailable: {dependency['name']}.")
    if schema_registry["status"] != "available":
        errors.append("The packaged schema registry is unavailable or invalid.")
    if analysis_plan["status"] != "available":
        errors.append("The project or packaged analysis plan is unavailable or invalid.")
    missing_inputs = [item["label"] for item in required_inputs if not item["exists"]]
    stale_required_inputs = [
        item["label"] for item in required_inputs if item.get("freshness_status") == "stale"
    ]
    if missing_inputs:
        warnings.append("Required cached inputs are missing: " + ", ".join(missing_inputs) + ".")
    if stale_required_inputs:
        warnings.append(
            "Required cached inputs exceed the configured age limit: "
            + ", ".join(stale_required_inputs)
            + "."
        )
    git_state = {"revision": git_revision(root), "dirty": git_dirty(root)}
    if git_state["dirty"]:
        warnings.append("The working tree contains uncommitted or untracked files.")
    if verification["status"] != "pass":
        warnings.append(
            "A passing output verification record is unavailable; run the pipeline verify stage."
        )
    if commands["status"] != "available" or commands["installation_status"] != "available":
        warnings.append(
            "Installed console command wrappers are incomplete: "
            f"{commands['installed_count']}/{commands['command_count']} available; "
            "reinstall the package before using strict readiness."
        )
    missing_outputs = [name for name in STRICT_OUTPUT_NAMES if not (output / name).is_file()]
    if missing_outputs:
        warnings.append("Required generated outputs are missing: " + ", ".join(missing_outputs) + ".")

    strict_ready = (
        not errors
        and not missing_inputs
        and not missing_outputs
        and git_state["dirty"] is False
        and verification["status"] == "pass"
        and commands["status"] == "available"
        and commands["installation_status"] == "available"
        and not stale_required_inputs
    )
    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "doctor_version": 56,
        "package_version": package_version(),
        "runtime": runtime_signature(),
        "checked_at": utc_now(),
        "project_root": str(root),
        "package_root": str(code_root),
        "python": python_check,
        "dependencies": dependencies,
        "inputs": {"required": required_inputs, "optional": optional_inputs},
        "outputs": outputs,
        "source_freshness": {
            "contract": SOURCE_FRESHNESS_CONTRACT,
            "max_input_age_days": max_input_age_days,
            "stale_required_inputs": stale_required_inputs,
            "required_inputs": required_inputs,
        },
        "capabilities": {
            "pipeline": pipeline_status(code_root),
            "commands": commands,
            "reports": report_capability_status(code_root, output, data_root=data),
            "report_server": report_server_status(code_root, output, data_root=data),
            "route_query": route_query_status(code_root),
            "geo3d": geo3d_capability_status(code_root),
            "query": query_data_status(code_root, output),
            "bundle": bundle_capability_status(code_root, output),
            "review": review_workflow_status(code_root, output),
            "exports": export_capability_status(output),
            "validation": validation_capability_status(output),
            "schema_registry": schema_registry,
            "analysis_plan": analysis_plan,
            "manifest_paths": manifest_path_status(output),
        },
        "verification": verification,
        "git": git_state,
        "summary": {
            "status": status,
            "strict_ready": strict_ready,
            "errors": errors,
            "warnings": warnings,
            "missing_required_inputs": len(missing_inputs),
            "missing_required_outputs": len(missing_outputs),
            "stale_required_inputs": len(stale_required_inputs),
            "max_input_age_days": max_input_age_days,
            "optional_inputs_available": sum(item["exists"] for item in optional_inputs),
            "generated_outputs_available": sum(item["exists"] for item in outputs),
        },
    }


def format_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    dirty = result["git"].get("dirty")
    git_status = "dirty" if dirty is True else ("clean" if dirty is False else "unknown")
    lines = [
        f"Project doctor: {summary['status']}",
        f"Package: {result['package_version']}",
        f"Python: {result['python']['version']} ({result['python']['status']})",
        (
            f"Git: {result['git'].get('revision') or 'unavailable'}"
            f" ({git_status})"
        ),
        "Dependencies:",
    ]
    for dependency in result["dependencies"]["required"] + result["dependencies"]["optional"]:
        version = f" {dependency['version']}" if dependency.get("version") else ""
        lines.append(f"  {dependency['status']}: {dependency['name']}{version}")
    lines.append("Inputs:")
    for item in result["inputs"]["required"] + result["inputs"]["optional"]:
        lines.append(f"  {item['status']}: {item['label']}")
    lines.append("Outputs:")
    for item in result["outputs"]:
        lines.append(f"  {item['status']}: {item['label']}")
    freshness = result["source_freshness"]
    limit = freshness.get("max_input_age_days")
    limit_text = f"; limit={limit:g}d" if isinstance(limit, (int, float)) else ""
    lines.append(
        "Source freshness: "
        f"{len(freshness['stale_required_inputs'])} stale required inputs{limit_text}"
    )
    lines.append("Capabilities:")
    for name, item in result["capabilities"].items():
        detail = ""
        if name == "commands":
            detail = (
                f" ({item['installed_count']}/{item['command_count']} command wrappers installed;"
                f" {item['installation_status']})"
            )
        lines.append(f"  {item['status']}: {item['label']}{detail}")
    lines.append(f"Verification: {result['verification']['status']}")
    for warning in summary["warnings"]:
        lines.append(f"WARNING: {warning}")
    for error in summary["errors"]:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--json", action="store_true", help="print the machine-readable report")
    parser.add_argument("--out", default=None, help="also write the JSON report to this path")
    parser.add_argument(
        "--max-input-age-days",
        type=float,
        default=None,
        help="mark required cached inputs stale when older than this positive number of days",
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 unless the project is ready to run cleanly"
    )
    args = parser.parse_args(argv)
    if args.max_input_age_days is not None and (
        not math.isfinite(args.max_input_age_days) or args.max_input_age_days <= 0
    ):
        parser.error("--max-input-age-days must be a finite positive number")
    result = inspect_project(
        args.project_root,
        data_root=args.data_root,
        out_dir=args.out_dir,
        max_input_age_days=args.max_input_age_days,
    )
    if args.out:
        root = Path(result["project_root"])
        destination = Path(args.out)
        if not destination.is_absolute():
            destination = root / destination
        atomic_write_json(destination, result, indent=2)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))
        if args.out:
            print(f"Wrote {destination}")
    if args.strict and not result["summary"]["strict_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
