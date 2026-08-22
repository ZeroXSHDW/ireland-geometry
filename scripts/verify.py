#!/usr/bin/env python3
"""Validate the complete Ireland geometry artifact set.

The verifier is intentionally independent of the report builder and analysis
stages. It checks row contracts, geometry/report alignment, statistical
fields, provenance, and (when Node is available) inline report JavaScript
syntax. A machine-readable result is written to ``output/verification.json``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        default_project_root,
        git_dirty,
        git_revision,
        output_counts,
        path_metadata_sha256,
        path_modified_at,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
        resolve_manifest_source_path,
        sha256_file,
        sha256_path,
        utc_now,
    )
except ImportError:
    from scripts.runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        default_analysis_plan_path,
        default_project_root,
        git_dirty,
        git_revision,
        output_counts,
        path_metadata_sha256,
        path_modified_at,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
        resolve_manifest_source_path,
        sha256_file,
        sha256_path,
        utc_now,
    )

try:
    from stage_cache import CACHE_VERSION, fingerprint_from_payload
except ImportError:
    from scripts.stage_cache import CACHE_VERSION, fingerprint_from_payload

try:
    from holdout import holdout, signal_value
    from stats import assign_verdict, holm_adjust
except ImportError:
    from scripts.holdout import holdout, signal_value
    from scripts.stats import assign_verdict, holm_adjust

try:
    from columnar import COLUMNAR_CONTRACT, audit_backend
except ImportError:
    from scripts.columnar import COLUMNAR_CONTRACT, audit_backend


MANIFEST_CACHE_CONTRACT = "ireland-geometry.manifest-cache.v1"


ANALYSIS_REQUIRED = {
    "osm_id",
    "group",
    "is_control",
    "area_m2",
    "convexity",
    "valid",
    "repaired",
    "multipart",
    "hole_count",
    "rectangularity",
    "angle_entropy",
    "fourier_1",
    "fourier_4",
    "building_tag",
    "height_m",
}
SIGNIFICANCE_REQUIRED = {"p_adjusted", "method", "verdict"}
VERDICTS = {"SIGNAL", "suggestive", "background"}
INTERPRETATION_CONTRACT = "ireland-geometry.interpretation.v1"
SCORING_CONTRACT = "ireland-geometry.exploratory-score.v1"


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def require_file(path: Path, errors: list[str]) -> bool:
    if not path.exists() or not path.is_file():
        errors.append(f"missing required artifact: {path}")
        return False
    if path.stat().st_size == 0:
        errors.append(f"empty artifact: {path}")
        return False
    return True


def check_scoring_config_contract(path: Path, errors: list[str]) -> dict:
    """Validate the versioned exploratory score provenance artifact."""
    if not require_file(path, errors):
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"scoring_config.json is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("scoring_config.json root is not an object")
        return {}
    if payload.get("contract") != SCORING_CONTRACT or payload.get("version") != 1:
        errors.append("scoring_config.json has an unsupported contract or version")
    config = payload.get("config")
    if not isinstance(config, dict):
        errors.append("scoring_config.json has no config object")
        return payload
    if config.get("contract") != SCORING_CONTRACT or config.get("version") != 1:
        errors.append("scoring_config.json config contract/version does not match")
    expected_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if payload.get("config_sha256") != expected_hash:
        errors.append("scoring_config.json config_sha256 does not match config")
    if "not an inferential statistic" not in str(config.get("label", "")).lower():
        errors.append("scoring_config.json must label the score as non-inferential")
    plan_path = Path(str(payload.get("plan_path", "")))
    if plan_path.is_file() and payload.get("plan_sha256") != sha256_file(plan_path):
        errors.append("scoring_config.json plan_sha256 does not match its plan file")
    return payload


def check_columns(
    rows: list[dict[str, str]], required: set[str], label: str, errors: list[str]
) -> None:
    if not rows:
        errors.append(f"{label} has no data rows")
        return
    missing = sorted(required - set(rows[0]))
    if missing:
        errors.append(f"{label} is missing columns: {', '.join(missing)}")


def check_header(path: Path, required: set[str], label: str, errors: list[str]) -> None:
    """Check a CSV header while allowing a valid zero-row diagnostic."""
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            fields = set(csv.DictReader(handle).fieldnames or [])
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"{label} could not be read: {exc}")
        return
    missing = sorted(required - fields)
    if missing:
        errors.append(f"{label} is missing columns: {', '.join(missing)}")
def check_probabilities(rows: list[dict[str, str]], label: str, errors: list[str]) -> None:
    check_columns(rows, SIGNIFICANCE_REQUIRED, label, errors)
    if rows and not {"p_value", "p"}.intersection(rows[0]):
        errors.append(f"{label} is missing a raw p-value column")
    for index, row in enumerate(rows, 2):
        for field in ("p_value", "p_adjusted", "p"):
            raw = row.get(field, "")
            if raw == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                errors.append(f"{label} row {index} has non-numeric {field}: {raw!r}")
                continue
            if not 0.0 < value <= 1.0:
                errors.append(f"{label} row {index} has invalid {field}: {value}")
        verdict = row.get("verdict", "")
        if verdict and verdict not in VERDICTS:
            errors.append(f"{label} row {index} has invalid verdict: {verdict!r}")


def check_holm_contract(rows: list[dict[str, str]], label: str, errors: list[str]) -> None:
    """Recompute family-wise Holm adjustments and directional verdicts.

    The analysis stages write adjusted values into CSVs, so checking only their
    range would allow a stale or manually edited significance result to look
    valid.  Recomputing the adjustment here keeps the statistical artifacts
    tied to their reported raw p-values and test families.
    """
    if not rows:
        return
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for index, row in enumerate(rows, 2):
        family = row.get("test_family") or "__default__"
        grouped[family].append((index, row))
    for family, members in grouped.items():
        raw_values: list[float] = []
        parsed_members: list[tuple[int, dict[str, str]]] = []
        for index, row in members:
            raw = row.get("p_value", "") or row.get("p", "")
            adjusted = row.get("p_adjusted", "")
            if raw == "" or adjusted == "":
                errors.append(f"{label} row {index} is missing raw or Holm-adjusted p-value")
                continue
            try:
                raw_value = float(raw)
                adjusted_value = float(adjusted)
            except ValueError:
                continue
            if not math.isfinite(raw_value) or not math.isfinite(adjusted_value):
                continue
            raw_values.append(raw_value)
            parsed_members.append((index, row))
        if len(parsed_members) != len(members):
            continue
        expected = holm_adjust(raw_values)
        for (index, row), expected_adjusted in zip(parsed_members, expected):
            actual_adjusted = float(row["p_adjusted"])
            tolerance = 1e-12 + 1e-9 * max(abs(expected_adjusted), abs(actual_adjusted))
            if abs(actual_adjusted - expected_adjusted) > tolerance:
                errors.append(
                    f"{label} row {index} has incorrect Holm adjustment for family {family!r}: "
                    f"{actual_adjusted} != {expected_adjusted}"
                )
            if row.get("direction", "") != "":
                try:
                    direction = int(row["direction"])
                except ValueError:
                    continue
                if direction not in {-1, 0, 1}:
                    errors.append(f"{label} row {index} has invalid direction: {direction}")
                    continue
                expected_verdict = assign_verdict(expected_adjusted, direction)
                if row.get("verdict", "") != expected_verdict:
                    errors.append(
                        f"{label} row {index} has incorrect verdict: "
                        f"{row.get('verdict')!r} != {expected_verdict!r}"
                    )


def check_statistical_contract(rows: list[dict[str, str]], label: str, errors: list[str]) -> None:
    check_probabilities(rows, label, errors)
    check_holm_contract(rows, label, errors)


def check_report(path: Path, errors: list[str], warnings: list[str]) -> None:
    if not require_file(path, errors):
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    for token in ("__DATA__", "__OUTLINES__", "__MARKER_LIMIT__", "${TOP_N_MARKERS}"):
        if token in text:
            errors.append(f"report still contains template token: {token}")
    if "const PACK =" not in text or "function init" not in text:
        errors.append("report is missing its data pack or initialization script")

    node = shutil.which("node")
    if not node:
        warnings.append("Node.js unavailable; skipped inline report JavaScript syntax check")
        return
    blocks = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>", text, flags=re.IGNORECASE | re.DOTALL
    )
    inline = "\n".join(blocks)
    result = subprocess.run(
        [node, "--check"], input=inline, capture_output=True, text=True, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        errors.append("report inline JavaScript failed syntax check: " + " ".join(detail[:3]))


def check_dashboard_review_contract(path: Path, label: str, errors: list[str]) -> None:
    """Require review queue scope and membership affordances in a dashboard artifact."""
    if not require_file(path, errors):
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        errors.append(f"{label} could not be read for review contract: {exc}")
        return
    required = {
        "review state filter": 'id="reviewState"',
        "review filter function": "function reviewFilterState(row)",
        "unqueued filter state": "not_queued",
        "queue coverage summary": "review_queue_targets",
        "unqueued review label": "Not in review queue",
        "review deep link": "function reviewHref(row)",
        "accessible sort control": 'class="sort-button"',
        "sort state": "function updateSortHeaders",
        "keyboard sort handling": "document.addEventListener('keydown'",
        "keyboard-focusable rows": 'tabindex="0"',
        "accessible search label": 'aria-label="Search analyzed targets"',
    }
    for name, token in required.items():
        if token not in text:
            errors.append(f"{label} is missing {name} contract token: {token}")


def check_review_page_contract(path: Path, errors: list[str]) -> None:
    """Require scope notices and shareable URL state in the generated review page."""
    if not require_file(path, errors):
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        errors.append(f"review page could not be read for review contract: {exc}")
        return
    required = {
        "target scope notice": 'id="targetNotice"',
        "out-of-queue notice text": "not in the current",
        "target deep-link parameter": "TARGET_ID",
        "review search filter": 'id="search"',
        "review state filter": 'id="statusFilter"',
        "URL-state restore": "restoreViewState",
        "URL-state synchronization": "syncViewState",
        "shareable URL update": "history.replaceState",
        "label export": "expert-labels.csv",
        "JSON label export": "function downloadJson",
        "JSON label import": "async function importJson",
        "JSON import input": 'aria-label="Import review labels JSON"',
        "JSON schema guard": "Unsupported JSON backup schema",
        "JSON queue guard": "different review queue",
        "JSON invalid-record guard": "invalid label records",
        "accessible search label": 'aria-label="Search review queue"',
        "accessible edit labels": 'aria-label="Review label for',
        "live progress region": 'aria-live="polite"',
    }
    for name, token in required.items():
        if token not in text:
            errors.append(f"review page is missing {name} contract token: {token}")


def check_review_queue_contract(
    rows: list[dict[str, str]],
    target_ids: set[str],
    errors: list[str],
) -> set[str]:
    """Validate that the review queue is a unique subset of analyzed targets."""
    queue_ids = [row.get("osm_id", "") for row in rows]
    if any(not osm_id for osm_id in queue_ids):
        errors.append("review_queue.csv contains a blank osm_id")
    if len(queue_ids) != len(set(queue_ids)):
        errors.append("review_queue.csv contains duplicate osm_id values")
    unknown = sorted(set(queue_ids).difference(target_ids))
    if unknown:
        errors.append(
            "review_queue.csv contains ids outside analyzed targets: "
            + ", ".join(unknown[:5])
        )
    return set(queue_ids)


def check_report_data_contract(
    path: Path,
    target_ids: set[str],
    queue_ids: set[str],
    errors: list[str],
) -> None:
    """Align report target membership, queue rows, and coverage summary."""
    if not require_file(path, errors):
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"report_data.json is not valid JSON: {exc}")
        return
    if not isinstance(payload, dict):
        errors.append("report_data.json root is not an object")
        return
    rows = payload.get("targets")
    if not isinstance(rows, list):
        errors.append("report_data.json has no target row list")
        return
    row_ids = [row.get("osm_id", "") for row in rows if isinstance(row, dict)]
    if len(row_ids) != len(rows) or any(not osm_id for osm_id in row_ids):
        errors.append("report_data.json contains target rows without osm_id")
    if len(row_ids) != len(set(row_ids)):
        errors.append("report_data.json contains duplicate target osm_id values")
    if set(row_ids) != target_ids:
        errors.append("report_data.json target ids do not exactly match analysis targets")

    membership_ids: set[str] = set()
    for index, row in enumerate(rows, 1):
        review = row.get("review") if isinstance(row, dict) else None
        if not isinstance(review, dict) or not isinstance(review.get("in_queue"), bool):
            errors.append(f"report_data.json target row {index} has no boolean review.in_queue")
            continue
        if review["in_queue"]:
            membership_ids.add(row.get("osm_id", ""))
    if membership_ids != queue_ids:
        errors.append("report_data.json review membership does not match review_queue.csv")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("report_data.json has no summary object")
        return
    try:
        summary_targets = int(summary.get("targets", ""))
        summary_queue = int(summary.get("review_queue_targets", ""))
        actual_pct = float(summary.get("review_queue_coverage_pct", ""))
    except (TypeError, ValueError):
        errors.append("report_data.json summary has invalid review coverage fields")
        return
    if summary_targets != len(rows):
        errors.append("report_data.json summary targets does not match target rows")
    if summary_queue != len(queue_ids):
        errors.append("report_data.json summary review_queue_targets does not match queue rows")
    expected_pct = round(100.0 * len(queue_ids) / len(rows), 4) if rows else 0.0
    if not math.isclose(actual_pct, expected_pct, abs_tol=0.00005):
        errors.append(
            f"report_data.json review coverage {actual_pct!r} != expected {expected_pct!r}"
        )

    validation = summary.get("validation")
    if not isinstance(validation, dict):
        errors.append("report_data.json summary has no validation object")
    else:
        if not isinstance(validation.get("passed"), bool):
            errors.append("report_data.json validation.passed is not boolean")
        if not isinstance(validation.get("manifest_available"), bool):
            errors.append("report_data.json validation.manifest_available is not boolean")
        records = validation.get("records")
        if not isinstance(records, dict):
            errors.append("report_data.json validation has no records object")
        else:
            expected_records = {"verification", "schema_validation", "reproducibility"}
            missing_records = sorted(expected_records.difference(records))
            if missing_records:
                errors.append(
                    "report_data.json validation is missing records: "
                    + ", ".join(missing_records)
                )
        expected_ready = (
            validation.get("passed") is True
            and validation.get("manifest_available") is True
        )
        if summary.get("analysis_ready") is not expected_ready:
            errors.append("report_data.json analysis_ready does not match validation gates")
    interpretation = payload.get("interpretation")
    if not isinstance(interpretation, dict):
        errors.append("report_data.json has no interpretation object")
    else:
        findings = interpretation.get("findings")
        if not isinstance(findings, list):
            errors.append("report_data.json interpretation has no findings list")
        else:
            finding_ids = []
            for index, finding in enumerate(findings, 1):
                if not isinstance(finding, dict):
                    errors.append(f"report_data.json interpretation finding {index} is not an object")
                    continue
                finding_ids.append(finding.get("id", ""))
                for field in ("id", "title", "status", "text"):
                    if not str(finding.get(field, "")).strip():
                        errors.append(
                            f"report_data.json interpretation finding {index} has no {field}"
                        )
            if len(finding_ids) != len(set(finding_ids)):
                errors.append("report_data.json interpretation contains duplicate finding ids")
        if not isinstance(interpretation.get("caveats"), list):
            errors.append("report_data.json interpretation has no caveats list")


def check_interpretation_artifact(
    path: Path, report_data_path: Path, errors: list[str]
) -> None:
    """Validate the compact interpretation sidecar against the report pack."""
    if not require_file(path, errors):
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"interpretation.json is not valid JSON: {exc}")
        return
    if not isinstance(payload, dict):
        errors.append("interpretation.json root is not an object")
        return
    try:
        report_data = json.loads(report_data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"interpretation.json could not read report_data.json: {exc}")
        return
    if not isinstance(report_data, dict):
        errors.append("interpretation.json comparison report_data.json root is not an object")
        return

    required = {
        "contract",
        "status",
        "available",
        "analysis_ready",
        "validation",
        "summary",
        "interpretation",
        "source",
    }
    missing = sorted(required.difference(payload))
    if missing:
        errors.append("interpretation.json is missing keys: " + ", ".join(missing))
    if payload.get("contract") != INTERPRETATION_CONTRACT:
        errors.append("interpretation.json has an invalid contract")
    if payload.get("status") not in {"ok", "not_provided"}:
        errors.append("interpretation.json has an invalid status")
    if not isinstance(payload.get("available"), bool):
        errors.append("interpretation.json available is not boolean")
    if not isinstance(payload.get("analysis_ready"), bool):
        errors.append("interpretation.json analysis_ready is not boolean")
    if not isinstance(payload.get("validation"), dict):
        errors.append("interpretation.json validation is not an object")
    if not isinstance(payload.get("summary"), dict):
        errors.append("interpretation.json summary is not an object")
    if not isinstance(payload.get("interpretation"), dict):
        errors.append("interpretation.json interpretation is not an object")
    if not isinstance(payload.get("source"), str) or not payload.get("source"):
        errors.append("interpretation.json source is not a non-empty string")

    report_summary = report_data.get("summary")
    report_interpretation = report_data.get("interpretation")
    if isinstance(report_summary, dict):
        if payload.get("validation") != report_summary.get("validation"):
            errors.append("interpretation.json validation does not match report_data.json")
        if payload.get("analysis_ready") != report_summary.get("analysis_ready"):
            errors.append("interpretation.json analysis_ready does not match report_data.json")
        sidecar_summary = payload.get("summary")
        if isinstance(sidecar_summary, dict):
            for key in ("targets", "controls"):
                if sidecar_summary.get(key) != report_summary.get(key):
                    errors.append(f"interpretation.json summary {key} does not match report_data.json")
    if isinstance(report_interpretation, dict):
        if payload.get("interpretation") != report_interpretation:
            errors.append("interpretation.json interpretation does not match report_data.json")
        expected_available = report_interpretation.get("status") == "available"
        if payload.get("available") != expected_available:
            errors.append("interpretation.json available does not match report_data.json")
        expected_status = "ok" if expected_available else "not_provided"
        if payload.get("status") != expected_status:
            errors.append("interpretation.json status does not match report_data.json")


def check_html_scripts(path: Path, label: str, errors: list[str], warnings: list[str]) -> None:
    if not require_file(path, errors):
        return
    node = shutil.which("node")
    if not node:
        warnings.append(f"Node.js unavailable; skipped {label} JavaScript syntax check")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>", text, flags=re.IGNORECASE | re.DOTALL
    )
    result = subprocess.run(
        [node, "--check"], input="\n".join(blocks), capture_output=True, text=True, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        errors.append(f"{label} inline JavaScript failed syntax check: " + " ".join(detail[:3]))


def check_columnar_contract(
    columnar: dict,
    out_dir: Path,
    expected_rows: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate columnar artifacts and their cross-backend parity contract."""
    if columnar.get("rows") != expected_rows:
        errors.append(
            f"columnar_status.json rows {columnar.get('rows')!r} != analysis rows {expected_rows}"
        )
    contract = columnar.get("contract")
    fields = columnar.get("columns")
    kinds = columnar.get("field_kinds")
    expected_schema = columnar.get("schema_sha256")
    expected_digest = columnar.get("row_digest")
    parity = columnar.get("parity")
    parity_enabled = contract == COLUMNAR_CONTRACT
    if contract is None:
        warnings.append(
            "columnar_status.json has no parity contract; cross-backend row integrity was not checked"
        )
    elif contract != COLUMNAR_CONTRACT:
        errors.append(f"unsupported columnar contract: {contract!r}")
    elif (
        not isinstance(fields, list)
        or not fields
        or not all(isinstance(item, str) and item for item in fields)
        or not isinstance(kinds, dict)
        or not isinstance(expected_schema, str)
        or not isinstance(expected_digest, str)
        or not isinstance(parity, dict)
    ):
        errors.append("columnar parity contract is missing its schema, digest, or parity fields")
        parity_enabled = False
    if parity_enabled:
        if parity.get("status") not in {"pass", "partial"}:
            errors.append(f"columnar parity status is not passing: {parity.get('status')!r}")
        if parity.get("expected_row_digest") != expected_digest:
            errors.append("columnar parity expected digest does not match row_digest")

    for name in ("csv", "jsonl", "parquet", "duckdb"):
        info = columnar.get(name, {})
        if not isinstance(info, dict):
            errors.append(f"columnar_status.json has invalid {name} entry")
            continue
        status = info.get("status")
        if name in {"csv", "jsonl"} and status is None:
            status = "available" if (out_dir / ("analysis_results.csv" if name == "csv" else "analysis_results.jsonl")).is_file() else "not_installed"
        if status not in {"available", "not_installed", "error"}:
            errors.append(f"columnar_status.json has invalid {name} status: {status!r}")
            continue
        raw_path = info.get("path")
        default_name = {
            "csv": "analysis_results.csv",
            "jsonl": "analysis_results.jsonl",
            "parquet": "analysis_results.parquet",
            "duckdb": "analysis.duckdb",
        }[name]
        path = Path(raw_path) if raw_path else out_dir / default_name
        if not path.is_absolute():
            path = out_dir / path
        if status != "available":
            if path.exists():
                errors.append(f"columnar {name} artifact exists while status is {status}: {path}")
            continue
        if not require_file(path, errors):
            continue
        parity_auditable = parity_enabled and (
            name in {"csv", "jsonl"}
            or (name == "parquet" and _module_available("pyarrow"))
            or (name == "duckdb" and _module_available("duckdb"))
        )
        if parity_auditable:
            try:
                audited = audit_backend(path, name, fields, kinds, expected_schema)
            except (OSError, ValueError, TypeError, json.JSONDecodeError, ImportError, RuntimeError) as exc:
                errors.append(f"columnar {name} parity audit failed: {exc}")
                continue
            if audited.get("rows") != expected_rows:
                errors.append(f"columnar {name} parity row count does not match analysis rows")
            if audited.get("schema_sha256") != expected_schema:
                errors.append(f"columnar {name} schema digest does not match source")
            if audited.get("row_digest") != expected_digest:
                errors.append(f"columnar {name} row digest does not match source")
            if audited.get("schema_issues"):
                errors.append(f"columnar {name} schema issues: {audited['schema_issues']}")
            for key in ("rows", "schema_sha256", "row_digest"):
                if info.get(key) is not None and info.get(key) != audited.get(key):
                    errors.append(f"columnar {name} recorded {key} does not match artifact")
        elif parity_enabled and name in {"parquet", "duckdb"}:
            warnings.append(f"{name.title()} parity audit skipped because its reader is unavailable")
        if name not in {"parquet", "duckdb"}:
            continue
        if name == "parquet":
            try:
                from pyarrow import parquet
            except ImportError:
                warnings.append("Parquet is advertised as available but pyarrow is not installed; skipped row-count check")
                continue
            try:
                actual_rows = parquet.ParquetFile(path).metadata.num_rows
            except Exception as exc:  # noqa: BLE001 - optional engine owns exception types
                errors.append(f"Parquet artifact could not be inspected: {exc}")
                continue
        else:
            try:
                import duckdb
            except ImportError:
                warnings.append("DuckDB is advertised as available but duckdb is not installed; skipped row-count check")
                continue
            connection = None
            try:
                connection = duckdb.connect(str(path), read_only=True)
                actual_rows = connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
            except Exception as exc:  # noqa: BLE001 - optional engine owns exception types
                errors.append(f"DuckDB artifact could not be inspected: {exc}")
                continue
            finally:
                if connection is not None:
                    connection.close()
        if actual_rows != expected_rows:
            errors.append(f"columnar {name} row count {actual_rows} != analysis rows {expected_rows}")


