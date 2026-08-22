"""Shared runtime helpers for portable, reproducible pipeline execution."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FRESHNESS_CONTRACT = "ireland-geometry.freshness.v1"
SOURCE_ALIGNMENT_CONTRACT = "ireland-geometry.source-alignment.v1"


def default_project_root() -> Path:
    """Resolve the user project root without confusing it with a wheel install."""
    configured = os.environ.get("IRELAND_GEOMETRY_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # Editable checkouts and direct source execution retain the historical
    # repository-relative behavior. A non-editable wheel has no project data
    # beside its modules, so relative paths belong to the caller's directory.
    if (PACKAGE_ROOT / "pyproject.toml").is_file():
        return PACKAGE_ROOT
    return Path.cwd().resolve()


ROOT = default_project_root()
SCHEMA_VERSION = 3
# Diagnostic reports contain timestamps and are generated outside the pipeline;
# they remain available on disk but must not invalidate the reproducible output
# manifest when a user runs Doctor after a build.
MANIFEST_EXCLUDED_OUTPUTS = frozenset({"doctor.json"})
PACKAGE_NAME = "ireland-geometry-scan"
RUNTIME_DISTRIBUTIONS = (
    "numpy",
    "shapely",
    "requests",
    "osmium",
    "duckdb",
    "pyarrow",
    "laspy",
    "rasterio",
)


def reject_symlink_root(path: str | Path, *, label: str = "output directory") -> Path:
    """Validate an output root before any writer creates or replaces files."""
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    if candidate.exists() and not candidate.is_dir():
        raise ValueError(f"{label} must be a directory: {candidate}")
    return candidate


def reject_symlink_path(path: str | Path, *, label: str = "path") -> Path:
    """Reject a symlink at an individual input or cache-file path."""
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    return candidate


def reject_symlink_tree(path: str | Path, *, label: str = "directory") -> Path:
    """Reject symlinks anywhere below an existing directory boundary."""
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    if not candidate.exists():
        return candidate
    links = path_symlink_paths(candidate)
    if links:
        displayed = ", ".join(links[:8])
        if len(links) > 8:
            displayed += f", … (+{len(links) - 8} more)"
        raise ValueError(f"{label} contains symlink(s): {displayed}")
    return candidate


def _is_canonical_macos_tmp_alias(path: str | Path) -> bool:
    """Allow macOS's stable ``/tmp`` alias when it is the system target."""
    candidate = Path(path).expanduser()
    return (
        platform.system() == "Darwin"
        and candidate == Path("/tmp")
        and candidate.is_symlink()
        and candidate.resolve() == Path("/private/tmp")
    )


def package_version() -> str:
    """Return the declared project version in source or the installed wheel version."""
    source_version = _source_project_version()
    if source_version:
        return source_version
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


def _source_project_version() -> str | None:
    """Read a simple static ``project.version`` from a source checkout."""
    path = PACKAGE_ROOT / "pyproject.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    in_project = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if not in_project or not stripped.startswith("version"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if key.strip() != "version" or not separator:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1] or None
    return None


def runtime_signature() -> dict[str, Any]:
    """Return runtime details that can affect generated artifacts."""
    distributions: dict[str, str | None] = {}
    for name in RUNTIME_DISTRIBUTIONS:
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "cache_tag": sys.implementation.cache_tag,
        "distributions": distributions,
    }


def project_path(value: str | Path | None, default: str | Path) -> Path:
    """Resolve a CLI path relative to the project root unless absolute."""
    candidate = Path(value) if value is not None else Path(default)
    return candidate if candidate.is_absolute() else ROOT / candidate


def project_output_path(value: str | Path | None, default: str | Path = "output") -> Path:
    """Resolve a CLI output root and fail clearly before any stage work begins."""
    try:
        return reject_symlink_root(project_path(value, default))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def project_output_tree_path(value: str | Path | None, default: str | Path = "output") -> Path:
    """Resolve a read/write output root and reject nested symlinks."""
    output_root = project_output_path(value, default)
    try:
        return reject_symlink_tree(output_root, label="output directory")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def reject_output_file(path: str | Path, *, label: str = "output file") -> Path:
    """Validate an output file and its existing parent directory."""
    candidate = Path(path).expanduser()
    reject_symlink_path(candidate, label=label)
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"{label} must be a file: {candidate}")
    if not _is_canonical_macos_tmp_alias(candidate.parent):
        reject_symlink_root(candidate.parent, label=f"{label} parent directory")
    return candidate


