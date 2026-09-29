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
        SOURCE_ALIGNMENT_CONTRACT,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        git_dirty,
        git_revision,
        git_worktree_status,
        manifest_source_alignment,
        package_version,
        path_age_seconds,
        path_modified_at,
        path_symlink_paths,
        project_output_file_path,
        runtime_signature,
        sha256_file,
        utc_now,
    )
    from runtime import ROOT as DEFAULT_PROJECT_ROOT
except ImportError:
    from scripts.runtime import (
        PACKAGE_NAME,
        PACKAGE_ROOT,
        SOURCE_ALIGNMENT_CONTRACT,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        git_dirty,
        git_revision,
        git_worktree_status,
        manifest_source_alignment,
        package_version,
        path_age_seconds,
        path_modified_at,
        path_symlink_paths,
        project_output_file_path,
        runtime_signature,
        sha256_file,
        utc_now,
    )
    from scripts.runtime import ROOT as DEFAULT_PROJECT_ROOT

try:
    from pages_audit import (
        PAGES_AUDIT_CONTRACT,
        PAGES_PUBLISH_CONTRACT,
        audit_site,
    )
except ImportError:
    try:
        from scripts.pages_audit import (
            PAGES_AUDIT_CONTRACT,
            PAGES_PUBLISH_CONTRACT,
            audit_site,
        )
    except ImportError:
        PAGES_AUDIT_CONTRACT = "ireland-geometry.pages-audit.v1"
        PAGES_PUBLISH_CONTRACT = "ireland-geometry.pages-publish.v1"
        audit_site = None


MIN_PYTHON = (3, 10)
DOCTOR_VERSION = 156
REQUIRED_DEPENDENCIES = ("numpy", "shapely", "requests", "osmium")
OPTIONAL_DEPENDENCIES = ("pyarrow", "duckdb", "rasterio", "laspy")
STRICT_OUTPUT_NAMES = (
    "analysis_results.csv",
    "report.html",
    "interpretation.json",
    "verification.json",
    "schema_validation.json",
    "reproducibility.json",
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
        "post_validation_bundle": False,
        "preserves_diagnostic_manifest_context": False,
        "diagnostic_json_outputs": False,
        "reproducibility_rejects_symlinks": False,
        "rejects_output_symlink": False,
        "rejects_output_non_directory": False,
        "rejects_nested_symlinks": False,
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
    result["post_validation_bundle"] = all(
        _file_contains(module_path, token)
        for token in (
            "--bundle",
            "build_bundle_command",
            "build_bundle_verify_command",
            'phase": "post_validation_bundle"',
            '"--require-verified"',
            '"--verify"',
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
    repro_module = root / "scripts" / "repro_check.py"
    result["reproducibility_rejects_symlinks"] = repro_module.is_file() and _file_contains(
        repro_module, "output contains a symlink"
    )
    result["rejects_output_symlink"] = _file_contains(module_path, "reject_symlink_root(")
    result["rejects_output_non_directory"] = result["rejects_output_symlink"]
    result["rejects_nested_symlinks"] = _file_contains(module_path, "reject_symlink_tree(")
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
            and result["rejects_output_symlink"]
            and result["rejects_nested_symlinks"]
            and result["post_validation_report_refresh"]
            and result["post_validation_bundle"]
        )
        else "incomplete"
    )
    return result


def output_safety_status(root: Path) -> dict[str, Any]:
    """Inventory fail-closed output-root guards across user-facing commands."""
    runtime_module = root / "scripts" / "runtime.py"
    modules = {
        "doctor": (root / "scripts" / "doctor.py", "project_output_file_path("),
        "pipeline": (root / "run_pipeline.py", "reject_symlink_root("),
        "report": (root / "scripts" / "report.py", "project_output_tree_path("),
        "schema_audit": (root / "scripts" / "schema_audit.py", "project_output_tree_path("),
        "columnar": (root / "scripts" / "columnar.py", "project_output_tree_path("),
        "verify": (root / "scripts" / "verify.py", "reject_symlink_root(out_dir)"),
        "repro_check": (root / "scripts" / "repro_check.py", "out.is_symlink()"),
        "bundle": (root / "scripts" / "bundle.py", "reject_symlink_root("),
        "report_server": (root / "scripts" / "serve_report.py", "reject_symlink_root("),
        "query": (root / "scripts" / "query_data.py", "project_output_tree_path("),
        "pages_publisher": (root / "scripts" / "publish_pages.py", "reject_symlink_root("),
        "pages_audit": (root / "scripts" / "pages_audit.py", "site_input.is_symlink()"),
        "release_check": (root / "scripts" / "release_check.py", "def _symlink_root_errors("),
        "route-query": (root / "scripts" / "route_query.py", "project_output_file_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_output_file_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_output_file_path("),
    }
    guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in modules.items()
    }
    non_directory_modules = {
        "doctor": (root / "scripts" / "doctor.py", "project_output_file_path("),
        "pipeline": (root / "run_pipeline.py", "reject_symlink_root("),
        "report": (root / "scripts" / "report.py", "project_output_tree_path("),
        "schema_audit": (root / "scripts" / "schema_audit.py", "project_output_tree_path("),
        "columnar": (root / "scripts" / "columnar.py", "project_output_tree_path("),
        "verify": (root / "scripts" / "verify.py", "reject_symlink_root(out_dir)"),
        "repro_check": (root / "scripts" / "repro_check.py", 'status = "not_a_directory"'),
        "bundle": (root / "scripts" / "bundle.py", "reject_symlink_root("),
        "report_server": (root / "scripts" / "serve_report.py", "reject_symlink_root("),
        "query": (root / "scripts" / "query_data.py", "project_output_tree_path("),
        "pages_publisher": (root / "scripts" / "publish_pages.py", "reject_symlink_root("),
        "pages_audit": (
            root / "scripts" / "pages_audit.py",
            "site_input.exists() and not site_input.is_dir()",
        ),
        "release_check": (
            root / "scripts" / "release_check.py",
            "output.exists() and not output.is_dir()",
        ),
        "route-query": (root / "scripts" / "route_query.py", "project_output_file_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_output_file_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_output_file_path("),
    }
    non_directory_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in non_directory_modules.items()
    }
    file_output_modules = {
        "doctor": (root / "scripts" / "doctor.py", "project_output_file_path("),
        "route-query": (root / "scripts" / "route_query.py", "project_output_file_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_output_file_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_output_file_path("),
    }
    file_output_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in file_output_modules.items()
    }
    nested_modules = {
        "verify": (root / "scripts" / "verify.py", "reject_symlink_tree("),
        "report_server": (
            root / "scripts" / "serve_report.py",
            "_require_symlink_free_output(",
        ),
        "query": (root / "scripts" / "query_data.py", "project_output_tree_path("),
        "schema_audit": (
            root / "scripts" / "schema_audit.py",
            "project_output_tree_path(",
        ),
        "report": (root / "scripts" / "report.py", "project_output_tree_path("),
        "columnar": (root / "scripts" / "columnar.py", "project_output_tree_path("),
    }
    nested_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in nested_modules.items()
    }
    stage_modules = {
        "analyze": root / "scripts" / "analyze.py",
        "negative-controls": root / "scripts" / "negative_controls.py",
        "niah": root / "scripts" / "niah.py",
        "architects": root / "scripts" / "architects.py",
        "sensitivity": root / "scripts" / "sensitivity.py",
        "spatial-covariates": root / "scripts" / "spatial_covariates.py",
        "osm-history": root / "scripts" / "osm_history.py",
        "validation": root / "scripts" / "validation.py",
        "building-parts": root / "scripts" / "building_parts.py",
        "historical": root / "scripts" / "historical_validation.py",
        "review": root / "scripts" / "review.py",
        "point-pattern": root / "scripts" / "point_pattern.py",
        "spatial-stats": root / "scripts" / "spatial_stats.py",
        "spatial-bootstrap": root / "scripts" / "spatial_bootstrap.py",
        "roads": root / "scripts" / "roads.py",
        "road-proximity": root / "scripts" / "road_proximity.py",
        "road-routing": root / "scripts" / "road_routing.py",
        "quality-audit": root / "scripts" / "data_quality.py",
        "holdout": root / "scripts" / "holdout.py",
    }
    stage_output_tree_guards = {
        name: module.is_file() and _file_contains(module, "project_output_tree_path(")
        for name, module in stage_modules.items()
    }
    # Keep the established field name while making its guarantee explicit in
    # the additional tree-specific field below.
    stage_output_guards = stage_output_tree_guards
    handles_non_directory = (
        all(non_directory_guards.values())
        and all(file_output_guards.values())
        and all(stage_output_guards.values())
    )
    shared_helper = runtime_module.is_file() and all(
        _file_contains(runtime_module, token)
        for token in (
            "def reject_symlink_root(",
            "def project_output_tree_path(",
            "def project_output_file_path(",
        )
    )
    rejects_non_directory = shared_helper and _file_contains(
        runtime_module, "must be a directory:"
    ) and handles_non_directory and all(nested_guards.values())
    return {
        "label": "output-root safety",
        "contract": "ireland-geometry.output-safety.v1",
        "shared_helper": shared_helper,
        "allows_canonical_macos_tmp_alias": runtime_module.is_file()
        and _file_contains(runtime_module, "def _is_canonical_macos_tmp_alias("),
        "rejects_non_directory": rejects_non_directory,
        "guards": guards,
        "non_directory_guards": non_directory_guards,
        "file_output_guards": file_output_guards,
        "nested_guards": nested_guards,
        "stage_output_guards": stage_output_guards,
        "stage_output_tree_guards": stage_output_tree_guards,
        "status": (
            "available"
            if shared_helper and rejects_non_directory and all(guards.values())
            else "incomplete"
        ),
    }