def _resolve_manifest_artifact_path(artifact: dict, out_dir: Path) -> Path:
    """Resolve an artifact using the relative contract when an old absolute path moved."""
    raw = Path(str(artifact.get("path", "")))
    legacy = raw if raw.is_absolute() else out_dir / raw
    relative = artifact.get("relative_path")
    if not isinstance(relative, str) or not relative.strip():
        return legacy
    candidate = (out_dir / relative).resolve()
    output_root = out_dir.resolve()
    try:
        candidate.relative_to(output_root)
    except ValueError:
        return legacy
    if not legacy.exists() and candidate.exists():
        return candidate
    return legacy


def _resolve_manifest_source_path(
    source: dict,
    manifest: dict,
    data_root: Path,
    out_dir: Path,
    project_root: Path | None = None,
) -> Path | None:
    """Resolve a source through the shared relocation-safe runtime contract."""
    active_project_root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None
        else Path(out_dir).expanduser().resolve().parent
    )
    return resolve_manifest_source_path(
        source,
        manifest,
        project_root=active_project_root,
        data_root=data_root,
        out_dir=out_dir,
    )


def check_manifest_path_contract(manifest: dict, out_dir: Path, errors: list[str]) -> None:
    """Validate optional portable path metadata while accepting legacy manifests."""
    contract = manifest.get("path_contract")
    if contract is None:
        return
    if not isinstance(contract, dict) or contract.get("version") != 1:
        errors.append("manifest path_contract must advertise version 1")
        return
    if contract.get("relative_path_field") != "relative_path":
        errors.append("manifest path_contract has an unsupported relative_path field")
    if contract.get("artifact_base") != "output_dir":
        errors.append("manifest path_contract must resolve artifact paths from output_dir")
    if contract.get("source_base") != "project_root":
        errors.append("manifest path_contract must resolve source paths from project_root")
    output_root = out_dir.resolve()
    for label, rows, expected_base in (
        ("artifact", manifest.get("artifacts", []), "output_dir"),
        ("source", manifest.get("sources", []), "project_root"),
    ):
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or "relative_path" not in row:
                continue
            relative = row.get("relative_path")
            if not isinstance(relative, str) or not relative.strip():
                errors.append(f"manifest {label} has an invalid relative_path")
                continue
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                errors.append(f"manifest {label} relative_path escapes its declared base: {relative}")
            if row.get("path_base") != expected_base:
                errors.append(f"manifest {label} has an invalid path_base")
            if label == "artifact":
                candidate = (out_dir / relative).resolve()
                try:
                    candidate.relative_to(output_root)
                except ValueError:
                    errors.append(f"manifest artifact relative_path is outside output directory: {relative}")


