#!/usr/bin/env python3
"""Check whether the project is ready for a reproducible publication release.

This command is deliberately read-only.  It composes the existing Doctor,
GitHub Pages audit, and bundle verification contracts into one compact result
that can be used by a release script or a human operator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from bundle import BUNDLE_CONTRACT, verify_bundle
    from doctor import inspect_project
    from pages_audit import PAGES_AUDIT_CONTRACT, audit_site
    from runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        manifest_source_alignment,
        package_version,
    )
except ImportError:
    from scripts.bundle import BUNDLE_CONTRACT, verify_bundle
    from scripts.doctor import inspect_project
    from scripts.pages_audit import PAGES_AUDIT_CONTRACT, audit_site
    from scripts.runtime import (
        MANIFEST_EXCLUDED_OUTPUTS,
        manifest_source_alignment,
        package_version,
    )


RELEASE_CHECK_CONTRACT = "ireland-geometry.release-check.v1"
PAGES_PUBLISH_CONTRACT = "ireland-geometry.pages-publish.v1"
VALIDATION_FILES = (
    "manifest.json",
    "verification.json",
    "schema_validation.json",
    "reproducibility.json",
)
REQUIRED_OUTPUT_ARTIFACTS = VALIDATION_FILES[1:]


def _resolve(root: Path, value: str | Path | None, default: str | Path) -> Path:
    candidate = Path(value) if value is not None else Path(default)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.expanduser().resolve()


def _input_path(root: Path, value: str | Path | None, default: str | Path) -> Path:
    """Resolve a user path without following its final symlink component."""
    candidate = Path(value) if value is not None else Path(default)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.expanduser()


def _symlink_root_errors(
    *,
    output: Path,
    site: Path,
    bundle: Path | None,
    check_pages: bool,
    check_bundle: bool,
) -> list[str]:
    """Return publication-root boundary errors without following symlinks."""
    errors: list[str] = []
    if output.is_symlink():
        errors.append(f"output directory must not be a symlink: {output}")
    elif output.exists() and not output.is_dir():
        errors.append(f"output directory must be a directory: {output}")
    if check_pages and site.is_symlink():
        errors.append(f"Pages site directory must not be a symlink: {site}")
    elif check_pages and site.exists() and not site.is_dir():
        errors.append(f"Pages site directory must be a directory: {site}")
    if check_bundle and bundle is not None and bundle.is_symlink():
        errors.append(f"bundle archive path must not be a symlink: {bundle}")
    return errors


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _output_artifact_alignment(output: Path) -> dict[str, Any]:
    """Recheck the current output inventory against the provenance manifest."""
    manifest = _read_json(output / "manifest.json")
    if manifest is None:
        return {
            "status": "not_provided",
            "passed": False,
            "checked_count": 0,
            "errors": [f"output manifest is unavailable: {output / 'manifest.json'}"],
        }
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return {
            "status": "invalid",
            "passed": False,
            "checked_count": 0,
            "errors": ["output manifest has no artifact records"],
        }

    errors: list[str] = []
    seen: set[str] = set()
    checked_count = 0
    output_root = output.resolve()
    for index, record in enumerate(artifacts, 1):
        if not isinstance(record, dict):
            errors.append(f"output manifest artifact {index} is not an object")
            continue
        relative = record.get("relative_path")
        if (
            record.get("path_base") != "output_dir"
            or not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
        ):
            errors.append(f"output manifest artifact {index} has an unsafe relative path")
            continue
        posix_path = PurePosixPath(relative)
        if (
            posix_path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in posix_path.parts)
        ):
            errors.append(f"output manifest artifact has an unsafe relative path: {relative}")
            continue
        if relative in seen:
            errors.append(f"output manifest contains a duplicate artifact: {relative}")
            continue
        seen.add(relative)
        candidate = output / Path(*posix_path.parts)
        try:
            candidate.resolve().relative_to(output_root)
        except ValueError:
            errors.append(f"output manifest artifact escapes output directory: {relative}")
            continue
        expected_bytes = record.get("bytes")
        expected_hash = record.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 0:
            errors.append(f"output manifest artifact has an invalid byte count: {relative}")
            continue
        if not _is_sha256(expected_hash):
            errors.append(f"output manifest artifact has an invalid SHA-256: {relative}")
            continue
        if not candidate.is_file() or candidate.is_symlink():
            errors.append(f"current output artifact is missing: {relative}")
            continue
        checked_count += 1
        actual_bytes = candidate.stat().st_size
        if actual_bytes != expected_bytes:
            errors.append(f"current output byte count differs from manifest: {relative}")
        actual_hash = _sha256(candidate)
        if actual_hash != expected_hash:
            errors.append(f"current output hash differs from manifest: {relative}")
    missing_required = [name for name in REQUIRED_OUTPUT_ARTIFACTS if name not in seen]
    errors.extend(
        f"output manifest is missing a required validation artifact record: {name}"
        for name in missing_required
    )
    allowed_unlisted = {"manifest.json"} | set(MANIFEST_EXCLUDED_OUTPUTS)
    symlinks = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_symlink()
    )
    errors.extend(f"current output contains a symlink: {relative}" for relative in symlinks)
    actual_files = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    unlisted = sorted(actual_files - seen - allowed_unlisted)
    errors.extend(f"current output contains an unlisted artifact: {relative}" for relative in unlisted)
    return {
        "status": "pass" if not errors else "fail",
        "passed": not errors,
        "checked_count": checked_count,
        "manifest_artifact_count": len(artifacts),
        "required_artifact_count": len(REQUIRED_OUTPUT_ARTIFACTS) - len(missing_required),
        "required_artifact_total": len(REQUIRED_OUTPUT_ARTIFACTS),
        "actual_file_count": len(actual_files),
        "symlink_count": len(symlinks),
        "unlisted_count": len(unlisted),
        "errors": errors,
    }


def _doctor_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    git_state = snapshot.get("git")
    if not isinstance(git_state, dict):
        git_state = {}
    validation = snapshot.get("capabilities", {}).get("validation")
    if not isinstance(validation, dict):
        validation = {}
    source_alignment = validation.get("source_alignment")
    if not isinstance(source_alignment, dict):
        source_alignment = {}
    return {
        "status": "pass" if summary.get("strict_ready") is True else "fail",
        "passed": summary.get("strict_ready") is True,
        "strict_ready": summary.get("strict_ready") is True,
        "doctor_status": summary.get("status"),
        "strict_blockers": summary.get("strict_blockers", []),
        "git_revision": git_state.get("revision"),
        "git_dirty": git_state.get("dirty"),
        "git_worktree": git_state.get("worktree"),
        "validation_status": validation.get("status"),
        "source_alignment_status": source_alignment.get("status"),
        "missing_required_inputs": summary.get("missing_required_inputs"),
        "missing_required_outputs": summary.get("missing_required_outputs"),
        "stale_required_inputs": summary.get("stale_required_inputs"),
    }


def _publication_alignment(
    site: Path,
    output: Path,
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Ensure the audited site is the current output publication, not merely valid."""
    errors: list[str] = []
    publication = audit.get("pages", {}).get("publication")
    if not isinstance(publication, dict) or publication.get("available") is not True:
        return {"status": "not_provided", "passed": False, "errors": ["Pages publication manifest is unavailable"]}

    manifest = _read_json(output / "manifest.json")
    if manifest is None:
        errors.append(f"current output manifest is unavailable: {output / 'manifest.json'}")
    else:
        revision = manifest.get("git_revision")
        generated_at = manifest.get("generated_at")
        if publication.get("source_revision") != revision:
            errors.append("Pages source revision does not match the current output manifest")
        published_output_time = publication.get("source_output_manifest_generated_at")
        if published_output_time is not None and published_output_time != generated_at:
            errors.append("Pages source output timestamp does not match the current output manifest")

    expected_hashes = {
        "manifest.json": publication.get("source_manifest_sha256"),
        "verification.json": publication.get("source_verification_sha256"),
    }
    source_validation = publication.get("source_validation")
    if isinstance(source_validation, dict):
        for name in VALIDATION_FILES[2:]:
            record = source_validation.get(name.removesuffix(".json"))
            if isinstance(record, dict):
                expected_hashes[name] = record.get("sha256")
    for name, expected in expected_hashes.items():
        actual = _sha256(output / name)
        if actual is None:
            errors.append(f"current output is missing the published source record: {name}")
        elif actual != expected:
            errors.append(f"Pages source hash does not match current output: {name}")

    page_pairs = {
        "index.html": "report.html",
        "review.html": "review.html",
    }
    for published_name, output_name in page_pairs.items():
        published_hash = _sha256(site / published_name)
        output_hash = _sha256(output / output_name)
        if published_hash is None or output_hash is None:
            errors.append(f"current output or published page is missing: {published_name}")
        elif published_hash != output_hash:
            errors.append(f"published page is stale relative to current output: {published_name}")

    return {
        "status": "pass" if not errors else "fail",
        "passed": not errors,
        "errors": errors,
        "source_revision": publication.get("source_revision"),
        "source_output_manifest_generated_at": publication.get(
            "source_output_manifest_generated_at"
        ),
    }