def data_safety_status(root: Path) -> dict[str, Any]:
    """Inventory fail-closed data-root guards across ingestion and analysis CLIs."""
    runtime_module = root / "scripts" / "runtime.py"
    modules = {
        "pipeline": (root / "run_pipeline.py", 'label="data directory"'),
        "fetch": (root / "scripts" / "fetch_geofabrik.py", 'label="data output directory"'),
        "fetch-niah": (root / "scripts" / "fetch_niah.py", "project_data_path("),
        "fetch-osm": (root / "scripts" / "fetch_osm.py", "project_data_path("),
        "fetch-satellite": (
            root / "scripts" / "fetch_satellite.py",
            'label="satellite data directory"',
        ),
        "niah": (root / "scripts" / "niah.py", "project_data_tree_path("),
        "architects": (root / "scripts" / "architects.py", "project_data_tree_path("),
        "building-parts": (root / "scripts" / "building_parts.py", "project_data_tree_path("),
        "historical": (root / "scripts" / "historical_validation.py", "project_data_tree_path("),
        "osm-history": (root / "scripts" / "osm_history.py", "project_data_tree_path("),
        "review": (root / "scripts" / "review.py", "project_data_tree_path("),
        "road-proximity": (root / "scripts" / "road_proximity.py", "project_data_tree_path("),
        "road-routing": (root / "scripts" / "road_routing.py", "project_data_tree_path("),
        "roads": (root / "scripts" / "roads.py", "project_data_tree_path("),
        "spatial-covariates": (root / "scripts" / "spatial_covariates.py", "project_data_tree_path("),
        "route-query": (root / "scripts" / "route_query.py", "project_data_tree_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_data_tree_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_data_tree_path("),
        "verify": (root / "scripts" / "verify.py", "reject_symlink_tree("),
        "report-server": (root / "scripts" / "serve_report.py", "reject_symlink_tree("),
    }
    guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in modules.items()
    }
    nested_modules = {
        "fetch-osm": (root / "scripts" / "fetch_osm.py", 'label="OSM raw data directory"'),
        "fetch-niah": (root / "scripts" / "fetch_niah.py", "extraction directory"),
    }
    nested_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in nested_modules.items()
    }
    extraction_tree_modules = {
        "fetch-niah": (root / "scripts" / "fetch_niah.py", "reject_symlink_tree("),
    }
    extraction_tree_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in extraction_tree_modules.items()
    }
    satellite_tree_modules = {
        "fetch-satellite": (
            root / "scripts" / "fetch_satellite.py",
            "reject_symlink_tree(",
        ),
    }
    satellite_tree_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in satellite_tree_modules.items()
    }
    file_modules = {
        "fetch": (root / "scripts" / "fetch_geofabrik.py", "reject_symlink_path("),
        "fetch-niah": (root / "scripts" / "fetch_niah.py", "reject_symlink_path("),
        "fetch-osm": (root / "scripts" / "fetch_osm.py", "reject_symlink_path("),
    }
    file_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in file_modules.items()
    }
    tree_modules = {
        "pipeline": (root / "run_pipeline.py", "reject_symlink_tree("),
        "niah": (root / "scripts" / "niah.py", "project_data_tree_path("),
        "architects": (root / "scripts" / "architects.py", "project_data_tree_path("),
        "building-parts": (
            root / "scripts" / "building_parts.py",
            "project_data_tree_path(",
        ),
        "historical": (
            root / "scripts" / "historical_validation.py",
            "project_data_tree_path(",
        ),
        "osm-history": (root / "scripts" / "osm_history.py", "project_data_tree_path("),
        "review": (root / "scripts" / "review.py", "project_data_tree_path("),
        "road-proximity": (
            root / "scripts" / "road_proximity.py",
            "project_data_tree_path(",
        ),
        "road-routing": (
            root / "scripts" / "road_routing.py",
            "project_data_tree_path(",
        ),
        "roads": (root / "scripts" / "roads.py", "project_data_tree_path("),
        "spatial-covariates": (
            root / "scripts" / "spatial_covariates.py",
            "project_data_tree_path(",
        ),
        "route-query": (root / "scripts" / "route_query.py", "project_data_tree_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_data_tree_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_data_tree_path("),
        "verify": (root / "scripts" / "verify.py", "reject_symlink_tree("),
        "report-server": (root / "scripts" / "serve_report.py", "reject_symlink_tree("),
    }
    tree_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in tree_modules.items()
    }
    graph_output_modules = {
        "road-routing": (
            root / "scripts" / "road_routing.py",
            "graph_output = reject_symlink_tree(",
        ),
    }
    graph_output_tree_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in graph_output_modules.items()
    }
    input_modules = {
        "analyze": (root / "scripts" / "analyze.py", "project_input_path("),
        "building-parts": (root / "scripts" / "building_parts.py", "project_input_path("),
        "historical": (
            root / "scripts" / "historical_validation.py",
            "project_input_path(",
        ),
        "osm-history": (root / "scripts" / "osm_history.py", "project_input_path("),
        "review": (root / "scripts" / "review.py", "project_input_path("),
        "road-proximity": (root / "scripts" / "road_proximity.py", "project_input_path("),
        "road-routing": (root / "scripts" / "road_routing.py", "project_input_path("),
        "roads": (root / "scripts" / "roads.py", "project_input_path("),
        "spatial-covariates": (
            root / "scripts" / "spatial_covariates.py",
            "project_input_path(",
        ),
        "route-query": (root / "scripts" / "route_query.py", "project_input_path("),
        "route-matrix": (root / "scripts" / "route_matrix.py", "project_input_path("),
        "route-compare": (root / "scripts" / "route_compare.py", "project_input_path("),
        "verify": (root / "scripts" / "verify.py", "reject_symlink_path("),
        "report-server": (root / "scripts" / "serve_report.py", "reject_symlink_path("),
        "holdout": (root / "scripts" / "holdout.py", "project_input_path("),
        "schema-audit": (root / "scripts" / "schema_audit.py", "project_input_path("),
    }
    input_guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in input_modules.items()
    }
    shared_helper = runtime_module.is_file() and all(
        _file_contains(runtime_module, token)
        for token in (
            "def project_data_path(",
            "def project_data_tree_path(",
            "def project_input_path(",
            "def reject_symlink_path(",
            "label=\"data directory\"",
            "must be a directory:",
        )
    )
    rejects_non_directory = (
        shared_helper
        and all(guards.values())
        and all(nested_guards.values())
        and all(extraction_tree_guards.values())
        and all(satellite_tree_guards.values())
        and all(file_guards.values())
        and all(tree_guards.values())
        and all(graph_output_tree_guards.values())
        and all(input_guards.values())
    )
    return {
        "label": "data-root safety",
        "contract": "ireland-geometry.data-safety.v1",
        "shared_helper": shared_helper,
        "rejects_non_directory": rejects_non_directory,
        "guards": guards,
        "nested_guards": nested_guards,
        "extraction_tree_guards": extraction_tree_guards,
        "satellite_tree_guards": satellite_tree_guards,
        "file_guards": file_guards,
        "tree_guards": tree_guards,
        "graph_output_tree_guards": graph_output_tree_guards,
        "input_guards": input_guards,
        "status": "available" if shared_helper and rejects_non_directory else "incomplete",
    }


def tree_root_boundary_status(path: Path, *, label: str) -> dict[str, Any]:
    """Report whether an active directory root and its existing tree are safe."""
    result: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "status": "not_provided",
        "passed": None,
        "reason": None,
        "symlink_count": 0,
        "symlink_paths": [],
        "symlink_paths_truncated": False,
    }
    if path.is_symlink():
        result.update(
            {
                "status": "fail",
                "passed": False,
                "reason": "symlink",
                "symlink_count": 1,
                "symlink_paths": ["."],
            }
        )
        return result
    if path.exists() and not path.is_dir():
        result.update(
            {
                "status": "fail",
                "passed": False,
                "reason": "not_a_directory",
            }
        )
        return result
    if not path.is_dir():
        return result
    try:
        symlink_paths = path_symlink_paths(path)
    except OSError as exc:
        result.update(
            {
                "status": "fail",
                "passed": False,
                "reason": "unreadable",
                "error": str(exc),
            }
        )
        return result
    display_limit = 128
    result.update(
        {
            "status": "fail" if symlink_paths else "pass",
            "passed": not symlink_paths,
            "symlink_count": len(symlink_paths),
            "symlink_paths": symlink_paths[:display_limit],
            "symlink_paths_truncated": len(symlink_paths) > display_limit,
        }
    )
    if symlink_paths:
        result["reason"] = "nested_symlinks"
    return result


def data_root_boundary_status(path: Path) -> dict[str, Any]:
    """Report whether the active project data root and its tree are safe."""
    return tree_root_boundary_status(path, label="data directory")


def output_root_boundary_status(path: Path) -> dict[str, Any]:
    """Report whether the active project output root and its tree are safe."""
    return tree_root_boundary_status(path, label="output directory")


