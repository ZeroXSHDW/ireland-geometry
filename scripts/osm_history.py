#!/usr/bin/env python3
"""Summarize OSM edit history without confusing mapping age with architecture.

The input contract accepts a normalized CSV/JSON export with one row per
version (``osm_id``, ``version``, ``timestamp``, ``changeset``, ``uid``,
``geometry_changed`` and ``tags_changed``).  A history extract is optional;
when absent every analyzed footprint receives ``status=not_provided``.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_history(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("history", payload.get("elements", []))
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def osm_id(row: dict) -> str:
    value = str(row.get("osm_id", "")).strip()
    if value:
        return value
    identifier = str(row.get("id", "")).strip()
    if identifier:
        return f"{row.get('osm_type', 'way')}/{identifier}"
    return ""


def parse_time(value: str) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def truthy(value: object) -> int:
    return int(str(value or "").strip().lower() in {"1", "true", "yes", "y"})


def build_history(analysis: list[dict[str, str]], history_rows: list[dict[str, str]], source: str) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in history_rows:
        key = osm_id(row)
        if key:
            grouped[key].append(row)
    output = []
    for row in analysis:
        key = row.get("osm_id", "")
        versions = grouped.get(key, [])
        dates = sorted(date for date in (parse_time(item.get("timestamp", "")) for item in versions) if date)
        first = dates[0] if dates else None
        last = dates[-1] if dates else None
        version_values = []
        for item in versions:
            try:
                version_values.append(int(float(item.get("version", ""))))
            except (TypeError, ValueError):
                continue
        editors = {str(item.get("uid", "")).strip() for item in versions if str(item.get("uid", "")).strip()}
        changesets = {str(item.get("changeset", "")).strip() for item in versions if str(item.get("changeset", "")).strip()}
        geometry_edits = sum(truthy(item.get("geometry_changed")) for item in versions)
        tag_edits = sum(truthy(item.get("tags_changed")) for item in versions)
        age_days = (last - first).total_seconds() / 86400 if first and last else None
        if versions:
            quality = "rich" if len(versions) >= 3 and len(editors) >= 2 else "sparse"
            status = "provided"
        else:
            quality = ""
            status = "not_provided"
        output.append(
            {
                "osm_id": key,
                "is_control": row.get("is_control", ""),
                "group": row.get("group", ""),
                "status": status,
                "source": source if versions else "unavailable",
                "version_count": len(versions),
                "current_version": max(version_values, default=""),
                "first_edit_at": first.isoformat() if first else "",
                "last_edit_at": last.isoformat() if last else "",
                "observed_history_age_days": round(age_days, 2) if age_days is not None else "",
                "editor_count": len(editors),
                "changeset_count": len(changesets),
                "geometry_edit_count": geometry_edits,
                "tag_edit_count": tag_edits,
                "mapping_quality_proxy": quality,
                "interpretation": "OSM edit-history diagnostic; not a construction date or design-intent measure",
            }
        )
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--history", default=None, help="optional normalized OSM history CSV/JSON")
    args = parser.parse_args(argv)
    data = project_data_tree_path(args.data_root)
    out = project_output_tree_path(args.out_dir)
    analysis_path = out / "analysis_results.csv"
    if not analysis_path.exists():
        raise SystemExit(f"Missing {analysis_path}. Run analyze.py first.")
    history_path = (
        project_input_path(
            args.history,
            str(data / "history" / "osm_history.csv"),
            label="OSM history input",
        )
        if args.history
        else data / "history" / "osm_history.csv"
    )
    analysis = read_csv(analysis_path)
    history_rows = read_history(history_path if history_path.exists() else None)
    source = str(history_path) if history_rows else "unavailable"
    rows = build_history(analysis, history_rows, source)
    atomic_write_csv(
        out / "mapping_history.csv",
        list(rows[0]) if rows else ["osm_id", "status"],
        rows,
    )
    provided = [row for row in rows if row["status"] == "provided"]
    summary = [
        {
            "status": "provided" if provided else "not_provided",
            "rows": len(rows),
            "provided_rows": len(provided),
            "target_rows": sum(row["is_control"] == "0" for row in rows),
            "control_rows": sum(row["is_control"] == "1" for row in rows),
            "source": source,
            "notes": "History age describes observed OSM edits and is not a proxy for building age.",
        }
    ]
    atomic_write_csv(
        out / "mapping_history_summary.csv",
        ["status", "rows", "provided_rows", "target_rows", "control_rows", "source", "notes"],
        summary,
    )
    print(f"[osm-history] wrote {len(rows):,} rows; provided={len(provided):,}")


if __name__ == "__main__":
    main()