def _pages_gate(
    site: Path,
    output: Path,
    *,
    required: bool,
    skipped: bool,
) -> dict[str, Any]:
    if skipped:
        return {
            "status": "skipped",
            "passed": None,
            "required": False,
            "site": str(site),
            "audit": None,
            "alignment": {"status": "skipped", "passed": None, "errors": []},
        }
    if not site.is_dir():
        return {
            "status": "not_provided",
            "passed": False if required else None,
            "required": required,
            "site": str(site),
            "audit": None,
            "alignment": {
                "status": "not_provided",
                "passed": False if required else None,
                "errors": [f"Pages site is missing: {site}"] if required else [],
            },
        }
    try:
        audit = audit_site(site)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        return {
            "status": "invalid",
            "passed": False,
            "required": required,
            "site": str(site),
            "audit": {"contract": PAGES_AUDIT_CONTRACT, "passed": False, "errors": [str(exc)]},
            "alignment": {"status": "not_provided", "passed": False, "errors": []},
        }
    alignment = _publication_alignment(site, output, audit)
    audit_passed = audit.get("passed") is True
    passed = audit_passed and alignment.get("passed") is True
    return {
        "status": "pass" if passed else "fail",
        "passed": passed,
        "required": required,
        "site": str(site),
        "audit": audit,
        "alignment": alignment,
    }


