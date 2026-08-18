#!/usr/bin/env python3
"""Check deterministic artifact hashes and optionally compare two output runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        atomic_write_json,
        package_version,
        project_path,
        sha256_file,
        utc_now,
    )
except ImportError:
    from scripts.runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        atomic_write_json,
        package_version,
        project_path,
        sha256_file,
        utc_now,
    )


VOLATILE = {
    "manifest.json",
    "verification.json",
    "reproducibility.json",
    "schema_validation.json",
    "stage_cache.json",
    "report.html",
    "report_lazy.html",
    "report_data.json",
} | set(MANIFEST_EXCLUDED_OUTPUTS)

CONTEXT_IGNORED_PARAMETERS = {"incremental", "no_network", "refresh"}


def provenance_context(out: Path) -> dict[str, Any]:
    """Return path-independent provenance needed to compare two output runs."""
    manifest_path = out / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "error": str(exc)}
    if not isinstance(manifest, dict):
        return {"status": "unavailable", "error": "manifest must be a JSON object"}

    raw_parameters = manifest.get("parameters", {})
    parameters = (
        {
            str(key): value
            for key, value in raw_parameters.items()
            if key not in CONTEXT_IGNORED_PARAMETERS
        }
        if isinstance(raw_parameters, dict)
        else {}
    )
    sources = []
    for source in manifest.get("sources", []):
        if not isinstance(source, dict):
            continue
        sources.append(
            {
                "kind": source.get("kind"),
                "source_kind": source.get("source_kind", source.get("kind")),
                "sha256": source.get("sha256"),
                "bytes": source.get("bytes"),
            }
        )
    sources.sort(
        key=lambda source: (
            str(source["source_kind"]),
            str(source["kind"]),
            str(source["sha256"]),
            str(source["bytes"]),
        )
    )
    raw_runtime = manifest.get("runtime")
    runtime = (
        {
            key: raw_runtime.get(key)
            for key in ("python", "implementation", "platform", "machine", "cache_tag", "distributions")
        }
        if isinstance(raw_runtime, dict)
        else None
    )
    return {
        "status": "available",
        "manifest_version": manifest.get("manifest_version"),
        "schema_version": manifest.get("schema_version"),
        "git_revision": manifest.get("git_revision"),
        "runtime": runtime,
        "parameters": parameters,
        "sources": sources,
    }


def context_differences(current: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    """Return stable labels for provenance fields that differ between runs."""
    differences = []
    if current.get("status") != "available" or reference.get("status") != "available":
        return ["manifest"]
    for key in ("manifest_version", "schema_version", "git_revision", "runtime", "parameters", "sources"):
        if current.get(key) != reference.get(key):
            differences.append(key)
    return differences


def hashes(out: Path) -> dict[str, str]:
    return {
        path.name: digest
        for path in sorted(out.iterdir())
        if path.is_file() and path.name not in VOLATILE and (digest := sha256_file(path))
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--reference-out-dir", default=None, help="optional second run to compare")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the reproducibility result as machine-readable JSON",
    )
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    current = hashes(out)
    errors = []
    reference = None
    current_context = provenance_context(out)
    reference_context = None
    context_errors: list[str] = []
    if args.reference_out_dir:
        reference = hashes(project_path(args.reference_out_dir, "output"))
        reference_context = provenance_context(project_path(args.reference_out_dir, "output"))
        for field in context_differences(current_context, reference_context):
            context_errors.append(f"reproducibility context differs: {field}")
        names = sorted(set(current) | set(reference))
        for name in names:
            if current.get(name) != reference.get(name):
                errors.append(f"deterministic artifact differs: {name}")
        errors.extend(context_errors)
    result = {
        "checked_at": utc_now(),
        "passed": not errors,
        "errors": errors,
        "artifact_count": len(current),
        "artifacts": current,
        "reference_comparison": bool(reference is not None),
        "context": current_context,
        "reference_context": reference_context,
        "context_differences": context_errors,
        "notes": "Time-stamped manifests, verification, diagnostic, and HTML report files are excluded from byte-for-byte comparison; normalized provenance context is compared when a reference run is supplied.",
    }
    atomic_write_json(out / "reproducibility.json", result, indent=2)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if errors:
            raise SystemExit(1)
        return
    if errors:
        for error in errors:
            print(f"[repro] {error}")
        raise SystemExit(1)
    print(f"[repro] PASS ({len(current):,} stable artifacts)")


if __name__ == "__main__":
    main()