def check_manifest_contract(manifest: dict, out_dir: Path, errors: list[str]) -> None:
    """Require a complete, in-directory manifest inventory with valid hashes."""
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("manifest artifacts is not a list")
        return
    counts = manifest.get("counts", {})
    output_root = out_dir.resolve()
    listed: set[Path] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not artifact.get("path"):
            errors.append("manifest contains an artifact without a path")
            continue
        path = _resolve_manifest_artifact_path(artifact, out_dir)
        resolved = path.resolve()
        try:
            resolved.relative_to(output_root)
        except ValueError:
            errors.append(f"manifest artifact is outside output directory: {path}")
            continue
        if resolved in listed:
            errors.append(f"manifest lists an artifact more than once: {path}")
        listed.add(resolved)
        if path.is_symlink():
            errors.append(f"manifest artifact is a symlink: {path}")
            continue
        if path.name == "verification.json" or path.name in MANIFEST_EXCLUDED_OUTPUTS:
            continue
        if not path.exists():
            errors.append(f"manifest artifact missing: {path}")
            continue
        if artifact.get("sha256") != sha256_file(path):
            errors.append(f"manifest artifact hash mismatch: {path}")
        if artifact.get("bytes") != path.stat().st_size:
            errors.append(f"manifest artifact byte-size mismatch: {path}")
        expected_rows = counts.get(path.name)
        if expected_rows is not None and artifact.get("rows") != expected_rows:
            errors.append(f"manifest artifact row count mismatch: {path}")

    actual: set[Path] = set()
    if out_dir.is_dir():
        for path in out_dir.rglob("*"):
            if path.is_symlink():
                errors.append(f"output contains a symlink: {path}")
                continue
            if not path.is_file():
                continue
            if path.name in {"manifest.json", "verification.json"}:
                continue
            if path.name in MANIFEST_EXCLUDED_OUTPUTS:
                continue
            actual.add(path.resolve())
    for path in sorted(actual - listed):
        errors.append(f"manifest does not list output artifact: {path}")