def project_output_file_path(
    value: str | Path | None,
    default: str | Path = "output.json",
    *,
    label: str = "output file",
) -> Path:
    """Resolve an output file and reject unsafe files or parent directories."""
    try:
        return reject_output_file(project_path(value, default), label=label)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def project_data_path(value: str | Path | None, default: str | Path = "data") -> Path:
    """Resolve a CLI data root and fail clearly before reading or writing it."""
    try:
        return reject_symlink_root(project_path(value, default), label="data directory")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def project_data_tree_path(value: str | Path | None, default: str | Path = "data") -> Path:
    """Resolve a direct-analysis data root and reject nested symlinks."""
    data_root = project_data_path(value, default)
    try:
        return reject_symlink_tree(data_root, label="data directory")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def project_input_path(
    value: str | Path | None,
    default: str | Path,
    *,
    label: str = "input path",
) -> Path:
    """Resolve a direct input and reject symlinked files or nested directories."""
    candidate = project_path(value, default)
    try:
        reject_symlink_path(candidate, label=label)
        if candidate.exists() and candidate.is_dir():
            reject_symlink_tree(candidate, label=label)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return candidate


def packaged_analysis_plan_path(package_root: str | Path | None = None) -> Path:
    """Return the immutable analysis plan shipped with the package."""
    base = Path(package_root).expanduser().resolve() if package_root else PACKAGE_ROOT
    return base / "schemas" / "analysis_plan.json"


def default_analysis_plan_path(
    project_root: str | Path | None = None,
    *,
    package_root: str | Path | None = None,
) -> Path:
    """Prefer a project override and fall back to the packaged plan."""
    root = Path(project_root).expanduser().resolve() if project_root else ROOT
    project_plan = root / "analysis_plan.json"
    if project_plan.is_file():
        return project_plan
    packaged_plan = packaged_analysis_plan_path(package_root)
    return packaged_plan if packaged_plan.is_file() else project_plan


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def path_modified_at(path: str | Path) -> str | None:
    """Return a UTC modification timestamp for a cached source path."""
    try:
        modified = Path(path).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, timezone.utc).replace(microsecond=0).isoformat()


def path_age_seconds(path: str | Path, *, observed_at: str | None = None) -> float | None:
    """Return the non-negative age of a source path at an optional UTC observation time."""
    try:
        modified = Path(path).stat().st_mtime
    except OSError:
        return None
    try:
        observed = (
            datetime.fromisoformat(observed_at).timestamp()
            if observed_at
            else datetime.now(timezone.utc).timestamp()
        )
    except ValueError:
        observed = datetime.now(timezone.utc).timestamp()
    return round(max(0.0, observed - modified), 3)


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: str | Path) -> str | None:
    """Hash a file or a directory tree with relative filenames included."""
    candidate = Path(path)
    if candidate.is_symlink():
        return None
    if candidate.is_file():
        return sha256_file(candidate)
    if not candidate.is_dir():
        return None
    if path_symlink_paths(candidate):
        return None
    digest = hashlib.sha256()
    for child in sorted(item for item in candidate.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(candidate)).encode("utf-8"))
        file_hash = sha256_file(child)
        if file_hash:
            digest.update(file_hash.encode("ascii"))
    return digest.hexdigest()


def path_symlink_paths(path: str | Path) -> list[str]:
    """Return deterministic relative symlink paths in a file or directory tree."""
    candidate = Path(path)
    if candidate.is_symlink():
        return ["."]
    if not candidate.is_dir():
        return []

    links: list[str] = []
    for directory, dirnames, filenames in os.walk(
        candidate, topdown=True, followlinks=False
    ):
        directory_path = Path(directory)
        dirnames.sort()
        filenames.sort()
        linked_dirs = {
            name for name in dirnames if (directory_path / name).is_symlink()
        }
        for name in sorted(linked_dirs):
            links.append((directory_path / name).relative_to(candidate).as_posix())
        # Do not descend into linked directories. Their link itself is the
        # boundary violation and following them could escape the source tree.
        dirnames[:] = [name for name in dirnames if name not in linked_dirs]
        for name in filenames:
            child = directory_path / name
            if child.is_symlink():
                links.append(child.relative_to(candidate).as_posix())
    return sorted(links)