def file_write_safety_status(root: Path) -> dict[str, Any]:
    """Inventory atomic file writers used by ingestion and pipeline commands."""
    runtime_module = root / "scripts" / "runtime.py"
    modules = {
        "pipeline": (root / "run_pipeline.py", "atomic_write_json("),
        "fetch": (root / "scripts" / "fetch_geofabrik.py", "atomic_write_stream("),
        "fetch-niah": (root / "scripts" / "fetch_niah.py", "atomic_write_stream("),
        "fetch-osm": (root / "scripts" / "fetch_osm.py", "atomic_write_json("),
        "fetch-satellite": (root / "scripts" / "fetch_satellite.py", "atomic_write_stream("),
    }
    guards = {
        name: module.is_file() and _file_contains(module, token)
        for name, (module, token) in modules.items()
    }
    shared_helper = runtime_module.is_file() and all(
        _file_contains(runtime_module, token)
        for token in (
            "def atomic_write_text(",
            "def atomic_write_bytes(",
            "def atomic_write_stream(",
            "os.replace(tmp_name, dest)",
        )
    )
    return {
        "label": "atomic file-write safety",
        "contract": "ireland-geometry.file-write-safety.v1",
        "shared_helper": shared_helper,
        "guards": guards,
        "status": "available" if shared_helper and all(guards.values()) else "incomplete",
    }


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
    report_module = root / "scripts" / "report.py"
    lazy_report = output / "report_lazy.html"
    data_pack = output / "report_data.json"
    server_available = module.is_file()
    route_api = server_available and _file_contains(module, "ROUTE_API_PATH")
    route_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "ROUTE_JSON_MAX_BODY_BYTES",
            "def _json_route_request(",
            "self._write_route(json_body=payload)",
            '"queryRouteJson"',
            '"RouteRequest"',
        )
    )
    route_matrix_api = server_available and all(
        _file_contains(module, token)
        for token in ("ROUTE_MATRIX_API_PATH", "_write_route_matrix", "query_route_matrix")
    )
    route_matrix_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "ROUTE_MATRIX_JSON_MAX_BODY_BYTES",
            "def _json_matrix_points(",
            "def _json_route_options(",
            "self._write_route_matrix(json_body=payload)",
            '"queryRouteMatrixJson"',
            '"RouteMatrixRequest"',
        )
    )
    route_comparison_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "ROUTE_COMPARISON_JSON_MAX_BODY_BYTES",
            "def _json_comparison_request(",
            "self._write_route_comparison(json_body=payload)",
            '"compareRouteProfilesJson"',
            '"RouteComparisonRequest"',
        )
    )
    route_comparison_api = server_available and all(
        _file_contains(module, token)
        for token in (
            "ROUTE_COMPARISON_API_PATH",
            "_write_route_comparison",
            "query_route_comparison",
        )
    )
    query_module = root / "scripts" / "query_data.py"
    query_api = (
        server_available
        and query_module.is_file()
        and _file_contains(module, "QUERY_API_PATH")
    )
    query_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "QUERY_JSON_MAX_BODY_BYTES",
            "def _json_query_request(",
            "self._write_query(json_body=payload)",
            '"queryAnalysisRowsJson"',
            '"QueryRequest"',
        )
    )
    report_page_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "REPORT_PAGE_JSON_MAX_BODY_BYTES",
            "def _json_report_request(",
            "self._write_report_page(json_body=payload)",
            '"getReportPageJson"',
            '"ReportPageRequest"',
        )
    )
    report_page_next_offset = server_available and all(
        (
            _file_contains(module, token)
            or _file_contains(report_module, token)
        )
        for token in ('"next_offset": offset + limit if offset + limit < len(matched) else None',)
    ) and all(
        _file_contains(module, token)
        for token in ('"ReportPagePagination"', '"pagination_continuation": "next_offset"')
    )
    report_page_sort_tiebreaker = server_available and all(
        (
            _file_contains(module, token)
            or _file_contains(report_module, token)
        )
        for token in (
            'REPORT_PAGE_SORT_TIEBREAKER = "osm_id"',
            "def sort_tiebreaker(",
            "matched = sorted(matched, key=sort_tiebreaker)",
        )
    ) and all(
        _file_contains(module, token)
        for token in (
            '"sort_tiebreaker": REPORT_PAGE_SORT_TIEBREAKER',
            '"sort_tiebreaker": {"const": REPORT_PAGE_SORT_TIEBREAKER}',
        )
    )
    conditional_api_responses = server_available and all(
        _file_contains(module, token)
        for token in (
            "CONDITIONAL_ENDPOINTS = (",
            '"conditional_endpoints": list(CONDITIONAL_ENDPOINTS)',
            'self._write_json(payload, cache_control="no-cache")',
            'self._write_json(result, cache_control="no-cache")',
            "def _openapi_conditional_headers(",
            "def _etag_matches(",
        )
    )
    api_json_gzip = server_available and all(
        _file_contains(module, token)
        for token in (
            "API_JSON_GZIP_MIN_BYTES = 1024",
            "gzip.compress(body, compresslevel=6, mtime=0)",
            '"api_json_gzip": True',
            '"Content-Encoding"',
        )
    )
    head_api = server_available and all(
        _file_contains(module, token)
        for token in (
            "HEAD_ENDPOINTS = CONDITIONAL_ENDPOINTS",
            "def do_HEAD(self)",
            '"head_endpoints": list(HEAD_ENDPOINTS)',
            "def _openapi_with_head_operations(",
            "self._head_only",
        )
    )
    api_error_request_ids = server_available and all(
        _file_contains(module, token)
        for token in (
            'API_ERROR_CONTRACT = "ireland-geometry.api-error.v1"',
            'REQUEST_ID_HEADER = "X-Ireland-Geometry-Request-ID"',
            "def send_error(",
            "def _request_id(",
            '"error_code"',
            '"request_id"',
            "self.send_header(REQUEST_ID_HEADER",
        )
    )
    api_request_logging = server_available and all(
        _file_contains(module, token)
        for token in (
            "def log_message(",
            "[request_id=",
            "super().log_message(format, *args)",
            '"api_request_logging": True',
            '"api_request_log_field": "request_id"',
        )
    )
    request_id_all_responses = server_available and all(
        _file_contains(module, token)
        for token in (
            "def end_headers(self)",
            "self.send_header(REQUEST_ID_HEADER, self._request_id())",
            "super().end_headers()",
            '"request_id_all_responses": True',
            '"request_id_all_response_logging": True',
        )
    )
    security_response_headers = server_available and all(
        _file_contains(module, token)
        for token in (
            "SECURITY_RESPONSE_HEADERS = {",
            "def end_headers(self)",
            '"X-Content-Type-Options": "nosniff"',
            '"Referrer-Policy": "no-referrer"',
            '"Permissions-Policy": "geolocation=(), camera=(), microphone=()"',
            '"security_response_headers": dict(SECURITY_RESPONSE_HEADERS)',
        )
    )
    api_request_timing = server_available and all(
        _file_contains(module, token)
        for token in (
            "import time",
            'REQUEST_TIMING_HEADER = "Server-Timing"',
            'REQUEST_TIMING_METRIC = "ireland_geometry"',
            'REQUEST_TIMING_LOG_FIELD = "duration_ms"',
            "def _request_duration_ms(",
            "Server processing time before response headers",
            '"api_request_timing": True',
            '"api_request_timing_log_field": REQUEST_TIMING_LOG_FIELD',
        )
    )
    report_export_json_post = server_available and all(
        _file_contains(module, token)
        for token in (
            "def do_POST(self)",
            "REPORT_EXPORT_JSON_MAX_BODY_BYTES",
            "def _json_report_request(",
            "self._write_report_export(json_body=payload)",
            '"exportFilteredReportJson"',
            '"ReportExportRequest"',
        )
    )
    metadata_api = server_available and _file_contains(module, "METADATA_API_PATH")
    capabilities_api = server_available and all(
        _file_contains(module, token)
        for token in ("CAPABILITIES_API_PATH", "_write_capabilities")
    )
    active_graph_capabilities = server_available and all(
        _file_contains(module, token)
        for token in (
            "def _routing_graph_inventory(",
            '"routing_graph": routing_graph',
            '"active_graph_features": dict(routing_graph["features"])',
        )
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
    rejects_symlinks = server_available and all(
        _file_contains(module, token)
        for token in (
            "def _output_symlink_paths(",
            "def _require_symlink_free_output(",
            "Output directory contains a symlink",
        )
    )
    checks_manifest_alignment = server_available and all(
        _file_contains(module, token)
        for token in (
            "MANIFEST_ALIGNMENT_CONTRACT",
            "def _manifest_artifact_alignment(",
            "_sha256_file(candidate)",
            "current output hash differs from manifest",
        )
    )
    report_page_runtime = server_available and all(
        _file_contains(module, token)
        for token in (
            "REPORT_RUNTIME_CONTRACT",
            "def _report_runtime_state(",
            "def _apply_report_runtime(",
        )
    )
    report_export_runtime_headers = server_available and all(
        _file_contains(module, token)
        for token in (
            "REPORT_RUNTIME_HEADER_NAMES",
            "def _report_runtime_headers(",
            "headers=runtime_headers",
        )
    )
    report_runtime_api = server_available and all(
        _file_contains(module, token)
        for token in (
            "REPORT_RUNTIME_API_PATH",
            "def _write_report_runtime(",
            "path == REPORT_RUNTIME_API_PATH",
        )
    )
    report_runtime_snapshot = server_available and all(
        _file_contains(module, token)
        for token in (
            "def _report_runtime_snapshot(",
            '"manifest_sha256": manifest_sha256',
            '"snapshot": snapshot',
        )
    )
    report_runtime_source_alignment = server_available and all(
        _file_contains(module, token)
        for token in (
            "SOURCE_ALIGNMENT_CONTRACT",
            "def _report_source_alignment(",
            '"source_alignment": source_alignment',
            "manifest_source_alignment(",
        )
    )
    report_source_alignment_surfaces = server_available and all(
        _file_contains(module, token)
        for token in (
            "SOURCE_ALIGNMENT_SURFACES",
            '"source_alignment": source_alignment',
            '"source_alignment_surfaces": list(SOURCE_ALIGNMENT_SURFACES)',
            "source_alignment = _report_source_alignment(",
        )
    )
    explicit_project_root = server_available and all(
        _file_contains(module, token)
        for token in ("--project-root", "project_root=active_project_root", "server.project_root")
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
        and route_json_post
        and route_matrix_api
        and route_matrix_json_post
        and route_comparison_api
        and route_comparison_json_post
        and query_api
        and query_json_post
        and report_page_json_post
        and report_page_next_offset
        and report_page_sort_tiebreaker
        and conditional_api_responses
        and api_json_gzip
        and head_api
        and api_error_request_ids
        and api_request_logging
        and request_id_all_responses
        and security_response_headers
        and api_request_timing
        and report_export_json_post
        and query_pagination
        and query_cursor
        and query_invalid_score_cursor
        and query_auto_backend_failover
        and query_backend_health
        and query_min_score_finite
        and health_readiness
        and metadata_api
        and capabilities_api
        and active_graph_capabilities
        and openapi_api
        and interpretation_api
        and conditional_data_pack
        and rejects_symlinks
        and checks_manifest_alignment
        and report_page_runtime
        and report_export_runtime_headers
        and report_runtime_api
        and report_runtime_snapshot
        and report_runtime_source_alignment
        and report_source_alignment_surfaces
        and explicit_project_root
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
        "rejects_symlinks": rejects_symlinks,
        "checks_manifest_alignment": checks_manifest_alignment,
        "report_page_runtime": report_page_runtime,
        "report_export_runtime_headers": report_export_runtime_headers,
        "report_runtime_api": report_runtime_api,
        "report_runtime_snapshot": report_runtime_snapshot,
        "report_runtime_source_alignment": report_runtime_source_alignment,
        "report_source_alignment_surfaces": report_source_alignment_surfaces,
        "explicit_project_root": explicit_project_root,
        "route_api": route_api,
        "route_endpoint": "/api/route",
        "route_json_post": route_json_post,
        "route_json_max_body_bytes": 128 * 1024 if route_json_post else None,
        "route_matrix_api": route_matrix_api,
        "route_matrix_endpoint": "/api/route/matrix",
        "route_matrix_max_pairs": 25 if route_matrix_api else None,
        "route_matrix_json_post": route_matrix_json_post,
        "route_comparison_api": route_comparison_api,
        "route_comparison_endpoint": "/api/route/compare",
        "route_comparison_min_profiles": 2 if route_comparison_api else None,
        "route_comparison_max_profiles": 8 if route_comparison_api else None,
        "route_comparison_json_post": route_comparison_json_post,
        "query_api": query_api,
        "query_endpoint": "/api/query",
        "query_json_post": query_json_post,
        "query_json_max_body_bytes": 128 * 1024 if query_json_post else None,
        "report_page_json_post": report_page_json_post,
        "report_page_json_max_body_bytes": 128 * 1024 if report_page_json_post else None,
        "report_page_next_offset": report_page_next_offset,
        "report_page_sort_tiebreaker": report_page_sort_tiebreaker,
        "conditional_api_responses": conditional_api_responses,
        "api_json_gzip": api_json_gzip,
        "head_api": head_api,
        "api_error_contract": "ireland-geometry.api-error.v1"
        if api_error_request_ids
        else None,
        "api_error_request_ids": api_error_request_ids,
        "api_error_request_id_header": "X-Ireland-Geometry-Request-ID"
        if api_error_request_ids
        else None,
        "api_request_logging": api_request_logging,
        "api_request_log_field": "request_id" if api_request_logging else None,
        "request_id_all_responses": request_id_all_responses,
        "request_id_all_response_logging": request_id_all_responses,
        "security_response_headers": security_response_headers,
        "security_response_header_names": [
            "X-Content-Type-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ]
        if security_response_headers
        else [],
        "api_request_timing": api_request_timing,
        "api_request_timing_header": "Server-Timing" if api_request_timing else None,
        "api_request_timing_metric": "ireland_geometry" if api_request_timing else None,
        "api_request_timing_log_field": "duration_ms" if api_request_timing else None,
        "report_export_json_post": report_export_json_post,
        "report_export_json_max_body_bytes": 128 * 1024 if report_export_json_post else None,
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
        "active_graph_capabilities": active_graph_capabilities,
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
    """Describe the standalone coordinate-based route query commands."""
    module = root / "scripts" / "route_query.py"
    matrix_module = root / "scripts" / "route_matrix.py"
    comparison_module = root / "scripts" / "route_compare.py"
    routing_module = root / "scripts" / "road_routing.py"
    tokens = {
        "supports_snap_metadata": "snap_distance_m",
        "supports_duration_and_arrival": "estimated_duration_s",
        "supports_departure_profiles": "parse_departure",
        "supports_path_reconstruction": "path_node_ids",
        "supports_path_segment_explainability": "path_segments",
        "supports_path_segment_constraints": "def _attach_path_constraints(",
        "supports_path_segment_conditional_rules": "conditional_rules",
        "supports_path_segment_transition_rules": "transition_rules",
        "supports_path_segment_way_context": "road_context",
        "supports_portable_path_segments": "PortableRoadGraph",
        "supports_path_maneuvers": "def _build_route_maneuvers(",
        "supports_geojson": "def route_geojson(",
        "supports_ferry_geometry": "include_ferries",
        "supports_ferry_schedules": "ferry_schedule_n",
        "supports_ferry_durations": "shortest_path_metrics",
        "supports_public_holiday_calendars": "public_holiday_contract",
        "supports_route_objectives": "ROUTE_OBJECTIVES",
        "supports_route_matrix": "def query_route_matrix(",
        "supports_route_comparison": "def query_route_comparison(",
        "supports_vehicle_weight_profiles": "validate_vehicle_weight_t",
        "supports_vehicle_rating_profiles": "validate_vehicle_rating_t",
        "supports_vehicle_height_profiles": "validate_vehicle_height_m",
        "supports_vehicle_width_profiles": "validate_vehicle_width_m",
        "supports_vehicle_length_profiles": "validate_vehicle_length_m",
        "supports_vehicle_axleload_profiles": "validate_vehicle_axleload_t",
        "supports_vehicle_class_profiles": "validate_vehicle_class",
        "supports_conditional_access_profiles": "conditional_access_n",
        "supports_directional_conditional_access_profiles": "conditional_access_direction_n",
        "supports_vehicle_weight_conditional_access_profiles": "conditional_access_weight_n",
        "supports_multi_clause_conditional_access_profiles": "conditional_access_multiclause_n",
        "supports_maxweight_profiles": "maxweight_profiles",
        "supports_hgv_maxweight_profiles": "maxweight_hgv_profiles",
        "supports_hgv_maxweightrating_profiles": "maxweightrating_hgv_profiles",
        "supports_hgv_destination_profiles": "allow_hgv_destination",
        "supports_maxspeed_profiles": "maxspeed_profiles",
        "supports_maxspeed_conditional_profiles": "maxspeed_conditional_profiles",
        "supports_oneway_conditional_profiles": "oneway_conditional_profiles",
    }
    features = {
        name: module.is_file() and _file_contains(module, token)
        for name, token in tokens.items()
    }
    features["supports_ferry_waiting"] = routing_module.is_file() and _file_contains(
        routing_module,
        "def next_active_at(",
    )
    ferry_schedules_modeled = features["supports_ferry_schedules"]
    matrix_cli = matrix_module.is_file() and all(
        _file_contains(matrix_module, token)
        for token in ("def main(", "--origin", "--destination", "query_route_matrix")
    )
    comparison_cli = comparison_module.is_file() and all(
        _file_contains(comparison_module, token)
        for token in ("def main(", "--profile", "parse_route_profile_spec", "query_route_comparison")
    )
    return {
        "label": "point-to-point route query",
        "module": str(module),
        "command": "ireland-geometry-route",
        "coordinate_system": "WGS84 latitude/longitude",
        **features,
        "route_objectives": ["distance", "duration"] if features["supports_route_objectives"] else [],
        "vehicle_weight_profiles": features["supports_vehicle_weight_profiles"],
        "vehicle_rating_profiles": features["supports_vehicle_rating_profiles"],
        "vehicle_height_profiles": features["supports_vehicle_height_profiles"],
        "vehicle_width_profiles": features["supports_vehicle_width_profiles"],
        "vehicle_length_profiles": features["supports_vehicle_length_profiles"],
        "vehicle_axleload_profiles": features["supports_vehicle_axleload_profiles"],
        "vehicle_class_profiles": features["supports_vehicle_class_profiles"],
        "vehicle_classes": ["general", "delivery", "hgv", "psv", "taxi"] if features["supports_vehicle_class_profiles"] else [],
        "conditional_access_profiles": features["supports_conditional_access_profiles"],
        "directional_conditional_access_profiles": features["supports_directional_conditional_access_profiles"],
        "vehicle_weight_conditional_access_profiles": features["supports_vehicle_weight_conditional_access_profiles"],
        "multi_clause_conditional_access_profiles": features["supports_multi_clause_conditional_access_profiles"],
        "maxweight_profiles": features["supports_maxweight_profiles"],
        "maxheight_profiles": features["supports_vehicle_height_profiles"],
        "maxwidth_profiles": features["supports_vehicle_width_profiles"],
        "maxlength_profiles": features["supports_vehicle_length_profiles"],
        "maxaxleload_profiles": features["supports_vehicle_axleload_profiles"],
        "hgv_maxweight_profiles": features["supports_hgv_maxweight_profiles"],
        "maxweightrating_hgv_profiles": features["supports_hgv_maxweightrating_profiles"],
        "hgv_destination_profiles": features["supports_hgv_destination_profiles"],
        "maxspeed_profiles": features["supports_maxspeed_profiles"],
        "maxspeed_conditional_profiles": features["supports_maxspeed_conditional_profiles"],
        "oneway_conditional_profiles": features["supports_oneway_conditional_profiles"],
        "path_segment_constraints": features["supports_path_segment_constraints"],
        "path_segment_conditional_rules": features["supports_path_segment_conditional_rules"],
        "path_segment_transition_rules": features["supports_path_segment_transition_rules"],
        "path_segment_way_context": features["supports_path_segment_way_context"],
        "portable_path_segments": features["supports_portable_path_segments"],
        "path_segment_sources": ["sqlite_edges", "portable_edges", "not_available"]
        if features["supports_portable_path_segments"]
        else [],
        "path_maneuvers": features["supports_path_maneuvers"],
        "route_matrix": features["supports_route_matrix"],
        "route_matrix_max_pairs": 25 if features["supports_route_matrix"] else None,
        "route_matrix_cli": matrix_cli,
        "route_matrix_command": "ireland-geometry-route-matrix",
        "route_matrix_module": str(matrix_module),
        "route_comparison": features["supports_route_comparison"],
        "route_comparison_min_profiles": 2 if features["supports_route_comparison"] else None,
        "route_comparison_max_profiles": 8 if features["supports_route_comparison"] else None,
        "route_comparison_cli": comparison_cli,
        "route_comparison_command": "ireland-geometry-route-compare",
        "route_comparison_module": str(comparison_module),
        "ferry_schedules_modeled": ferry_schedules_modeled,
        "ferry_waiting_modeled": features["supports_ferry_waiting"],
        "ferry_schedule_contract": "ireland-geometry.ferry-schedules.v1"
        if ferry_schedules_modeled
        else None,
        "public_holiday_contract": "ireland-geometry.public-holidays.v1"
        if features["supports_public_holiday_calendars"]
        else None,
        "status": "available"
        if module.is_file() and routing_module.is_file() and all(features.values())
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
    report_source = root / "scripts" / "report.py"
    if not report_source.is_file():
        report_source = Path(__file__).resolve().with_name("report.py")
    feature_sources = (
        (standalone, lazy)
        if standalone.is_file() and lazy.is_file()
        else (report_source,)
    )
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
    route_objective_selection = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ('id="routeObjective"', "objective:$('routeObjective').value", "fastest duration")
    )
    route_wait_observability = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("ferry_wait_s", "ferry_wait_n", "function routeWaitText(route)")
    )
    route_ferry_breakdown = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("ferry_way_ids", "ferry_distance_m", "ferry_crossing_s", "ferry_edge_n", "function routeFerryText(route)")
    )
    route_segment_explainability = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("path_segment_source", "function routeSegmentText(route)")
    )
    route_transition_rule_provenance = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("transition_rules", "function routeSegmentText(route)")
    )
    route_way_context = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("road_context", "function routeSegmentText(route)")
    )
    route_maneuvers = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ("maneuvers", "function routeSegmentText(route)")
    )
    route_maneuver_list = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="routeManeuvers"',
            "function renderRouteManeuvers(payload)",
            "focusRouteManeuver",
        )
    )
    route_segment_inspector = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="routeSegments"',
            "function renderRouteSegments(payload)",
            "routeSegmentChecksText",
            "function routeSegmentChecksHtml(segment)",
            "route-segment-check-list",
        )
    )
    route_shareable_state = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="routeCopyLink"',
            "function restoreRouteState()",
            "function syncRouteState(fields)",
            "params.set('route','1')",
        )
    )
    route_response_download = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="routeDownloadJson"',
            'id="routeDownloadGeojson"',
            "function downloadRouteResponse(format)",
            "function routeGeojsonPayload(payload)",
        )
    )
    route_weight_profile = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="routeWeight"',
            "weight_t:$('routeWeight').value",
            "vehicle-weight",
        )
    )
    route_comparison_dashboard = all(
        _file_contains(path, token)
        for path in feature_sources
        for token in (
            'id="routeCompareRun"',
            'id="routeCompareProfiles"',
            "function runRouteComparison()",
            "fetch('/api/route/compare'",
            "method:'POST'",
            "Content-Type':'application/json",
            "delta_from_baseline",
            "function selectRouteComparisonProfile(index)",
            "params.set('compare','1')",
        )
    )
    route_matrix_dashboard = all(
        _file_contains(path, token)
        for path in feature_sources
        for token in (
            'id="routeMatrixRun"',
            'id="routeMatrixOrigins"',
            'id="routeMatrixDestinations"',
            "function runRouteMatrix()",
            "fetch('/api/route/matrix'",
            "method:'POST'",
            "Content-Type':'application/json",
            "function selectRouteMatrixPair(index)",
            "params.set('matrix','1')",
        )
    )
    report_page_json_dashboard = all(
        _file_contains(path, token)
        for path in feature_sources
        for token in (
            "function reportRequestBody(params, extra={})",
            "function fetchServerPage()",
            "body:JSON.stringify(reportRequestBody(params,{initial:false}))",
            "method:'POST'",
            "Content-Type':'application/json",
        )
    )
    report_export_json_dashboard = all(
        _file_contains(path, token)
        for path in feature_sources
        for token in (
            "function reportRequestBody(params, extra={})",
            "function downloadFiltered(format)",
            "body:JSON.stringify(reportRequestBody(params,{format}))",
            "method:'POST'",
            "Content-Type':'application/json",
        )
    )
    route_json_post = False
    route_matrix_json_post = False
    route_comparison_json_post = False
    report_page_json_post = False
    report_export_json_post = False
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
    interpretation_state = all(
        any(_file_contains(path, token) for token in ("const INTERPRETATION", "let INTERPRETATION"))
        for path in (standalone, lazy)
    )
    interpretation_panel = interpretation_state and all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in ('id="interpretation"', "function renderInterpretation()")
    )
    live_runtime_refresh = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="runtimeStatus"',
            "function applyRuntime(runtime)",
            "function applyRuntimeHeaders(headers)",
            "const BASE_INTERPRETATION = PACK.interpretation || {};",
            "INTERPRETATION={...BASE_INTERPRETATION};",
            "function renderRuntimeStatus()",
            "const runtimeChanged=applyRuntime(payload.runtime)",
            "applyRuntimeHeaders(response.headers)",
            "function runtimeIdentityText()",
            "const snapshot=REPORT_RUNTIME?.snapshot",
            "const sourceStatus=String(sourceAlignment.status||'not_reported')",
            "function refreshRuntime()",
            "function startRuntimeRefresh()",
            "setInterval(refreshRuntime,RUNTIME_REFRESH_MS)",
            "function renderMethod()",
        )
    )
    runtime_reload_notice = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="runtimeReloadNotice"',
            'id="runtimeReload"',
            "function runtimeDataIdentity(runtime)",
            "function renderRuntimeReloadNotice()",
            "runtimeReloadRequired",
            "location.reload()",
        )
    )
    report_error_recovery = all(
        _file_contains(path, token)
        for path in (standalone, lazy)
        for token in (
            'id="reportLoadError"',
            'id="reportRetry"',
            "function showReportError(error",
            "function revealReportError()",
            "function hideReportError()",
            "function retryReportRequest()",
            "document.body.classList.remove('intro-open')",
            "reportRetry'",
        )
    )
    lazy_initial_load_retry = lazy.is_file() and all(
        _file_contains(lazy, token)
        for token in (
            "function showInitialReportError(error)",
            "loadPack().catch(showInitialReportError)",
            "document.body.classList.remove('intro-open')",
            "classList.add('is-dismissed')",
            "retry?.addEventListener('click',()=>location.reload()",
        )
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
    route_json_post = server["route_json_post"]
    route_matrix_json_post = server["route_matrix_json_post"]
    route_comparison_json_post = server["route_comparison_json_post"]
    report_page_json_post = server["report_page_json_post"]
    report_export_json_post = server["report_export_json_post"]
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
        "route_objective_selection": route_objective_selection,
        "route_wait_observability": route_wait_observability,
        "route_ferry_breakdown": route_ferry_breakdown,
        "route_segment_explainability": route_segment_explainability,
        "route_transition_rule_provenance": route_transition_rule_provenance,
        "route_way_context": route_way_context,
        "route_maneuvers": route_maneuvers,
        "route_maneuver_list": route_maneuver_list,
        "route_segment_inspector": route_segment_inspector,
        "route_shareable_state": route_shareable_state,
        "route_response_download": route_response_download,
        "route_weight_profile": route_weight_profile,
        "route_comparison_dashboard": route_comparison_dashboard,
        "route_matrix_dashboard": route_matrix_dashboard,
        "report_page_json_dashboard": report_page_json_dashboard,
        "report_export_json_dashboard": report_export_json_dashboard,
        "route_json_post": route_json_post,
        "route_matrix_json_post": route_matrix_json_post,
        "route_comparison_json_post": route_comparison_json_post,
        "report_page_json_post": report_page_json_post,
        "report_export_json_post": report_export_json_post,
        "review_link": review_link,
        "review_filter": review_filter,
        "review_queue_membership": review_queue_membership,
        "quality_panel": quality_panel,
        "quality_findings": quality_findings,
        "quality_audit": quality_audit,
        "interpretation_panel": interpretation_panel,
        "live_runtime_refresh": live_runtime_refresh,
        "runtime_reload_notice": runtime_reload_notice,
        "report_error_recovery": report_error_recovery,
        "lazy_initial_load_retry": lazy_initial_load_retry,
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
        and route_objective_selection
        and route_wait_observability
        and route_ferry_breakdown
        and route_segment_explainability
        and route_transition_rule_provenance
        and route_way_context
        and route_maneuvers
        and route_maneuver_list
        and route_segment_inspector
        and route_shareable_state
        and route_response_download
        and route_weight_profile
        and route_comparison_dashboard
        and route_matrix_dashboard
        and report_page_json_dashboard
        and report_export_json_dashboard
        and review_filter
        and review_queue_membership
        and quality_panel
        and quality_findings
        and quality_audit
        and interpretation_panel
        and live_runtime_refresh
        and runtime_reload_notice
        and report_error_recovery
        and lazy_initial_load_retry
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
    requires_complete_validation = implementation and all(
        _file_contains(module, token)
        for token in (
            "VALIDATION_FILES",
            "source_validation",
            "schema_validation.json",
            "reproducibility.json",
        )
    )
    return {
        "label": "portable artifact bundle",
        "module": str(module),
        "command": "ireland-geometry-bundle",
        "contract": "ireland-geometry.bundle.v1" if implementation else None,
        "deterministic_zip": implementation,
        "verifies_archives": implementation,
        "extracts_archives": implementation,
        "requires_verified_guard": implementation and _file_contains(module, "--require-verified"),
        "requires_complete_validation": requires_complete_validation,
        "rejects_symlinks": implementation and _file_contains(module, "path.is_symlink()"),
        "rejects_archive_input_symlink": implementation
        and _file_contains(module, "Bundle archive input"),
        "rejects_extraction_parent_symlink": implementation
        and _file_contains(module, "Extraction parent directory"),
        "rejects_archive_inside_output": implementation
        and all(
            _file_contains(module, token)
            for token in ("archive.relative_to(output)", "outside the output directory")
        ),
        "checks_manifest_artifacts": implementation
        and _file_contains(module, "def _validate_verified_manifest("),
        "json_output": implementation and _file_contains(module, "--json"),
        "default_archive": str(output.parent / f"{output.name}.bundle.zip"),
        "manifest_exists": manifest.is_file(),
        "status": "available" if implementation else ("incomplete" if module.is_file() else "not_installed"),
    }


def release_check_capability_status(root: Path) -> dict[str, Any]:
    """Describe the unified, read-only release/readiness gate."""
    module = root / "scripts" / "release_check.py"
    implementation = module.is_file() and all(
        _file_contains(module, token)
        for token in (
            "RELEASE_CHECK_CONTRACT",
            "def check_release(",
            "def _output_artifact_alignment(",
            "def _publication_alignment(",
            "def _bundle_alignment(",
            "def _bundle_gate(",
            "def _symlink_root_errors(",
            "manifest_source_alignment(",
            "--require-pages",
            "--require-bundle",
            "--check-input-hashes",
            "--strict",
        )
    )
    return {
        "label": "unified release/readiness check",
        "module": str(module),
        "command": "ireland-geometry-release-check",
        "contract": "ireland-geometry.release-check.v1" if implementation else None,
        "read_only": implementation,
        "checks_doctor": implementation and _file_contains(module, "inspect_project("),
        "checks_output": implementation and _file_contains(module, "_output_artifact_alignment("),
        "checks_output_inventory": implementation and _file_contains(module, "unlisted artifact"),
        "checks_output_symlinks": implementation
        and _file_contains(module, "current output contains a symlink"),
        "rejects_symlink_roots": implementation
        and all(
            _file_contains(module, token)
            for token in (
                "def _symlink_root_errors(",
                "output directory must not be a symlink",
                "Pages site directory must not be a symlink",
            )
        ),
        "handles_non_directory_roots": implementation
        and all(
            _file_contains(module, token)
            for token in (
                "output.exists() and not output.is_dir()",
                "site.exists() and not site.is_dir()",
            )
        ),
        "checks_validation_records": implementation and _file_contains(module, "REQUIRED_OUTPUT_ARTIFACTS"),
        "checks_pages": implementation and _file_contains(module, "audit_site("),
        "checks_bundle": implementation and _file_contains(module, "verify_bundle("),
        "checks_source_alignment": implementation
        and _file_contains(module, "manifest_source_alignment("),
        "source_alignment_contract": SOURCE_ALIGNMENT_CONTRACT if implementation else None,
        "json_output": implementation and _file_contains(module, "--json"),
        "status": "available"
        if implementation
        else ("incomplete" if module.is_file() else "not_installed"),
    }


def current_output_alignment_status(
    output: Path, code_root: Path | None = None
) -> dict[str, Any]:
    """Recheck current output bytes through the shared release-gate helper."""
    module = (code_root or PACKAGE_ROOT) / "scripts" / "release_check.py"
    unavailable = {
        "status": "not_installed" if not module.is_file() else "incomplete",
        "passed": False,
        "checked_count": 0,
        "errors": ["current output alignment checker is unavailable"],
        "checker": str(module),
    }
    if not module.is_file():
        return unavailable
    try:
        from release_check import _output_artifact_alignment
    except ImportError:
        try:
            from scripts.release_check import _output_artifact_alignment
        except ImportError:
            return unavailable
    try:
        result = _output_artifact_alignment(output)
    except (AttributeError, KeyError, OSError, TypeError, UnicodeError, ValueError) as exc:
        return {
            **unavailable,
            "status": "fail",
            "errors": [f"current output alignment check failed: {exc}"],
        }
    if not isinstance(result, dict):
        return {
            **unavailable,
            "status": "fail",
            "errors": ["current output alignment checker returned an invalid result"],
        }
    return {**result, "checker": str(module)}


def current_source_alignment_status(
    project_root: Path,
    data_root: Path,
    output: Path,
) -> dict[str, Any]:
    """Recheck current input source metadata against the output manifest."""
    path = output / "manifest.json"
    unavailable = {
        "contract": SOURCE_ALIGNMENT_CONTRACT,
        "status": "not_provided",
        "passed": False,
        "mode": "metadata",
        "checked_count": 0,
        "unavailable_count": 0,
        "unhashed_count": 0,
        "hash_checked_count": 0,
        "metadata_checked_count": 0,
        "symlink_count": 0,
        "errors": [],
        "sources": [],
        "manifest": str(path),
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return unavailable
    if not isinstance(payload, dict):
        return unavailable
    result = manifest_source_alignment(
        payload,
        project_root=project_root,
        data_root=data_root,
        out_dir=output,
    )
    return {**result, "manifest": str(path)}


def validation_capability_status(
    output: Path,
    code_root: Path | None = None,
    *,
    source_alignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize persisted validation and current artifact-alignment gates."""
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
    verifier = code_root / "scripts" / "verify.py" if code_root is not None else None
    recursive_manifest_coverage = verifier is not None and _file_contains(verifier, "out_dir.rglob(")
    symlink_safe_manifest_coverage = verifier is not None and _file_contains(verifier, "path.is_symlink()")
    output_alignment = current_output_alignment_status(output, code_root)
    records_pass = all(item["passed"] for item in records.values())
    if not records_pass:
        status = "incomplete"
    elif output_alignment["status"] == "fail":
        status = "fail"
    elif output_alignment["status"] != "pass":
        status = "incomplete"
    elif source_alignment is not None and source_alignment.get("status") == "fail":
        status = "fail"
    else:
        status = "pass"
    result = {
        "label": "validation gates",
        "records": records,
        "output_alignment": output_alignment,
        "manifest_coverage": {
            "verifier": str(verifier) if verifier is not None else None,
            "recursive": recursive_manifest_coverage,
            "rejects_symlinks": symlink_safe_manifest_coverage,
            "status": "available"
            if recursive_manifest_coverage and symlink_safe_manifest_coverage
            else "incomplete",
        },
        "status": status,
    }
    if source_alignment is not None:
        result["source_alignment"] = source_alignment
    return result


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
    ferry_schedule_path = data / "roads" / "ferry_schedules.json"
    public_holiday_path = data / "roads" / "public_holidays.json"
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
            "restriction_conditional_weight_n": metadata.get("restriction_conditional_weight_n"),
            "restriction_conditional_unsupported_n": metadata.get("restriction_conditional_unsupported_n"),
            "restriction_conditional_stored_n": metadata.get("restriction_conditional_stored_n"),
            "restriction_conditional_unresolved_n": metadata.get("restriction_conditional_unresolved_n"),
            "restriction_conditional_geometry_unsupported_n": metadata.get("restriction_conditional_geometry_unsupported_n"),
            "conditional_access_n": metadata.get("conditional_access_n"),
            "conditional_access_vehicle_class_n": metadata.get("conditional_access_vehicle_class_n"),
            "conditional_access_direction_n": metadata.get("conditional_access_direction_n"),
            "conditional_access_weight_n": metadata.get("conditional_access_weight_n"),
            "conditional_access_multiclause_n": metadata.get("conditional_access_multiclause_n"),
            "conditional_access_unsupported_n": metadata.get("conditional_access_unsupported_n"),
            "conditional_access_excluded_n": metadata.get("conditional_access_excluded_n"),
            "maxweight_way_n": metadata.get("maxweight_way_n"),
            "maxweight_supported_way_n": metadata.get("maxweight_supported_way_n"),
            "maxweight_unlimited_way_n": metadata.get("maxweight_unlimited_way_n"),
            "maxweight_unsupported_way_n": metadata.get("maxweight_unsupported_way_n"),
            "maxweight_segment_n": metadata.get("maxweight_segment_n"),
            "maxweight_hgv_way_n": metadata.get("maxweight_hgv_way_n"),
            "maxweight_hgv_supported_way_n": metadata.get("maxweight_hgv_supported_way_n"),
            "maxweight_hgv_unlimited_way_n": metadata.get("maxweight_hgv_unlimited_way_n"),
            "maxweight_hgv_unsupported_way_n": metadata.get("maxweight_hgv_unsupported_way_n"),
            "maxweight_hgv_segment_n": metadata.get("maxweight_hgv_segment_n"),
            "maxweightrating_hgv_way_n": metadata.get("maxweightrating_hgv_way_n"),
            "maxweightrating_hgv_supported_way_n": metadata.get("maxweightrating_hgv_supported_way_n"),
            "maxweightrating_hgv_unlimited_way_n": metadata.get("maxweightrating_hgv_unlimited_way_n"),
            "maxweightrating_hgv_unsupported_way_n": metadata.get("maxweightrating_hgv_unsupported_way_n"),
            "maxweightrating_hgv_segment_n": metadata.get("maxweightrating_hgv_segment_n"),
            "hgv_destination_way_n": metadata.get("hgv_destination_way_n"),
            "hgv_destination_supported_way_n": metadata.get("hgv_destination_supported_way_n"),
            "hgv_destination_unsupported_way_n": metadata.get("hgv_destination_unsupported_way_n"),
            "hgv_destination_segment_n": metadata.get("hgv_destination_segment_n"),
            "maxheight_way_n": metadata.get("maxheight_way_n"),
            "maxheight_supported_way_n": metadata.get("maxheight_supported_way_n"),
            "maxheight_unlimited_way_n": metadata.get("maxheight_unlimited_way_n"),
            "maxheight_unsupported_way_n": metadata.get("maxheight_unsupported_way_n"),
            "maxheight_segment_n": metadata.get("maxheight_segment_n"),
            "maxheight_physical_way_n": metadata.get("maxheight_physical_way_n"),
            "maxheight_physical_supported_way_n": metadata.get("maxheight_physical_supported_way_n"),
            "maxheight_physical_unlimited_way_n": metadata.get("maxheight_physical_unlimited_way_n"),
            "maxheight_physical_unsupported_way_n": metadata.get("maxheight_physical_unsupported_way_n"),
            "maxheight_physical_segment_n": metadata.get("maxheight_physical_segment_n"),
            "maxwidth_way_n": metadata.get("maxwidth_way_n"),
            "maxwidth_supported_way_n": metadata.get("maxwidth_supported_way_n"),
            "maxwidth_unlimited_way_n": metadata.get("maxwidth_unlimited_way_n"),
            "maxwidth_unsupported_way_n": metadata.get("maxwidth_unsupported_way_n"),
            "maxwidth_segment_n": metadata.get("maxwidth_segment_n"),
            "maxlength_way_n": metadata.get("maxlength_way_n"),
            "maxlength_supported_way_n": metadata.get("maxlength_supported_way_n"),
            "maxlength_unlimited_way_n": metadata.get("maxlength_unlimited_way_n"),
            "maxlength_unsupported_way_n": metadata.get("maxlength_unsupported_way_n"),
            "maxlength_segment_n": metadata.get("maxlength_segment_n"),
            "maxaxleload_way_n": metadata.get("maxaxleload_way_n"),
            "maxaxleload_supported_way_n": metadata.get("maxaxleload_supported_way_n"),
            "maxaxleload_unlimited_way_n": metadata.get("maxaxleload_unlimited_way_n"),
            "maxaxleload_unsupported_way_n": metadata.get("maxaxleload_unsupported_way_n"),
            "maxaxleload_segment_n": metadata.get("maxaxleload_segment_n"),
            "maxspeed_way_n": metadata.get("maxspeed_way_n"),
            "maxspeed_supported_way_n": metadata.get("maxspeed_supported_way_n"),
            "maxspeed_unlimited_way_n": metadata.get("maxspeed_unlimited_way_n"),
            "maxspeed_unsupported_way_n": metadata.get("maxspeed_unsupported_way_n"),
            "maxspeed_segment_n": metadata.get("maxspeed_segment_n"),
            "maxspeed_conditional_way_n": metadata.get("maxspeed_conditional_way_n"),
            "maxspeed_conditional_supported_way_n": metadata.get("maxspeed_conditional_supported_way_n"),
            "maxspeed_conditional_unsupported_way_n": metadata.get("maxspeed_conditional_unsupported_way_n"),
            "maxspeed_conditional_segment_n": metadata.get("maxspeed_conditional_segment_n"),
            "oneway_conditional_way_n": metadata.get("oneway_conditional_way_n"),
            "oneway_conditional_supported_way_n": metadata.get("oneway_conditional_supported_way_n"),
            "oneway_conditional_unsupported_way_n": metadata.get("oneway_conditional_unsupported_way_n"),
            "oneway_conditional_segment_n": metadata.get("oneway_conditional_segment_n"),
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
            "ferry_schedule_contract": metadata.get("ferry_schedule_contract"),
            "ferry_schedule_n": metadata.get("ferry_schedule_n"),
            "ferry_schedule_unsupported_n": metadata.get("ferry_schedule_unsupported_n"),
            "ferry_public_holiday_schedule_n": metadata.get("ferry_public_holiday_schedule_n"),
            "ferry_duration_n": metadata.get("ferry_duration_n"),
            "ferry_schedule_path": str(ferry_schedule_path),
            "ferry_schedule_exists": ferry_schedule_path.is_file(),
            "ferry_schedule_symlink": ferry_schedule_path.is_symlink(),
            "ferry_schedule_contract_status": (
                "unsafe_symlink"
                if ferry_schedule_path.is_symlink()
                else ("available" if ferry_schedule_path.is_file() else "not_provided")
            ),
            "public_holiday_path": str(public_holiday_path),
            "public_holiday_exists": public_holiday_path.is_file(),
            "public_holiday_symlink": public_holiday_path.is_symlink(),
            "public_holiday_n": metadata.get("public_holiday_n"),
            "public_holiday_min_date": metadata.get("public_holiday_min_date"),
            "public_holiday_max_date": metadata.get("public_holiday_max_date"),
            "public_holiday_contract": metadata.get("public_holiday_contract"),
            "public_holiday_contract_status": (
                "unsafe_symlink"
                if public_holiday_path.is_symlink()
                else ("available" if public_holiday_path.is_file() else "not_provided")
            ),
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
    alignment = payload.get("manifest_cache_alignment")
    if isinstance(alignment, dict):
        mismatches = alignment.get("mismatches")
        unavailable = alignment.get("unavailable_stages")
        result["manifest_cache_alignment"] = {
            "contract": alignment.get("contract"),
            "status": alignment.get("status"),
            "check_count": alignment.get("check_count", 0),
            "mismatch_count": len(mismatches) if isinstance(mismatches, list) else 0,
            "unavailable_stages": unavailable if isinstance(unavailable, list) else [],
        }
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


def pages_capability_status(root: Path, package_root: Path | None = None) -> dict[str, Any]:
    """Report whether the committed static Pages site is publishable."""
    code_root = package_root or root
    site = root / "docs"
    audit_module = code_root / "scripts" / "pages_audit.py"
    publisher_module = code_root / "scripts" / "publish_pages.py"
    publication_manifest = site / "pages_manifest.json"
    result: dict[str, Any] = {
        "label": "static GitHub Pages publication",
        "site": str(site),
        "audit_module": str(audit_module),
        "publisher_module": str(publisher_module),
        "audit_command": "python scripts/pages_audit.py --json",
        "publish_command": "python scripts/publish_pages.py --json",
        "audit_contract": PAGES_AUDIT_CONTRACT,
        "publication_contract": PAGES_PUBLISH_CONTRACT,
        "audit_available": audit_module.is_file(),
        "publisher_available": publisher_module.is_file(),
        "publisher_checks_manifest_artifacts": publisher_module.is_file()
        and _file_contains(publisher_module, "_validate_manifest_artifacts("),
        "publisher_rejects_site_inside_output": publisher_module.is_file()
        and all(
            _file_contains(publisher_module, token)
            for token in ("site.relative_to(output)", "outside the output directory")
        ),
        "publisher_rejects_symlink_roots": publisher_module.is_file()
        and all(
            _file_contains(publisher_module, token)
            for token in ("reject_symlink_root(",)
        ),
        "publisher_handles_non_directory_roots": publisher_module.is_file()
        and _file_contains(publisher_module, "reject_symlink_root("),
        "audit_rejects_symlink_root": audit_module.is_file()
        and all(
            _file_contains(audit_module, token)
            for token in ("site_input.is_symlink()", "Pages site directory must not be a symlink")
        ),
        "audit_handles_non_directory_root": audit_module.is_file()
        and _file_contains(audit_module, "site_input.exists() and not site_input.is_dir()"),
        "site_exists": site.is_dir(),
        "publication_manifest": str(publication_manifest),
        "publication_manifest_exists": publication_manifest.is_file(),
        "source_revision": None,
        "published_file_count": sum(
            (site / name).is_file() for name in ("index.html", "review.html", "report.html")
        ),
        "audit": None,
        "status": "not_installed",
    }
    if not result["audit_available"] or not result["publisher_available"]:
        return result
    if not site.is_dir():
        result["status"] = "not_provided"
        return result
    if callable(audit_site):
        try:
            audit = audit_site(site)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            result["status"] = "invalid"
            result["error"] = str(exc)
            return result
        errors = audit.get("errors") if isinstance(audit, dict) else None
        pages = audit.get("pages") if isinstance(audit, dict) else None
        result["audit"] = {
            "contract": audit.get("contract") if isinstance(audit, dict) else None,
            "passed": audit.get("passed") is True if isinstance(audit, dict) else False,
            "error_count": len(errors) if isinstance(errors, list) else 0,
            "pages": pages if isinstance(pages, dict) else {},
        }
        result["status"] = "available" if result["audit"]["passed"] else "incomplete"
    else:
        result["status"] = "invalid"
        result["error"] = "Pages audit module could not be imported"
    if publication_manifest.is_file():
        try:
            payload = json.loads(publication_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            revision = payload.get("source_manifest_revision")
            if isinstance(revision, str):
                result["source_revision"] = revision
    return result


def _strict_readiness_blockers(
    *,
    errors: list[str],
    missing_inputs: list[str],
    missing_outputs: list[str],
    git_state: dict[str, Any],
    verification: dict[str, Any],
    validation: dict[str, Any],
    source_alignment: dict[str, Any],
    commands: dict[str, Any],
    output_safety: dict[str, Any],
    data_safety: dict[str, Any],
    file_write_safety: dict[str, Any],
    stale_required_inputs: list[str],
) -> list[dict[str, Any]]:
    """Explain each condition that keeps Doctor strict readiness false."""
    blockers: list[dict[str, Any]] = [
        {"code": "doctor_error", "message": error} for error in errors
    ]
    if missing_inputs:
        blockers.append(
            {
                "code": "missing_required_inputs",
                "message": "Required cached inputs are missing.",
                "items": missing_inputs,
            }
        )
    if missing_outputs:
        blockers.append(
            {
                "code": "missing_required_outputs",
                "message": "Required generated outputs are missing.",
                "items": missing_outputs,
            }
        )

    dirty = git_state.get("dirty")
    if dirty is True:
        worktree = git_state.get("worktree")
        worktree = worktree if isinstance(worktree, dict) else {}
        path_count = worktree.get("path_count")
        changed_count = worktree.get("changed_path_count")
        untracked_count = worktree.get("untracked_path_count")
        if all(isinstance(value, int) for value in (path_count, changed_count, untracked_count)):
            message = (
                "The working tree contains "
                f"{path_count} changed path(s) ({changed_count} tracked, {untracked_count} untracked)."
            )
        else:
            message = "The working tree contains uncommitted or untracked files."
        blockers.append(
            {
                "code": "git_dirty",
                "message": message,
                "worktree": worktree,
            }
        )
    elif dirty is not False:
        blockers.append(
            {
                "code": "git_unavailable",
                "message": "Git worktree cleanliness could not be confirmed.",
            }
        )
    if verification.get("status") != "pass":
        blockers.append(
            {
                "code": "verification_not_passing",
                "message": f"Output verification status is {verification.get('status')}.",
            }
        )
    if validation.get("status") != "pass":
        blockers.append(
            {
                "code": "validation_not_passing",
                "message": f"Complete validation gate status is {validation.get('status')}.",
            }
        )
    output_alignment = validation.get("output_alignment")
    if not isinstance(output_alignment, dict) or output_alignment.get("status") != "pass":
        blockers.append(
            {
                "code": "output_alignment_not_passing",
                "message": (
                    "Current output-manifest alignment status is "
                    f"{output_alignment.get('status') if isinstance(output_alignment, dict) else None}."
                ),
            }
        )
    if source_alignment.get("status") == "fail":
        blockers.append(
            {
                "code": "source_alignment_not_passing",
                "message": "Current input sources do not match the output manifest.",
                "errors": source_alignment.get("errors", []),
            }
        )
    if commands.get("status") != "available" or commands.get("installation_status") != "available":
        blockers.append(
            {
                "code": "console_commands_incomplete",
                "message": (
                    "Installed console command wrappers are incomplete: "
                    f"{commands.get('installed_count', 0)}/{commands.get('command_count', 0)} available."
                ),
            }
        )
    if output_safety.get("status") != "available":
        blockers.append(
            {
                "code": "output_safety_incomplete",
                "message": "Output-root safety guards are not complete across user-facing commands.",
                "guards": output_safety.get("guards", {}),
            }
        )
    observed_output_root = output_safety.get("observed_root")
    if isinstance(observed_output_root, dict) and observed_output_root.get("status") == "fail":
        blockers.append(
            {
                "code": "output_root_invalid",
                "message": "The active project output root is not a safe directory.",
                "output_root": observed_output_root,
            }
        )
    if data_safety.get("status") != "available":
        blockers.append(
            {
                "code": "data_safety_incomplete",
                "message": "Data-root safety guards are not complete across ingestion and analysis commands.",
                "guards": data_safety.get("guards", {}),
            }
        )
    if file_write_safety.get("status") != "available":
        blockers.append(
            {
                "code": "file_write_safety_incomplete",
                "message": "Atomic file-write guards are not complete across ingestion and pipeline commands.",
                "guards": file_write_safety.get("guards", {}),
            }
        )
    observed_root = data_safety.get("observed_root")
    if isinstance(observed_root, dict) and observed_root.get("status") == "fail":
        blockers.append(
            {
                "code": "data_root_invalid",
                "message": "The active project data root is not a safe directory.",
                "data_root": observed_root,
            }
        )
    if stale_required_inputs:
        blockers.append(
            {
                "code": "stale_required_inputs",
                "message": "Required cached inputs exceed the configured age limit.",
                "items": stale_required_inputs,
            }
        )
    return blockers


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
        path_status(output / "schema_validation.json", required=False, label="schema validation"),
        path_status(output / "reproducibility.json", required=False, label="reproducibility record"),
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
    output_safety = output_safety_status(code_root)
    data_safety = data_safety_status(code_root)
    data_safety["observed_root"] = data_root_boundary_status(data)
    output_safety["observed_root"] = output_root_boundary_status(output)
    file_write_safety = file_write_safety_status(code_root)
    pages = pages_capability_status(root, code_root)
    source_alignment = current_source_alignment_status(root, data, output)
    validation = validation_capability_status(
        output,
        code_root,
        source_alignment=source_alignment,
    )

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
    git_state = {
        "revision": git_revision(root),
        "dirty": git_dirty(root),
        "worktree": git_worktree_status(root),
    }
    if git_state["dirty"]:
        warnings.append("The working tree contains uncommitted or untracked files.")
    elif git_state["dirty"] is None:
        warnings.append("Git worktree cleanliness could not be confirmed.")
    if verification["status"] != "pass":
        warnings.append(
            "A passing output verification record is unavailable; run the pipeline verify stage."
        )
    if validation["status"] != "pass":
        warnings.append(
            "The complete validation gate is unavailable; verification, schema, and reproducibility records must all pass."
        )
    if validation["output_alignment"]["status"] != "pass":
        warnings.append(
            "Current output artifacts do not match the manifest inventory; rerun the build or release check."
        )
    if source_alignment["status"] == "fail":
        warnings.append(
            "Current input sources do not match the output manifest; rerun the build or release check."
        )
    if pages["status"] == "incomplete":
        warnings.append("The committed GitHub Pages site fails its publication audit.")
    elif pages["status"] == "invalid":
        warnings.append("The GitHub Pages publication capability is unavailable or invalid.")
    if commands["status"] != "available" or commands["installation_status"] != "available":
        warnings.append(
            "Installed console command wrappers are incomplete: "
            f"{commands['installed_count']}/{commands['command_count']} available; "
            "reinstall the package before using strict readiness."
        )
    if output_safety["status"] != "available":
        warnings.append(
            "Output-root safety guards are incomplete across user-facing commands; "
            "reinstall or upgrade the package before using strict readiness."
        )
    if data_safety["status"] != "available":
        warnings.append(
            "Data-root safety guards are incomplete across ingestion and analysis commands; "
            "reinstall or upgrade the package before using strict readiness."
        )
    if data_safety["observed_root"]["status"] == "fail":
        warnings.append("The active project data root is a symlink or non-directory.")
    if output_safety["observed_root"]["status"] == "fail":
        warnings.append(
            "The active project output root contains a symlink or is not a directory."
        )
    if file_write_safety["status"] != "available":
        warnings.append(
            "Atomic file-write guards are incomplete across ingestion and pipeline commands; "
            "reinstall or upgrade the package before using strict readiness."
        )
    missing_outputs = [name for name in STRICT_OUTPUT_NAMES if not (output / name).is_file()]
    if missing_outputs:
        warnings.append("Required generated outputs are missing: " + ", ".join(missing_outputs) + ".")

    strict_blockers = _strict_readiness_blockers(
        errors=errors,
        missing_inputs=missing_inputs,
        missing_outputs=missing_outputs,
        git_state=git_state,
        verification=verification,
        validation=validation,
        source_alignment=source_alignment,
        commands=commands,
        output_safety=output_safety,
        data_safety=data_safety,
        file_write_safety=file_write_safety,
        stale_required_inputs=stale_required_inputs,
    )
    strict_ready = (
        not errors
        and not missing_inputs
        and not missing_outputs
        and git_state["dirty"] is False
        and verification["status"] == "pass"
        and validation["status"] == "pass"
        and validation["output_alignment"]["status"] == "pass"
        and source_alignment["status"] != "fail"
        and commands["status"] == "available"
        and commands["installation_status"] == "available"
        and output_safety["status"] == "available"
        and output_safety["observed_root"]["status"] != "fail"
        and data_safety["status"] == "available"
        and data_safety["observed_root"]["status"] != "fail"
        and file_write_safety["status"] == "available"
        and not stale_required_inputs
    )
    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "doctor_version": DOCTOR_VERSION,
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
            "output_safety": output_safety,
            "data_safety": data_safety,
            "file_write_safety": file_write_safety,
            "commands": commands,
            "reports": report_capability_status(code_root, output, data_root=data),
            "report_server": report_server_status(code_root, output, data_root=data),
            "route_query": route_query_status(code_root),
            "geo3d": geo3d_capability_status(code_root),
            "query": query_data_status(code_root, output),
            "bundle": bundle_capability_status(code_root, output),
            "release_check": release_check_capability_status(code_root),
            "review": review_workflow_status(code_root, output),
            "exports": export_capability_status(output),
            "validation": validation,
            "schema_registry": schema_registry,
            "analysis_plan": analysis_plan,
            "manifest_paths": manifest_path_status(output),
            "pages": pages,
        },
        "verification": verification,
        "git": git_state,
        "summary": {
            "status": status,
            "strict_ready": strict_ready,
            "strict_blockers": strict_blockers,
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
    worktree = result["git"].get("worktree")
    if isinstance(worktree, dict) and worktree.get("available") is True:
        lines.append(
            "Git worktree: "
            f"{worktree.get('path_count', 0)} path(s), "
            f"{worktree.get('changed_path_count', 0)} tracked change(s), "
            f"{worktree.get('untracked_path_count', 0)} untracked"
        )
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
    lines.append("Active tree boundaries:")
    for label, capability_name in (
        ("data", "data_safety"),
        ("output", "output_safety"),
    ):
        capability = result["capabilities"].get(capability_name, {})
        observed = capability.get("observed_root") if isinstance(capability, dict) else None
        if not isinstance(observed, dict):
            continue
        symlink_count = observed.get("symlink_count", 0)
        detail = f"; symlinks={symlink_count}" if symlink_count else ""
        lines.append(
            f"  {observed.get('status', 'unknown')}: {label} root "
            f"{observed.get('path', 'unknown')}{detail}"
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
    for blocker in summary.get("strict_blockers", []):
        if isinstance(blocker, dict):
            lines.append(f"BLOCKER [{blocker.get('code', 'unknown')}]: {blocker.get('message', '')}")
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
        destination = project_output_file_path(
            destination,
            "doctor.json",
            label="doctor output",
        )
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
