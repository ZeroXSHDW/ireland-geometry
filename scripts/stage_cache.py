#!/usr/bin/env python3
"""Safe fingerprints and dependency declarations for incremental pipeline runs."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from runtime import (
        default_analysis_plan_path,
        git_revision,
        runtime_signature,
        sha256_file,
        sha256_path,
    )
except ImportError:
    from scripts.runtime import (
        default_analysis_plan_path,
        git_revision,
        runtime_signature,
        sha256_file,
        sha256_path,
    )


CACHE_VERSION = 4
CACHE_NAME = "stage_cache.json"
NON_CACHEABLE = {"report", "repro-check", "verify"}
CACHE_MODULE_PATH = Path(__file__).resolve()
RUNTIME_MODULE_PATH = CACHE_MODULE_PATH.with_name("runtime.py")
SOURCE_ROOT = CACHE_MODULE_PATH.parent.parent
STAGE_OUTPUT_NAMES: dict[str, tuple[str, ...]] = {
    "fetch": (),
    "fetch-niah": (),
    "analyze": ("analysis_results.csv", "top_patterns.csv", "ireland_buildings.geojson", "significance.csv"),
    "negative-controls": ("negative_controls.csv",),
    "niah": ("niah_join.csv", "niah_significance.csv", "niah_decades.csv", "niah_golden_angles.csv"),
    "architects": ("architects.csv", "architects_binary.csv", "architects_evidence.csv"),
    "sensitivity": ("matched_controls.csv", "matched_control_summary.csv", "matched_significance.csv", "hierarchical_model.csv"),
    "spatial-covariates": ("spatial_covariates.csv", "spatial_covariates_summary.csv"),
    "osm-history": ("mapping_history.csv", "mapping_history_summary.csv"),
    "validation": ("matched_controls_strict.csv", "matched_strict_summary.csv", "matched_strict_significance.csv", "matched_strict_balance.csv"),
    "building-parts": ("building_parts.csv", "lidar_coverage.csv"),
    "historical": ("historical_validation.csv", "candidate_dossiers.csv", "historical_source_register.csv"),
    "review": ("review_queue.csv", "review_calibration.csv", "review_confusion.csv", "review.html"),
    "quality-audit": ("data_quality.csv", "data_quality_summary.json", "data_quality_duplicates.csv"),
    "point-pattern": ("point_pattern.csv", "point_pattern_turns.csv"),
    "spatial-stats": ("ripley.csv", "moran.csv", "county_permutation.csv"),
    "spatial-bootstrap": ("spatial_bootstrap.csv",),
    "roads": ("roads_compare.csv",),
    "road-proximity": ("road_proximity.csv",),
    "road-routing": ("road_routing.csv", "road_routing_pairs.csv"),
    "holdout": ("holdout_assignments.csv", "holdout_results.csv", "analysis_plan_used.json"),
    "columnar": ("analysis_results.jsonl", "columnar_status.json"),
    "schema-audit": ("schema_validation.json",),
}

def _empty_cache(status: str = "empty") -> dict[str, Any]:
    return {
        "cache_version": CACHE_VERSION,
        "runtime": runtime_signature(),
        "cache_status": status,
        "stages": {},
    }


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_cache("missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cache("invalid_json")
    if (
        not isinstance(payload, dict)
        or payload.get("cache_version") != CACHE_VERSION
        or payload.get("runtime") != runtime_signature()
        or not isinstance(payload.get("stages"), dict)
    ):
        status = "invalid_shape"
        if isinstance(payload, dict):
            if payload.get("cache_version") != CACHE_VERSION:
                status = "version_mismatch"
            elif payload.get("runtime") != runtime_signature():
                status = "runtime_mismatch"
        return _empty_cache(status)
    payload["cache_status"] = "valid"
    return payload


def save_record(
    cache: dict[str, Any],
    stage: str,
    fingerprint: str,
    outputs: dict[str, str | None],
    *,
    fingerprint_inputs: dict[str, Any] | None = None,
) -> None:
    cache.setdefault("cache_version", CACHE_VERSION)
    cache["runtime"] = runtime_signature()
    cache["cache_status"] = "valid"
    cache.setdefault("stages", {})
    record: dict[str, Any] = {
        "fingerprint": fingerprint,
        "outputs": outputs,
    }
    if fingerprint_inputs is not None:
        record["fingerprint_inputs"] = fingerprint_inputs
    cache["stages"][stage] = record


def reuse_diagnostics(
    cache: dict[str, Any], stage: str, fingerprint: str, outputs: list[Path]
) -> dict[str, Any]:
    """Explain whether a stage can reuse its cache record.

    The diagnostic is deliberately derived from the same checks as
    ``can_reuse`` so dry-run automation and execution cannot disagree about a
    cache hit. ``reason`` is stable enough for dashboards and CI summaries.
    """
    result: dict[str, Any] = {
        "cacheable": stage not in NON_CACHEABLE,
        "reusable": False,
        "reason": "always_run" if stage in NON_CACHEABLE else "unknown",
        "fingerprint": fingerprint,
    }
    if stage in NON_CACHEABLE:
        return result
    if stage == "columnar" and outputs and _columnar_has_stale_optional_outputs(outputs[0].parent):
        result["reason"] = "stale_optional_output"
        return result
    record = cache.get("stages", {}).get(stage, {})
    if not isinstance(record, dict) or not record:
        status = cache.get("cache_status")
        result["reason"] = (
            f"cache_{status}"
            if isinstance(status, str) and status not in {"", "valid", "empty"}
            else "cache_record_missing"
        )
        return result
    result["recorded_fingerprint"] = record.get("fingerprint")
    expected = record.get("outputs")
    result["recorded_output_count"] = len(expected) if isinstance(expected, dict) else 0
    result["fingerprint_inputs_present"] = isinstance(record.get("fingerprint_inputs"), dict)
    if not result["fingerprint_inputs_present"]:
        result["reason"] = "fingerprint_inputs_missing"
        return result
    if record.get("fingerprint") != fingerprint:
        result["reason"] = "fingerprint_mismatch"
        return result
    if not isinstance(expected, dict) or set(expected) != {str(path) for path in outputs}:
        result["reason"] = "outputs_changed"
        return result
    missing = [path for path in outputs if not path.is_file()]
    if missing:
        result["reason"] = "output_missing"
        result["missing_outputs"] = [str(path) for path in missing]
        return result
    mismatched = [
        path
        for path in outputs
        if expected.get(str(path)) != sha256_file(path)
    ]
    if mismatched:
        result["reason"] = "output_hash_mismatch"
        result["mismatched_outputs"] = [str(path) for path in mismatched]
        return result
    result["reusable"] = True
    result["reason"] = "cache_hit"
    return result


def can_reuse(cache: dict[str, Any], stage: str, fingerprint: str, outputs: list[Path]) -> bool:
    return bool(reuse_diagnostics(cache, stage, fingerprint, outputs)["reusable"])


def stage_outputs(stage: str, data_root: Path, out_dir: Path) -> list[Path]:
    if stage == "fetch":
        return [data_root / "combined.json"]
    if stage == "fetch-niah":
        return [data_root / "niah" / "niah.json"]
    outputs = [out_dir / name for name in STAGE_OUTPUT_NAMES.get(stage, ())]
    if stage == "columnar":
        try:
            status = json.loads((out_dir / "columnar_status.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = {}
        for name, filename in (("parquet", "analysis_results.parquet"), ("duckdb", "analysis.duckdb")):
            if status.get(name, {}).get("status") == "available":
                outputs.append(out_dir / filename)
    return outputs


def _columnar_has_stale_optional_outputs(out_dir: Path) -> bool:
    """Return whether an unavailable backend has left a generated file behind."""
    try:
        status = json.loads((out_dir / "columnar_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {}
    for name, filename in (("parquet", "analysis_results.parquet"), ("duckdb", "analysis.duckdb")):
        if status.get(name, {}).get("status") != "available" and (out_dir / filename).is_file():
            return True
    return False


def record_output_hashes(outputs: list[Path]) -> dict[str, str | None]:
    return {str(path): sha256_path(path) for path in outputs}


def _source_root_for_script(script_path: Path) -> Path:
    """Resolve the project root containing a stage script or use the package root."""
    path = script_path.resolve()
    if path.parent.name == "scripts":
        return path.parent.parent
    return SOURCE_ROOT


def _local_module_path(name: str, source_root: Path) -> Path | None:
    """Resolve a top-level or ``scripts.`` import to a local Python module."""
    parts = [part for part in name.lstrip(".").split(".") if part]
    if parts and parts[0] == "scripts":
        parts = parts[1:]
    if not parts:
        return None
    candidate = source_root / "scripts" / Path(*parts)
    module = candidate.with_suffix(".py")
    return module if module.is_file() else None


def _import_names(tree: ast.AST) -> set[str]:
    """Return local-looking module names from a parsed Python module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = prefix + (node.module or "")
            if module:
                names.add(module)
            for alias in node.names:
                if node.module in {None, "", "scripts"}:
                    names.add(f"{module}{'.' if module else ''}{alias.name}")
    return names


