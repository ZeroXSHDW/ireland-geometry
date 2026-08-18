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
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FRESHNESS_CONTRACT = "ireland-geometry.freshness.v1"


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
    if candidate.is_file():
        return sha256_file(candidate)
    if not candidate.is_dir():
        return None
    digest = hashlib.sha256()
    for child in sorted(item for item in candidate.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(candidate)).encode("utf-8"))
        file_hash = sha256_file(child)
        if file_hash:
            digest.update(file_hash.encode("ascii"))
    return digest.hexdigest()


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
            p = Path(row["path"]).expanduser().resolve()
            if row.get("kind"):
                row["source_kind"] = row["kind"]
            row["path"] = str(p)
            relative = manifest_relative_path(p, manifest_root)
            row["path_base"] = "project_root" if relative is not None else "external"
            if relative is not None:
                row["relative_path"] = relative
            row["sha256"] = sha256_path(p)
            row["bytes"] = p.stat().st_size if p.exists() and p.is_file() else None
            row["kind"] = "directory" if p.is_dir() else "file"
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
    artifact_paths = sorted(out_dir.iterdir()) if out_dir.exists() else []
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