def check_manifest_runtime_contract(
    manifest: dict,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate package/runtime provenance without rejecting legacy manifests."""
    package_version = manifest.get("package_version")
    if package_version is None:
        warnings.append("manifest has no package_version; treating it as a legacy record")
    elif not isinstance(package_version, str) or not package_version.strip():
        errors.append("manifest package_version must be a non-empty string")

    runtime = manifest.get("runtime")
    if runtime is None:
        warnings.append("manifest has no runtime provenance; treating it as a legacy record")
        return
    if not isinstance(runtime, dict):
        errors.append("manifest runtime provenance must be an object")
        return
    for key in ("python", "implementation", "platform", "machine", "cache_tag"):
        if not isinstance(runtime.get(key), str) or not runtime[key].strip():
            errors.append(f"manifest runtime is missing a non-empty {key} value")
    distributions = runtime.get("distributions")
    if not isinstance(distributions, dict):
        errors.append("manifest runtime distributions must be an object")
        return
    for name, version in distributions.items():
        if not isinstance(name, str) or not name.strip():
            errors.append("manifest runtime contains a distribution with an invalid name")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            errors.append(f"manifest runtime distribution {name!r} has an invalid version")


def _manifest_timestamp(value: object, label: str, errors: list[str]) -> datetime | None:
    """Parse a manifest timestamp and require an explicit timezone."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label} is not a valid ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label} must include a timezone")
        return None
    return parsed.astimezone(timezone.utc)


def _manifest_source_identity(source: dict) -> tuple[str, str, str] | None:
    """Return the portable identity used to align source and freshness rows."""
    relative = source.get("relative_path")
    path_base = source.get("path_base")
    if isinstance(relative, str) and relative.strip():
        return ("relative", str(path_base or ""), relative)
    raw_path = source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    return ("path", "", str(Path(raw_path).expanduser().resolve()))


def check_manifest_freshness_contract(
    manifest: dict,
    data_root: Path,
    out_dir: Path,
    errors: list[str],
    warnings: list[str],
    *,
    project_root: Path | None = None,
) -> None:
    """Validate the versioned source-age record and its alignment with inputs."""
    record = manifest.get("source_freshness")
    schema_version = manifest.get("schema_version", 0)
    if record is None:
        if isinstance(schema_version, (int, float)) and schema_version >= 3:
            errors.append("manifest schema version 3 is missing source_freshness")
        else:
            warnings.append("manifest has no source_freshness; treating it as a legacy record")
        return
    if not isinstance(record, dict):
        errors.append("manifest source_freshness must be an object")
        return
    if record.get("contract") != SOURCE_FRESHNESS_CONTRACT:
        errors.append(
            "manifest source_freshness has an unsupported contract: "
            f"{record.get('contract')!r}"
        )
    observed = _manifest_timestamp(record.get("observed_at"), "manifest source_freshness observed_at", errors)
    rows = record.get("sources")
    if not isinstance(rows, list):
        errors.append("manifest source_freshness sources must be a list")
        return

    freshness_by_identity: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for index, row in enumerate(rows, 1):
        label = f"manifest source_freshness source {index}"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        source_kind = row.get("source_kind")
        if not isinstance(source_kind, str) or not source_kind.strip():
            errors.append(f"{label} is missing a non-empty source_kind")
        path_base = row.get("path_base")
        if path_base not in {"project_root", "external"}:
            errors.append(f"{label} has an invalid path_base")
        if not isinstance(row.get("path"), str) or not str(row.get("path")).strip():
            errors.append(f"{label} is missing a path")
        relative = row.get("relative_path")
        if relative is not None:
            if not isinstance(relative, str) or not relative.strip():
                errors.append(f"{label} has an invalid relative_path")
            elif Path(relative).is_absolute() or ".." in Path(relative).parts:
                errors.append(f"{label} relative_path escapes its declared base: {relative}")
        modified = _manifest_timestamp(row.get("modified_at"), f"{label} modified_at", errors)
        age = row.get("age_seconds")
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age):
            errors.append(f"{label} age_seconds must be a finite number")
        elif age < 0:
            errors.append(f"{label} age_seconds must be non-negative")
        if observed is not None and modified is not None and isinstance(age, (int, float)) and not isinstance(age, bool) and math.isfinite(age):
            expected_age = max(0.0, (observed - modified).total_seconds())
            if abs(float(age) - expected_age) > 1.0:
                errors.append(f"{label} age_seconds does not match observed_at and modified_at")
        identity = _manifest_source_identity(row)
        if identity is not None:
            freshness_by_identity[identity].append(row)

    manifest_sources = manifest.get("sources", [])
    if not isinstance(manifest_sources, list):
        errors.append("manifest sources is not a list")
        return
    expected_sources: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for index, source in enumerate(manifest_sources, 1):
        if not isinstance(source, dict):
            continue
        source_path = _resolve_manifest_source_path(
            source,
            manifest,
            data_root,
            out_dir,
            project_root=project_root,
        )
        modified_at = source.get("modified_at")
        if source_path is not None and source_path.exists() and modified_at is None:
            errors.append(f"manifest source {index} is missing modified_at: {source_path}")
        if modified_at is None:
            continue
        if _manifest_timestamp(modified_at, f"manifest source {index} modified_at", errors) is None:
            continue
        identity = _manifest_source_identity(source)
        if identity is None:
            errors.append(f"manifest source {index} has no portable identity")
            continue
        expected_sources[identity].append(source)

    for identity, sources in expected_sources.items():
        freshness_rows = freshness_by_identity.get(identity, [])
        if len(freshness_rows) != len(sources):
            errors.append(
                "manifest source_freshness does not align with source rows: "
                f"{identity[-1]}"
            )
            continue
        for source, freshness in zip(sources, freshness_rows, strict=False):
            if source.get("modified_at") != freshness.get("modified_at"):
                errors.append(
                    "manifest source modified_at does not match source_freshness: "
                    f"{source.get('path')}"
                )
            source_path = _resolve_manifest_source_path(
                source,
                manifest,
                data_root,
                out_dir,
                project_root=project_root,
            )
            if source_path is not None and source_path.exists():
                actual_modified = path_modified_at(source_path)
                if actual_modified is not None and actual_modified != freshness.get("modified_at"):
                    errors.append(
                        "manifest source_freshness modified_at is stale: "
                        f"{source_path}"
                    )

    expected_identities = set(expected_sources)
    for identity in freshness_by_identity:
        if identity not in expected_identities:
            errors.append(
                "manifest source_freshness contains a source not present in sources: "
                f"{identity[-1]}"
            )


def _command_option(command: object, option: str) -> tuple[bool, str | None]:
    """Return whether a command contains an option and its following value."""
    if not isinstance(command, list):
        return False, None
    for index, token in enumerate(command):
        if str(token) != option:
            continue
        if index + 1 >= len(command):
            return True, None
        return True, str(command[index + 1])
    return False, None


def _manifest_parameter_root(manifest: dict, out_dir: Path) -> Path:
    """Resolve the current project root, falling back after a project move."""
    raw_root = manifest.get("project_root")
    root = (
        Path(raw_root).expanduser()
        if isinstance(raw_root, str) and raw_root.strip()
        else out_dir.parent
    )
    return root if root.is_dir() else out_dir.parent