def _bundle_alignment(output: Path, verification: dict[str, Any]) -> dict[str, Any]:
    """Ensure a verified archive carries the current output provenance records."""
    errors: list[str] = []
    source_records: dict[str, Any] = {
        "manifest.json": verification.get("source_manifest"),
        "verification.json": verification.get("source_verification"),
    }
    source_validation = verification.get("source_validation")
    if isinstance(source_validation, dict):
        for name in VALIDATION_FILES[2:]:
            source_records[name] = source_validation.get(name)
    matched_count = 0
    for name in VALIDATION_FILES:
        record = source_records.get(name)
        if not isinstance(record, dict):
            errors.append(f"bundle is missing current-output provenance: {name}")
            continue
        actual = _sha256(output / name)
        if actual is None:
            errors.append(f"current output is missing the bundle provenance record: {name}")
        elif actual != record.get("sha256"):
            errors.append(f"bundle source hash does not match current output: {name}")
        else:
            matched_count += 1
    return {
        "status": "pass" if not errors else "fail",
        "passed": not errors,
        "errors": errors,
        "matched_record_count": matched_count,
    }


def _bundle_gate(
    path: Path,
    *,
    output: Path,
    required: bool,
) -> dict[str, Any]:
    if not path.is_file():
        return {
            "contract": BUNDLE_CONTRACT,
            "status": "not_provided",
            "passed": False if required else None,
            "required": required,
            "archive": str(path),
            "verification": None,
            "alignment": {"status": "not_provided", "passed": None, "errors": []},
            "errors": [f"bundle archive is missing: {path}"] if required else [],
        }
    try:
        verification = verify_bundle(path)
    except (OSError, TypeError, ValueError) as exc:
        return {
            "contract": BUNDLE_CONTRACT,
            "status": "fail",
            "passed": False,
            "required": required,
            "archive": str(path),
            "verification": None,
            "alignment": {"status": "invalid", "passed": False, "errors": []},
            "errors": [str(exc)],
        }
    errors = []
    if verification.get("passed") is not True:
        errors.append("bundle verification did not pass")
    if verification.get("require_verified") is not True:
        errors.append("bundle was not built with the complete verified publication guard")
    alignment = _bundle_alignment(output, verification)
    errors.extend(alignment["errors"])
    return {
        "contract": BUNDLE_CONTRACT,
        "status": "pass" if not errors else "fail",
        "passed": not errors,
        "required": required,
        "archive": str(path),
        "verification": verification,
        "alignment": alignment,
        "errors": errors,
    }