def path_metadata_sha256(path: str | Path) -> str | None:
    """Hash source paths and metadata without reading regular-file contents."""
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.exists():
        return None

    entries: list[tuple[str, str, int, int]] = []

    def add_entry(entry_path: Path, kind: str) -> None:
        stat = entry_path.stat()
        relative = "." if entry_path == candidate else entry_path.relative_to(candidate).as_posix()
        size = stat.st_size if kind == "file" else 0
        entries.append((relative, kind, size, stat.st_mtime_ns))

    try:
        if candidate.is_file():
            add_entry(candidate, "file")
        elif candidate.is_dir():
            add_entry(candidate, "directory")
            walk_errors: list[OSError] = []

            def onerror(error: OSError) -> None:
                walk_errors.append(error)

            for directory, dirnames, filenames in os.walk(
                candidate,
                topdown=True,
                followlinks=False,
                onerror=onerror,
            ):
                directory_path = Path(directory)
                dirnames.sort()
                filenames.sort()
                for name in dirnames:
                    child = directory_path / name
                    if child.is_symlink():
                        return None
                    add_entry(child, "directory")
                for name in filenames:
                    child = directory_path / name
                    if child.is_symlink():
                        return None
                    add_entry(child, "file")
            if walk_errors:
                return None
        else:
            return None
    except OSError:
        return None

    digest = hashlib.sha256()
    for relative, kind, size, modified_ns in sorted(entries):
        digest.update(f"{kind}\0{relative}\0{size}\0{modified_ns}\n".encode())
    return digest.hexdigest()


def resolve_manifest_source_path(
    source: dict[str, Any],
    manifest: dict[str, Any],
    project_root: Path,
    data_root: Path,
    out_dir: Path,
) -> Path | None:
    """Resolve a source, preferring current portable paths over legacy absolutes."""
    candidates: list[Path] = []

    def add(candidate: Path) -> None:
        candidates.append(candidate.expanduser())

    relative = source.get("relative_path")
    path_base = source.get("path_base")
    if isinstance(relative, str) and relative.strip():
        if path_base == "project_root":
            # The current checkout is authoritative after a project move. The
            # other current-root candidates support custom data/output paths;
            # the recorded root is retained only as a historical fallback.
            for base in (project_root, out_dir.parent, data_root.parent):
                add(base / relative)
            recorded_root = manifest.get("project_root")
            if isinstance(recorded_root, str) and recorded_root.strip():
                add(Path(recorded_root).expanduser() / relative)
        elif path_base == "data_root":
            add(data_root / relative)
        else:
            add(out_dir.parent / relative)

    raw_path = source.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        raw = Path(raw_path).expanduser()
        if raw.is_absolute():
            add(raw)
        else:
            for base in (project_root, out_dir.parent, data_root.parent):
                add(base / raw)

    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.expanduser()
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized.exists():
            return normalized
    return None


def _manifest_source_path(
    source: dict[str, Any],
    manifest: dict[str, Any],
    project_root: Path,
    data_root: Path,
    out_dir: Path,
) -> Path | None:
    """Backward-compatible private alias for the shared source resolver."""
    return resolve_manifest_source_path(source, manifest, project_root, data_root, out_dir)


