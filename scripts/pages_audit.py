#!/usr/bin/env python3
"""Audit the committed GitHub Pages dashboard before deployment.

The Pages site embeds a report pack in ``docs/index.html`` and a review queue
in ``docs/review.html``.  This audit is deliberately standard-library-only so
the deployment workflow can run it without installing the analytical stack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

PAGES_AUDIT_CONTRACT = "ireland-geometry.pages-audit.v1"
PAGES_PUBLISH_CONTRACT = "ireland-geometry.pages-publish.v1"
VALIDATION_RECORDS = {
    "schema_validation": "schema_validation.json",
    "reproducibility": "reproducibility.json",
}
PLACEHOLDER_TOKENS = ("__DATA__", "__OUTLINES__", "__MARKER_LIMIT__", "${TOP_N_MARKERS}")


def _extract_json_assignment(
    text: str,
    marker: str,
    label: str,
    errors: list[str],
) -> Any | None:
    """Decode a JSON literal assigned to a JavaScript constant."""
    start = text.find(marker)
    if start < 0:
        errors.append(f"{label} is missing JavaScript assignment: {marker}")
        return None
    start += len(marker)
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as exc:
        errors.append(f"{label} has invalid embedded JSON: {exc.msg} at character {exc.pos}")
        return None
    if end >= len(text) or text[end] != ";":
        errors.append(f"{label} embedded JSON is not terminated by a semicolon")
    return value


def _read_page(path: Path, label: str, errors: list[str]) -> str | None:
    if path.is_symlink():
        errors.append(f"{label} must not be a symlink: {path}")
        return None
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label} could not be read: {exc}")
        return None
    if not text.strip():
        errors.append(f"{label} is empty")
    for token in PLACEHOLDER_TOKENS:
        if token in text:
            errors.append(f"{label} still contains template token: {token}")
    return text


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_json_file(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if path.is_symlink():
        errors.append(f"{label} must not be a symlink: {path}")
        return None
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not valid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return payload


def _row_ids(rows: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(rows, list):
        errors.append(f"{label} must be a list")
        return set()
    ids: list[str] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"{label} row {index} must be an object")
            continue
        osm_id = row.get("osm_id")
        if not isinstance(osm_id, str) or not osm_id.strip():
            errors.append(f"{label} row {index} is missing a non-empty osm_id")
            continue
        ids.append(osm_id)
    if len(ids) != len(set(ids)):
        errors.append(f"{label} contains duplicate osm_id values")
    return set(ids)


def _audit_pack(pack: Any, errors: list[str]) -> tuple[set[str], dict[str, Any]]:
    if not isinstance(pack, dict):
        errors.append("dashboard report pack must be an object")
        return set(), {}

    required = {
        "targets",
        "summary",
        "geojson",
        "manifest",
        "pattern_catalog",
        "scoring",
        "interpretation",
    }
    missing = sorted(required.difference(pack))
    if missing:
        errors.append("dashboard report pack is missing keys: " + ", ".join(missing))

    targets = pack.get("targets")
    target_ids = _row_ids(targets, "dashboard targets", errors)
    if not target_ids:
        errors.append("dashboard targets is empty")

    summary = pack.get("summary")
    if not isinstance(summary, dict):
        errors.append("dashboard summary must be an object")
        summary = {}
    elif summary.get("targets") != len(target_ids):
        errors.append(
            "dashboard summary targets does not match embedded target rows: "
            f"{summary.get('targets')!r} != {len(target_ids)}"
        )
    if not isinstance(summary.get("analysis_ready"), bool):
        errors.append("dashboard summary analysis_ready must be boolean")

    geojson = pack.get("geojson")
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        errors.append("dashboard geojson must be a FeatureCollection")
    else:
        features = geojson.get("features")
        feature_ids = _row_ids(
            [feature.get("properties", {}) for feature in features]
            if isinstance(features, list)
            else features,
            "dashboard GeoJSON features",
            errors,
        )
        if feature_ids != target_ids:
            errors.append("dashboard GeoJSON ids do not exactly match embedded target ids")

    manifest = pack.get("manifest")
    if not isinstance(manifest, dict):
        errors.append("dashboard manifest must be an object")
    else:
        if not isinstance(manifest.get("generated_at"), str) or not manifest.get("generated_at"):
            errors.append("dashboard manifest is missing generated_at")
        if not isinstance(manifest.get("git_revision"), str) or not manifest.get("git_revision"):
            errors.append("dashboard manifest is missing git_revision")

    scoring = pack.get("scoring")
    if not isinstance(scoring, dict):
        errors.append("dashboard scoring provenance must be an object")
    else:
        if scoring.get("contract") != "ireland-geometry.exploratory-score.v1":
            errors.append("dashboard scoring provenance has an unsupported contract")
        if scoring.get("version") != 1:
            errors.append("dashboard scoring provenance has an unsupported version")
        config_hash = scoring.get("config_sha256")
        if not isinstance(config_hash, str) or len(config_hash) != 64:
            errors.append("dashboard scoring provenance has an invalid config_sha256")

    if not isinstance(pack.get("pattern_catalog"), list) or not pack["pattern_catalog"]:
        errors.append("dashboard pattern_catalog must be a non-empty list")
    interpretation = pack.get("interpretation")
    if not isinstance(interpretation, dict) or not isinstance(interpretation.get("caveats"), list):
        errors.append("dashboard interpretation must include a caveats list")

    return target_ids, {
        "bytes": None,
        "target_count": len(target_ids),
        "feature_count": len(geojson.get("features", [])) if isinstance(geojson, dict) and isinstance(geojson.get("features"), list) else 0,
        "generated_at": manifest.get("generated_at") if isinstance(manifest, dict) else None,
        "git_revision": manifest.get("git_revision") if isinstance(manifest, dict) else None,
    }


def _audit_review_page(
    text: str,
    target_ids: set[str],
    expected_queue_count: Any,
    errors: list[str],
) -> dict[str, Any]:
    required_tokens = {
        "review queue key": "const QUEUE_KEY=",
        "review backup schema": "const BACKUP_SCHEMA=",
        "local review storage": "localStorage",
        "JSON export": "function downloadJson",
        "JSON import": "async function importJson",
    }
    for label, token in required_tokens.items():
        if token not in text:
            errors.append(f"review page is missing {label} token: {token}")

    data = _extract_json_assignment(text, "const DATA=", "review page", errors)
    review_ids = _row_ids(data, "review queue", errors)
    if target_ids and not review_ids.issubset(target_ids):
        errors.append("review queue contains ids outside the dashboard target set")
    if isinstance(expected_queue_count, int) and len(review_ids) != expected_queue_count:
        errors.append(
            "review queue count does not match dashboard summary: "
            f"{len(review_ids)} != {expected_queue_count}"
        )
    return {"bytes": len(text.encode("utf-8")), "queue_count": len(review_ids)}


def _audit_publication_manifest(
    site: Path,
    pack: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """Check that the committed site records how it was published."""
    path = site / "pages_manifest.json"
    publication = _read_json_file(path, "Pages publication manifest", errors)
    if publication is None:
        return {"available": False}
    if publication.get("contract") != PAGES_PUBLISH_CONTRACT:
        errors.append("Pages publication manifest has an unsupported contract")
    if publication.get("version") != 1:
        errors.append("Pages publication manifest has an unsupported version")

    embedded_manifest = pack.get("manifest") if isinstance(pack, dict) else None
    source_revision = publication.get("source_manifest_revision")
    source_generated_at = publication.get("source_manifest_generated_at")
    if not isinstance(source_revision, str) or not source_revision:
        errors.append("Pages publication manifest is missing source_manifest_revision")
    elif isinstance(embedded_manifest, dict) and source_revision != embedded_manifest.get("git_revision"):
        errors.append("Pages publication revision does not match the embedded report manifest")
    if not isinstance(source_generated_at, str) or not source_generated_at:
        errors.append("Pages publication manifest is missing source_manifest_generated_at")
    elif isinstance(embedded_manifest, dict) and source_generated_at != embedded_manifest.get("generated_at"):
        errors.append("Pages publication timestamp does not match the embedded report manifest")
    source_output_generated_at = publication.get("source_output_manifest_generated_at")
    if not isinstance(source_output_generated_at, str) or not source_output_generated_at:
        errors.append(
            "Pages publication manifest is missing source_output_manifest_generated_at"
        )
    for field in ("source_manifest_sha256", "source_verification_sha256"):
        if not _is_sha256(publication.get(field)):
            errors.append(f"Pages publication manifest has an invalid {field}")

    source_verification = publication.get("source_verification")
    if not isinstance(source_verification, dict) or source_verification.get("passed") is not True:
        errors.append("Pages publication manifest does not record passing source verification")
    else:
        alignment = source_verification.get("manifest_cache_alignment")
        if not isinstance(alignment, dict) or alignment.get("status") != "pass":
            errors.append("Pages publication manifest does not record full manifest/cache alignment")

    source_validation = publication.get("source_validation")
    if not isinstance(source_validation, dict) or set(source_validation) != set(VALIDATION_RECORDS):
        errors.append("Pages publication manifest does not record complete validation provenance")
    else:
        for key, expected_path in VALIDATION_RECORDS.items():
            record = source_validation.get(key)
            if not isinstance(record, dict):
                errors.append(f"Pages publication validation record is missing: {key}")
                continue
            if record.get("path") != expected_path:
                errors.append(f"Pages publication validation path mismatch: {key}")
            if record.get("status") != "pass" or record.get("passed") is not True:
                errors.append(f"Pages publication validation record does not pass: {key}")
            if not _is_sha256(record.get("sha256")):
                errors.append(f"Pages publication validation hash is invalid: {key}")

    files = publication.get("files")
    if not isinstance(files, dict):
        errors.append("Pages publication manifest files must be an object")
        return {"available": True, "source_revision": source_revision}
    expected_files = {"index.html", "review.html", "report.html"}
    if set(files) != expected_files:
        errors.append("Pages publication manifest files do not match the published site")
    for name in sorted(expected_files):
        record = files.get(name)
        path = site / name
        if not isinstance(record, dict):
            errors.append(f"Pages publication manifest is missing file record: {name}")
            continue
        actual_hash = _sha256_file(path) if path.is_file() else None
        if actual_hash != record.get("sha256"):
            errors.append(f"Pages publication hash mismatch: {name}")
        if path.is_file() and path.stat().st_size != record.get("bytes"):
            errors.append(f"Pages publication byte-size mismatch: {name}")
    return {
        "available": True,
        "source_revision": source_revision,
        "source_generated_at": source_generated_at,
        "source_output_manifest_generated_at": source_output_generated_at,
        "source_manifest_sha256": publication.get("source_manifest_sha256"),
        "source_verification_sha256": publication.get("source_verification_sha256"),
        "source_validation": source_validation,
        "validation_count": len(source_validation) if isinstance(source_validation, dict) else 0,
        "file_count": len(files),
    }


def audit_site(site_dir: str | Path = "docs") -> dict[str, Any]:
    """Return a machine-readable audit of the committed Pages site."""
    site_input = Path(site_dir).expanduser()
    if site_input.is_symlink():
        return {
            "contract": PAGES_AUDIT_CONTRACT,
            "site_dir": str(site_input),
            "passed": False,
            "errors": [f"Pages site directory must not be a symlink: {site_input}"],
            "pages": {},
        }
    if site_input.exists() and not site_input.is_dir():
        return {
            "contract": PAGES_AUDIT_CONTRACT,
            "site_dir": str(site_input),
            "passed": False,
            "errors": [f"Pages site directory must be a directory: {site_input}"],
            "pages": {},
        }
    site = site_input.resolve()
    errors: list[str] = []
    pages: dict[str, Any] = {}

    index_path = site / "index.html"
    index_text = _read_page(index_path, "dashboard page", errors)
    target_ids: set[str] = set()
    pack: dict[str, Any] = {}
    if index_text is not None:
        pack_value = _extract_json_assignment(index_text, "const PACK = ", "dashboard page", errors)
        target_ids, pack_info = _audit_pack(pack_value, errors)
        pack_info["bytes"] = len(index_text.encode("utf-8"))
        pages["index"] = pack_info
        if isinstance(pack_value, dict):
            pack = pack_value

    review_path = site / "review.html"
    review_text = _read_page(review_path, "review page", errors)
    if review_text is not None:
        summary = pack.get("summary", {})
        expected_queue_count = summary.get("review_queue_targets") if isinstance(summary, dict) else None
        pages["review"] = _audit_review_page(
            review_text,
            target_ids,
            expected_queue_count,
            errors,
        )

    report_path = site / "report.html"
    report_text = _read_page(report_path, "report entrypoint", errors)
    if report_text is not None:
        if 'http-equiv="refresh"' not in report_text or "url=./" not in report_text:
            errors.append("report entrypoint must redirect to the static dashboard")
        pages["report"] = {"bytes": len(report_text.encode("utf-8"))}

    pages["publication"] = _audit_publication_manifest(site, pack, errors)

    return {
        "contract": PAGES_AUDIT_CONTRACT,
        "site_dir": str(site),
        "passed": not errors,
        "errors": errors,
        "pages": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", default="docs", help="committed Pages directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    result = audit_site(args.site_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[pages-audit] {status}: {result['site_dir']}")
        for error in result["errors"]:
            print(f"[pages-audit] error: {error}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