def _manifest_parameter_path(manifest: dict, value: object, out_dir: Path) -> Path:
    """Resolve a manifest path parameter using its recorded project root."""
    root = _manifest_parameter_root(manifest, out_dir)
    candidate = Path(str(value)).expanduser()
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def check_manifest_cache_contract(
    manifest: dict,
    stage_cache: dict[str, Any] | None,
    out_dir: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    """Prove that manifest parameters agree with cached stage commands.

    Diagnostic-only pipeline runs intentionally preserve the previous build
    parameters while recording their own ``last_invocation``.  This contract
    compares only the preserved build context with the commands that produced
    cache fingerprints, so harmless diagnostic overrides do not create false
    failures while a changed analytical parameter cannot pass unnoticed.
    """
    result: dict[str, Any] = {
        "contract": MANIFEST_CACHE_CONTRACT,
        "status": "not_provided",
        "checked_stages": [],
        "unavailable_stages": [],
        "check_count": 0,
        "mismatches": [],
    }
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        warnings.append("manifest/cache alignment is unavailable: manifest parameters are missing")
        return result
    stages = stage_cache.get("stages") if isinstance(stage_cache, dict) else None
    if not isinstance(stages, dict):
        warnings.append("manifest/cache alignment is unavailable: stage cache records are missing")
        return result

    raw_plan_parameter = parameters.get("analysis_plan")
    if isinstance(raw_plan_parameter, str) and raw_plan_parameter.strip():
        plan_path = _manifest_parameter_path(manifest, raw_plan_parameter, out_dir)
    else:
        plan_path = default_analysis_plan_path(
            _manifest_parameter_root(manifest, out_dir),
            package_root=Path(__file__).resolve().parent.parent,
        ).resolve()
    specs: dict[str, tuple[tuple[str, str, str], ...]] = {
        "fetch": (
            ("flag", "refresh", "--refresh"),
            ("flag", "no_network", "--no-download"),
        ),
        "fetch-niah": (
            ("flag", "refresh", "--refresh"),
            ("flag", "no_network", "--no-network"),
        ),
        "analyze": (("path", "analysis_plan", "--plan"),),
        "spatial-covariates": (
            ("path", "boundaries", "--boundaries"),
            ("path", "settlements", "--settlements"),
        ),
        "osm-history": (("path", "osm_history", "--history"),),
        "building-parts": (("path", "lidar", "--lidar"),),
        "historical": (("path", "historical_references", "--references"),),
        "review": (("path", "review_labels", "--labels"),),
        "point-pattern": (
            ("int", "seed", "--seed"),
            ("int", "mc", "--mc"),
        ),
        "spatial-stats": (
            ("int", "seed", "--seed"),
            ("int", "mc", "--mc"),
        ),
        "spatial-bootstrap": (
            ("int", "seed", "--seed"),
            ("int", "bootstrap_iterations", "--iterations"),
        ),
        "road-proximity": (("int", "seed", "--seed"),),
        "road-routing": (
            ("int", "routing_max_pairs", "--max-pairs"),
            ("float", "routing_speed_kmh", "--speed-kmh"),
            ("float", "routing_weight_t", "--weight-t"),
            ("text", "routing_departure", "--departure"),
            ("text", "routing_vehicle_class", "--vehicle-class"),
            ("path", "road_graph", "--road-graph"),
            ("flag", "road_from_pbf", "--from-pbf"),
            ("int", "routing_max_ways", "--max-ways"),
            ("flag", "routing_include_restricted", "--include-restricted"),
            ("flag", "routing_include_ferries", "--include-ferries"),
        ),
        "holdout": (
            ("int", "seed", "--seed"),
            ("path", "analysis_plan", "--plan"),
            ("float", "holdout_fraction", "--fraction"),
        ),
    }

    error_start = len(errors)

    def mismatch(stage: str, parameter: str, expected: object, actual: object) -> None:
        expected_value = str(expected) if isinstance(expected, Path) else expected
        item = {
            "stage": stage,
            "parameter": parameter,
            "expected": expected_value,
            "actual": actual,
        }
        result["mismatches"].append(item)
        errors.append(
            "manifest/cache alignment mismatch for "
            f"{stage}.{parameter}: expected {expected_value!r}, cached command has {actual!r}"
        )

    def compare(stage: str, kind: str, parameter: str, option: str, command: list[Any]) -> None:
        if parameter not in parameters:
            return
        if parameter == "routing_max_ways" and not parameters.get("road_from_pbf"):
            return
        expected = plan_path if parameter == "analysis_plan" else parameters.get(parameter)
        present, raw = _command_option(command, option)
        result["check_count"] += 1
        if kind == "flag":
            if not isinstance(expected, bool):
                errors.append(f"manifest parameter {parameter} must be boolean")
                return
            if present != expected:
                mismatch(stage, parameter, expected, present)
            return
        if expected is None:
            if present:
                mismatch(stage, parameter, None, raw)
            return
        if not present or raw is None:
            mismatch(stage, parameter, expected, None)
            return
        try:
            if kind == "int":
                actual: object = int(raw)
                equal = actual == expected and not isinstance(expected, bool)
            elif kind == "float":
                actual = float(raw)
                equal = (
                    isinstance(expected, (int, float))
                    and not isinstance(expected, bool)
                    and math.isfinite(float(expected))
                    and math.isfinite(float(actual))
                    and abs(float(actual) - float(expected)) <= 1e-9
                )
            elif kind == "path":
                actual = str(Path(raw).expanduser().resolve())
                equal = Path(actual) == (
                    expected
                    if isinstance(expected, Path)
                    else _manifest_parameter_path(manifest, expected, out_dir)
                )
            else:
                actual = raw
                equal = actual == str(expected)
        except (TypeError, ValueError, OSError):
            actual = raw
            equal = False
        if not equal:
            mismatch(stage, parameter, expected, actual)

    for stage, stage_specs in specs.items():
        record = stages.get(stage)
        if not isinstance(record, dict):
            result["unavailable_stages"].append(stage)
            continue
        fingerprint_inputs = record.get("fingerprint_inputs")
        command = fingerprint_inputs.get("command") if isinstance(fingerprint_inputs, dict) else None
        if not isinstance(command, list):
            errors.append(f"manifest/cache alignment cannot inspect command for stage {stage}")
            continue
        result["checked_stages"].append(stage)
        for kind, parameter, option in stage_specs:
            compare(stage, kind, parameter, option, command)

    for stage in result["unavailable_stages"]:
        warnings.append(f"manifest/cache alignment could not inspect missing stage record: {stage}")
    if len(errors) > error_start:
        result["status"] = "fail"
    elif result["check_count"] and result["unavailable_stages"]:
        result["status"] = "partial"
    elif result["check_count"]:
        result["status"] = "pass"
    return result


def _close_enough(actual: float, expected: float, tolerance: float = 1e-3) -> bool:
    return math.isfinite(actual) and abs(actual - expected) <= tolerance


def _holdout_plan_path(used_plan: dict, out_dir: Path) -> Path | None:
    raw_path = used_plan.get("plan_path")
    candidates: list[Path] = []
    if raw_path:
        candidate = Path(str(raw_path))
        candidates.append(candidate if candidate.is_absolute() else out_dir.parent / candidate)
        candidates.append(candidate)
    candidates.append(out_dir.parent / "analysis_plan.json")
    candidates.append(default_analysis_plan_path(out_dir.parent))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def check_holdout_contract(
    analysis: list[dict[str, str]],
    assignments: list[dict[str, str]],
    results: list[dict[str, str]],
    used_plan: dict,
    out_dir: Path,
    errors: list[str],
) -> None:
    """Check that holdout artifacts are an exact, reproducible projection.

    Presence checks alone cannot detect a changed seed, fraction, plan, row
    assignment, or target/control count.  This contract recomputes the hash
    split and the expected aggregate counts from the analysis CSV.
    """
    analysis_by_id = {row.get("osm_id", ""): row for row in analysis}
    analysis_ids = set(analysis_by_id)
    assignment_ids = [row.get("osm_id", "") for row in assignments]
    if len(assignment_ids) != len(set(assignment_ids)):
        errors.append("holdout_assignments.csv contains duplicate osm_id values")
    if set(assignment_ids) != analysis_ids:
        errors.append("holdout_assignments.csv ids do not exactly match analysis_results.csv")

    seed: int | None = None
    fraction: float | None = None
    if assignments:
        for index, assignment in enumerate(assignments, 2):
            identifier = assignment.get("osm_id", "")
            if identifier not in analysis_by_id:
                errors.append(f"holdout_assignments.csv row {index} references an unknown analysis id")
                continue
            try:
                row_seed = int(assignment.get("seed", ""))
                row_fraction = float(assignment.get("fraction", ""))
            except ValueError:
                errors.append(f"holdout_assignments.csv row {index} has an invalid seed or fraction")
                continue
            if not 0.0 < row_fraction < 1.0 or not math.isfinite(row_fraction):
                errors.append(f"holdout_assignments.csv row {index} has an invalid fraction")
                continue
            if seed is None:
                seed = row_seed
                fraction = row_fraction
            elif row_seed != seed or not math.isclose(row_fraction, fraction, abs_tol=1e-12):
                errors.append("holdout_assignments.csv does not use one seed/fraction pair")
            source = analysis_by_id[identifier]
            expected_split = "holdout" if holdout(source, row_seed, row_fraction) else "discovery"
            if assignment.get("split") != expected_split:
                errors.append(
                    f"holdout_assignments.csv row {index} has split {assignment.get('split')!r}; "
                    f"expected {expected_split!r}"
                )
            if assignment.get("is_control") != source.get("is_control"):
                errors.append(f"holdout_assignments.csv row {index} disagrees on is_control")
            if assignment.get("group") != source.get("group"):
                errors.append(f"holdout_assignments.csv row {index} disagrees on group")
            if assignment.get("rule") != "sha256(seed:osm_id) < holdout_fraction":
                errors.append(f"holdout_assignments.csv row {index} has an unexpected split rule")

    plan_path = _holdout_plan_path(used_plan, out_dir)
    plan: dict = used_plan if isinstance(used_plan, dict) else {}
    plan_hash = None
    if plan_path is None:
        errors.append("holdout analysis plan could not be resolved")
    else:
        plan_hash = sha256_file(plan_path)
        if used_plan.get("plan_sha256") != plan_hash:
            errors.append("analysis_plan_used.json plan_sha256 does not match its plan file")
        try:
            loaded_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_plan, dict):
                errors.append("analysis plan is not a JSON object")
            else:
                plan = loaded_plan
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"analysis plan is not valid JSON: {exc}")
    if seed is not None and used_plan.get("seed") is not None:
        try:
            if int(used_plan["seed"]) != seed:
                errors.append("analysis_plan_used.json seed does not match holdout_assignments.csv")
        except (TypeError, ValueError):
            errors.append("analysis_plan_used.json seed is invalid")
    if fraction is not None and used_plan.get("holdout_fraction") is not None:
        try:
            if not math.isclose(float(used_plan["holdout_fraction"]), fraction, abs_tol=1e-12):
                errors.append("analysis_plan_used.json holdout_fraction does not match assignments")
        except (TypeError, ValueError):
            errors.append("analysis_plan_used.json holdout_fraction is invalid")

    if not assignments:
        return
    expected_holdout = {
        identifier
        for identifier, source in analysis_by_id.items()
        if seed is not None and fraction is not None and holdout(source, seed, fraction)
    }
    holdout_sources = [analysis_by_id[identifier] for identifier in expected_holdout]
    controls = [row for row in holdout_sources if row.get("is_control") == "1"]
    target_groups = {
        row.get("group", "other")
        for row in holdout_sources
        if row.get("is_control") == "0"
    }
    raw_signals = plan.get("primary_signals", ["golden_angle"])
    signals = raw_signals if isinstance(raw_signals, list) else []
    if not signals:
        errors.append("holdout analysis plan has no primary_signals list")
    expected_keys = {(str(signal), group) for group in target_groups for signal in signals}
    actual_keys: set[tuple[str, str]] = set()
    if results:
        for index, row in enumerate(results, 2):
            key = (row.get("signal", ""), row.get("target_group", ""))
            if key in actual_keys:
                errors.append(f"holdout_results.csv row {index} duplicates signal/group result")
            actual_keys.add(key)
            if row.get("split") != "holdout":
                errors.append(f"holdout_results.csv row {index} is not marked holdout")
            if row.get("pre_registered") != "1":
                errors.append(f"holdout_results.csv row {index} is not marked pre_registered")
            if plan_hash and row.get("plan_sha256") != plan_hash:
                errors.append(f"holdout_results.csv row {index} has a plan hash mismatch")
            try:
                target_n = int(row.get("target_n", ""))
                control_n = int(row.get("control_n", ""))
                target_successes = int(row.get("target_successes", "0") or 0)
                control_successes = int(row.get("control_successes", "0") or 0)
            except ValueError:
                errors.append(f"holdout_results.csv row {index} has invalid count fields")
                continue
            if not 0 <= target_successes <= target_n or not 0 <= control_successes <= control_n:
                errors.append(f"holdout_results.csv row {index} has successes outside its sample size")
            if row.get("status") == "no_target_rows" and not target_groups:
                if target_n != 0 or control_n != len(controls):
                    errors.append(f"holdout_results.csv row {index} has invalid no-target counts")
                continue
            for field in ("target_rate", "control_rate", "risk_difference_pp", "z", "p_value", "alpha"):
                try:
                    value = float(row.get(field, ""))
                except ValueError:
                    errors.append(f"holdout_results.csv row {index} has invalid {field}")
                    continue
                if not math.isfinite(value):
                    errors.append(f"holdout_results.csv row {index} has non-finite {field}")
                elif field in {"target_rate", "control_rate"} and not 0.0 <= value <= 100.0:
                    errors.append(f"holdout_results.csv row {index} has invalid {field}: {value}")
                elif field == "p_value" and not 0.0 < value <= 1.0:
                    errors.append(f"holdout_results.csv row {index} has invalid p_value: {value}")
                elif field == "alpha" and not 0.0 < value <= 1.0:
                    errors.append(f"holdout_results.csv row {index} has invalid alpha: {value}")
            if (
                row.get("status") not in {"available", "insufficient_holdout_rows"}
                and (row.get("status") != "no_target_rows" or target_groups)
            ):
                errors.append(f"holdout_results.csv row {index} has an invalid status")
            if key in expected_keys:
                targets = [
                    source for source in holdout_sources
                    if source.get("is_control") == "0" and source.get("group", "other") == key[1]
                ]
                try:
                    target_expected = sum(signal_value(source, key[0]) for source in targets)
                    control_expected = sum(signal_value(source, key[0]) for source in controls)
                except ValueError as exc:
                    errors.append(f"holdout_results.csv row {index} has an unknown signal: {exc}")
                    continue
                expected_status = "available" if targets and controls else "insufficient_holdout_rows"
                expected_target_rate = 100.0 * target_expected / len(targets) if targets else 0.0
                expected_control_rate = 100.0 * control_expected / len(controls) if controls else 0.0
                for field, actual, expected in (
                    ("target_n", target_n, len(targets)),
                    ("control_n", control_n, len(controls)),
                    ("target_successes", target_successes, target_expected),
                    ("control_successes", control_successes, control_expected),
                ):
                    if actual != expected:
                        errors.append(f"holdout_results.csv row {index} {field} {actual} != expected {expected}")
                try:
                    target_rate = float(row.get("target_rate", ""))
                    control_rate = float(row.get("control_rate", ""))
                    risk_difference = float(row.get("risk_difference_pp", ""))
                except ValueError:
                    continue
                if not _close_enough(target_rate, expected_target_rate):
                    errors.append(f"holdout_results.csv row {index} has an incorrect target_rate")
                if not _close_enough(control_rate, expected_control_rate):
                    errors.append(f"holdout_results.csv row {index} has an incorrect control_rate")
                if not _close_enough(risk_difference, expected_target_rate - expected_control_rate):
                    errors.append(f"holdout_results.csv row {index} has an incorrect risk_difference_pp")
                if row.get("status") != expected_status:
                    errors.append(f"holdout_results.csv row {index} status does not match sample availability")
                try:
                    expected_alpha = float(plan.get("alpha", 0.05))
                    if not _close_enough(float(row.get("alpha", "")), expected_alpha, 1e-9):
                        errors.append(f"holdout_results.csv row {index} alpha does not match the plan")
                except (TypeError, ValueError):
                    errors.append(f"holdout_results.csv row {index} has an invalid plan alpha")
    if target_groups and actual_keys != expected_keys:
        errors.append("holdout_results.csv signal/group keys do not match the holdout analysis plan")
    if not target_groups and not any(row.get("status") == "no_target_rows" for row in results):
        errors.append("holdout_results.csv is missing its no-target diagnostic")