def manifest_source_alignment(
    manifest: dict[str, Any],
    *,
    project_root: str | Path,
    data_root: str | Path,
    out_dir: str | Path,
    check_hashes: bool = False,
) -> dict[str, Any]:
    """Check current source metadata against the output manifest without rebuilding."""
    result: dict[str, Any] = {
        "contract": SOURCE_ALIGNMENT_CONTRACT,
        "status": "not_provided",
        "passed": False,
        "mode": "hash" if check_hashes else "metadata",
        "checked_count": 0,
        "unavailable_count": 0,
        "unhashed_count": 0,
        "hash_checked_count": 0,
        "metadata_checked_count": 0,
        "symlink_count": 0,
        "errors": [],
        "sources": [],
    }
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        return result

    root = Path(project_root).expanduser().resolve()
    data = Path(data_root).expanduser().resolve()
    output = Path(out_dir).expanduser().resolve()
    errors: list[str] = []
    available_count = 0
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict):
            errors.append(f"manifest source {index} is not an object")
            continue
        source_kind = source.get("source_kind") or source.get("kind") or f"source_{index}"
        source_path = _manifest_source_path(source, manifest, root, data, output)
        expected_hash = source.get("sha256")
        expected_metadata_hash = source.get("metadata_sha256")
        expected_bytes = source.get("bytes")
        expected_modified = source.get("modified_at")
        record: dict[str, Any] = {
            "source_kind": str(source_kind),
            "path": str(source_path) if source_path is not None else source.get("path"),
            "available": source_path is not None,
            "status": "not_provided",
            "hash_checked": False,
            "metadata_checked": False,
            "expected_modified_at": expected_modified,
            "expected_metadata_sha256": expected_metadata_hash,
            "actual_modified_at": None,
            "actual_metadata_sha256": None,
            "expected_bytes": expected_bytes,
            "actual_bytes": None,
            "symlink_paths": [],
            "error": None,
        }
        if source_path is None:
            if expected_hash:
                message = f"manifest source is unavailable: {source.get('path', source_kind)}"
                errors.append(message)
                record["status"] = "fail"
                record["error"] = message
            else:
                result["unavailable_count"] += 1
            result["sources"].append(record)
            continue

        available_count += 1
        current_symlinks = path_symlink_paths(source_path)
        recorded_symlinks = source.get("symlink_paths")
        if not isinstance(recorded_symlinks, list):
            recorded_symlinks = []
        symlinks = sorted(
            {
                str(value)
                for value in (*current_symlinks, *recorded_symlinks)
                if isinstance(value, str) and value
            }
        )
        record["symlink_paths"] = symlinks
        result["symlink_count"] += len(symlinks)
        if symlinks:
            displayed = ", ".join(symlinks)
            if current_symlinks:
                message = f"manifest source contains symlink(s): {source_path} ({displayed})"
            else:
                message = f"manifest source was recorded with symlink(s): {source_path} ({displayed})"
            errors.append(message)
            record["status"] = "fail"
            record["error"] = message
            result["sources"].append(record)
            continue
        try:
            actual_modified = path_modified_at(source_path)
            actual_bytes = source_path.stat().st_size if source_path.is_file() else None
        except OSError as exc:
            message = f"manifest source could not be inspected: {source_path}: {exc}"
            errors.append(message)
            record["status"] = "fail"
            record["error"] = message
            result["sources"].append(record)
            continue
        record["actual_modified_at"] = actual_modified
        record["actual_bytes"] = actual_bytes
        source_errors: list[str] = []
        if expected_metadata_hash is not None:
            actual_metadata_hash = path_metadata_sha256(source_path)
            record["metadata_checked"] = True
            record["actual_metadata_sha256"] = actual_metadata_hash
            result["metadata_checked_count"] += 1
            if actual_metadata_hash != expected_metadata_hash:
                source_errors.append(
                    f"manifest source metadata hash mismatch: {source_path}"
                )
        if expected_modified is not None and actual_modified != expected_modified:
            source_errors.append(
                f"manifest source modified_at mismatch: {source_path}"
            )
        if expected_bytes is not None and actual_bytes != expected_bytes:
            source_errors.append(f"manifest source byte-size mismatch: {source_path}")
        if expected_hash is None:
            result["unhashed_count"] += 1
        elif check_hashes:
            actual_hash = sha256_path(source_path)
            record["hash_checked"] = True
            result["hash_checked_count"] += 1
            if actual_hash != expected_hash:
                source_errors.append(f"manifest source hash mismatch: {source_path}")
        if source_errors:
            errors.extend(source_errors)
            record["status"] = "fail"
            record["error"] = "; ".join(source_errors)
        else:
            result["checked_count"] += 1
            record["status"] = "pass" if expected_hash is not None else "unhashed"
        result["sources"].append(record)

    result["available_count"] = available_count
    result["errors"] = errors
    if errors:
        result["status"] = "fail"
    elif result["checked_count"]:
        result["status"] = "pass"
        result["passed"] = True
    return result


def manifest_relative_path(path: str | Path, root: str | Path) -> str | None:
    """Return a stable POSIX path when ``path`` is inside ``root``.

    Manifests keep their historical absolute ``path`` values for consumers
    that already depend on them, but also expose relative references so a
    copied project can resolve its own artifacts without the original machine
    path. ``None`` identifies an external source that cannot be made
    project-relative.
    """
    candidate = Path(path).expanduser().resolve()
    base = Path(root).expanduser().resolve()
    try:
        return candidate.relative_to(base).as_posix()
    except ValueError:
        return None


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Write a text file atomically in the destination directory."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Write bytes atomically in the destination directory."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def atomic_write_stream(
    path: str | Path,
    source: Any,
    *,
    chunk_size: int = 1024 * 1024,
    on_chunk: Callable[[int], None] | None = None,
) -> int:
    """Copy a binary stream or chunk iterable into a destination atomically."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    total = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            reader = getattr(source, "read", None)
            if callable(reader):
                chunks = iter(lambda: reader(chunk_size), b"")
            else:
                chunks = iter(source)
            for chunk in chunks:
                if not chunk:
                    continue
                fh.write(chunk)
                total += len(chunk)
                if on_chunk is not None:
                    on_chunk(total)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
        return total
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: str | Path, value: Any, **kwargs: Any) -> None:
    options = {"ensure_ascii": False, "separators": (",", ":")}
    options.update(kwargs)
    atomic_write_text(path, json.dumps(value, **options))


def atomic_write_csv(
    path: str | Path, fieldnames: Iterable[str], rows: Iterable[dict], **kwargs: Any
) -> None:
    """Serialize a CSV completely before replacing the destination."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(fieldnames), **kwargs)
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, stream.getvalue())


