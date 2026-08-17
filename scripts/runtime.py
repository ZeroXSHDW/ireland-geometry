"""Shared runtime helpers for portable, reproducible pipeline execution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 2


def project_path(value: str | Path | None, default: str | Path) -> Path:
    """Resolve a CLI path relative to the project root unless absolute."""
    candidate = Path(value) if value is not None else Path(default)
    return candidate if candidate.is_absolute() else ROOT / candidate


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


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


def output_counts(out_dir: str | Path) -> dict[str, int]:
    """Collect lightweight row counts for the run manifest."""
    out = Path(out_dir)
    counts: dict[str, int] = {}
    for name in (
        "analysis_results.csv",
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
    ):
        path = out / name
        if not path.exists():
            continue
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                counts[name] = max(0, sum(1 for _ in csv.reader(fh)) - 1)
        except (OSError, UnicodeError, csv.Error):
            continue
    for name in ("ireland_buildings.geojson", "report.html", "verification.json", "combined.json"):
        path = out / name
        if path.exists():
            counts[f"{name}_bytes"] = path.stat().st_size
    return counts


def build_manifest(
    *,
    data_root: Path,
    out_dir: Path,
    parameters: dict[str, Any],
    sources: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    source_rows = []
    for src in sources:
        row = dict(src)
        if row.get("path"):
            p = Path(row["path"])
            row["path"] = str(p)
            row["sha256"] = sha256_file(p)
            row["bytes"] = p.stat().st_size if p.exists() else None
        source_rows.append(row)
    artifacts = []
    counts = output_counts(out_dir)
    artifact_paths = sorted(out_dir.iterdir()) if out_dir.exists() else []
    for path in artifact_paths:
        if not path.is_file() or path.name == "manifest.json":
            continue
        artifacts.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": counts.get(path.name),
            }
        )
    return {
        "manifest_version": 2,
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "project_root": str(ROOT),
        "git_revision": git_revision(ROOT),
        "data_root": str(data_root),
        "output_dir": str(out_dir),
        "parameters": parameters,
        "sources": source_rows,
        "counts": counts,
        "artifacts": artifacts,
    }