def check_release(
    project_root: str | Path | None = None,
    *,
    data_root: str | Path | None = None,
    out_dir: str | Path | None = None,
    site_dir: str | Path | None = None,
    bundle_path: str | Path | None = None,
    require_pages: bool = False,
    skip_pages: bool = False,
    require_bundle: bool = False,
    max_input_age_days: float | None = None,
    package_root: str | Path | None = None,
    check_input_hashes: bool = False,
) -> dict[str, Any]:
    """Return the combined release/readiness contract without writing files."""
    if require_pages and skip_pages:
        raise ValueError("require_pages and skip_pages cannot both be true")
    if max_input_age_days is not None and (
        not math.isfinite(max_input_age_days) or max_input_age_days <= 0
    ):
        raise ValueError("max_input_age_days must be a finite positive number")
    doctor = inspect_project(
        project_root,
        data_root=data_root,
        out_dir=out_dir,
        package_root=package_root,
        max_input_age_days=max_input_age_days,
    )
    root = Path(doctor["project_root"]).resolve()
    output_input = _input_path(root, out_dir, "output")
    site_input = _input_path(root, site_dir, "docs")
    bundle_input = _input_path(root, bundle_path, "output.bundle.zip") if bundle_path is not None else None
    output = _resolve(root, out_dir, "output")
    site = _resolve(root, site_dir, "docs")
    default_bundle = output.parent / f"{output.name}.bundle.zip"
    bundle = _resolve(root, bundle_path, default_bundle) if bundle_path is not None else default_bundle
    bundle_required = require_bundle or bundle_path is not None
    root_boundary = _symlink_root_errors(
        output=output_input,
        site=site_input,
        bundle=bundle_input,
        check_pages=not skip_pages,
        check_bundle=bundle_required,
    )

    pages = _pages_gate(site, output, required=require_pages, skipped=skip_pages)
    bundle_result = _bundle_gate(bundle, output=output, required=bundle_required)
    output_alignment = _output_artifact_alignment(output)
    doctor_result = _doctor_gate(doctor)
    source_alignment = manifest_source_alignment(
        _read_json(output / "manifest.json") or {},
        project_root=root,
        data_root=_resolve(root, data_root, "data"),
        out_dir=output,
        check_hashes=check_input_hashes,
    )

    blocking_errors: list[str] = []
    blocking_errors.extend(f"Path gate: {error}" for error in root_boundary)
    if not doctor_result["passed"]:
        blockers = doctor_result.get("strict_blockers")
        if isinstance(blockers, list) and blockers:
            for blocker in blockers:
                if isinstance(blocker, dict):
                    code = blocker.get("code", "unknown")
                    message = blocker.get("message", "strict readiness blocker")
                    blocking_errors.append(f"Doctor gate [{code}]: {message}")
        else:
            blocking_errors.append("Doctor strict readiness is not passing")
    if output_alignment["passed"] is not True:
        blocking_errors.extend(
            [f"Output gate: {error}" for error in output_alignment["errors"]]
        )
    if source_alignment["status"] == "fail":
        blocking_errors.extend(
            [f"Source gate: {error}" for error in source_alignment["errors"]]
        )
    if pages["required"] and pages.get("passed") is not True:
        blocking_errors.extend(
            [f"Pages gate: {error}" for error in pages.get("alignment", {}).get("errors", [])]
        )
        audit = pages.get("audit")
        if isinstance(audit, dict):
            blocking_errors.extend([f"Pages audit: {error}" for error in audit.get("errors", [])])
        if not blocking_errors or not any(error.startswith("Pages gate:") for error in blocking_errors):
            blocking_errors.append("Pages gate is not passing")
    if bundle_result["required"] and bundle_result.get("passed") is not True:
        blocking_errors.extend(
            [f"Bundle gate: {error}" for error in bundle_result.get("errors", [])]
        )

    return {
        "contract": RELEASE_CHECK_CONTRACT,
        "version": 1,
        "package_version": package_version(),
        "project_root": str(root),
        "data_root": str(_resolve(root, data_root, "data")),
        "out_dir": str(output),
        "site_dir": str(site),
        "bundle_path": str(bundle),
        "pages_contracts": {
            "audit": PAGES_AUDIT_CONTRACT,
            "publication": PAGES_PUBLISH_CONTRACT,
        },
        "path_boundary": {
            "status": "pass" if not root_boundary else "fail",
            "passed": not root_boundary,
            "errors": root_boundary,
        },
        "doctor": doctor_result,
        "source_alignment": source_alignment,
        "output": output_alignment,
        "pages": pages,
        "bundle": bundle_result,
        "passed": not blocking_errors,
        "status": "pass" if not blocking_errors else "fail",
        "errors": blocking_errors,
    }