def git_revision(root: Path = ROOT) -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def git_dirty(root: Path = ROOT) -> bool | None:
    """Return whether tracked or untracked project files differ from HEAD."""
    try:
        result = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.strip())


def git_worktree_status(root: Path = ROOT, *, max_paths: int = 100) -> dict[str, Any]:
    """Return a bounded, JSON-safe inventory of the current Git worktree."""
    if type(max_paths) is not int or max_paths < 0:
        raise ValueError("max_paths must be a non-negative integer")
    try:
        result = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return {
            "available": False,
            "dirty": None,
            "path_count": None,
            "changed_path_count": None,
            "untracked_path_count": None,
            "paths": [],
            "paths_truncated": False,
        }

    entries: list[dict[str, str]] = []
    for line in result.splitlines():
        if not line.strip():
            continue
        status = line[:2] if len(line) >= 2 else "??"
        path = line[3:] if len(line) >= 4 else line[2:].lstrip()
        entry = {"status": status, "path": path}
        if status[:1] in {"R", "C"} and " -> " in path:
            original, _, destination = path.partition(" -> ")
            entry["path"] = destination
            entry["original_path"] = original
        entries.append(entry)
    entries.sort(key=lambda item: (item["path"], item["status"]))
    untracked_count = sum(item["status"] == "??" for item in entries)
    return {
        "available": True,
        "dirty": bool(entries),
        "path_count": len(entries),
        "changed_path_count": len(entries) - untracked_count,
        "untracked_path_count": untracked_count,
        "paths": entries[:max_paths],
        "paths_truncated": len(entries) > max_paths,
    }


def output_counts(out_dir: str | Path) -> dict[str, int]:
    """Collect lightweight row counts for the run manifest."""
    out = Path(out_dir)
    counts: dict[str, int] = {}
    for name in (
        "analysis_results.csv",
        "negative_controls.csv",
        "top_patterns.csv",
        "niah_join.csv",
        "niah_significance.csv",
        "niah_decades.csv",
        "niah_golden_angles.csv",
        "point_pattern.csv",
        "point_pattern_turns.csv",
        "roads_compare.csv",
        "architects.csv",
        "architects_binary.csv",
        "architects_evidence.csv",
        "matched_controls.csv",
        "matched_control_summary.csv",
        "matched_significance.csv",
        "hierarchical_model.csv",
        "building_parts.csv",
        "lidar_coverage.csv",
        "historical_validation.csv",
        "candidate_dossiers.csv",
        "historical_source_register.csv",
        "ripley.csv",
        "moran.csv",
        "county_permutation.csv",
        "road_proximity.csv",
        "spatial_covariates.csv",
        "spatial_covariates_summary.csv",
        "mapping_history.csv",
        "mapping_history_summary.csv",
        "data_quality.csv",
        "data_quality_duplicates.csv",
        "matched_controls_strict.csv",
        "matched_strict_summary.csv",
        "matched_strict_significance.csv",
        "matched_strict_balance.csv",
        "road_routing.csv",
        "road_routing_pairs.csv",
        "spatial_bootstrap.csv",
        "holdout_assignments.csv",
        "holdout_results.csv",
        "review_queue.csv",
        "review_calibration.csv",
        "review_confusion.csv",
    ):
        path = out / name
        if not path.exists():
            continue
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                counts[name] = max(0, sum(1 for _ in csv.reader(fh)) - 1)
        except (OSError, UnicodeError, csv.Error):
            continue
    for name in (
        "ireland_buildings.geojson",
        "report.html",
        "report_lazy.html",
        "report_data.json",
        "interpretation.json",
        "verification.json",
        "reproducibility.json",
        "analysis_results.jsonl",
        "analysis_results.parquet",
        "analysis.duckdb",
        "columnar_status.json",
        "data_quality_summary.json",
        "schema_validation.json",
        "stage_cache.json",
        "combined.json",
    ):
        path = out / name
        if path.exists():
            counts[f"{name}_bytes"] = path.stat().st_size
    analysis_rows = counts.get("analysis_results.csv")
    if analysis_rows is not None:
        for name in ("analysis_results.jsonl", "analysis_results.parquet", "analysis.duckdb"):
            if (out / name).is_file():
                counts[name] = analysis_rows
    geojson_path = out / "ireland_buildings.geojson"
    if geojson_path.is_file():
        try:
            payload = json.loads(geojson_path.read_text(encoding="utf-8"))
            features = payload.get("features") if isinstance(payload, dict) else None
            if isinstance(features, list):
                counts[geojson_path.name] = len(features)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return counts