def local_module_paths(script_path: Path) -> list[Path]:
    """Return the transitive local-import closure for a stage script.

    Only modules under the project ``scripts/`` directory are followed. This
    catches edits to shared analytical helpers without invalidating a stage
    for unrelated operational modules such as the report server or query CLI.
    """
    root = _source_root_for_script(script_path)
    pending = [Path(script_path).resolve()]
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file() or path.suffix != ".py":
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for name in _import_names(tree):
            module = _local_module_path(name, root)
            if module is not None and module not in seen:
                pending.append(module)
    return sorted(seen)


def local_module_signatures(script_path: Path) -> dict[str, str | None]:
    """Hash the local import closure with project-relative keys."""
    root = _source_root_for_script(script_path)
    signatures: dict[str, str | None] = {}
    for path in local_module_paths(script_path):
        try:
            key = str(path.relative_to(root))
        except ValueError:
            key = str(path)
        signatures[key] = sha256_file(path)
    return signatures


def _configured_path(args: Any, argument: str, default: Path, root: Path) -> Path:
    value = getattr(args, argument, None)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else root / path


def input_paths(
    stage: str,
    *,
    data_root: Path,
    out_dir: Path,
    pbf: Path,
    args: Any,
    schema_path: Path,
    root: Path,
) -> list[Path]:
    """Declare the files a stage actually depends on.

    Missing optional sources are included deliberately.  Their later arrival
    changes the fingerprint and therefore invalidates a prior cache decision.
    """
    analysis = out_dir / "analysis_results.csv"
    combined = data_root / "combined.json"
    niah = data_root / "niah" / "niah.json"
    paths: dict[str, list[Path]] = {
        "fetch": [pbf],
        "fetch-niah": [
            path
            for path in (data_root / "niah").rglob("*")
            if path.is_file() and path.name != "niah.json"
        ],
        "analyze": [combined],
        "negative-controls": [analysis],
        "niah": [combined, niah, analysis],
        "architects": [niah, out_dir / "niah_join.csv", analysis],
        "sensitivity": [analysis],
        "spatial-covariates": [
            analysis,
            out_dir / "niah_join.csv",
            _configured_path(args, "boundaries", data_root / "boundaries" / "admin.geojson", root),
            _configured_path(args, "settlements", data_root / "boundaries" / "settlements.geojson", root),
        ],
        "osm-history": [
            analysis,
            _configured_path(args, "osm_history", data_root / "history" / "osm_history.csv", root),
        ],
        "validation": [analysis, out_dir / "spatial_covariates.csv", out_dir / "niah_join.csv"],
        "building-parts": [
            combined,
            analysis,
            _configured_path(args, "lidar", data_root / "lidar" / "building_heights.csv", root),
        ],
        "historical": [
            combined,
            analysis,
            out_dir / "niah_join.csv",
            out_dir / "architects_evidence.csv",
            _configured_path(args, "historical_references", data_root / "historical" / "references.csv", root),
        ],
        "review": [
            out_dir / "candidate_dossiers.csv",
            out_dir / "historical_validation.csv",
            _configured_path(args, "review_labels", data_root / "review" / "labels.csv", root),
            schema_path.parent.parent / "scripts" / "review_ui.py",
        ],
        "quality-audit": [analysis, out_dir / "niah_join.csv", out_dir / "mapping_history.csv", out_dir / "spatial_covariates.csv", out_dir / "lidar_coverage.csv", out_dir / "road_routing.csv", out_dir / "review_queue.csv"],
        "point-pattern": [analysis],
        "spatial-stats": [analysis, out_dir / "niah_join.csv"],
        "spatial-bootstrap": [analysis],
        "roads": [analysis, pbf],
        "road-proximity": [analysis, pbf],
        "road-routing": [analysis, out_dir / "matched_controls_strict.csv", out_dir / "matched_controls.csv"],
        "holdout": [analysis, _configured_path(args, "analysis_plan", default_analysis_plan_path(root), root)],
        "columnar": [analysis],
        "schema-audit": [schema_path],
    }
    if stage == "road-routing":
        graph = getattr(args, "road_graph", None)
        if graph:
            paths[stage].append(_configured_path(args, "road_graph", data_root / "roads", root))
        else:
            paths[stage].extend(
                (
                    data_root / "roads" / "road_nodes.csv",
                    data_root / "roads" / "road_edges.csv",
                    data_root / "roads" / "road_graph.sqlite",
                    data_root / "roads" / "road_graph_metadata.json",
                )
            )
    selected = list(paths.get(stage, []))
    if stage == "schema-audit":
        try:
            registry = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            registry = {}
        selected.extend(out_dir / str(name) for name in registry.get("artifacts", {}))
    if stage == "road-routing" and getattr(args, "road_from_pbf", False):
        selected.append(pbf)
    unique: dict[str, Path] = {str(path): path for path in selected}
    return [unique[key] for key in sorted(unique)]


def fingerprint_payload(
    stage: str,
    *,
    command: list[str],
    script_path: Path,
    controller_path: Path,
    input_signatures: dict[str, str | None],
) -> dict[str, Any]:
    return {
        "cache_version": CACHE_VERSION,
        "stage": stage,
        "revision": git_revision(),
        "command": command,
        "script_sha256": sha256_file(script_path),
        "controller_sha256": sha256_file(controller_path),
        "cache_module_sha256": sha256_file(CACHE_MODULE_PATH),
        "runtime_module_sha256": sha256_file(RUNTIME_MODULE_PATH),
        "local_module_sha256": local_module_signatures(script_path),
        "inputs": input_signatures,
        "runtime": runtime_signature(),
    }


def fingerprint_from_payload(payload: dict[str, Any]) -> str:
    """Hash a serialized fingerprint payload into the stage cache key."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint(
    stage: str,
    *,
    command: list[str],
    script_path: Path,
    controller_path: Path,
    input_signatures: dict[str, str | None],
) -> str:
    return fingerprint_from_payload(
        fingerprint_payload(
            stage,
            command=command,
            script_path=script_path,
            controller_path=controller_path,
            input_signatures=input_signatures,
        )
    )
