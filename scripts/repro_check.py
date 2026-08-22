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
    """Return recursive stable artifact hashes, treating symlinks as non-artifacts."""
    if not out.is_dir() or out.is_symlink():
        return {}
    result: dict[str, str] = {}
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in VOLATILE:
            continue
        digest = sha256_file(path)
        if digest:
            result[path.relative_to(out).as_posix()] = digest
    return result


def symlink_paths(out: Path) -> list[str]:
    """Return portable paths for symlinks inside an output directory."""
    if not out.is_dir() or out.is_symlink():
        return []
    return sorted(
        path.relative_to(out).as_posix()
        for path in out.rglob("*")
        if path.is_symlink()
    )


def output_directory_status(path: Path) -> dict[str, Any]:
    """Describe whether a diagnostic directory can be inspected."""
    if path.is_symlink():
        status = "symlink"
    elif path.is_dir():
        status = "available"
    elif path.exists():
        status = "not_a_directory"
    else:
        status = "missing"
    return {"path": str(path), "status": status, "available": status == "available"}


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
    current_directory = output_directory_status(out)
    current_symlinks = symlink_paths(out)
    if not current_directory["available"]:
        errors.append(f"output directory is unavailable: {out}")
    errors.extend(f"output contains a symlink: {relative}" for relative in current_symlinks)
    reference = None
    current_context = provenance_context(out)
    reference_context = None
    reference_directory = None
    reference_symlinks: list[str] | None = None
    context_errors: list[str] = []
    if args.reference_out_dir:
        reference_out = project_path(args.reference_out_dir, "output")
        reference_directory = output_directory_status(reference_out)
        reference_symlinks = symlink_paths(reference_out)
        if not reference_directory["available"]:
            errors.append(f"reference output directory is unavailable: {reference_out}")
        errors.extend(
            f"reference output contains a symlink: {relative}" for relative in reference_symlinks
        )
        reference = hashes(reference_out)
        reference_context = provenance_context(reference_out)
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
        "output_directory": current_directory,
        "symlinks": current_symlinks,
        "reference_output_directory": reference_directory,
        "reference_symlinks": reference_symlinks,
        "context": current_context,
        "reference_context": reference_context,
        "context_differences": context_errors,
        "notes": "Time-stamped manifests, verification, diagnostic, and HTML report files are excluded from byte-for-byte comparison; normalized provenance context is compared when a reference run is supplied.",
    }
    # A missing directory can be created for its diagnostic record, but a path
    # that is an existing file cannot accept one; keep that failure on stdout
    # rather than leaking a NotADirectoryError traceback.
    if current_directory["status"] in {"available", "missing"}:
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