def build_manifest(
    *,
    data_root: Path,
    out_dir: Path,
    parameters: dict[str, Any],
    sources: Iterable[dict[str, Any]] = (),
    project_root: Path | None = None,
) -> dict[str, Any]:
    manifest_root = Path(project_root).expanduser().resolve() if project_root else ROOT
    data_root_path = Path(data_root).expanduser().resolve()
    out_dir_path = Path(out_dir).expanduser().resolve()
    generated_at = utc_now()
    source_rows = []
    freshness_rows = []
    for src in sources:
        row = dict(src)
        if row.get("path"):
            raw_path = Path(row["path"]).expanduser()
            recorded_symlinks = path_symlink_paths(raw_path)
            p = raw_path.resolve()
            if row.get("kind"):
                row["source_kind"] = row["kind"]
            row["path"] = str(p)
            relative = manifest_relative_path(p, manifest_root)
            row["path_base"] = "project_root" if relative is not None else "external"
            if relative is not None:
                row["relative_path"] = relative
            row["sha256"] = sha256_path(p)
            if not recorded_symlinks:
                metadata_hash = path_metadata_sha256(p)
                if metadata_hash is not None:
                    row["metadata_sha256"] = metadata_hash
            row["bytes"] = p.stat().st_size if p.exists() and p.is_file() else None
            row["kind"] = "directory" if p.is_dir() else "file"
            if recorded_symlinks:
                row["symlink_paths"] = recorded_symlinks
            modified_at = path_modified_at(p)
            if modified_at is not None:
                row["modified_at"] = modified_at
                freshness = {
                    "source_kind": row.get("source_kind", row.get("kind")),
                    "path": str(p),
                    "path_base": row.get("path_base"),
                    "modified_at": modified_at,
                    "age_seconds": path_age_seconds(p, observed_at=generated_at),
                }
                if relative is not None:
                    freshness["relative_path"] = relative
                freshness_rows.append(freshness)
        source_rows.append(row)
    artifacts = []
    counts = output_counts(out_dir)
    artifact_paths = (
        sorted(
            path
            for path in out_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if out_dir.is_dir()
        else []
    )
    for path in artifact_paths:
        if not path.is_file() or path.name == "manifest.json" or path.name in MANIFEST_EXCLUDED_OUTPUTS:
            continue
        artifacts.append(
            {
                "path": str(path),
                "relative_path": manifest_relative_path(path, out_dir_path),
                "path_base": "output_dir",
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": counts.get(path.name),
            }
        )
    return {
        "manifest_version": 2,
        "schema_version": SCHEMA_VERSION,
        "package_version": package_version(),
        "runtime": runtime_signature(),
        "generated_at": generated_at,
        "project_root": str(manifest_root),
        "git_revision": git_revision(manifest_root),
        "git_dirty": git_dirty(manifest_root),
        "data_root": str(data_root_path),
        "output_dir": str(out_dir_path),
        "data_root_relative": manifest_relative_path(data_root_path, manifest_root),
        "output_dir_relative": manifest_relative_path(out_dir_path, manifest_root),
        "path_contract": {
            "version": 1,
            "relative_path_field": "relative_path",
            "source_base": "project_root",
            "artifact_base": "output_dir",
            "absolute_path_field": "path",
        },
        "source_freshness": {
            "contract": SOURCE_FRESHNESS_CONTRACT,
            "observed_at": generated_at,
            "sources": freshness_rows,
        },
        "parameters": parameters,
        "sources": source_rows,
        "counts": counts,
        "artifacts": artifacts,
    }