def _format_result(result: dict[str, Any]) -> str:
    lines = [
        f"Release check: {result['status']}",
        f"Doctor strict readiness: {result['doctor']['status']}",
        f"Sources: {result['source_alignment']['status']}"
        + (" (full hashes)" if result["source_alignment"].get("mode") == "hash" else ""),
        f"Pages: {result['pages']['status']}"
        + (" (required)" if result["pages"].get("required") else ""),
        f"Bundle: {result['bundle']['status']}"
        + (" (required)" if result["bundle"].get("required") else ""),
    ]
    for error in result["errors"]:
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
    parser.add_argument("--site-dir", default=None)
    parser.add_argument(
        "--bundle",
        dest="bundle_path",
        default=None,
        help="existing bundle archive to verify; providing it makes the bundle gate required",
    )
    parser.add_argument(
        "--require-pages",
        action="store_true",
        help="require a passing and current GitHub Pages publication",
    )
    parser.add_argument(
        "--skip-pages",
        action="store_true",
        help="omit the Pages audit and freshness check",
    )
    parser.add_argument(
        "--require-bundle",
        action="store_true",
        help="require the default or supplied archive to be a complete verified bundle",
    )
    parser.add_argument(
        "--max-input-age-days",
        type=float,
        default=None,
        help="mark required cached inputs stale when older than this positive number of days",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 unless every required release gate passes",
    )
    parser.add_argument(
        "--check-input-hashes",
        action="store_true",
        help="rehash every manifest source; the default source gate checks metadata only",
    )
    args = parser.parse_args(argv)
    if args.require_pages and args.skip_pages:
        parser.error("--require-pages and --skip-pages cannot be combined")
    if args.max_input_age_days is not None and (
        not math.isfinite(args.max_input_age_days) or args.max_input_age_days <= 0
    ):
        parser.error("--max-input-age-days must be a finite positive number")
    result = check_release(
        args.project_root,
        data_root=args.data_root,
        out_dir=args.out_dir,
        site_dir=args.site_dir,
        bundle_path=args.bundle_path,
        require_pages=args.require_pages,
        skip_pages=args.skip_pages,
        require_bundle=args.require_bundle,
        max_input_age_days=args.max_input_age_days,
        check_input_hashes=args.check_input_hashes,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_result(result))
    if args.strict and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
