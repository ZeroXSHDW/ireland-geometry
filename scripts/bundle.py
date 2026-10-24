#!/usr/bin/env python3
"""Create a deterministic, portable archive of generated analysis artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from runtime import (
        package_version,
        path_symlink_paths,
        project_path,
        reject_output_file,
        reject_symlink_path,
        reject_symlink_root,
    )
except ImportError:
    from scripts.runtime import (
        package_version,
        path_symlink_paths,
        project_path,
        reject_output_file,
        reject_symlink_path,
        reject_symlink_root,
    )


BUNDLE_CONTRACT = "ireland-geometry.bundle.v1"
BUNDLE_MANIFEST_NAME = "bundle.json"
DEFAULT_EXCLUDES = ("doctor.json", "stage_cache.json", BUNDLE_MANIFEST_NAME)
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
PROVENANCE_FIELDS = ("source_manifest", "source_verification")
VALIDATION_FILES = ("schema_validation.json", "reproducibility.json")
VERIFICATION_STATUSES = {"not_provided", "invalid", "pass", "fail"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_contract(out_dir: Path) -> dict[str, Any]:
    """Describe whether the source manifest carries the portable path contract."""
    path = out_dir / "manifest.json"
    if not path.is_file():
        return {"status": "not_provided", "version": None}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "version": None, "error": str(exc)}
    contract = manifest.get("path_contract") if isinstance(manifest, dict) else None
    if not isinstance(contract, dict):
        return {"status": "legacy", "version": None}
    if (
        contract.get("version") == 1
        and contract.get("relative_path_field") == "relative_path"
        and contract.get("artifact_base") == "output_dir"
        and contract.get("source_base") == "project_root"
    ):
        return {"status": "portable", "version": 1}
    return {"status": "unsupported", "version": contract.get("version")}


def _validation_record(out_dir: Path, name: str) -> dict[str, Any]:
    """Return a validation-record provenance summary for a generated output."""
    path = out_dir / name
    record: dict[str, Any] = {
        "path": name,
        "available": path.is_file(),
        "included": False,
        "sha256": _sha256(path) if path.is_file() else None,
        "status": "not_provided",
        "passed": None,
    }
    if not path.is_file():
        return record
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        record["status"] = "invalid"
        record["error"] = str(exc)
        return record
    if not isinstance(payload, dict):
        record["status"] = "invalid"
        record["error"] = "validation record must be an object"
        return record
    raw_passed = payload.get("passed")
    if not isinstance(raw_passed, bool):
        record["status"] = "invalid"
        record["error"] = "validation record passed must be a boolean"
        return record
    record["passed"] = raw_passed
    record["status"] = "pass" if raw_passed else "fail"
    if type(payload.get("artifact_count")) is int and payload["artifact_count"] >= 0:
        record["artifact_count"] = payload["artifact_count"]
    if type(payload.get("schema_version")) is int and payload["schema_version"] >= 0:
        record["schema_version"] = payload["schema_version"]
    return record


def _provenance_records(
    out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Return source manifest, verification, and validation summaries."""
    manifest_path = out_dir / "manifest.json"
    manifest = {
        "path": "manifest.json",
        "available": manifest_path.is_file(),
        "included": False,
        "sha256": _sha256(manifest_path) if manifest_path.is_file() else None,
        "path_contract": _manifest_contract(out_dir),
    }
    verification_path = out_dir / "verification.json"
    verification: dict[str, Any] = {
        "path": "verification.json",
        "available": verification_path.is_file(),
        "included": False,
        "sha256": _sha256(verification_path) if verification_path.is_file() else None,
        "status": "not_provided",
        "passed": None,
    }
    validation = {name: _validation_record(out_dir, name) for name in VALIDATION_FILES}
    if not verification_path.is_file():
        return manifest, verification, validation
    try:
        payload = json.loads(verification_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        verification["status"] = "invalid"
        verification["error"] = str(exc)
        return manifest, verification, validation
    if not isinstance(payload, dict):
        verification["status"] = "invalid"
        verification["error"] = "verification record must be an object"
        return manifest, verification, validation
    raw_passed = payload.get("passed")
    if not isinstance(raw_passed, bool):
        verification["status"] = "invalid"
        verification["error"] = "verification record passed must be a boolean"
        return manifest, verification, validation
    passed = raw_passed
    verification["passed"] = passed
    verification["status"] = "pass" if passed else "fail"
    for key in ("analysis_rows", "target_rows", "control_rows"):
        if type(payload.get(key)) is int and payload[key] >= 0:
            verification[key] = payload[key]
    return manifest, verification, validation


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_verified_manifest(manifest: dict[str, Any], out_dir: Path) -> None:
    """Require current output files to match the manifest before bundling."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("--require-verified needs a complete manifest artifact inventory")
    required = {"verification.json", *VALIDATION_FILES}
    seen: set[str] = set()
    output_root = out_dir.resolve()
    for index, record in enumerate(artifacts, 1):
        if not isinstance(record, dict):
            raise TypeError(f"manifest artifact {index} is not an object")
        relative = record.get("relative_path")
        if (
            record.get("path_base") != "output_dir"
            or not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
        ):
            raise ValueError(f"manifest artifact {index} has an unsafe relative path")
        posix_path = PurePosixPath(relative)
        if (
            posix_path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in posix_path.parts)
        ):
            raise ValueError(f"manifest artifact has an unsafe relative path: {relative}")
        if relative in seen:
            raise ValueError(f"manifest contains a duplicate artifact: {relative}")
        seen.add(relative)
        candidate = out_dir.joinpath(*posix_path.parts)
        try:
            candidate.resolve().relative_to(output_root)
        except ValueError as exc:
            raise ValueError(f"manifest artifact escapes output directory: {relative}") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"current manifest artifact is missing or symlinked: {relative}")
        expected_bytes = record.get("bytes")
        expected_hash = record.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 0:
            raise ValueError(f"manifest artifact has an invalid byte count: {relative}")
        if not _is_sha256(expected_hash):
            raise ValueError(f"manifest artifact has an invalid SHA-256: {relative}")
        if candidate.stat().st_size != expected_bytes:
            raise ValueError(f"current output byte count differs from manifest: {relative}")
        if _sha256(candidate) != expected_hash:
            raise ValueError(f"current output hash differs from manifest: {relative}")
    missing = sorted(required - seen)
    if missing:
        raise ValueError(
            "manifest is missing required verified artifact records: " + ", ".join(missing)
        )


def _validate_validation_records(
    source_validation: object,
    artifact_records: dict[str, dict[str, Any]],
    require_verified: bool,
) -> None:
    """Validate schema and reproducibility provenance in a bundle."""
    if not isinstance(source_validation, dict):
        if require_verified:
            raise ValueError("verified bundle is missing validation provenance records")
        raise TypeError("bundle source_validation must be an object")
    if set(source_validation) != set(VALIDATION_FILES):
        raise ValueError("bundle source_validation must include schema and reproducibility records")
    records: list[dict[str, Any]] = []
    for name in VALIDATION_FILES:
        record = source_validation[name]
        if not isinstance(record, dict):
            raise TypeError(f"bundle source_validation record must be an object: {name}")
        if record.get("path") != name:
            raise ValueError(f"bundle source_validation path must be {name}")
        if not isinstance(record.get("available"), bool):
            raise TypeError(f"bundle source_validation availability must be a boolean: {name}")
        if not isinstance(record.get("included"), bool):
            raise TypeError(f"bundle source_validation inclusion must be a boolean: {name}")
        available = record["available"]
        included = record["included"]
        digest = record.get("sha256")
        if available:
            if not _is_sha256(digest):
                raise ValueError(f"bundle source_validation has an invalid SHA-256: {name}")
        elif digest is not None:
            raise ValueError(f"bundle source_validation has a hash without a source: {name}")
        if included and not available:
            raise ValueError(f"bundle source_validation includes an unavailable source: {name}")
        artifact = artifact_records.get(name)
        if included != (artifact is not None):
            state = "missing" if included else "unexpected"
            raise ValueError(f"bundle source_validation member is {state}: {name}")
        if artifact is not None and digest != artifact.get("sha256"):
            raise ValueError(f"bundle source_validation hash differs for {name}")
        status = record.get("status")
        if not isinstance(status, str) or status not in VERIFICATION_STATUSES:
            raise ValueError(f"bundle source_validation has an unsupported status: {name}")
        passed = record.get("passed")
        if status in {"pass", "fail"}:
            if not isinstance(passed, bool) or passed is not (status == "pass"):
                raise ValueError(f"bundle source_validation status disagrees with passed: {name}")
        elif passed is not None:
            raise ValueError(f"bundle source_validation passed must be null when unavailable: {name}")
        if available and status == "not_provided":
            raise ValueError(f"bundle source_validation cannot be not_provided when available: {name}")
        if not available and status != "not_provided":
            raise ValueError(f"bundle source_validation status requires an available record: {name}")
        for key in ("artifact_count", "schema_version"):
            if key in record and (type(record[key]) is not int or record[key] < 0):
                raise ValueError(f"bundle source_validation has an invalid {key}: {name}")
        records.append(record)
    if require_verified and any(
        not record["available"]
        or not record["included"]
        or record["status"] != "pass"
        or record["passed"] is not True
        for record in records
    ):
        raise ValueError("verified bundle validation provenance does not contain passing included records")


def _validate_provenance(
    manifest: dict[str, Any], artifact_records: dict[str, dict[str, Any]]
) -> None:
    """Validate the optional source provenance carried by new v1 bundles."""
    require_verified = manifest.get("require_verified", False)
    if not isinstance(require_verified, bool):
        raise TypeError("bundle.json require_verified must be a boolean")
    present = [field for field in PROVENANCE_FIELDS if field in manifest]
    if not present:
        if require_verified:
            raise ValueError("verified bundle is missing provenance records")
        return
    if len(present) != len(PROVENANCE_FIELDS):
        raise ValueError("bundle provenance records must include both source records")

    records: dict[str, dict[str, Any]] = {}
    for field, expected_path in zip(PROVENANCE_FIELDS, ("manifest.json", "verification.json")):
        record = manifest.get(field)
        if not isinstance(record, dict):
            raise TypeError(f"bundle.json {field} provenance must be an object")
        if record.get("path") != expected_path:
            raise ValueError(f"bundle {field} provenance path must be {expected_path}")
        if not isinstance(record.get("available"), bool):
            raise TypeError(f"bundle {field} provenance availability must be a boolean")
        if not isinstance(record.get("included"), bool):
            raise TypeError(f"bundle {field} provenance inclusion must be a boolean")
        available = record["available"]
        included = record["included"]
        digest = record.get("sha256")
        if available:
            if not _is_sha256(digest):
                raise ValueError(f"bundle {field} provenance has an invalid SHA-256")
        elif digest is not None:
            raise ValueError(f"bundle {field} provenance has a hash without a source")
        if included and not available:
            raise ValueError(f"bundle {field} provenance includes an unavailable source")
        artifact = artifact_records.get(expected_path)
        if included != (artifact is not None):
            state = "missing" if included else "unexpected"
            raise ValueError(f"bundle {field} provenance member is {state}: {expected_path}")
        if artifact is not None and digest != artifact.get("sha256"):
            raise ValueError(f"bundle provenance hash differs for {expected_path}")
        records[field] = record

    source_manifest = records["source_manifest"]
    if not isinstance(source_manifest.get("path_contract"), dict):
        raise TypeError("bundle source_manifest path_contract must be an object")

    source_verification = records["source_verification"]
    status = source_verification.get("status")
    if not isinstance(status, str) or status not in VERIFICATION_STATUSES:
        raise ValueError("bundle source_verification has an unsupported status")
    passed = source_verification.get("passed")
    if status in {"pass", "fail"}:
        if not isinstance(passed, bool) or passed is not (status == "pass"):
            raise ValueError("bundle source_verification status disagrees with passed")
    elif passed is not None:
        raise ValueError("bundle source_verification passed must be null for an unavailable record")
    if source_verification["available"] and status == "not_provided":
        raise ValueError("bundle source_verification cannot be not_provided when available")
    if not source_verification["available"] and status != "not_provided":
        raise ValueError("bundle source_verification status requires an available record")
    for key in ("analysis_rows", "target_rows", "control_rows"):
        if key in source_verification and (
            type(source_verification[key]) is not int or source_verification[key] < 0
        ):
            raise ValueError(f"bundle source_verification has an invalid {key} count")

    if "source_validation" in manifest or require_verified:
        _validate_validation_records(
            manifest.get("source_validation"),
            artifact_records,
            require_verified,
        )

    if require_verified and (
        not source_manifest["available"]
        or not source_manifest["included"]
        or not source_verification["available"]
        or not source_verification["included"]
        or status != "pass"
        or passed is not True
    ):
        raise ValueError("verified bundle provenance does not contain a passing included record")


def _matches_exclude(relative_path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in patterns)


def _collect_files(
    out_dir: Path,
    archive: Path,
    exclude_patterns: Iterable[str],
) -> list[tuple[Path, str]]:
    """Collect regular output files with safe POSIX archive names."""
    archive_resolved = archive.resolve()
    files: list[tuple[Path, str]] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Output contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(out_dir).as_posix()
        if path.resolve() == archive_resolved:
            continue
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        if _matches_exclude(relative, exclude_patterns):
            continue
        files.append((path, relative))
    return files


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    return info


def _write_member(archive: zipfile.ZipFile, path: Path, name: str) -> None:
    with path.open("rb") as source, archive.open(_zip_info(name), "w") as target:
        while chunk := source.read(1 << 20):
            target.write(chunk)


def _safe_member_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
    ):
        raise ValueError("bundle artifact paths must be relative POSIX paths")
    path = PurePosixPath(value)
    if (
        value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.endswith(":") for part in path.parts)
    ):
        raise ValueError(f"unsafe bundle artifact path: {value!r}")
    return value


def verify_bundle(archive_path: str | Path) -> dict[str, Any]:
    """Validate a bundle manifest and every payload hash without extracting it."""
    archive_input = reject_symlink_path(archive_path, label="Bundle archive input")
    reject_symlink_root(
        archive_input.parent,
        label="Bundle archive input parent directory",
    )
    archive_path = archive_input.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Bundle archive does not exist: {archive_path}")
    with zipfile.ZipFile(archive_path) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("bundle contains duplicate ZIP members")
        if BUNDLE_MANIFEST_NAME not in names:
            raise ValueError("bundle is missing bundle.json")
        try:
            manifest = json.loads(source.read(BUNDLE_MANIFEST_NAME))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"bundle.json is invalid: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("contract") != BUNDLE_CONTRACT:
            raise ValueError("bundle.json has an unsupported contract")
        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise TypeError("bundle.json artifacts must be a list")
        expected_names = {BUNDLE_MANIFEST_NAME}
        artifact_records: dict[str, dict[str, Any]] = {}
        artifact_bytes = 0
        for item in raw_artifacts:
            if not isinstance(item, dict):
                raise TypeError("bundle artifact records must be objects")
            name = _safe_member_name(item.get("path"))
            if name == BUNDLE_MANIFEST_NAME or name in expected_names:
                raise ValueError(f"bundle contains a duplicate or reserved artifact: {name}")
            declared_bytes = item.get("bytes")
            declared_sha256 = item.get("sha256")
            if not isinstance(declared_bytes, int) or declared_bytes < 0:
                raise ValueError(f"bundle artifact has invalid byte count: {name}")
            if (
                not isinstance(declared_sha256, str)
                or len(declared_sha256) != 64
                or any(character not in "0123456789abcdef" for character in declared_sha256)
            ):
                raise ValueError(f"bundle artifact has invalid SHA-256: {name}")
            if name not in names:
                raise ValueError(f"bundle is missing artifact: {name}")
            info = source.getinfo(name)
            if info.file_size != declared_bytes:
                raise ValueError(f"bundle byte count differs for {name}")
            digest = hashlib.sha256()
            actual_bytes = 0
            with source.open(name) as payload:
                while chunk := payload.read(1 << 20):
                    digest.update(chunk)
                    actual_bytes += len(chunk)
            if actual_bytes != declared_bytes or digest.hexdigest() != declared_sha256:
                raise ValueError(f"bundle hash differs for {name}")
            expected_names.add(name)
            artifact_records[name] = item
            artifact_bytes += actual_bytes
        if set(names) != expected_names:
            extras = sorted(set(names) - expected_names)
            raise ValueError("bundle contains unexpected members: " + ", ".join(extras))
        if manifest.get("artifact_count") != len(raw_artifacts):
            raise ValueError("bundle artifact_count does not match artifacts")
        if manifest.get("artifact_bytes") != artifact_bytes:
            raise ValueError("bundle artifact_bytes does not match artifacts")
        _validate_provenance(manifest, artifact_records)
    result = {
        "contract": BUNDLE_CONTRACT,
        "archive": str(archive_path),
        "artifact_count": len(raw_artifacts),
        "artifact_bytes": artifact_bytes,
        "archive_bytes": archive_path.stat().st_size,
        "passed": True,
    }
    result["require_verified"] = manifest.get("require_verified", False)
    for field in PROVENANCE_FIELDS:
        if field in manifest:
            result[field] = manifest[field]
    if "source_validation" in manifest:
        result["source_validation"] = manifest["source_validation"]
    return result


def extract_bundle(archive_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Verify a bundle, then safely extract it into a new destination directory."""
    verification = verify_bundle(archive_path)
    destination_input = reject_symlink_path(
        destination,
        label="Extraction destination",
    )
    destination_path = destination_input.resolve()
    if destination_path.exists():
        raise FileExistsError(
            f"Extraction destination already exists; choose a new directory: {destination_path}"
        )
    reject_symlink_root(
        destination_input.parent,
        label="Extraction parent directory",
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(
        tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent)
    )
    try:
        with zipfile.ZipFile(Path(archive_path).expanduser().resolve()) as source:
            for name in source.namelist():
                safe_name = _safe_member_name(name)
                target = temporary_path.joinpath(*PurePosixPath(safe_name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(name) as payload, target.open("xb") as destination_file:
                    shutil.copyfileobj(payload, destination_file, length=1 << 20)
        os.replace(temporary_path, destination_path)
        temporary_path = None  # type: ignore[assignment]
    finally:
        if temporary_path is not None:
            shutil.rmtree(temporary_path, ignore_errors=True)
    result = dict(verification)
    result["destination"] = str(destination_path)
    result["extracted"] = True
    return result


def build_bundle(
    out_dir: str | Path,
    archive_path: str | Path,
    *,
    excludes: Iterable[str] = (),
    require_verified: bool = False,
) -> dict[str, Any]:
    """Write a deterministic ZIP bundle and return its machine-readable summary."""
    output_input = reject_symlink_root(Path(out_dir).expanduser(), label="Output directory")
    output = output_input.resolve()
    if not output.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output}")
    output_symlinks = path_symlink_paths(output)
    if output_symlinks:
        raise ValueError(f"Output contains a symlink: {output / output_symlinks[0]}")
    archive_input = reject_output_file(archive_path, label="Bundle archive")
    archive = archive_input.resolve()
    if archive == output or archive.is_dir():
        raise ValueError("Bundle archive must be a file path outside the output directory")
    try:
        archive.relative_to(output)
    except ValueError:
        pass
    else:
        raise ValueError("Bundle archive must be outside the output directory")

    source_manifest, source_verification, source_validation = _provenance_records(output)
    if require_verified and not source_manifest["available"]:
        raise ValueError("--require-verified needs output/manifest.json")
    if require_verified and source_verification["status"] != "pass":
        raise ValueError(
            "--require-verified needs a passing output/verification.json record"
        )
    if require_verified:
        missing_validation = [
            name for name, record in source_validation.items() if record["status"] != "pass"
        ]
        if missing_validation:
            raise ValueError(
                "--require-verified needs passing validation records: "
                + ", ".join(missing_validation)
            )
        manifest_path = output / "manifest.json"
        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"--require-verified needs a valid output/manifest.json: {exc}") from exc
        if not isinstance(manifest_payload, dict):
            raise ValueError("--require-verified needs output/manifest.json to be an object")
        _validate_verified_manifest(manifest_payload, output)

    patterns = tuple(DEFAULT_EXCLUDES) + tuple(str(pattern) for pattern in excludes)
    protected_provenance = ("manifest.json", "verification.json", *VALIDATION_FILES)
    if require_verified and any(
        _matches_exclude(name, patterns) for name in protected_provenance
    ):
        raise ValueError(
            "--require-verified cannot exclude manifest, verification, schema, or reproducibility records"
        )
    files = _collect_files(output, archive, patterns)
    if not files:
        raise ValueError(f"No bundleable files found in {output}")
    artifact_paths = {relative for _, relative in files}
    source_manifest["included"] = source_manifest["path"] in artifact_paths
    source_verification["included"] = source_verification["path"] in artifact_paths
    for name, record in source_validation.items():
        record["included"] = name in artifact_paths

    artifacts = [
        {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path, relative in files
    ]
    bundle_manifest: dict[str, Any] = {
        "contract": BUNDLE_CONTRACT,
        "package_version": package_version(),
        "source_output_name": output.name,
        "manifest_path_contract": _manifest_contract(output),
        "source_manifest": source_manifest,
        "source_verification": source_verification,
        "source_validation": source_validation,
        "require_verified": require_verified,
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(int(item["bytes"]) for item in artifacts),
        "artifacts": artifacts,
        "excluded_patterns": list(patterns),
    }
    manifest_bytes = (
        json.dumps(bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )

    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
        with zipfile.ZipFile(
            temporary_name,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as destination:
            destination.writestr(_zip_info(BUNDLE_MANIFEST_NAME), manifest_bytes)
            for path, relative in files:
                _write_member(destination, path, relative)
        os.replace(temporary_name, archive)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass

    result = dict(bundle_manifest)
    result["archive"] = str(archive)
    result["archive_bytes"] = archive.stat().st_size
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--out-dir", default=None, help="generated output directory; defaults to output/")
    parser.add_argument(
        "--archive",
        default=None,
        help="ZIP destination (default: beside the output directory as <name>.bundle.zip)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify",
        metavar="ARCHIVE",
        default=None,
        help="verify an existing bundle archive without extracting it",
    )
    mode.add_argument(
        "--extract",
        metavar="ARCHIVE",
        default=None,
        help="verify and safely extract an existing bundle archive",
    )
    parser.add_argument(
        "--destination",
        default=None,
        help="new directory for --extract (required; it must not already exist)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="relative output path or glob to exclude; may be repeated",
    )
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="fail unless manifest, verification, schema, and reproducibility records all pass",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the operation result as machine-readable JSON",
    )
    args = parser.parse_args(argv)
    if args.verify:
        try:
            result = verify_bundle(args.verify)
        except (FileNotFoundError, OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"[bundle] PASS {result['archive']} ({result['artifact_count']:,} artifacts, "
                f"{result['artifact_bytes']:,} payload bytes)"
            )
        return
    if args.extract:
        if not args.destination:
            parser.error("--extract requires --destination")
        try:
            result = extract_bundle(args.extract, args.destination)
        except (FileExistsError, FileNotFoundError, OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"[bundle] extracted {result['artifact_count']:,} artifacts to "
                f"{result['destination']} after verification"
            )
        return
    output = project_path(args.out_dir, "output").expanduser()
    archive = (
        project_path(args.archive, str(output.parent / f"{output.name}.bundle.zip")).expanduser()
        if args.archive
        else output.parent / f"{output.name}.bundle.zip"
    )
    try:
        result = build_bundle(
            output,
            archive,
            excludes=args.exclude,
            require_verified=args.require_verified,
        )
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"[bundle] wrote {result['archive']} ({result['artifact_count']:,} artifacts, "
            f"{result['artifact_bytes']:,} payload bytes; {result['manifest_path_contract']['status']} manifest)"
        )


if __name__ == "__main__":
    main()