def verify_outputs(
    data_root: Path,
    out_dir: Path,
    manifest_path: Path | None = None,
    *,
    project_root: Path | None = None,
) -> dict:
    data_root = reject_symlink_root(data_root, label="data directory")
    data_root = reject_symlink_tree(data_root, label="data directory")
    out_dir = reject_symlink_root(out_dir)
    out_dir = reject_symlink_tree(out_dir, label="output directory")
    if manifest_path is not None:
        manifest_path = reject_symlink_path(manifest_path, label="manifest file")
    errors: list[str] = []
    warnings: list[str] = []
    stage_cache_payload: dict[str, Any] | None = None
    active_project_root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None
        else Path(out_dir).expanduser().resolve().parent
    )

    combined = data_root / "combined.json"
    if require_file(combined, errors):
        try:
            payload = json.loads(combined.read_text(encoding="utf-8"))
            if not isinstance(payload.get("elements"), list) or not payload["elements"]:
                errors.append("combined.json has no non-empty elements list")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"combined.json is not valid JSON: {exc}")

    analysis_path = out_dir / "analysis_results.csv"
    analysis = read_csv(analysis_path) if require_file(analysis_path, errors) else []
    check_columns(analysis, ANALYSIS_REQUIRED, "analysis_results.csv", errors)
    analysis_ids = [row.get("osm_id", "") for row in analysis]
    analysis_id_set = set(analysis_ids)
    if len(analysis_ids) != len(set(analysis_ids)):
        errors.append("analysis_results.csv contains duplicate osm_id values")
    targets = []
    controls = []
    for index, row in enumerate(analysis, 2):
        if row.get("is_control") == "1":
            controls.append(row)
        elif row.get("is_control") == "0":
            targets.append(row)
        else:
            errors.append(f"analysis_results.csv row {index} has invalid is_control")
        try:
            area = float(row.get("area_m2", ""))
            convexity = float(row.get("convexity", ""))
        except ValueError:
            errors.append(f"analysis_results.csv row {index} has invalid numeric geometry")
            continue
        if area <= 0:
            errors.append(f"analysis_results.csv row {index} has non-positive area")
        if not 0.0 <= convexity <= 1.000001:
            errors.append(f"analysis_results.csv row {index} has convexity outside [0,1]")

    geojson_path = out_dir / "ireland_buildings.geojson"
    if require_file(geojson_path, errors):
        try:
            geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
            features = geojson.get("features", [])
            feature_ids = [feature.get("properties", {}).get("osm_id") for feature in features]
            if geojson.get("type") != "FeatureCollection":
                errors.append("ireland_buildings.geojson is not a FeatureCollection")
            if len(features) != len(targets):
                errors.append(
                    f"GeoJSON feature count {len(features)} != target CSV count {len(targets)}"
                )
            if len(feature_ids) != len(set(feature_ids)):
                errors.append("ireland_buildings.geojson contains duplicate osm_id values")
            if set(feature_ids) != {row["osm_id"] for row in targets}:
                errors.append("GeoJSON ids do not exactly match non-control analysis ids")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"ireland_buildings.geojson is not valid JSON: {exc}")

    scoring_config = check_scoring_config_contract(out_dir / "scoring_config.json", errors)

    significance = out_dir / "significance.csv"
    if require_file(significance, errors):
        check_statistical_contract(read_csv(significance), "significance.csv", errors)
    negative_path = out_dir / "negative_controls.csv"
    if require_file(negative_path, errors):
        negative = read_csv(negative_path)
        check_statistical_contract(negative, "negative_controls.csv", errors)
        check_columns(negative, {"signal", "group", "test_family"}, "negative_controls.csv", errors)
        if any(row.get("test_family") != "negative_control_angle" for row in negative):
            errors.append("negative_controls.csv contains a row outside its declared test family")

    matched_path = out_dir / "matched_controls.csv"
    if require_file(matched_path, errors):
        matches = read_csv(matched_path)
        check_columns(
            matches,
            {"target_osm_id", "control_osm_id", "distance_m", "area_ratio", "rank"},
            "matched_controls.csv",
            errors,
        )
        ids = analysis_id_set
        for index, row in enumerate(matches, 2):
            if row.get("target_osm_id") not in ids or row.get("control_osm_id") not in ids:
                errors.append(f"matched_controls.csv row {index} references an unknown analysis id")
            try:
                if float(row.get("distance_m", "")) < 0 or float(row.get("area_ratio", "")) < 1:
                    errors.append(f"matched_controls.csv row {index} has invalid match geometry")
            except ValueError:
                errors.append(f"matched_controls.csv row {index} has invalid match numeric field")
    for name in ("matched_control_summary.csv", "building_parts.csv", "lidar_coverage.csv"):
        path = out_dir / name
        if require_file(path, errors):
            rows = read_csv(path)
            if name == "building_parts.csv":
                check_columns(rows, {"osm_id", "part_count", "part_geometry_status"}, name, errors)
            elif name == "lidar_coverage.csv":
                check_columns(rows, {"osm_id", "lidar_available", "source", "quality"}, name, errors)
            else:
                check_columns(rows, {"target_group", "target_n", "matched_pair_n"}, name, errors)
    for name in ("matched_significance.csv", "hierarchical_model.csv"):
        path = out_dir / name
        if require_file(path, errors):
            check_statistical_contract(read_csv(path), name, errors)

    strict_path = out_dir / "matched_controls_strict.csv"
    if require_file(strict_path, errors):
        strict = read_csv(strict_path)
        check_columns(
            strict,
            {"target_osm_id", "control_osm_id", "replacement_allowed", "stratum"},
            "matched_controls_strict.csv",
            errors,
        )
        controls_used = [row.get("control_osm_id") for row in strict]
        if len(controls_used) != len(set(controls_used)):
            errors.append("matched_controls_strict.csv reuses a control despite no-replacement contract")
        if any(row.get("replacement_allowed") != "0" for row in strict):
            errors.append("matched_controls_strict.csv contains replacement_allowed != 0")
    for name in ("matched_strict_summary.csv", "matched_strict_balance.csv"):
        if require_file(out_dir / name, errors):
            check_columns(read_csv(out_dir / name), {"method"}, name, errors)
    if require_file(out_dir / "matched_strict_significance.csv", errors):
        check_statistical_contract(read_csv(out_dir / "matched_strict_significance.csv"), "matched_strict_significance.csv", errors)

    covariates_path = out_dir / "spatial_covariates.csv"
    if require_file(covariates_path, errors):
        covariates = read_csv(covariates_path)
        check_columns(covariates, {"osm_id", "settlement_class", "mapping_density_bin", "boundary_status"}, "spatial_covariates.csv", errors)
        if {row.get("osm_id") for row in covariates} != analysis_id_set:
            errors.append("spatial_covariates.csv ids do not exactly match analysis_results.csv")
    if require_file(out_dir / "spatial_covariates_summary.csv", errors):
        check_columns(read_csv(out_dir / "spatial_covariates_summary.csv"), {"covariate", "status"}, "spatial_covariates_summary.csv", errors)

    quality_path = out_dir / "data_quality.csv"
    if require_file(quality_path, errors):
        check_columns(
            read_csv(quality_path),
            {"scope", "field", "missing_pct", "invalid_n", "quality_status"},
            "data_quality.csv",
            errors,
        )
    quality_summary: dict[str, object] = {}
    quality_summary_path = out_dir / "data_quality_summary.json"
    if require_file(quality_summary_path, errors):
        try:
            quality_summary = json.loads(quality_summary_path.read_text(encoding="utf-8"))
            for field in ("analysis_rows", "valid_geometry_pct", "duplicate_osm_id_n", "quality_status"):
                if field not in quality_summary:
                    errors.append(f"data_quality_summary.json missing key: {field}")
            if not 0.0 <= float(quality_summary.get("valid_geometry_pct", -1)) <= 100.0:
                errors.append("data_quality_summary.json has invalid valid_geometry_pct")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"data_quality_summary.json is not valid JSON: {exc}")

    duplicate_quality_path = out_dir / "data_quality_duplicates.csv"
    if require_file(duplicate_quality_path, errors):
        duplicate_rows = read_csv(duplicate_quality_path)
        duplicate_fields = {
            "lat",
            "lon",
            "row_n",
            "duplicate_n",
            "osm_ids",
            "groups",
            "target_n",
            "control_n",
            "review_status",
        }
        check_header(duplicate_quality_path, duplicate_fields, "data_quality_duplicates.csv", errors)
        seen_duplicate_ids: set[str] = set()
        duplicate_total = 0
        for index, row in enumerate(duplicate_rows, 2):
            try:
                row_n = int(row.get("row_n", ""))
                duplicate_n = int(row.get("duplicate_n", ""))
            except ValueError:
                errors.append(f"data_quality_duplicates.csv row {index} has invalid counts")
                continue
            if row_n < 2 or duplicate_n != row_n - 1:
                errors.append(f"data_quality_duplicates.csv row {index} has inconsistent duplicate counts")
            duplicate_total += duplicate_n
            ids = [value for value in row.get("osm_ids", "").split("|") if value]
            if len(ids) != len(set(ids)) or any(identifier not in analysis_id_set for identifier in ids):
                errors.append(f"data_quality_duplicates.csv row {index} references invalid or repeated ids")
            if seen_duplicate_ids.intersection(ids):
                errors.append(f"data_quality_duplicates.csv row {index} reuses an id across groups")
            seen_duplicate_ids.update(ids)
            if row.get("review_status") != "review":
                errors.append(f"data_quality_duplicates.csv row {index} is not marked review")
        if duplicate_total != int(quality_summary.get("duplicate_centroid_n", duplicate_total)):
            errors.append("data_quality_duplicates.csv total does not match data_quality_summary.json")

    schema_validation_path = out_dir / "schema_validation.json"
    if require_file(schema_validation_path, errors):
        try:
            schema_validation = json.loads(schema_validation_path.read_text(encoding="utf-8"))
            if schema_validation.get("passed") is not True:
                errors.append("schema_validation.json reports a failed schema audit")
            if not schema_validation.get("artifacts"):
                errors.append("schema_validation.json has no artifact results")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"schema_validation.json is not valid JSON: {exc}")

    cache_path = out_dir / "stage_cache.json"
    if require_file(cache_path, errors):
        try:
            stage_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(stage_cache, dict):
                stage_cache_payload = stage_cache
            if (
                stage_cache.get("cache_version") != CACHE_VERSION
                or not isinstance(stage_cache.get("runtime"), dict)
                or not isinstance(stage_cache.get("stages"), dict)
                or stage_cache.get("cache_status") != "valid"
            ):
                errors.append("stage_cache.json has an invalid cache version, status, or stage map")
            for stage, record in stage_cache.get("stages", {}).items():
                if not isinstance(record, dict):
                    errors.append(f"stage_cache.json stage {stage} is not an object")
                    continue
                if not record.get("fingerprint"):
                    errors.append(f"stage_cache.json stage {stage} has no fingerprint")
                fingerprint_inputs = record.get("fingerprint_inputs")
                if not isinstance(fingerprint_inputs, dict):
                    errors.append(f"stage_cache.json stage {stage} has no fingerprint inputs")
                else:
                    if fingerprint_inputs.get("stage") != stage:
                        errors.append(f"stage_cache.json stage {stage} fingerprint names a different stage")
                    if fingerprint_from_payload(fingerprint_inputs) != record.get("fingerprint"):
                        errors.append(f"stage_cache.json stage {stage} fingerprint inputs do not match fingerprint")
                outputs = record.get("outputs")
                if not isinstance(outputs, dict):
                    errors.append(f"stage_cache.json stage {stage} has invalid outputs")
                    continue
                for raw_path, expected_hash in outputs.items():
                    path = Path(raw_path)
                    if not path.exists():
                        errors.append(f"stage_cache.json output missing for {stage}: {path}")
                    elif expected_hash != sha256_path(path):
                        errors.append(f"stage_cache.json output hash mismatch for {stage}: {path}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"stage_cache.json is not valid JSON: {exc}")

    history_path = out_dir / "mapping_history.csv"
    if require_file(history_path, errors):
        history_rows = read_csv(history_path)
        check_columns(history_rows, {"osm_id", "status", "version_count", "mapping_quality_proxy"}, "mapping_history.csv", errors)
        if {row.get("osm_id") for row in history_rows} != analysis_id_set:
            errors.append("mapping_history.csv ids do not exactly match analysis_results.csv")
    if require_file(out_dir / "mapping_history_summary.csv", errors):
        check_columns(read_csv(out_dir / "mapping_history_summary.csv"), {"status", "provided_rows"}, "mapping_history_summary.csv", errors)

    historical_path = out_dir / "historical_validation.csv"
    if require_file(historical_path, errors):
        history = read_csv(historical_path)
        check_columns(history, {"osm_id", "validation_status", "review_priority"}, "historical_validation.csv", errors)
        if any(row.get("osm_id") not in analysis_id_set for row in history):
            errors.append("historical_validation.csv contains an unknown analysis id")
    for name in ("candidate_dossiers.csv", "historical_source_register.csv"):
        path = out_dir / name
        if require_file(path, errors):
            check_columns(read_csv(path), {"source_type"} if name.endswith("register.csv") else {"osm_id", "review_priority"}, name, errors)

    join_path = out_dir / "niah_join.csv"
    if require_file(join_path, errors):
        joins = read_csv(join_path)
        check_columns(
            joins, {"osm_id", "source_region", "match_mode", "dist_m"}, "niah_join.csv", errors
        )
        if any(not row.get("source_region") for row in joins):
            errors.append("niah_join.csv contains a blank source_region")
        if len({row.get("osm_id") for row in joins}) != len(joins):
            errors.append("niah_join.csv contains duplicate osm_id values")
        for index, row in enumerate(joins, 2):
            if row.get("match_mode") not in {"contained", "near"}:
                errors.append(f"niah_join.csv row {index} has invalid match_mode")
            try:
                distance = float(row.get("dist_m", ""))
            except ValueError:
                errors.append(f"niah_join.csv row {index} has invalid dist_m")
                continue
            if distance < 0 or (row.get("match_mode") == "near" and distance > 50.01):
                errors.append(f"niah_join.csv row {index} has invalid match distance")

    niah_sig = out_dir / "niah_significance.csv"
    if require_file(niah_sig, errors):
        check_statistical_contract(read_csv(niah_sig), "niah_significance.csv", errors)
    decade_path = out_dir / "niah_decades.csv"
    if require_file(decade_path, errors):
        check_statistical_contract(read_csv(decade_path), "niah_decades.csv", errors)

    for name in (
        "point_pattern.csv",
        "point_pattern_turns.csv",
        "roads_compare.csv",
        "road_proximity.csv",
        "road_routing.csv",
        "road_routing_pairs.csv",
        "architects.csv",
        "architects_binary.csv",
        "architects_evidence.csv",
    ):
        require_file(out_dir / name, errors)
    for name in ("moran.csv", "county_permutation.csv"):
        path = out_dir / name
        if require_file(path, errors):
            check_statistical_contract(read_csv(path), name, errors)
    if require_file(out_dir / "road_routing.csv", errors):
        check_columns(read_csv(out_dir / "road_routing.csv"), {"status", "method"}, "road_routing.csv", errors)
    if require_file(out_dir / "road_routing_pairs.csv", errors):
        check_columns(read_csv(out_dir / "road_routing_pairs.csv"), {"status", "reachable"}, "road_routing_pairs.csv", errors)
    bootstrap_path = out_dir / "spatial_bootstrap.csv"
    if require_file(bootstrap_path, errors):
        bootstrap = read_csv(bootstrap_path)
        check_columns(
            bootstrap,
            {"signal", "target_group", "ci_low_pp", "ci_high_pp", "prob_positive", "prob_negative", "status"},
            "spatial_bootstrap.csv",
            errors,
        )
        for index, row in enumerate(bootstrap, 2):
            for field in ("prob_positive", "prob_negative"):
                if row.get(field, "") == "":
                    continue
                try:
                    value = float(row[field])
                except ValueError:
                    errors.append(f"spatial_bootstrap.csv row {index} has non-numeric {field}")
                    continue
                if not 0.0 <= value <= 1.0:
                    errors.append(f"spatial_bootstrap.csv row {index} has invalid {field}: {value}")
    holdout_assignments: list[dict[str, str]] = []
    holdout_results: list[dict[str, str]] = []
    if require_file(out_dir / "holdout_assignments.csv", errors):
        holdout_assignments = read_csv(out_dir / "holdout_assignments.csv")
        check_columns(
            holdout_assignments,
            {"osm_id", "is_control", "group", "split", "seed", "fraction", "rule"},
            "holdout_assignments.csv",
            errors,
        )
    if require_file(out_dir / "holdout_results.csv", errors):
        holdout_results = read_csv(out_dir / "holdout_results.csv")
        check_columns(
            holdout_results,
            {
                "signal", "target_group", "split", "target_n", "control_n", "target_successes",
                "control_successes", "target_rate", "control_rate", "risk_difference_pp", "z",
                "p_value", "alpha", "pre_registered", "plan_sha256", "status",
            },
            "holdout_results.csv",
            errors,
        )
    used_plan: dict = {}
    if require_file(out_dir / "analysis_plan_used.json", errors):
        try:
            loaded_plan = json.loads((out_dir / "analysis_plan_used.json").read_text(encoding="utf-8"))
            if isinstance(loaded_plan, dict):
                used_plan = loaded_plan
            if not isinstance(loaded_plan, dict) or not loaded_plan.get("plan_sha256"):
                errors.append("analysis_plan_used.json has no plan hash")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"analysis_plan_used.json is not valid JSON: {exc}")
    check_holdout_contract(
        analysis,
        holdout_assignments,
        holdout_results,
        used_plan,
        out_dir,
        errors,
    )
    review_queue_ids: set[str] = set()
    review_queue_path = out_dir / "review_queue.csv"
    if require_file(review_queue_path, errors):
        review_queue_rows = read_csv(review_queue_path)
        check_columns(review_queue_rows, {"osm_id", "label"}, "review_queue.csv", errors)
        review_queue_ids = check_review_queue_contract(
            review_queue_rows,
            {row.get("osm_id", "") for row in targets},
            errors,
        )
    for name in ("review_calibration.csv", "review_confusion.csv"):
        if require_file(out_dir / name, errors):
            check_columns(read_csv(out_dir / name), {"status"}, name, errors)
    review_page_path = out_dir / "review.html"
    check_review_page_contract(review_page_path, errors)
    check_html_scripts(out_dir / "review.html", "review", errors, warnings)
    if require_file(out_dir / "columnar_status.json", errors):
        try:
            columnar = json.loads((out_dir / "columnar_status.json").read_text(encoding="utf-8"))
            if columnar.get("jsonl", {}).get("status") != "available":
                errors.append("columnar_status.json does not advertise JSONL availability")
            check_columnar_contract(columnar, out_dir, len(analysis), errors, warnings)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"columnar_status.json is not valid JSON: {exc}")
    require_file(out_dir / "analysis_results.jsonl", errors)
    report_data_path = out_dir / "report_data.json"
    check_report_data_contract(
        report_data_path,
        {row.get("osm_id", "") for row in targets},
        review_queue_ids,
        errors,
    )
    if scoring_config and report_data_path.is_file():
        try:
            report_payload = json.loads(report_data_path.read_text(encoding="utf-8"))
            report_scoring = report_payload.get("scoring", {}) if isinstance(report_payload, dict) else {}
            if report_scoring.get("config_sha256") != scoring_config.get("config_sha256"):
                errors.append("report_data.json scoring provenance does not match scoring_config.json")
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    check_interpretation_artifact(
        out_dir / "interpretation.json",
        report_data_path,
        errors,
    )
    report_lazy_path = out_dir / "report_lazy.html"
    require_file(report_lazy_path, errors)
    check_dashboard_review_contract(report_lazy_path, "lazy report", errors)
    check_html_scripts(out_dir / "report_lazy.html", "lazy report", errors, warnings)
    if require_file(out_dir / "ripley.csv", errors):
        check_columns(read_csv(out_dir / "ripley.csv"), {"group", "radius_m", "l_minus_r_m"}, "ripley.csv", errors)
    check_dashboard_review_contract(out_dir / "report.html", "report", errors)
    check_report(out_dir / "report.html", errors, warnings)

    manifest_path = manifest_path or out_dir / "manifest.json"
    manifest = {}
    if require_file(manifest_path, errors):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("manifest_version", "generated_at", "git_revision", "git_dirty", "sources", "counts"):
                if key not in manifest:
                    errors.append(f"manifest missing key: {key}")
            current_revision = git_revision(active_project_root)
            if current_revision and manifest.get("git_revision") != current_revision:
                errors.append(
                    f"manifest Git revision {manifest.get('git_revision')} != {current_revision}"
                )
            if manifest.get("manifest_version", 0) < 2 or manifest.get("schema_version", 0) < 3:
                errors.append("manifest does not advertise schema version 3")
            check_manifest_runtime_contract(manifest, errors, warnings)
            check_manifest_freshness_contract(
                manifest,
                data_root,
                out_dir,
                errors,
                warnings,
                project_root=active_project_root,
            )
            for source in manifest.get("sources", []):
                source_path = _resolve_manifest_source_path(
                    source,
                    manifest,
                    data_root,
                    out_dir,
                    project_root=active_project_root,
                )
                if source_path is not None and source_path.exists():
                    expected_metadata_hash = source.get("metadata_sha256")
                    if expected_metadata_hash is not None:
                        actual_metadata_hash = path_metadata_sha256(source_path)
                        if actual_metadata_hash != expected_metadata_hash:
                            errors.append(
                                f"manifest source metadata hash mismatch: {source_path}"
                            )
                    actual_hash = sha256_path(source_path)
                    if not source.get("sha256"):
                        errors.append(f"manifest source has no sha256: {source_path}")
                    elif source["sha256"] != actual_hash:
                        errors.append(f"manifest source hash mismatch: {source_path}")
                    if source_path.is_file() and source.get("bytes") != source_path.stat().st_size:
                        errors.append(f"manifest source byte-size mismatch: {source_path}")
            check_manifest_path_contract(manifest, out_dir, errors)
            check_manifest_contract(manifest, out_dir, errors)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"manifest is not valid JSON: {exc}")

    manifest_cache_alignment = check_manifest_cache_contract(
        manifest,
        stage_cache_payload,
        out_dir,
        errors,
        warnings,
    )

    counts = output_counts(out_dir)
    # A file cannot contain an exact byte count for itself without a
    # self-referential serialization loop. Keep the verifier's own size out
    # of its embedded counts; the final manifest records it after the write.
    counts.pop("verification.json_bytes", None)
    result = {
        "verified_at": utc_now(),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "git_revision": git_revision(active_project_root),
        "git_dirty": git_dirty(active_project_root),
        "counts": counts,
        "analysis_rows": len(analysis),
        "target_rows": len(targets),
        "control_rows": len(controls),
        "manifest_revision": manifest.get("git_revision"),
        "manifest_cache_alignment": manifest_cache_alignment,
    }
    atomic_write_json(out_dir / "verification.json", result, indent=2)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root", default=None, help="data directory; defaults to project data/"
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="active project root for portable manifest source paths",
    )
    parser.add_argument(
        "--out-dir", default=None, help="output directory; defaults to project output/"
    )
    parser.add_argument(
        "--manifest", default=None, help="manifest path; defaults to output/manifest.json"
    )
    args = parser.parse_args(argv)
    project_root = (
        Path(args.project_root).expanduser().resolve()
        if args.project_root
        else default_project_root()
    )

    def resolve_cli_path(value: str | None, default: str | Path) -> Path:
        candidate = Path(value) if value is not None else Path(default)
        return candidate if candidate.is_absolute() else project_root / candidate

    data_root = resolve_cli_path(args.data_root, "data")
    out_dir = resolve_cli_path(args.out_dir, "output")
    manifest = resolve_cli_path(args.manifest, out_dir / "manifest.json") if args.manifest else None
    try:
        result = verify_outputs(
            data_root,
            out_dir,
            manifest,
            project_root=project_root,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if result["passed"]:
        print(
            f"[verify] PASS ({result['analysis_rows']} analysis rows, {result['target_rows']} targets)"
        )
        for warning in result["warnings"]:
            print(f"[verify] warning: {warning}")
        return
    print("[verify] FAIL", flush=True)
    for error in result["errors"]:
        print(f"[verify]   {error}", flush=True)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
