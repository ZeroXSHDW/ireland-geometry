#!/usr/bin/env python3
"""Build a standalone, data-driven Leaflet research dashboard."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
from collections import Counter
from pathlib import Path

try:
    from runtime import (
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        atomic_write_text,
        project_output_tree_path,
    )
except ImportError:
    from scripts.runtime import (
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        atomic_write_text,
        project_output_tree_path,
    )


TOP_N_MARKERS = 800
TOP_N_POLYGONS = 100
PAGE_SIZE = 50
VALIDATION_RECORD_NAMES = ("verification", "schema_validation", "reproducibility")
INTERPRETATION_CONTRACT = "ireland-geometry.interpretation.v1"
REPORT_PAGE_CONTRACT = "ireland-geometry.report-page.v1"
REPORT_EXPORT_CONTRACT = "ireland-geometry.report-export.v1"
REPORT_PAGE_DEFAULT_LIMIT = 50
REPORT_PAGE_MAX_LIMIT = 100
REPORT_FILTER_SORT_KEYS = ("name", "group", "area_m2", "score", "flags")
REPORT_PAGE_SORT_TIEBREAKER = "osm_id"
REPORT_EXPORT_COLUMNS = (
    "osm_id",
    "name",
    "group",
    "lat",
    "lon",
    "score",
    "area_m2",
    "aspect_ratio",
    "convexity",
    "circularity",
    "flags",
    "niah_name",
    "niah_rating",
    "niah_century",
)

PATTERN_METADATA = (
    ("golden_ratio", "Golden ratio", "Aspect ratio is within the configured 3% golden-ratio tolerance.", "Ratios"),
    ("fib_ratio", "Fibonacci ratio", "Aspect ratio matches a non-trivial Fibonacci ratio within 2%.", "Ratios"),
    ("fib_dimension", "Fibonacci dimension", "Length or width is close to a Fibonacci-number dimension.", "Ratios"),
    ("golden_angle", "Golden angle", "At least one vertex angle is within 3° of 137.5°.", "Angles"),
    ("orthogonal", "Orthogonal", "At least half of measured vertices are near right angles.", "Angles"),
    ("reflective_symmetry", "Reflective symmetry", "The footprint overlaps strongly with its mirror reflection.", "Symmetry"),
    ("rot180_symmetry", "180° rotational symmetry", "The footprint overlaps strongly after a 180° rotation.", "Symmetry"),
    ("rot90_symmetry", "90° rotational symmetry", "The footprint overlaps strongly after a 90° rotation.", "Symmetry"),
    ("pentagonal", "Pentagonal symmetry", "The footprint overlaps strongly after a 72° rotation.", "Symmetry"),
    ("hexagonal", "Hexagonal symmetry", "The footprint overlaps strongly after a 60° rotation.", "Symmetry"),
    ("octagonal", "Octagonal symmetry", "The footprint overlaps strongly after a 45° rotation.", "Symmetry"),
    ("circular", "Circular", "Circularity is at least 0.85 using 4πA/P².", "Shape"),
    ("cruciform_candidate", "Cruciform candidate", "A concave, multi-vertex, larger footprint matching the cruciform screen.", "Shape"),
)
CULTURE_LENS_KEYS = ("named", "heritage", "pobal", "civic")
CULTURE_LENS_LABELS = {
    "named": "Ainm / named places",
    "heritage": "Oidhreacht / heritage joins",
    "pobal": "Pobal / shared life",
    "civic": "Civic / public institutions",
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_pattern_catalog(rows: list[dict]) -> list[dict]:
    """Return every known geometry flag with a count and plain-language description."""
    counts = Counter(
        str(flag)
        for row in rows
        for flag in (row.get("flags") or [])
        if str(flag)
    )
    metadata = {key: (label, description, category) for key, label, description, category in PATTERN_METADATA}
    ordered_keys = [key for key, *_ in PATTERN_METADATA]
    ordered_keys.extend(sorted(set(counts) - set(metadata), key=lambda value: (value.casefold(), value)))
    total = len(rows)
    catalog = []
    for key in ordered_keys:
        label, description, category = metadata.get(
            key,
            (key.replace("_", " ").title(), "Additional geometry screening flag.", "Other"),
        )
        count = counts.get(key, 0)
        catalog.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "category": category,
                "count": count,
                "pct": round(100.0 * count / total, 4) if total else 0.0,
            }
        )
    return catalog


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_field_walk(rows: list[dict]) -> list[dict]:
    """Choose a small, reproducible set of waypoints for first-time visitors."""
    ranked = sorted(
        (row for row in rows if isinstance(row, dict) and row.get("osm_id")),
        key=lambda row: (-number(row.get("score")), str(row.get("osm_id"))),
    )
    used: set[str] = set()

    def choose(*predicates):
        for predicate in predicates:
            for row in ranked:
                osm_id = str(row.get("osm_id"))
                if osm_id in used or not predicate(row):
                    continue
                used.add(osm_id)
                return row
        return None

    def has_heritage(row):
        return bool((row.get("niah") or {}).get("reg_no"))

    def is_named(row):
        return bool(
            str(row.get("name") or "").strip()
            or str((row.get("niah") or {}).get("name") or "").strip()
        )

    waypoints = [
        (
            "proportion",
            "φ",
            "Proportion / measured screen",
            "Trace a measured proportion",
            "Start with a footprint whose length-to-width relationship enters the configured φ screen.",
            lambda row: bool(row.get("has_golden_ratio")) and has_heritage(row) and is_named(row),
            lambda row: bool(row.get("has_golden_ratio")),
        ),
        (
            "angle",
            "θ",
            "Rotation / angular screen",
            "Find an angular turn",
            "Follow a vertex-angle screen into a real place, then read the geometry beside its source context.",
            lambda row: bool(row.get("has_golden_angle"))
            and row.get("group") in {"worship", "civic", "government"}
            and is_named(row),
            lambda row: bool(row.get("has_golden_angle")),
        ),
        (
            "memory",
            "O",
            "Oidhreacht / record",
            "Read a memory beside the form",
            "Let a heritage-linked record sit beside the footprint before making any interpretation about it.",
            lambda row: has_heritage(row) and row.get("group") in {"worship", "historic"} and is_named(row),
            lambda row: has_heritage(row),
        ),
        (
            "civic",
            "P",
            "Pobal / shared life",
            "Meet the civic room",
            "End at a government or civic footprint and carry its measured scale into the shared-space question.",
            lambda row: row.get("group") in {"civic", "government"} and has_heritage(row),
            lambda row: row.get("group") in {"civic", "government"},
        ),
    ]
    result = []
    for key, symbol, eyebrow, title, description, *predicates in waypoints:
        row = choose(*predicates)
        if row is None:
            continue
        result.append(
            {
                "key": key,
                "step": f"{len(result) + 1:02d}",
                "symbol": symbol,
                "eyebrow": eyebrow,
                "title": title,
                "description": description,
                "row": row,
            }
        )
    return result


def integer(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def json_safe(value):
    """Prevent data values from terminating the inline script tag."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def polygon_rings(feature: dict) -> list[list[list[float]]]:
    try:
        from geometry import geometry_from_geojson, iter_polygons
    except ImportError:
        from scripts.geometry import geometry_from_geojson, iter_polygons

    geom = geometry_from_geojson(feature.get("geometry"))
    rings = []
    for polygon in iter_polygons(geom):
        rings.append([[round(y, 5), round(x, 5)] for x, y in polygon.exterior.coords])
    return rings


def normalize_row(
    row: dict,
    niah_by_id: dict[str, dict],
    parts_by_id: dict[str, dict] | None = None,
    history_by_id: dict[str, dict] | None = None,
    lidar_by_id: dict[str, dict] | None = None,
    mapping_history_by_id: dict[str, dict] | None = None,
    covariates_by_id: dict[str, dict] | None = None,
    review_by_id: dict[str, dict] | None = None,
) -> dict:
    niah = niah_by_id.get(row["osm_id"], {})
    parts = (parts_by_id or {}).get(row["osm_id"], {})
    history = (history_by_id or {}).get(row["osm_id"], {})
    lidar = (lidar_by_id or {}).get(row["osm_id"], {})
    mapping_history = (mapping_history_by_id or {}).get(row["osm_id"], {})
    covariates = (covariates_by_id or {}).get(row["osm_id"], {})
    review = (review_by_id or {}).get(row["osm_id"], {})
    flags = [flag for flag in row.get("flags", "").split(",") if flag]
    return {
        "osm_id": row["osm_id"],
        "name": row.get("name", ""),
        "group": row.get("group", "other"),
        "subtype": row.get("subtype", ""),
        "lat": number(row.get("lat")),
        "lon": number(row.get("lon")),
        "score": number(row.get("score")),
        "area_m2": number(row.get("area_m2")),
        "perimeter_m": number(row.get("perimeter_m")),
        "length_m": number(row.get("length_m")),
        "width_m": number(row.get("width_m")),
        "aspect_ratio": number(row.get("aspect_ratio")),
        "n_vertices": integer(row.get("n_vertices")),
        "convexity": number(row.get("convexity")),
        "circularity": number(row.get("circularity")),
        "rectangularity": number(row.get("rectangularity")),
        "angle_entropy": number(row.get("angle_entropy")),
        "radial_cv": number(row.get("radial_cv")),
        "fourier_1": number(row.get("fourier_1")),
        "fourier_2": number(row.get("fourier_2")),
        "fourier_3": number(row.get("fourier_3")),
        "fourier_4": number(row.get("fourier_4")),
        "vertex_density": number(row.get("vertex_density")),
        "height_m": number(row.get("height_m")),
        "building_levels": number(row.get("building_levels")),
        "building_tag": row.get("building_tag", ""),
        "address_city": row.get("address_city", ""),
        "has_golden_angle": integer(row.get("has_golden_angle")),
        "has_golden_ratio": int(number(row.get("golden_ratio_err_pct"), 999) <= 3),
        "golden_ratio_err_pct": number(row.get("golden_ratio_err_pct"), 999),
        "fib_ratio_err_pct": number(row.get("fib_ratio_err_pct"), 999),
        "valid": integer(row.get("valid"), 1),
        "repaired": integer(row.get("repaired")),
        "multipart": integer(row.get("multipart")),
        "hole_count": integer(row.get("hole_count")),
        "geometry_warning": row.get("geometry_warning", ""),
        "flags": flags,
        "niah": {
            "reg_no": niah.get("reg_no", ""),
            "name": niah.get("niah_name", ""),
            "county": niah.get("county", ""),
            "source_region": niah.get("source_region", ""),
            "rating": niah.get("rating", ""),
            "type": niah.get("niah_type", ""),
            "century": niah.get("century", ""),
            "century50": niah.get("century50", ""),
            "date_mid": number(niah.get("date_mid"), 0),
            "match_mode": niah.get("match_mode", ""),
            "dist_m": number(niah.get("dist_m"), 0),
        },
        "parts": {
            "count": integer(parts.get("part_count")),
            "coverage_pct": number(parts.get("part_coverage_pct")),
            "max_height_m": number(parts.get("max_part_height_m")),
            "max_levels": number(parts.get("max_part_levels")),
            "status": parts.get("part_geometry_status", ""),
        },
        "lidar": {
            "available": integer(lidar.get("lidar_available")),
            "roof_height_m": number(lidar.get("roof_height_m")),
            "source": lidar.get("source", ""),
            "quality": lidar.get("quality", ""),
        },
        "history": {
            "status": history.get("validation_status", ""),
            "priority": history.get("review_priority", ""),
            "architect": history.get("architect", ""),
            "reference_count": integer(history.get("reference_count")),
            "warnings": history.get("review_warnings", ""),
        },
        "mapping_history": {
            "status": mapping_history.get("status", ""),
            "version_count": integer(mapping_history.get("version_count")),
            "first_edit_at": mapping_history.get("first_edit_at", ""),
            "last_edit_at": mapping_history.get("last_edit_at", ""),
            "quality": mapping_history.get("mapping_quality_proxy", ""),
        },
        "spatial": {
            "county": covariates.get("county", ""),
            "settlement_name": covariates.get("settlement_name", ""),
            "settlement_class": covariates.get("settlement_class", ""),
            "mapping_density_bin": covariates.get("mapping_density_bin", ""),
            "boundary_status": covariates.get("boundary_status", ""),
        },
        "review": {
            "label": review.get("label", "not_reviewed"),
            "reviewer": review.get("reviewer", ""),
            "in_queue": bool(review),
        },
        "osm_url": f"https://www.openstreetmap.org/{html.escape(row['osm_id'])}",
    }


def report_review_state(row: dict) -> str:
    """Return the review filter value used by both the dashboard and API."""
    review = row.get("review") or {}
    return str(review.get("label") or "not_reviewed") if review.get("in_queue") else "not_queued"


def report_filter_options(data: dict) -> dict[str, list[str]]:
    """Return compact, deterministic select options for the lazy dashboard."""
    rows = data.get("targets", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        rows = []

    def values(getter):
        return sorted(
            {str(value) for row in rows if isinstance(row, dict) if (value := getter(row))},
            key=lambda value: (value.casefold(), value),
        )

    return {
        "group": values(lambda row: row.get("group", "")),
        "century": values(lambda row: (row.get("niah") or {}).get("century", "")),
        "rating": values(lambda row: (row.get("niah") or {}).get("rating", "")),
        "type": values(lambda row: (row.get("niah") or {}).get("type", "")),
        "county": values(
            lambda row: (row.get("spatial") or {}).get("county")
            or (row.get("niah") or {}).get("county", "")
        ),
        "review": values(report_review_state),
        "pattern": [item["key"] for item in build_pattern_catalog(rows)],
        "culture": list(CULTURE_LENS_KEYS),
    }


def cultural_lens_matches(row: dict, lens: str) -> bool:
    """Return whether a target belongs to a data-derived cultural lens."""
    if lens == "named":
        return (row.get("spatial") or {}).get("settlement_class") == "named_place"
    if lens == "heritage":
        return bool((row.get("niah") or {}).get("reg_no"))
    if lens == "pobal":
        return row.get("group") in {"worship", "government", "civic"}
    if lens == "civic":
        return row.get("group") in {"government", "civic"}
    return False


def report_matching_targets(
    data: dict,
    *,
    query: str = "",
    group: str = "",
    century: str = "",
    rating: str = "",
    niah_type: str = "",
    county: str = "",
    review_state: str = "",
    pattern: str = "",
    culture: str = "",
    min_score: float = 0.0,
    only_angle: bool = False,
    only_ratio: bool = False,
    only_circular: bool = False,
    only_multi: bool = False,
    sort_key: str = "score",
    sort_desc: bool = True,
) -> list[dict]:
    """Apply the report's target filters outside the browser.

    Keeping this predicate in the report module makes the paginated API and
    CSV/GeoJSON exports use exactly the same semantics.
    """
    if sort_key not in REPORT_FILTER_SORT_KEYS:
        raise ValueError(f"sort must be one of {', '.join(REPORT_FILTER_SORT_KEYS)}")
    if culture not in {"", *CULTURE_LENS_KEYS}:
        raise ValueError(f"culture must be one of {', '.join(CULTURE_LENS_KEYS)}")
    if not math.isfinite(min_score):
        raise ValueError("score must be finite")
    rows = data.get("targets", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        rows = []
    needle = str(query).strip().casefold()

    def matches(row: dict) -> bool:
        niah = row.get("niah") or {}
        history = row.get("history") or {}
        flags = row.get("flags") or []
        flags_text = ", ".join(str(flag) for flag in flags)
        haystack = " ".join(
            str(value or "")
            for value in (
                row.get("name"),
                row.get("osm_id"),
                row.get("group"),
                row.get("subtype"),
                row.get("address_city"),
                flags_text,
                niah.get("name"),
                niah.get("county"),
                niah.get("type"),
                history.get("status"),
                history.get("architect"),
            )
        ).casefold()
        return (
            (not needle or needle in haystack)
            and (not group or row.get("group") == group)
            and (not century or niah.get("century") == century)
            and (not rating or niah.get("rating") == rating)
            and (not niah_type or niah.get("type") == niah_type)
            and (
                not county
                or (
                    (row.get("spatial") or {}).get("county")
                    or niah.get("county")
                )
                == county
            )
            and (not review_state or report_review_state(row) == review_state)
            and (not pattern or pattern in flags)
            and (not culture or cultural_lens_matches(row, culture))
            and number(row.get("score")) >= min_score
            and (not only_angle or bool(row.get("has_golden_angle")))
            and (not only_ratio or bool(row.get("has_golden_ratio")))
            and (not only_circular or "circular" in flags)
            and (not only_multi or bool(row.get("multipart")) or bool(row.get("repaired")))
        )

    matched = [row for row in rows if isinstance(row, dict) and matches(row)]

    def sort_tiebreaker(row):
        value = str(row.get(REPORT_PAGE_SORT_TIEBREAKER) or "")
        return value.casefold(), value

    matched = sorted(matched, key=sort_tiebreaker)
    if sort_key in {"name", "group", "flags"}:
        def sort_value(row):
            if sort_key == "flags":
                return ", ".join(str(flag) for flag in (row.get("flags") or []))
            return str(row.get(sort_key) or "").casefold()
    else:
        def sort_value(row):
            return number(row.get(sort_key))
    return sorted(matched, key=sort_value, reverse=sort_desc)


def report_page_payload(
    data: dict,
    *,
    limit: int = REPORT_PAGE_DEFAULT_LIMIT,
    offset: int = 0,
    initial: bool = False,
    include_static: bool = False,
    **filters,
) -> dict:
    """Build a bounded lazy-report response without embedding the full pack."""
    if not 1 <= limit <= REPORT_PAGE_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {REPORT_PAGE_MAX_LIMIT}")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    matched = report_matching_targets(data, **filters)
    page_rows = matched[offset : offset + limit]
    payload: dict[str, object] = {
        "contract": REPORT_PAGE_CONTRACT,
        "status": "ok",
        "paged": True,
        "initial": bool(initial),
        "targets": page_rows,
        "page": {
            "limit": limit,
            "offset": offset,
            "count": len(page_rows),
            "total": len(matched),
            "has_more": offset + limit < len(matched),
            "next_offset": offset + limit if offset + limit < len(matched) else None,
            "sort_tiebreaker": REPORT_PAGE_SORT_TIEBREAKER,
            "matching_golden_angle": sum(bool(row.get("has_golden_angle")) for row in matched),
            "matching_niah": sum(bool((row.get("niah") or {}).get("reg_no")) for row in matched),
        },
        "filters": dict(filters),
        "filter_options": report_filter_options(data),
        "endpoints": {
            "page": "/api/report/page",
            "runtime": "/api/report/runtime",
            "export": "/api/report/export",
        },
    }
    if include_static:
        payload.update(
            {
                key: value
                for key, value in data.items()
                if key not in {"targets", "geojson", "candidate_dossiers"}
            }
        )
    return payload


def report_csv(data: dict, **filters) -> str:
    """Serialize the current target filter as the dashboard CSV export."""
    rows = report_matching_targets(data, **filters)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(REPORT_EXPORT_COLUMNS)
    for row in rows:
        niah = row.get("niah") or {}
        writer.writerow(
            [
                row.get("osm_id", ""),
                row.get("name", ""),
                row.get("group", ""),
                row.get("lat", ""),
                row.get("lon", ""),
                row.get("score", ""),
                row.get("area_m2", ""),
                row.get("aspect_ratio", ""),
                row.get("convexity", ""),
                row.get("circularity", ""),
                ", ".join(str(flag) for flag in (row.get("flags") or [])),
                niah.get("name", ""),
                niah.get("rating", ""),
                niah.get("century", ""),
            ]
        )
    return output.getvalue()


def report_geojson(data: dict, **filters) -> dict:
    """Return the full-geometry export for the current target filter."""
    rows = report_matching_targets(data, **filters)
    ids = {str(row.get("osm_id")) for row in rows}
    source = data.get("geojson") if isinstance(data, dict) else None
    source = source if isinstance(source, dict) else {"type": "FeatureCollection", "features": []}
    features = [
        feature
        for feature in source.get("features", [])
        if isinstance(feature, dict)
        and str((feature.get("properties") or {}).get("osm_id")) in ids
    ]
    return {
        "type": "FeatureCollection",
        "contract": REPORT_EXPORT_CONTRACT,
        "status": "ok",
        "features": features,
    }


def load_manifest(out: Path) -> dict:
    path = out / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def status_from_rows(rows: list[dict], field: str = "status") -> str:
    """Summarize a status column without collapsing mixed coverage into missing."""
    values = sorted(
        {str(row.get(field, "")).strip() for row in rows if str(row.get(field, "")).strip()}
    )
    if not values:
        return "not_provided"
    return values[0] if len(values) == 1 else "mixed"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def validation_status(out: Path) -> dict:
    """Summarize the independent analytical validation records for the report."""
    records = {}
    for name in VALIDATION_RECORD_NAMES:
        path = out / f"{name}.json"
        if not path.is_file():
            records[name] = {
                "available": False,
                "status": "not_provided",
                "passed": False,
                "error": None,
            }
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("record root is not an object")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            records[name] = {
                "available": False,
                "status": "fail",
                "passed": False,
                "error": str(exc),
            }
            continue
        passed = payload.get("passed") is True
        records[name] = {
            "available": True,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "error": None,
        }
    statuses = {record["status"] for record in records.values()}
    if statuses == {"pass"}:
        status = "pass"
    elif "fail" in statuses:
        status = "fail"
    elif "pass" in statuses:
        status = "incomplete"
    else:
        status = "not_provided"
    return {
        "status": status,
        "passed": status == "pass",
        "manifest_available": (out / "manifest.json").is_file(),
        "records": records,
    }


def maybe_number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def source_freshness_summary(manifest: dict) -> dict:
    """Return a compact dashboard summary of manifest source ages."""
    record = manifest.get("source_freshness") if isinstance(manifest, dict) else None
    if not isinstance(record, dict):
        return {
            "status": "not_provided",
            "contract": None,
            "observed_at": "",
            "source_count": 0,
            "oldest_age_days": None,
            "newest_age_days": None,
        }
    ages = []
    rows = record.get("sources")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            age_seconds = maybe_number(row.get("age_seconds"))
            if age_seconds is not None and age_seconds >= 0:
                ages.append(age_seconds / 86_400.0)
    contract = record.get("contract")
    return {
        "status": "reported" if contract == SOURCE_FRESHNESS_CONTRACT else "invalid",
        "contract": contract,
        "observed_at": record.get("observed_at", ""),
        "source_count": len(rows) if isinstance(rows, list) else 0,
        "oldest_age_days": round(max(ages), 3) if ages else None,
        "newest_age_days": round(min(ages), 3) if ages else None,
    }


def p_text(value) -> str:
    parsed = maybe_number(value)
    if parsed is None:
        return "not reported"
    if parsed < 0.0001:
        return "<0.0001"
    return f"{parsed:.4f}".rstrip("0").rstrip(".")


def percent_text(value) -> str:
    parsed = maybe_number(value)
    return "not reported" if parsed is None else f"{parsed:.2f}%"


def _row_group(row: dict) -> str:
    return str(row.get("group") or row.get("target_group") or "").strip()


def preferred_focus_group(*row_sets: list[dict]) -> str:
    """Choose a stable focus group for the compact interpretation panel."""
    rows = [row for row_set in row_sets for row in row_set]
    groups = sorted({_row_group(row) for row in rows if _row_group(row)})
    if "worship" in groups:
        return "worship"
    return groups[0] if groups else ""


def best_result_row(
    rows: list[dict], *, signal: str, focus_group: str, group_field: str = "group"
) -> dict | None:
    candidates = [
        row
        for row in rows
        if row.get("signal") == signal and row.get(group_field) == focus_group
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: (
            maybe_number(row.get("p_adjusted"))
            if maybe_number(row.get("p_adjusted")) is not None
            else 1.0,
            str(row.get("stratum", "")),
        ),
    )


def result_status(row: dict | None) -> str:
    if not row:
        return "not_provided"
    return str(row.get("verdict") or "not_reported")


def comparison_rates(row: dict) -> tuple[float | None, float | None]:
    for observed_key, reference_key in (
        ("observed_rate", "control_rate"),
        ("target_rate", "matched_control_rate"),
        ("target_rate", "control_rate"),
        ("observed_rate", "reference_rate"),
    ):
        observed = maybe_number(row.get(observed_key))
        reference = maybe_number(row.get(reference_key))
        if observed is not None and reference is not None:
            return observed, reference
    return None, None


def effect_value(row: dict) -> float | None:
    for field in ("risk_difference", "risk_difference_pp", "observed_difference_pp"):
        value = maybe_number(row.get(field))
        if value is not None:
            return value
    return None


def comparison_text(row: dict, label: str, focus_group: str) -> str:
    observed, reference = comparison_rates(row)
    effect = effect_value(row)
    parts = [f"{label} for {focus_group or 'the selected group'}"]
    if observed is not None and reference is not None:
        parts.append(f"is {percent_text(observed)} versus {percent_text(reference)} in its comparison set")
    if effect is not None:
        parts.append(f"({effect:+.2f} percentage points)")
    adjusted = p_text(row.get("p_adjusted"))
    if adjusted != "not reported":
        parts.append(f"with Holm-adjusted p {adjusted}")
    parts.append(f"and is classified as {result_status(row)}")
    return " ".join(parts) + "."


def build_interpretation(
    summary: dict,
    significance: list[dict],
    negative_controls: list[dict],
    niah_significance: list[dict],
    matched_significance: list[dict],
    county_permutation: list[dict],
    moran: list[dict],
    holdout: list[dict],
    validation: dict,
) -> dict:
    """Build a compact interpretation from result rows, never snapshot constants."""
    focus_group = preferred_focus_group(
        significance,
        negative_controls,
        niah_significance,
        matched_significance,
        county_permutation,
        moran,
        holdout,
    )
    findings = []
    primary = best_result_row(significance, signal="golden_angle", focus_group=focus_group)
    if primary:
        findings.append(
            {
                "id": "global_primary",
                "title": "Global golden-angle comparison",
                "status": result_status(primary),
                "text": comparison_text(primary, "The golden-angle flag", focus_group),
            }
        )
    ratio = best_result_row(significance, signal="golden_ratio", focus_group=focus_group)
    if ratio:
        findings.append(
            {
                "id": "global_ratio",
                "title": "Golden-ratio aspect comparison",
                "status": result_status(ratio),
                "text": comparison_text(ratio, "Golden-ratio aspect matching", focus_group),
            }
        )

    negative = [row for row in negative_controls if _row_group(row) == focus_group]
    if negative:
        positive_negative = [row for row in negative if result_status(row) != "background"]
        selected = sorted(
            positive_negative or negative,
            key=lambda row: (
                maybe_number(row.get("p_adjusted"))
                if maybe_number(row.get("p_adjusted")) is not None
                else 1.0,
                str(row.get("signal", "")),
            ),
        )[:3]
        labels = ", ".join(
            f"{str(row.get('signal', 'control')).replace('_', ' ')} ({result_status(row)})"
            for row in selected
        )
        findings.append(
            {
                "id": "negative_controls",
                "title": "Conventional-angle negative controls",
                "status": result_status(selected[0]) if positive_negative else "background",
                "text": (
                    f"The available negative-control results for {focus_group or 'the selected group'} "
                    f"are {labels}. Elevated conventional-angle controls reduce specificity for a "
                    "golden-angle interpretation."
                ),
            }
        )

    matched = best_result_row(
        matched_significance,
        signal="golden_angle",
        focus_group=focus_group,
        group_field="target_group",
    )
    if matched:
        findings.append(
            {
                "id": "matched_sensitivity",
                "title": "Matched-control sensitivity",
                "status": result_status(matched),
                "text": comparison_text(matched, "The matched golden-angle comparison", focus_group),
            }
        )

    era_rows = [
        row
        for row in niah_significance
        if row.get("signal") == "golden_angle"
        and row.get("group") == focus_group
        and "era-matched" in str(row.get("reference", ""))
    ]
    if era_rows:
        supported = [row for row in era_rows if result_status(row) != "background"]
        era_text = ", ".join(
            f"{row.get('stratum', 'stratum')} ({result_status(row)})"
            for row in sorted(supported or era_rows, key=lambda row: str(row.get("stratum", "")))
        )
        findings.append(
            {
                "id": "niah_era",
                "title": "NIAH era-matched sensitivity",
                "status": result_status(supported[0]) if supported else "background",
                "text": (
                    f"The era-matched NIAH results for {focus_group or 'the selected group'} "
                    f"classify {era_text}; these rows cover only buildings with usable NIAH dates."
                ),
            }
        )

    county = best_result_row(
        county_permutation,
        signal="golden_angle",
        focus_group=focus_group,
    )
    moran_row = next((row for row in moran if row.get("group") == focus_group), None)
    if county or moran_row:
        county_label = result_status(county) if county else "not reported"
        moran_label = result_status(moran_row) if moran_row else "not reported"
        findings.append(
            {
                "id": "spatial_sensitivity",
                "title": "Spatial sensitivity checks",
                "status": county_label if county and county_label != "background" else moran_label,
                "text": (
                    f"County-preserving permutation status is {county_label}; local Moran's I status "
                    f"is {moran_label}. These are spatial diagnostics, not causal tests."
                ),
            }
        )

    holdout_row = best_result_row(
        holdout,
        signal="golden_angle",
        focus_group=focus_group,
        group_field="target_group",
    )
    if holdout_row:
        difference = effect_value(holdout_row)
        p_value = maybe_number(holdout_row.get("p_value"))
        alpha = maybe_number(holdout_row.get("alpha")) or 0.05
        holdout_status = (
            "supportive"
            if p_value is not None and p_value < alpha and (difference is None or difference > 0)
            else "not_confirmed"
        )
        findings.append(
            {
                "id": "holdout",
                "title": "Pre-registered holdout",
                "status": holdout_status,
                "text": (
                    f"The deterministic holdout is {holdout_status}: target rate "
                    f"{percent_text(holdout_row.get('target_rate'))} versus "
                    f"{percent_text(holdout_row.get('control_rate'))}, unadjusted p "
                    f"{p_text(holdout_row.get('p_value'))}."
                ),
            }
        )

    has_statistical_findings = bool(findings)
    validation_status_value = str(validation.get("status", "not_provided"))
    findings.append(
        {
            "id": "validation",
            "title": "Analytical validation gates",
            "status": "pass" if summary.get("analysis_ready") else validation_status_value,
            "text": (
                "Manifest, verification, schema validation, and reproducibility records all pass."
                if summary.get("analysis_ready")
                else f"Analytical validation is {validation_status_value}; numerical findings should be treated as provisional."
            ),
        }
    )

    caveats = []
    if not negative:
        caveats.append("No conventional-angle negative-control rows were available in this report pack.")
    elif any(result_status(row) != "background" for row in negative):
        caveats.append("At least one conventional-angle negative control is elevated, so specificity is limited.")
    source_status = summary.get("source_status", {})
    if source_status.get("lidar") not in {"available", "provided"}:
        caveats.append("LiDAR is not provided; the report does not make a 3-D or roof-height inference.")
    if source_status.get("historical_references") not in {"available", "provided"}:
        caveats.append("Curated historical references are not provided; historical rows remain inventory-based evidence.")
    if not summary.get("analysis_ready"):
        caveats.append("The report pack is not analytically ready until all validation records and the manifest are present and passing.")
    if not has_statistical_findings:
        caveats.append("No statistical result rows were available to summarize.")
    headline = (
        findings[0]["text"]
        if primary
        else "No primary golden-angle comparison is available in this report pack."
    )
    return {
        "status": "available" if has_statistical_findings else "not_provided",
        "focus_group": focus_group,
        "headline": headline,
        "findings": findings,
        "caveats": caveats,
    }


def build_report_data(out: Path) -> dict:
    rows = read_csv(out / "analysis_results.csv")
    if not rows:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    niah_rows = read_csv(out / "niah_join.csv")
    niah_by_id = {row["osm_id"]: row for row in niah_rows}
    parts_by_id = {row["osm_id"]: row for row in read_csv(out / "building_parts.csv")}
    historical_rows = read_csv(out / "historical_validation.csv")
    lidar_rows = read_csv(out / "lidar_coverage.csv")
    history_by_id = {row["osm_id"]: row for row in historical_rows}
    lidar_by_id = {row["osm_id"]: row for row in lidar_rows}
    mapping_history_by_id = {row["osm_id"]: row for row in read_csv(out / "mapping_history.csv")}
    covariates_by_id = {row["osm_id"]: row for row in read_csv(out / "spatial_covariates.csv")}
    review_by_id = {row["osm_id"]: row for row in read_csv(out / "review_queue.csv")}
    mapping_history_summary = read_csv(out / "mapping_history_summary.csv")
    spatial_covariates_summary = read_csv(out / "spatial_covariates_summary.csv")
    road_routing_rows = read_csv(out / "road_routing.csv")
    review_confusion = read_csv(out / "review_confusion.csv")
    historical_source_register = read_csv(out / "historical_source_register.csv")
    columnar_status = read_json(out / "columnar_status.json")
    scoring_config = read_json(out / "scoring_config.json")
    verification = read_json(out / "verification.json")
    significance = read_csv(out / "significance.csv")
    negative_controls = read_csv(out / "negative_controls.csv")
    niah_significance = read_csv(out / "niah_significance.csv")
    decades = read_csv(out / "niah_decades.csv")
    matched_significance = read_csv(out / "matched_significance.csv")
    hierarchical_model = read_csv(out / "hierarchical_model.csv")
    matched_control_summary = read_csv(out / "matched_control_summary.csv")
    strict_matched_significance = read_csv(out / "matched_strict_significance.csv")
    strict_match_summary = read_csv(out / "matched_strict_summary.csv")
    point_pattern = read_csv(out / "point_pattern.csv")
    ripley = read_csv(out / "ripley.csv")
    moran = read_csv(out / "moran.csv")
    county_permutation = read_csv(out / "county_permutation.csv")
    road_proximity = read_csv(out / "road_proximity.csv")
    road_routing = read_csv(out / "road_routing.csv")
    holdout = read_csv(out / "holdout_results.csv")
    review_calibration = read_csv(out / "review_calibration.csv")
    spatial_bootstrap = read_csv(out / "spatial_bootstrap.csv")
    data_quality = read_csv(out / "data_quality.csv")
    data_quality_duplicates = read_csv(out / "data_quality_duplicates.csv")
    architects = read_csv(out / "architects.csv")
    architects_binary = read_csv(out / "architects_binary.csv")
    candidate_dossiers = read_csv(out / "candidate_dossiers.csv")
    validation = validation_status(out)
    quality_summary = {}
    quality_path = out / "data_quality_summary.json"
    if quality_path.exists():
        try:
            quality_summary = json.loads(quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            quality_summary = {}
    targets = [
        normalize_row(
            row,
            niah_by_id,
            parts_by_id,
            history_by_id,
            lidar_by_id,
            mapping_history_by_id,
            covariates_by_id,
            review_by_id,
        )
        for row in rows
        if row.get("group") != "control"
    ]
    controls = [row for row in rows if row.get("group") == "control"]
    targets.sort(key=lambda row: (-row["score"], row["osm_id"]))

    gj_path = out / "ireland_buildings.geojson"
    geojson = (
        json.loads(gj_path.read_text(encoding="utf-8"))
        if gj_path.exists()
        else {"type": "FeatureCollection", "features": []}
    )
    features_by_id = {
        feature.get("properties", {}).get("osm_id"): feature
        for feature in geojson.get("features", [])
    }
    outlines = []
    for row in targets:
        feature = features_by_id.get(row["osm_id"])
        if not feature:
            continue
        rings = polygon_rings(feature)
        if rings:
            outlines.append(
                {
                    "rings": rings,
                    "osm_id": row["osm_id"],
                    "score": row["score"],
                    "name": row["name"],
                    "group": row["group"],
                    "flags": row["flags"],
                }
            )
        if len(outlines) >= TOP_N_POLYGONS:
            break

    modes = Counter(row.get("match_mode", "") for row in niah_rows)
    manifest = load_manifest(out)
    freshness_summary = source_freshness_summary(manifest)
    county_counts = Counter(
        str((row.get("spatial") or {}).get("county") or "").strip()
        for row in targets
        if bool((row.get("niah") or {}).get("reg_no"))
        if str((row.get("spatial") or {}).get("county") or "").strip()
    )
    culture_summary = {
        "named_places": sum(
            (row.get("spatial") or {}).get("settlement_class") == "named_place"
            for row in targets
        ),
        "heritage_joins": sum(bool((row.get("niah") or {}).get("reg_no")) for row in targets),
        "shared_life": sum(row.get("group") in {"worship", "government", "civic"} for row in targets),
        "civic_life": sum(row.get("group") in {"government", "civic"} for row in targets),
        "county_contexts": len(county_counts),
        "top_counties": dict(county_counts.most_common(5)),
    }
    covariate_status = {
        row.get("covariate", ""): row.get("status", "not_provided")
        for row in spatial_covariates_summary
    }
    reference_status = next(
        (
            row.get("status", "not_provided")
            for row in historical_source_register
            if row.get("source_type") == "Curated historical references"
        ),
        "not_provided",
    )
    source_status = {
        "lidar": status_from_rows(lidar_rows),
        "osm_history": status_from_rows(mapping_history_summary),
        "administrative_boundaries": covariate_status.get("administrative_boundary", "not_provided"),
        "settlements": covariate_status.get("settlement_layer", "not_provided"),
        "routing": status_from_rows(road_routing_rows),
        "historical_references": reference_status,
        "review_labels": status_from_rows(review_confusion),
        "jsonl": columnar_status.get("jsonl", {}).get("status", "not_provided"),
        "parquet": columnar_status.get("parquet", {}).get("status", "not_provided"),
        "duckdb": columnar_status.get("duckdb", {}).get("status", "not_provided"),
        "verification": "pass" if verification.get("passed") is True else ("fail" if verification else "not_provided"),
    }
    summary = {
        "targets": len(targets),
        "controls": len(controls),
        "golden_angle": sum(row["has_golden_angle"] for row in targets),
        "golden_ratio": sum(row["has_golden_ratio"] for row in targets),
        "niah_matches": len(niah_rows),
        "niah_contained": modes.get("contained", 0),
        "niah_near": modes.get("near", 0),
        "groups": dict(Counter(row["group"] for row in targets)),
        "culture": culture_summary,
        "pattern_count": len(build_pattern_catalog(targets)),
        "part_mapped": sum(row["parts"]["count"] > 0 for row in targets),
        "lidar_available": sum(row["lidar"]["available"] > 0 for row in targets),
        "history_review": sum(row["history"]["priority"] in {"high", "medium"} for row in targets),
        "mapping_history_available": sum(row["mapping_history"]["status"] == "provided" for row in targets),
        "spatial_covariates": sum(bool(row["spatial"]["mapping_density_bin"]) for row in targets),
        "reviewed": sum(row["review"]["label"] != "not_reviewed" for row in targets),
        "review_queue_targets": len(review_by_id),
        "review_queue_coverage_pct": round(100.0 * len(review_by_id) / len(targets), 4) if targets else 0.0,
        "quality_status": quality_summary.get("quality_status", ""),
        "valid_geometry_pct": number(quality_summary.get("valid_geometry_pct"), 0),
        "duplicate_centroid_n": integer(quality_summary.get("duplicate_centroid_n")),
        "generated_at": manifest.get("generated_at", ""),
        "source_status": source_status,
        "source_freshness": freshness_summary,
        "scoring": {
            "contract": scoring_config.get("contract", ""),
            "version": scoring_config.get("version"),
            "config_sha256": scoring_config.get("config_sha256", ""),
            "label": scoring_config.get("label", ""),
        },
        "validation": validation,
        "analysis_ready": validation["passed"] and validation["manifest_available"],
    }
    interpretation = build_interpretation(
        summary,
        significance,
        negative_controls,
        niah_significance,
        matched_significance,
        county_permutation,
        moran,
        holdout,
        validation,
    )
    data = {
        "targets": targets,
        "field_walk": build_field_walk(targets),
        "pattern_catalog": build_pattern_catalog(targets),
        "outlines": outlines,
        "summary": summary,
        "scoring": scoring_config,
        "interpretation": interpretation,
        "significance": significance,
        "negative_controls": negative_controls,
        "niah_significance": niah_significance,
        "decades": decades,
        "matched_significance": matched_significance,
        "hierarchical_model": hierarchical_model,
        "matched_control_summary": matched_control_summary,
        "strict_matched_significance": strict_matched_significance,
        "strict_match_summary": strict_match_summary,
        "point_pattern": point_pattern,
        "ripley": ripley,
        "moran": moran,
        "county_permutation": county_permutation,
        "road_proximity": road_proximity,
        "road_routing": road_routing,
        "holdout": holdout,
        "review_calibration": review_calibration,
        "spatial_bootstrap": spatial_bootstrap,
        "data_quality": data_quality,
        "data_quality_duplicates": data_quality_duplicates,
        "data_quality_summary": quality_summary,
        "mapping_history_summary": mapping_history_summary,
        "spatial_covariates_summary": spatial_covariates_summary,
        "architects": architects,
        "architects_binary": architects_binary,
        "candidate_dossiers": candidate_dossiers,
        "historical_source_register": read_csv(out / "historical_source_register.csv"),
        "manifest": manifest,
        "geojson": geojson,
    }
    return data


def build_report(out: Path) -> str:
    return render_report_data(build_report_data(out))


def render_report_data(data: dict) -> str:
    return TEMPLATE.replace("__MARKER_LIMIT__", str(TOP_N_MARKERS)).replace(
        "__DATA__", json_safe(data)
    )


def interpretation_artifact(data: dict) -> dict:
    """Return the compact machine-readable interpretation sidecar."""
    summary = data["summary"]
    interpretation = data["interpretation"]
    available = interpretation.get("status") == "available"
    return {
        "contract": INTERPRETATION_CONTRACT,
        "status": "ok" if available else "not_provided",
        "available": available,
        "analysis_ready": bool(summary.get("analysis_ready")),
        "validation": summary.get("validation", {}),
        "summary": {
            "targets": summary.get("targets", 0),
            "controls": summary.get("controls", 0),
            "focus_group": interpretation.get("focus_group", ""),
        },
        "interpretation": interpretation,
        "source": "/interpretation.json",
    }


def build_lazy_report(out: Path) -> str:
    """Return a server-backed dashboard with an explicit full-pack offline mode."""
    template = TEMPLATE.replace("__MARKER_LIMIT__", str(TOP_N_MARKERS)).replace("__DATA__", "null")
    marker = "<script>\nconst PACK = null;\n"
    start = template.index(marker) + len(marker)
    end = template.index("\n</script>", start)
    body = template[start:end]
    body = body.rsplit("reportLaunch();", 1)[0]
    script = """<script>
function showInitialReportError(error) {
  const message=error?.message || String(error || 'unknown error');
  const notice=document.getElementById('reportLoadError');
  const text=document.getElementById('reportLoadErrorText');
  const retry=document.getElementById('reportRetry');
  if(notice && text) {
    const intro=document.getElementById('siteIntro');
    if(intro) { intro.hidden=true; intro.classList.add('is-dismissed'); }
    document.body.classList.remove('intro-open');
    text.textContent=`Report could not load: ${message}`;
    notice.hidden=false;
    retry?.addEventListener('click',()=>location.reload(),{once:true});
    return;
  }
  document.body.textContent='';
  const pre=document.createElement('pre');
  pre.style.padding='20px';
  pre.textContent=`Report could not load: ${message}`;
  document.body.appendChild(pre);
}
async function loadPack() {
  const params = new URLSearchParams(location.search);
  const offline = params.get('offline') === '1';
  const response = offline
    ? await fetch('report_data.json')
    : await fetch('/api/report/page',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initial:true,limit:50,offset:0})});
  if (!response.ok) {
    throw new Error(offline
      ? `Could not load report_data.json (${response.status})`
      : `Could not load the paginated report API (${response.status}); serve this file with ireland-geometry-serve or use ?offline=1`);
  }
  boot(await response.json());
}
function boot(PACK) {
""" + body + "\nreportLaunch();\n}\nloadPack().catch(showInitialReportError);\n</script>"
    return template[: start - len(marker)] + script + template[end + len("\n</script>") :]


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Cruth — Ireland Field Atlas V3</title>
<style>
:root { color-scheme: light; --ink:#183233; --muted:#66736f; --line:#ded8ca;
        --blue:#356c69; --red:#bf5b45; --green:#4c765f; --gold:#d5a84b;
        --deep:#103537; --deep-2:#1f514f; --paper:#f7f3ea; --panel:rgba(247,243,234,.97); }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; color:var(--ink); background:var(--deep); font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
#map { position:fixed; inset:0; background:radial-gradient(circle at 18% 22%,rgba(94,148,128,.24),transparent 28%),radial-gradient(circle at 76% 70%,rgba(184,132,61,.18),transparent 30%),linear-gradient(135deg,#173e40 0%,#0d2d31 47%,#1a4745 100%); }
#map::before { content:""; position:absolute; inset:-18%; pointer-events:none; opacity:.48; background:radial-gradient(ellipse at 30% 44%,rgba(218,189,109,.16),transparent 20%),radial-gradient(ellipse at 70% 58%,rgba(87,145,122,.15),transparent 22%); filter:blur(24px); animation:map-breathe 14s ease-in-out infinite alternate; }
#map::after { content:""; position:absolute; inset:0; pointer-events:none; opacity:.22; background-image:linear-gradient(rgba(232,218,184,.16) 1px,transparent 1px),linear-gradient(90deg,rgba(232,218,184,.16) 1px,transparent 1px); background-size:64px 64px; mask-image:linear-gradient(90deg,rgba(0,0,0,.95),transparent 68%); }
@keyframes map-breathe { from { transform:translate3d(-1%,1%,0) scale(1); } to { transform:translate3d(2%,-1%,0) scale(1.06); } }
#panel { transition:filter .72s ease,transform .72s ease; }
body.intro-open #panel { filter:blur(10px); transform:translateY(16px) scale(.985); pointer-events:none; }
body.intro-open #mapHud, body.intro-open #mapLabel { opacity:.18; transition:opacity .72s ease; }
.site-intro { position:fixed; inset:0; z-index:1800; display:grid; place-items:center; padding:28px; color:#f8f2e5; background:radial-gradient(circle at 58% 45%,rgba(53,108,105,.32),transparent 30%),linear-gradient(135deg,rgba(9,35,38,.985),rgba(13,48,48,.96) 56%,rgba(23,55,48,.98)); transition:opacity .72s ease,transform .72s ease; overflow:hidden; }
.site-intro[hidden] { display:none; }
.site-intro::before { content:""; position:absolute; inset:6%; border:1px solid rgba(225,197,124,.2); border-radius:32px; pointer-events:none; }
.site-intro::after { content:"᚛ ᚜"; position:absolute; right:5vw; bottom:-7vw; color:rgba(231,201,135,.09); font:clamp(190px,30vw,460px)/1 Georgia,serif; letter-spacing:-.22em; transform:rotate(-9deg); pointer-events:none; }
.site-intro.is-dismissed { opacity:0; transform:scale(1.035); pointer-events:none; }
.intro-shell { position:relative; z-index:1; display:grid; grid-template-columns:minmax(0,1.08fr) minmax(280px,.92fr); align-items:center; gap:clamp(34px,7vw,100px); width:min(1180px,100%); }
.intro-main { max-width:690px; }
.intro-topline { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:34px; color:#dec17b; font-size:10px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
.intro-topline span:last-child { color:rgba(248,242,229,.48); }
.intro-kicker { display:flex; align-items:center; gap:10px; color:#a6cfaf; font-size:11px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
.intro-kicker::before { content:""; width:34px; height:1px; background:#e1bd66; }
.intro-main h2 { max-width:700px; margin:18px 0 0; font:700 clamp(48px,8vw,104px)/.88 Georgia,serif; letter-spacing:-.075em; }
.intro-main h2 em { color:#e1bd66; font-style:normal; }
.intro-main p { max-width:570px; margin:24px 0 0; color:rgba(248,242,229,.68); font-size:15px; line-height:1.65; }
.intro-actions { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-top:30px; }
.intro-actions button { min-height:42px; padding:9px 15px; border-color:rgba(225,189,102,.6); border-radius:999px; color:#163a3a; background:#e1bd66; font-size:11px; font-weight:800; letter-spacing:.04em; }
.intro-actions button:hover { border-color:#f7e2a8; color:#163a3a; background:#f0d48c; }
.intro-actions button.secondary { border-color:rgba(248,242,229,.22); color:rgba(248,242,229,.7); background:rgba(248,242,229,.06); }
.intro-actions button.secondary:hover { border-color:rgba(248,242,229,.55); color:#f8f2e5; background:rgba(248,242,229,.12); }
.intro-aside { position:relative; min-height:440px; display:grid; place-items:center; }
.intro-orbit { position:relative; width:min(39vw,430px); aspect-ratio:1; border:1px solid rgba(225,189,102,.26); border-radius:50%; transform:rotate(-12deg); }
.intro-orbit::before, .intro-orbit::after { content:""; position:absolute; inset:11%; border:1px dashed rgba(166,207,175,.32); border-radius:50%; }
.intro-orbit::after { inset:27%; border-style:solid; border-color:rgba(191,91,69,.4); }
.intro-orbit-line { position:absolute; top:50%; left:2%; width:96%; height:1px; background:linear-gradient(90deg,transparent,#e1bd66 22%,rgba(248,242,229,.55) 50%,transparent 78%); transform:rotate(37deg); }
.intro-orbit-line.second { transform:rotate(-53deg); background:linear-gradient(90deg,transparent,#77a897 20%,rgba(248,242,229,.42) 50%,transparent 80%); }
.intro-orbit-core { position:absolute; inset:39%; display:grid; place-items:center; border:1px solid #e1bd66; border-radius:50%; color:#153b3b; background:#e1bd66; box-shadow:0 0 0 14px rgba(225,189,102,.08),0 0 70px rgba(225,189,102,.22); font:700 clamp(28px,4vw,44px)/1 Georgia,serif; }
.intro-orbit-label { position:absolute; padding:5px 8px; border:1px solid rgba(248,242,229,.2); border-radius:999px; color:rgba(248,242,229,.66); background:rgba(8,31,34,.4); font:700 9px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; backdrop-filter:blur(8px); }
.intro-orbit-label.north { top:2%; left:50%; transform:translateX(-50%); }
.intro-orbit-label.east { top:51%; right:-5%; }
.intro-orbit-label.south { bottom:2%; left:50%; transform:translateX(-50%); }
.intro-orbit-label.west { top:51%; left:-7%; }
.intro-foot { position:absolute; right:28px; bottom:22px; left:28px; display:flex; align-items:center; justify-content:space-between; gap:20px; color:rgba(248,242,229,.42); font-size:10px; }
.intro-proof { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px 12px; min-width:0; color:rgba(248,242,229,.5); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.intro-proof span { white-space:nowrap; }
.intro-proof strong { color:#f5d887; font:700 14px/1 Georgia,serif; letter-spacing:-.04em; }
.intro-proof em { color:rgba(248,242,229,.4); font-style:normal; letter-spacing:0; text-transform:none; }
.intro-foot > span:last-child { margin-left:auto; color:#dec17b; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
@media (min-width:721px) { .intro-shell { transform:translateY(-18px); } .intro-proof { position:absolute; left:260px; right:140px; } }
#panel { position:fixed; z-index:1000; top:18px; right:18px; bottom:18px; width:min(780px,calc(100vw - 36px));
         display:flex; flex-direction:column; overflow-y:auto; overflow-x:hidden; border:1px solid rgba(228,218,193,.78); border-radius:26px;
         background:var(--panel); box-shadow:0 22px 80px rgba(4,20,23,.38); }
#panel > header { flex:0 0 auto; padding:24px 26px 22px; border-bottom:1px solid rgba(231,219,193,.2); color:#f7f2e6; background:linear-gradient(135deg,var(--deep) 0%,#154241 54%,#2b5c53 100%); position:relative; overflow:hidden; }
#panel > header::after { content:"᚛ ᚜"; position:absolute; right:22px; bottom:-26px; color:rgba(231,201,135,.16); font:130px/1 Georgia,serif; letter-spacing:-.18em; transform:rotate(-10deg); }
#panel > * { flex:0 0 auto; }
#panel > .table-wrap { flex:0 0 auto; }
#panel > header h1 { margin:0; max-width:620px; font-size:clamp(28px,4vw,44px); line-height:.97; letter-spacing:-.06em; font-weight:760; }
#panel > header h1 em { color:#e0b962; font-style:normal; }
#panel > header .subtitle { margin-top:12px; max-width:620px; color:rgba(247,242,230,.76); font-size:13px; line-height:1.55; }
.hero-topline { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:22px; color:#dac38d; font-size:10px; font-weight:750; letter-spacing:.14em; text-transform:uppercase; }
.hero-tag { padding:5px 9px; border:1px solid rgba(232,207,148,.34); border-radius:999px; color:#f2db9f; letter-spacing:.08em; }
.hero-note { max-width:610px; margin:14px 0 0; color:rgba(247,242,230,.58); font-size:11px; }
.header-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:18px; position:relative; z-index:1; }
.header-actions a, .review-link { display:inline-block; color:#173637; text-decoration:none; font-size:11px; font-weight:750; }
.header-actions a { padding:8px 11px; border:1px solid rgba(247,242,230,.26); border-radius:999px; background:#f1d893; }
.header-actions a:last-child { color:#f7f2e6; background:transparent; }
.header-actions a:hover, .review-link:hover { text-decoration:underline; }
.header-actions button { display:inline-block; padding:8px 11px; border:1px solid rgba(247,242,230,.26); border-radius:999px; color:#f7f2e6; background:transparent; font-size:11px; font-weight:750; }
.header-actions button:hover { border-color:#e0bd6e; color:#f1d893; }
.hero-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:20px; padding-top:15px; border-top:1px solid rgba(247,242,230,.14); }
.hero-metric { min-width:0; }
.hero-metric strong { display:block; color:#f7f0dc; font:700 18px/1 Georgia,serif; letter-spacing:-.04em; }
.hero-metric span { display:block; margin-top:4px; color:rgba(247,242,230,.5); font-size:9px; line-height:1.2; text-transform:uppercase; letter-spacing:.08em; }
.field-section { position:relative; padding:28px 24px 25px; border-bottom:1px solid #d9cfbd; color:#f7f0dc; background:linear-gradient(135deg,#153f40 0%,#1c504c 58%,#345d50 100%); overflow:hidden; }
.field-section::before { content:""; position:absolute; inset:0; opacity:.23; background-image:linear-gradient(rgba(238,218,174,.22) 1px,transparent 1px),linear-gradient(90deg,rgba(238,218,174,.22) 1px,transparent 1px); background-size:30px 30px; mask-image:linear-gradient(90deg,black,transparent 72%); pointer-events:none; }
.field-section::after { content:""; position:absolute; right:-86px; top:-100px; width:270px; height:270px; border:1px solid rgba(225,189,102,.34); border-radius:50%; box-shadow:0 0 0 21px rgba(225,189,102,.05),0 0 0 46px rgba(225,189,102,.04); pointer-events:none; }
.field-content { position:relative; z-index:1; display:grid; grid-template-columns:minmax(0,1fr) minmax(245px,.75fr); gap:22px; align-items:start; }
.field-kicker { display:flex; align-items:center; gap:8px; color:#e1bd66; font-size:10px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
.field-kicker::before { content:"01"; display:grid; place-items:center; width:23px; height:23px; border:1px solid rgba(225,189,102,.58); border-radius:50%; font:700 9px/1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-section h2 { max-width:620px; margin:14px 0 0; color:#f7f0dc; font:700 clamp(27px,3.4vw,44px)/.98 Georgia,serif; letter-spacing:-.055em; }
.field-section h2 em { color:#e1bd66; font-style:normal; }
.field-section p { max-width:650px; margin:12px 0 0; color:rgba(247,240,220,.7); font-size:12px; line-height:1.6; }
.field-principles { display:flex; flex-wrap:wrap; gap:6px; margin-top:17px; }
.field-principles button { display:inline-flex; align-items:center; gap:7px; padding:6px 8px; border:1px solid rgba(247,240,220,.18); border-radius:999px; color:rgba(247,240,220,.72); background:rgba(7,33,35,.2); font:700 9px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; cursor:pointer; transition:transform .18s ease,border-color .18s ease,color .18s ease,background .18s ease,box-shadow .18s ease; }
.field-principles button b { color:#e1bd66; font:700 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-principles button:hover, .field-principles button:focus-visible, .field-principles button.is-active { border-color:rgba(225,189,102,.78); color:#f7f0dc; background:rgba(7,33,35,.42); box-shadow:0 5px 14px rgba(4,20,23,.16); transform:translateY(-1px); }
.field-principles button:focus-visible { outline:2px solid #e1bd66; outline-offset:3px; }
.field-principles button.is-active b { color:#f5d887; }
.field-coordinate { min-height:187px; padding:15px; border:1px solid rgba(225,189,102,.3); background:rgba(8,34,36,.22); }
.field-coordinate-top { display:flex; align-items:center; justify-content:space-between; gap:12px; color:#dec17b; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.field-coordinate-top small { color:rgba(247,240,220,.48); font:10px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; }
.coordinate-plot { position:relative; height:104px; margin-top:14px; border-top:1px solid rgba(225,189,102,.38); border-bottom:1px solid rgba(225,189,102,.22); background:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),rgba(225,189,102,.16) 25%,transparent calc(25% + 1px)),repeating-linear-gradient(0deg,transparent 0,transparent calc(25% - 1px),rgba(225,189,102,.11) 25%,transparent calc(25% + 1px)); }
.coordinate-plot::before { content:""; position:absolute; top:50%; left:4%; width:90%; height:1px; background:linear-gradient(90deg,transparent,#8ab89f 17%,#e1bd66 48%,#bf5b45 83%,transparent); transform:rotate(-8deg); transform-origin:center; }
.coordinate-plot::after { content:""; position:absolute; top:var(--coordinate-y,14%); left:var(--coordinate-x,59%); width:7px; height:7px; border:2px solid #e1bd66; border-radius:50%; box-shadow:0 0 0 5px rgba(225,189,102,.1),0 0 30px rgba(225,189,102,.38); transform:translate(-50%,-50%); transition:top .45s ease,left .45s ease,box-shadow .45s ease; }
.coordinate-plot.is-focused::after { box-shadow:0 0 0 7px rgba(225,189,102,.14),0 0 34px rgba(225,189,102,.5); }
.coordinate-plot-readout { position:absolute; top:8px; right:8px; max-width:68%; overflow:hidden; color:rgba(247,240,220,.55); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; text-align:right; text-overflow:ellipsis; text-transform:uppercase; white-space:nowrap; }
.coordinate-plot.is-focused .coordinate-plot-readout { color:#f5d887; }
.coordinate-axis { position:absolute; color:rgba(247,240,220,.45); font:8px ui-monospace,SFMono-Regular,Menlo,monospace; pointer-events:none; }
.coordinate-latitude-axis { top:9px; bottom:20px; left:5px; display:flex; flex-direction:column; justify-content:space-between; align-items:flex-start; }
.coordinate-longitude-axis { right:6px; bottom:5px; left:32px; display:flex; justify-content:space-between; gap:4px; }
.coordinate-longitude-axis span, .coordinate-latitude-axis span { white-space:nowrap; }
.coordinate-note { margin-top:10px; color:rgba(247,240,220,.5); font-size:9px; line-height:1.35; }
.field-light { margin-top:10px; padding:10px; border:1px solid rgba(225,189,102,.22); background:rgba(7,33,35,.2); }
.field-light-top { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:#dec17b; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.field-light-top small { color:rgba(247,240,220,.45); font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; text-transform:none; }
.field-light strong { display:block; margin-top:6px; color:#f7f0dc; font:700 15px/1.05 Georgia,serif; letter-spacing:-.035em; }
.field-light-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:8px; }
.field-light-metric { min-width:0; padding:6px; border:1px solid rgba(225,189,102,.16); background:rgba(225,189,102,.05); }
.field-light-metric b, .field-light-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-light-metric b { color:#e1bd66; font:700 11px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-light-metric small { margin-top:3px; color:rgba(247,240,220,.46); font-size:7px; line-height:1.2; text-transform:uppercase; letter-spacing:.04em; }
.field-light-note { margin:8px 0 0; color:rgba(247,240,220,.48); font-size:8px; line-height:1.35; }
.field-shape { margin-top:10px; padding:10px; border:1px solid rgba(122,183,159,.26); background:rgba(7,33,35,.24); }
.field-shape-top { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:#8ab89f; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.field-shape-top small { max-width:56%; overflow:hidden; color:rgba(247,240,220,.45); font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; text-align:right; text-overflow:ellipsis; text-transform:none; white-space:nowrap; }
.field-shape strong { display:block; margin-top:6px; color:#f7f0dc; font:700 14px/1.08 Georgia,serif; letter-spacing:-.035em; }
.field-shape-grid { display:grid; grid-template-columns:minmax(82px,.82fr) minmax(0,1.18fr); gap:7px; margin-top:8px; }
.field-shape-visual { min-width:0; min-height:84px; padding:5px; border:1px solid rgba(122,183,159,.22); background:rgba(225,189,102,.04); }
.field-shape-visual svg { display:block; width:100%; height:74px; }
.field-shape-visual-note { display:block; margin-top:3px; color:rgba(247,240,220,.42); font:7px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-transform:uppercase; }
.field-shape-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; align-content:start; }
.field-shape-metric { min-width:0; padding:6px; border:1px solid rgba(122,183,159,.16); background:rgba(122,183,159,.05); }
.field-shape-metric b, .field-shape-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-shape-metric b { color:#8ab89f; font:700 11px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-shape-metric small { margin-top:3px; color:rgba(247,240,220,.46); font-size:7px; line-height:1.2; text-transform:uppercase; letter-spacing:.04em; }
.field-shape-note { margin:8px 0 0; color:rgba(247,240,220,.48); font-size:8px; line-height:1.35; }
.field-roots { margin-top:10px; padding:10px; border:1px solid rgba(225,194,118,.28); background:linear-gradient(135deg,rgba(7,33,35,.24),rgba(71,91,64,.24)); }
.field-roots-top { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:#e1c276; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.field-roots-top small { max-width:56%; overflow:hidden; color:rgba(247,240,220,.45); font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; text-align:right; text-overflow:ellipsis; text-transform:none; white-space:nowrap; }
.field-roots strong { display:block; margin-top:6px; color:#f7f0dc; font:700 14px/1.08 Georgia,serif; letter-spacing:-.035em; }
.field-roots-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:8px; }
.field-roots-metric { min-width:0; padding:6px; border:1px solid rgba(225,194,118,.16); background:rgba(225,194,118,.05); }
.field-roots-metric b, .field-roots-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-roots-metric b { color:#e1c276; font:700 10px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-roots-metric small { margin-top:3px; color:rgba(247,240,220,.46); font-size:7px; line-height:1.2; text-transform:uppercase; letter-spacing:.04em; }
.field-roots-note { margin:8px 0 0; color:rgba(247,240,220,.48); font-size:8px; line-height:1.35; }
.field-signals { position:relative; z-index:1; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:20px; }
.field-sequence { position:relative; z-index:1; display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:4px; margin-top:22px; padding-top:16px; }
.field-sequence::before { content:""; position:absolute; top:5px; right:4%; left:4%; height:1px; background:linear-gradient(90deg,#8ab89f,#e1bd66 46%,#bf5b45 83%,rgba(247,240,220,.2)); }
.field-sequence-step { position:relative; display:block; width:100%; min-width:0; padding:0 5px; border:0; color:rgba(247,240,220,.62); background:transparent; font:inherit; text-align:left; cursor:pointer; }
.field-sequence-step::before { content:""; position:absolute; top:-20px; left:8px; width:7px; height:7px; border:1px solid #e1bd66; border-radius:50%; background:#153f40; box-shadow:0 0 0 4px rgba(225,189,102,.1); }
.field-sequence-step:hover, .field-sequence-step:focus-visible, .field-sequence-step.is-active { color:#f7f0dc; }
.field-sequence-step:hover::before, .field-sequence-step:focus-visible::before, .field-sequence-step.is-active::before { background:#e1bd66; box-shadow:0 0 0 4px rgba(225,189,102,.2),0 0 18px rgba(225,189,102,.24); }
.field-sequence-step:focus-visible { outline:2px solid #e1bd66; outline-offset:4px; }
.field-sequence-step span { display:block; color:#e1bd66; font:700 9px/1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-sequence-step strong { display:block; margin-top:5px; color:#f7f0dc; font:700 13px/1.05 Georgia,serif; letter-spacing:-.03em; }
.field-sequence-step.is-active strong { color:#e1bd66; }
.field-sequence-step small { display:block; margin-top:4px; color:rgba(247,240,220,.46); font-size:9px; line-height:1.25; }
.field-signal { display:block; width:100%; min-width:0; min-height:192px; padding:11px; border:1px solid rgba(247,240,220,.16); color:#f7f0dc; background:rgba(9,37,39,.28); text-align:left; font:inherit; cursor:pointer; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.field-signal:hover, .field-signal:focus-visible { border-color:rgba(225,189,102,.72); background:rgba(9,37,39,.48); box-shadow:0 7px 18px rgba(4,20,23,.14); transform:translateY(-2px); }
.field-signal.is-active { border-color:#e1bd66; background:rgba(9,37,39,.58); box-shadow:0 0 0 2px rgba(225,189,102,.16); }
.field-signal:nth-child(2) { border-top:2px solid #e1bd66; }
.field-signal:nth-child(3) { border-top:2px solid #bf5b45; }
.field-signal:nth-child(4) { border-top:2px solid #8ab89f; }
.field-signal-top { display:flex; align-items:center; justify-content:space-between; gap:8px; color:rgba(247,240,220,.58); font:700 9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.06em; text-transform:uppercase; }
.field-signal-top b { color:#e1bd66; font:700 18px/1 Georgia,serif; letter-spacing:-.05em; }
.field-signal strong { display:block; margin-top:13px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.field-signal p { min-height:38px; margin:6px 0 0; color:rgba(247,240,220,.55); font-size:10px; line-height:1.4; }
.field-meter { height:4px; margin-top:10px; overflow:hidden; border-radius:99px; background:rgba(247,240,220,.12); }
.field-meter i { display:block; width:0; height:100%; border-radius:inherit; background:linear-gradient(90deg,#e1bd66,#bf5b45); transition:width .7s ease; }
.field-signal-action { display:block; margin-top:11px; color:#e1bd66; font:700 9px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; text-transform:uppercase; }
.field-signal-detail { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:8px; padding:11px 12px; border:1px solid rgba(225,189,102,.26); background:rgba(8,34,36,.24); }
.field-signal-detail > span { color:#e1bd66; font:700 9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.field-signal-detail strong { display:block; color:#f7f0dc; font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.field-signal-detail p { margin:4px 0 0; color:rgba(247,240,220,.56); font-size:10px; line-height:1.4; }
.field-walk { position:relative; z-index:1; margin-top:21px; padding-top:15px; border-top:1px solid rgba(225,189,102,.24); scroll-margin-top:54px; }
.field-walk-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.field-walk-head > div:first-child { min-width:0; }
.field-walk-kicker { display:block; color:#e1bd66; font:700 9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.12em; text-transform:uppercase; }
.field-walk h3 { margin:6px 0 0; color:#f7f0dc; font:700 24px/1.02 Georgia,serif; letter-spacing:-.045em; }
.field-walk h3 em { color:#e1bd66; font-style:normal; }
.field-walk-intro { max-width:560px; margin:6px 0 0; color:rgba(247,240,220,.58); font-size:10px; line-height:1.45; }
.field-walk-count { flex:0 0 auto; min-width:94px; padding:8px 9px; border:1px solid rgba(225,189,102,.3); color:rgba(247,240,220,.56); background:rgba(8,34,36,.25); text-align:right; }
.field-walk-count strong { display:block; color:#e1bd66; font:700 20px/1 Georgia,serif; }
.field-walk-count small { display:block; margin-top:4px; font-size:8px; line-height:1.25; text-transform:uppercase; letter-spacing:.08em; }
.field-walk-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:12px; }
.field-walk-stop { display:flex; flex-direction:column; min-width:0; min-height:245px; padding:11px; border:1px solid rgba(247,240,220,.18); color:#f7f0dc; background:rgba(8,34,36,.3); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.field-walk-stop:hover, .field-walk-stop:focus-visible { border-color:rgba(225,189,102,.74); background:rgba(8,34,36,.52); box-shadow:0 8px 20px rgba(4,20,23,.16); transform:translateY(-2px); }
.field-walk-stop.is-active { border-color:#e1bd66; background:rgba(8,34,36,.58); box-shadow:0 0 0 2px rgba(225,189,102,.14); }
.field-walk-stop-top { display:flex; align-items:center; justify-content:space-between; gap:6px; color:rgba(247,240,220,.48); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.06em; text-transform:uppercase; }
.field-walk-stop-top b { display:grid; flex:0 0 auto; width:25px; height:25px; place-items:center; border:1px solid rgba(225,189,102,.42); border-radius:50%; color:#e1bd66; font:700 14px/1 Georgia,serif; }
.field-walk-shape { display:flex; align-items:center; justify-content:space-between; gap:7px; min-height:70px; margin-top:10px; padding:6px 7px; border:1px solid rgba(225,189,102,.2); background:rgba(225,189,102,.055); }
.field-walk-shape svg { display:block; flex:1 1 auto; width:100%; height:62px; min-width:0; }
.field-walk-shape span { flex:0 0 54px; color:rgba(247,240,220,.4); font:700 7px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-align:right; text-transform:uppercase; }
.field-walk-stop h4 { min-height:31px; margin:12px 0 0; color:#f7f0dc; font:700 16px/1.06 Georgia,serif; letter-spacing:-.035em; }
.field-walk-stop p { min-height:56px; margin:7px 0 0; color:rgba(247,240,220,.56); font-size:9px; line-height:1.4; }
.field-walk-record { min-width:0; margin-top:10px; padding-top:8px; border-top:1px solid rgba(247,240,220,.13); }
.field-walk-record strong, .field-walk-record small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-walk-record strong { color:#f7f0dc; font:700 11px/1.2 Georgia,serif; }
.field-walk-record small { margin-top:3px; color:rgba(247,240,220,.45); font-size:8px; }
.field-walk-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; margin-top:9px; }
.field-walk-metric { min-width:0; padding:6px; border:1px solid rgba(225,189,102,.17); background:rgba(225,189,102,.06); }
.field-walk-metric small, .field-walk-metric b { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-walk-metric small { color:rgba(247,240,220,.42); font:7px/1.15 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.field-walk-metric b { margin-top:3px; color:#e1bd66; font:700 10px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-walk-action { display:block; margin-top:auto; padding-top:12px; color:#e1bd66; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.field-walk-controls { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:11px; padding-top:10px; border-top:1px solid rgba(225,189,102,.18); }
.field-walk-controls[hidden] { display:none; }
.field-walk-nav { min-width:116px; padding:7px 9px; border:1px solid rgba(225,189,102,.3); border-radius:0; color:#e1bd66; background:rgba(8,34,36,.24); font:700 8px/1.15 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.field-walk-nav:hover, .field-walk-nav:focus-visible { border-color:#e1bd66; color:#fff6df; background:rgba(8,34,36,.56); }
.field-walk-nav:disabled { cursor:not-allowed; opacity:.35; }
.field-walk-progress { flex:1 1 auto; min-width:0; color:rgba(247,240,220,.5); font:700 8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-align:center; text-transform:uppercase; }
.field-walk-note { margin:10px 0 0; color:rgba(247,240,220,.42); font-size:9px; line-height:1.4; }
.field-journey { margin-top:12px; padding:13px; border:1px solid rgba(225,189,102,.28); background:linear-gradient(135deg,rgba(8,34,36,.44),rgba(66,91,69,.28)); }
.field-journey-head { display:grid; grid-template-columns:minmax(0,1.08fr) minmax(230px,.92fr); gap:14px; align-items:start; }
.field-journey-head h4 { margin:6px 0 0; color:#f7f0dc; font:700 20px/1.05 Georgia,serif; letter-spacing:-.04em; }
.field-journey-head h4 em { color:#e1bd66; font-style:normal; }
.field-journey-head p { max-width:560px; margin:6px 0 0; color:rgba(247,240,220,.55); font-size:9px; line-height:1.45; }
.field-journey-readout { min-height:84px; padding:10px 11px; border:1px solid rgba(225,189,102,.36); background:rgba(8,34,36,.32); }
.field-journey-readout > span { color:#e1bd66; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.field-journey-readout strong { display:block; margin-top:7px; overflow-wrap:anywhere; color:#f7f0dc; font:700 14px/1.08 Georgia,serif; letter-spacing:-.03em; }
.field-journey-readout p { margin:5px 0 0; color:rgba(247,240,220,.55); font-size:8px; line-height:1.4; }
.field-journey-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:8px; }
.field-journey-actions button { min-height:26px; padding:4px 8px; border:1px solid rgba(225,189,102,.45); border-radius:6px; color:#173f40; background:#e1bd66; font:700 8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; text-transform:uppercase; }
.field-journey-actions button:hover, .field-journey-actions button:focus-visible { border-color:#fff4d6; color:#fff4d6; background:#315c57; }
.field-journey-actions button[hidden] { display:none; }
.field-journey-action-status { color:rgba(247,240,220,.45); font-size:8px; line-height:1.3; }
.field-journey-track { display:flex; align-items:stretch; gap:5px; margin-top:13px; padding:1px 0 5px; overflow-x:auto; scrollbar-color:#c6a85d rgba(247,240,220,.12); }
.field-journey-node { display:flex; flex:1 1 0; flex-direction:column; min-width:116px; padding:8px; border:1px solid rgba(247,240,220,.18); color:#f7f0dc; background:rgba(8,34,36,.3); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.field-journey-node:hover, .field-journey-node:focus-visible { border-color:#e1bd66; background:rgba(8,34,36,.54); box-shadow:0 6px 16px rgba(4,20,23,.16); transform:translateY(-2px); }
.field-journey-node[aria-pressed="true"] { border-color:#e1bd66; background:rgba(8,34,36,.62); box-shadow:0 0 0 2px rgba(225,189,102,.14); }
.field-journey-node-top { display:flex; align-items:center; justify-content:space-between; gap:5px; color:rgba(247,240,220,.48); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.field-journey-node-top b { display:grid; flex:0 0 auto; width:22px; height:22px; place-items:center; border:1px solid rgba(225,189,102,.42); border-radius:50%; color:#e1bd66; font:700 12px/1 Georgia,serif; }
.field-journey-node strong { display:block; margin-top:12px; overflow:hidden; color:#f7f0dc; font:700 13px/1.1 Georgia,serif; letter-spacing:-.025em; text-overflow:ellipsis; white-space:nowrap; }
.field-journey-node small { display:block; margin-top:5px; overflow:hidden; color:rgba(247,240,220,.46); font-size:8px; line-height:1.25; text-overflow:ellipsis; white-space:nowrap; }
.field-journey-segment { display:flex; flex:0 0 82px; flex-direction:column; justify-content:center; gap:5px; min-width:66px; color:rgba(247,240,220,.42); text-align:center; }
.field-journey-segment i { display:block; height:3px; min-width:14px; border-radius:99px; background:linear-gradient(90deg,rgba(225,189,102,.45),#e1bd66); transform:scaleX(var(--journey-weight,1)); transform-origin:left center; }
.field-journey-segment small { color:rgba(247,240,220,.48); font:700 7px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; white-space:nowrap; }
.field-journey-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:5px; margin-top:8px; }
.field-journey-metric { min-width:0; padding:7px 8px; border:1px solid rgba(225,189,102,.18); background:rgba(225,189,102,.055); }
.field-journey-metric b, .field-journey-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.field-journey-metric b { color:#e1bd66; font:700 12px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.field-journey-metric small { margin-top:4px; color:rgba(247,240,220,.42); font:7px/1.15 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.field-journey-note { margin:9px 0 0; color:rgba(247,240,220,.4); font-size:8px; line-height:1.4; }
.measure-ledger { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:1px; margin-top:8px; border:1px solid rgba(225,189,102,.2); background:rgba(225,189,102,.2); }
.measure-ledger article { min-width:0; min-height:78px; padding:10px; background:rgba(8,34,36,.3); }
.measure-ledger span { display:block; color:rgba(247,240,220,.47); font:700 9px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; }
.measure-ledger strong { display:block; margin-top:7px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.04em; }
.measure-ledger small { display:block; margin-top:5px; color:rgba(247,240,220,.44); font-size:9px; line-height:1.25; }
.field-footnote { position:relative; z-index:1; margin:15px 0 0; color:rgba(247,240,220,.43); font-size:9px; line-height:1.45; }
.maths-section { padding:22px 24px 24px; border-bottom:1px solid #0c3738; color:#f7f0dc; background:linear-gradient(140deg,#103d3e 0%,#1a5651 58%,#2b6b5d 100%); }
.maths-head { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; }
.maths-kicker { color:#e1bd66; font-size:10px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
.maths-head h2 { max-width:620px; margin:8px 0 0; color:#f7f0dc; font:700 clamp(27px,3vw,39px)/1.02 Georgia,serif; letter-spacing:-.05em; }
.maths-head h2 em { color:#e1bd66; font-style:normal; }
.maths-head p { max-width:650px; margin:10px 0 0; color:rgba(247,240,220,.68); font-size:11px; line-height:1.55; }
.maths-reading-context { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:13px; padding:11px 13px; border:1px solid rgba(225,189,102,.48); border-radius:10px; background:rgba(7,29,32,.3); }
.maths-reading-context[hidden] { display:none; }
.maths-reading-context > div { min-width:0; }
.maths-reading-context span { display:block; color:#e1bd66; font-size:8px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.maths-reading-context strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:#f7f0dc; font:700 17px/1.05 Georgia,serif; letter-spacing:-.035em; }
.maths-reading-context p { margin:5px 0 0; color:rgba(247,240,220,.62); font-size:9px; line-height:1.45; }
.maths-reading-context button { flex:0 0 auto; min-height:29px; padding:5px 9px; border:1px solid #e1bd66; border-radius:7px; color:#173b3d; background:#e1bd66; font-size:10px; font-weight:800; }
.maths-reading-context button:hover, .maths-reading-context button:focus-visible { border-color:#f7f0dc; color:#173b3d; background:#f7f0dc; }
.maths-notation { flex:0 0 148px; display:flex; align-items:center; justify-content:center; width:148px; height:108px; border:1px solid rgba(225,189,102,.42); border-radius:50%; color:#e1bd66; font:700 20px/1.5 Georgia,serif; letter-spacing:.08em; transform:rotate(-7deg); }
.maths-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:6px; margin-top:18px; }
.maths-card { min-width:0; min-height:184px; padding:11px; border:1px solid rgba(247,240,220,.2); border-radius:10px; color:#f7f0dc; background:rgba(7,29,32,.28); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.maths-card:hover, .maths-card:focus-visible { border-color:#e1bd66; background:rgba(7,29,32,.48); box-shadow:0 7px 18px rgba(4,20,23,.18); transform:translateY(-2px); }
.maths-card[aria-pressed="true"] { border-color:#e1bd66; background:rgba(7,29,32,.58); box-shadow:0 0 0 2px rgba(225,189,102,.16); }
.maths-card-top { display:flex; align-items:center; justify-content:space-between; gap:7px; color:rgba(247,240,220,.57); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; }
.maths-card-symbol { display:grid; width:31px; height:31px; place-items:center; margin-top:12px; border:1px solid rgba(225,189,102,.42); border-radius:50%; color:#e1bd66; font:700 16px/1 Georgia,serif; }
.maths-card h3 { min-height:28px; margin:10px 0 0; color:#f7f0dc; font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.maths-card-equation { margin-top:6px; color:#e1bd66; font:700 10px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
.maths-card p { min-height:44px; margin:7px 0 0; color:rgba(247,240,220,.6); font-size:9px; line-height:1.4; }
.maths-card-meta { display:block; margin-top:8px; color:rgba(247,240,220,.42); font:8px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
.maths-card-action { display:block; margin-top:8px; color:#e1bd66; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; text-transform:uppercase; }
.maths-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:8px; padding:10px 11px; border:1px solid rgba(225,189,102,.3); background:rgba(7,29,32,.27); }
.maths-readout > span { color:#e1bd66; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.maths-readout strong { display:block; color:#f7f0dc; font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.maths-readout p { margin:4px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.maths-caveat { margin:12px 0 0; color:rgba(247,240,220,.44); font-size:9px; line-height:1.45; }
.atlas-nav { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:10px; min-height:45px; padding:6px 18px; border-bottom:1px solid rgba(215,203,178,.9); background:rgba(248,244,236,.94); box-shadow:0 5px 14px rgba(31,63,59,.06); backdrop-filter:blur(12px); }
.atlas-nav-links { display:flex; align-items:center; gap:3px; min-width:0; overflow-x:auto; scrollbar-width:none; }
.atlas-nav-links::-webkit-scrollbar { display:none; }
.atlas-nav a { flex:0 0 auto; min-height:29px; padding:6px 9px; border:1px solid transparent; border-radius:999px; color:#69766e; font-size:10px; font-weight:800; text-decoration:none; }
.atlas-nav a:hover { border-color:#cdbf9e; color:var(--deep); background:#fbf7ee; }
.atlas-nav a[aria-current="page"] { border-color:#cdbf9e; color:var(--deep); background:#efe4ca; box-shadow:inset 0 -2px 0 var(--gold); }
.atlas-nav-status { min-width:0; margin-left:auto; overflow:hidden; color:#897c67; font-size:9px; font-weight:750; letter-spacing:.08em; text-overflow:ellipsis; text-transform:uppercase; white-space:nowrap; }
.atlas-section { scroll-margin-top:54px; }
.review-state { color:#526071; font-size:10px; white-space:nowrap; }
.review-state.not_queued { color:var(--muted); font-style:italic; }
.review-state.supportive { color:var(--green); font-weight:700; }
.review-state.ambiguous { color:#a05a00; font-weight:700; }
.review-state.not_supportive { color:var(--red); font-weight:700; }
.kpis { display:grid; grid-template-columns:repeat(5,1fr); gap:7px; padding:10px 12px; border-bottom:1px solid var(--line); }
.kpi { min-width:0; padding:8px 9px; border:1px solid var(--line); border-radius:9px; background:#fff; }
.kpi b { display:block; font-size:18px; line-height:1.1; }
.kpi span { color:var(--muted); font-size:10px; }
.filters { display:grid; grid-template-columns:1.6fr 1fr 1fr; gap:7px; padding:10px 12px 8px; border-bottom:1px solid var(--line); }
input,select,button { min-height:31px; border:1px solid #cfd5dd; border-radius:7px; background:#fff; color:var(--ink); padding:5px 8px; font:inherit; }
input[type=range] { padding:0; accent-color:var(--blue); }
button { cursor:pointer; font-weight:600; }
button:hover { border-color:var(--blue); color:var(--blue); }
.quick-views { display:flex; align-items:center; flex-wrap:wrap; gap:5px; grid-column:1 / -1; }
.quick-views-label { margin-right:2px; color:var(--muted); font-size:10px; font-weight:750; letter-spacing:.05em; text-transform:uppercase; }
.quick-view { min-height:27px; padding:4px 8px; border-color:#d5cbbd; border-radius:999px; color:#5d6d66; background:#fffdf8; font-size:10px; font-weight:750; }
.quick-view:hover, .quick-view[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.quick-view[aria-pressed="true"]::before { content:"• "; color:#e5c874; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.sort-button { width:100%; min-height:auto; padding:0; border:0; border-radius:0; background:transparent; color:inherit; text-align:left; font-size:inherit; font-weight:inherit; }
.sort-button:focus-visible, tr[data-id]:focus-visible td { outline:2px solid var(--blue); outline-offset:-2px; }
.filter-wide { grid-column:1 / -1; display:flex; align-items:center; gap:8px; color:var(--muted); font-size:11px; }
.filter-wide input { flex:1; }
.checks { display:flex; flex-wrap:wrap; gap:8px; grid-column:1 / -1; color:var(--muted); font-size:11px; }
.checks label { display:flex; align-items:center; gap:3px; }
.checks input { min-height:auto; }
.route-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; }
.route-grid label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:10px; }
.route-grid input, .route-grid select { min-width:0; }
.route-grid .route-check { flex-direction:row; align-items:center; grid-column:span 2; }
.route-grid .route-check input { min-height:auto; }
.route-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; grid-column:1 / -1; }
.route-actions button { min-width:80px; }
.route-status { margin-top:7px; }
.route-share-status { flex:1 1 180px; min-width:180px; }
.route-compare { margin-top:10px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-compare-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-compare-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-compare-grid { display:grid; grid-template-columns:minmax(0,2fr) minmax(190px,1fr); gap:7px; margin-top:7px; }
.route-compare-grid label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:10px; }
.route-compare-profiles { min-height:78px; resize:vertical; font:10px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }
.route-compare-actions { display:flex; align-content:flex-start; align-items:flex-start; flex-wrap:wrap; gap:7px; }
.route-compare-actions .route-check { flex:1 1 100%; min-width:150px; }
.route-compare-actions .route-check input { min-height:auto; }
.route-compare-status { margin-top:7px; }
.route-compare-results { margin-top:8px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-compare-results[hidden] { display:none; }
.route-compare-results-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-compare-results-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-compare-table-wrap { margin-top:7px; overflow:auto; border:1px solid #e4dccd; border-radius:7px; background:#fff; }
.route-compare-table { width:100%; min-width:700px; border-collapse:collapse; font-size:10px; }
.route-compare-table th, .route-compare-table td { padding:5px 6px; border-bottom:1px solid #eee8dc; text-align:left; vertical-align:top; }
.route-compare-table thead th { color:var(--muted); background:#f7f2e8; font-size:9px; letter-spacing:.04em; text-transform:uppercase; white-space:nowrap; }
.route-compare-table tbody tr:last-child th, .route-compare-table tbody tr:last-child td { border-bottom:0; }
.route-compare-table tbody th { color:var(--deep); font-variant-numeric:tabular-nums; }
.route-compare-table td strong, .route-compare-table td small, .route-compare-table th strong, .route-compare-table th small { display:block; overflow-wrap:anywhere; }
.route-compare-table td small, .route-compare-table th small { margin-top:2px; color:var(--muted); font-weight:500; }
.route-compare-table .route-compare-unreachable { color:#a5322e; font-weight:700; }
.route-compare-path { min-height:26px; padding:4px 7px; font-size:10px; }
.route-compare-path[aria-current="true"] { border-color:var(--blue); color:var(--blue); background:#eef5f2; }
.route-compare-result { max-height:180px; }
.route-matrix { margin-top:10px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-matrix-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-matrix-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-matrix-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; margin-top:7px; }
.route-matrix-grid label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:10px; }
.route-matrix-points { min-height:78px; resize:vertical; font:10px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }
.route-matrix-actions { display:flex; align-items:flex-start; flex-wrap:wrap; gap:7px; grid-column:1 / -1; }
.route-matrix-actions .route-check { min-width:150px; }
.route-matrix-actions .route-check input { min-height:auto; }
.route-matrix-status { margin-top:7px; }
.route-matrix-results { margin-top:8px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-matrix-results[hidden] { display:none; }
.route-matrix-results-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-matrix-results-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-matrix-table-wrap { margin-top:7px; max-height:270px; overflow:auto; border:1px solid #e4dccd; border-radius:7px; background:#fff; }
.route-matrix-table { width:100%; min-width:690px; border-collapse:collapse; font-size:10px; }
.route-matrix-table th, .route-matrix-table td { padding:5px 6px; border-bottom:1px solid #eee8dc; text-align:left; vertical-align:top; }
.route-matrix-table thead th { position:sticky; top:0; color:var(--muted); background:#f7f2e8; font-size:9px; letter-spacing:.04em; text-transform:uppercase; white-space:nowrap; }
.route-matrix-table tbody tr:last-child th, .route-matrix-table tbody tr:last-child td { border-bottom:0; }
.route-matrix-table tbody th { color:var(--deep); font-variant-numeric:tabular-nums; }
.route-matrix-table td strong, .route-matrix-table td small, .route-matrix-table th strong, .route-matrix-table th small { display:block; overflow-wrap:anywhere; }
.route-matrix-table td small, .route-matrix-table th small { margin-top:2px; color:var(--muted); font-weight:500; }
.route-matrix-table .route-matrix-unreachable { color:#a5322e; font-weight:700; }
.route-matrix-path { min-height:26px; padding:4px 7px; font-size:10px; }
.route-matrix-path[aria-current="true"] { border-color:var(--blue); color:var(--blue); background:#eef5f2; }
.route-matrix-result { max-height:180px; }
.route-maneuvers { margin-top:8px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-maneuver-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-maneuver-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-maneuver-list { display:grid; gap:4px; margin:7px 0 0; padding:0; list-style:none; }
.route-maneuver { display:grid; grid-template-columns:25px minmax(0,1fr); width:100%; min-height:0; padding:6px; border:1px solid #e4dccd; border-radius:7px; color:var(--ink); background:#fff; text-align:left; }
.route-maneuver:hover, .route-maneuver[aria-current="true"] { border-color:var(--blue); background:#eef5f2; }
.route-maneuver-index { display:grid; place-items:center; width:20px; height:20px; border-radius:50%; color:#f7f2e6; background:var(--deep-2); font-size:10px; font-weight:800; }
.route-maneuver-copy { min-width:0; }
.route-maneuver-copy strong, .route-maneuver-copy small { display:block; overflow-wrap:anywhere; }
.route-maneuver-copy strong { color:var(--deep); font-size:11px; line-height:1.3; }
.route-maneuver-copy small { margin-top:2px; color:var(--muted); font-size:10px; font-weight:500; }
.route-segments { margin-top:8px; padding:8px; border:1px solid #cfc09c; border-radius:9px; background:#fffdf8; }
.route-segment-heading { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:var(--deep); font-size:11px; }
.route-segment-heading small { color:var(--muted); font-size:10px; font-weight:500; }
.route-segment-table-wrap { margin-top:7px; max-height:260px; overflow:auto; border:1px solid #e4dccd; border-radius:7px; background:#fff; }
.route-segment-table { width:100%; min-width:570px; border-collapse:collapse; font-size:10px; }
.route-segment-table th, .route-segment-table td { padding:5px 6px; border-bottom:1px solid #eee8dc; text-align:left; vertical-align:top; }
.route-segment-table thead th { position:sticky; top:0; color:var(--muted); background:#f7f2e8; font-size:9px; letter-spacing:.04em; text-transform:uppercase; }
.route-segment-table tbody tr:last-child th, .route-segment-table tbody tr:last-child td { border-bottom:0; }
.route-segment-table tbody th { color:var(--deep); font-variant-numeric:tabular-nums; white-space:nowrap; }
.route-segment-table td strong, .route-segment-table td small { display:block; overflow-wrap:anywhere; }
.route-segment-table td strong { color:var(--deep); font-weight:700; }
.route-segment-table td small { margin-top:2px; color:var(--muted); }
.route-segment-checks summary { cursor:pointer; color:var(--blue); font-weight:700; }
.route-segment-check-list { display:grid; gap:3px; min-width:220px; margin:5px 0 0; padding-left:16px; color:var(--muted); }
.route-segment-check-list li { overflow-wrap:anywhere; }
.route-result { max-height:180px; margin:7px 0 0; padding:7px; overflow:auto; border:1px solid var(--line);
                border-radius:7px; background:#f7f8fa; white-space:pre-wrap; word-break:break-word; font:11px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace; }
.toolbar { display:flex; justify-content:space-between; align-items:center; gap:6px; padding:8px 12px; border-bottom:1px solid var(--line); }
.toolbar small { color:var(--muted); }
.toolbar .actions { display:flex; gap:5px; }
.toolbar-left { display:flex; align-items:center; gap:8px; min-width:0; flex-wrap:wrap; }
.toolbar-actions { display:flex; align-items:center; gap:5px; flex-wrap:wrap; justify-content:flex-end; }
.active-filter { color:var(--blue); font-size:11px; font-weight:700; }
.runtime-status { display:inline-block; max-width:100%; padding:2px 6px; border-radius:5px; color:#526071; background:#eef1f5; font-size:10px; }
.runtime-status.pass { color:#166534; background:#e8f5ee; font-weight:700; }
.runtime-status.incomplete { color:#a05a00; background:#fff7e8; font-weight:700; }
.runtime-status.fail { color:#a5322e; background:#fff1f0; font-weight:700; }
.runtime-reload { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:8px 0 0; padding:8px 10px; border:1px solid #e1b45f; border-radius:6px; color:#744b00; background:#fff8e8; font-size:11px; }
.runtime-reload[hidden] { display:none; }
.runtime-reload button { border:1px solid #a87922; border-radius:4px; padding:4px 8px; color:#fff; background:#8b6419; font:inherit; font-weight:700; cursor:pointer; }
.report-error { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:8px 0 0; padding:8px 10px; border:1px solid #d48b87; border-radius:6px; color:#7b2621; background:#fff1f0; font-size:11px; }
.report-error[hidden] { display:none; }
.report-error button { border:1px solid #a5322e; border-radius:4px; padding:4px 8px; color:#fff; background:#a5322e; font:inherit; font-weight:700; cursor:pointer; }
.clear-button { color:#526071; font-size:11px; }
.selection-card { margin:0 18px 10px; padding:13px 14px; border:1px solid #cfc09c; border-radius:12px; background:linear-gradient(135deg,#f7f0df 0%,#edf3eb 100%); box-shadow:0 7px 18px rgba(31,63,59,.07); scroll-margin-top:54px; }
.selection-card[hidden] { display:none; }
.selection-card.selection-card-arrived { animation:selection-arrival .72s ease both; }
@keyframes selection-arrival { 0% { transform:translateY(7px); box-shadow:0 0 0 0 rgba(191,91,69,0); } 45% { box-shadow:0 0 0 5px rgba(191,91,69,.18),0 12px 28px rgba(31,63,59,.13); } 100% { transform:translateY(0); box-shadow:0 7px 18px rgba(31,63,59,.07); } }
.selection-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.selection-kicker { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.selection-card h2 { margin:4px 0 0; color:var(--deep); font:700 21px/1.05 Georgia,serif; letter-spacing:-.04em; }
.selection-card p { margin:5px 0 0; color:#617069; font-size:10px; }
.selection-close { flex:0 0 auto; min-height:27px; padding:4px 8px; color:#65726a; background:rgba(255,253,248,.72); font-size:10px; }
.selection-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:11px; }
.selection-fact { min-width:0; padding:8px; border:1px solid rgba(207,192,156,.75); border-radius:8px; background:rgba(255,253,248,.7); }
.selection-fact span { display:block; color:#897c67; font-size:9px; font-weight:750; letter-spacing:.08em; text-transform:uppercase; }
.selection-fact strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:var(--deep); font-size:11px; line-height:1.3; }
.selection-focus-bar { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:8px; padding:8px 10px; border:1px solid #c7d0c7; border-radius:8px; background:linear-gradient(135deg,#edf2eb 0%,#f7f0e3 100%); }
.selection-focus-bar > div { min-width:0; }
.selection-focus-bar span { display:block; color:#527b85; font-size:8px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-focus-bar p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.3; }
.selection-focus-bar button { flex:0 0 auto; min-height:27px; padding:4px 8px; border:1px solid #4c765f; border-radius:7px; color:#fff8eb; background:#4c765f; font-size:10px; font-weight:800; }
.selection-focus-bar button:hover, .selection-focus-bar button:focus-visible { border-color:var(--deep); background:var(--deep); }
.selection-math { margin-top:8px; padding:9px 10px; border-left:3px solid var(--gold); background:rgba(255,253,248,.72); }
.selection-math span { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-math strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-math small { display:block; margin-top:4px; color:#6c786f; font-size:9px; line-height:1.35; }
.selection-math-action { display:inline-flex; align-items:center; min-height:27px; margin-top:7px; padding:4px 8px; border:1px solid #b9aa7c; border-radius:7px; color:#315c57; background:rgba(255,253,248,.78); font-size:10px; font-weight:750; }
.selection-math-action:hover, .selection-math-action:focus-visible { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.selection-culture { margin-top:8px; padding:9px 10px; border:1px solid rgba(76,118,95,.46); background:linear-gradient(135deg,rgba(232,241,231,.84),rgba(247,240,222,.78)); }
.selection-culture[hidden] { display:none; }
.selection-culture-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.selection-culture-head > div { min-width:0; }
.selection-culture-head span { display:block; color:#4c765f; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-culture-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-culture-head p { max-width:620px; margin:5px 0 0; color:#64746a; font-size:9px; line-height:1.4; }
.selection-culture-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; margin-top:9px; }
.selection-culture-fact { min-width:0; padding:7px 8px; border:1px solid rgba(76,118,95,.25); background:rgba(255,253,248,.62); }
.selection-culture-fact span, .selection-culture-fact strong { display:block; }
.selection-culture-fact span { color:#897c67; font-size:8px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.selection-culture-fact strong { margin-top:4px; overflow-wrap:anywhere; color:var(--deep); font-size:10px; line-height:1.25; }
.selection-culture-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:9px; }
.selection-culture-actions button { min-height:28px; padding:5px 9px; border:1px solid #4c765f; border-radius:7px; color:#fff8eb; background:#4c765f; font-size:10px; font-weight:800; }
.selection-culture-actions button:hover, .selection-culture-actions button:focus-visible { border-color:var(--deep); background:var(--deep); }
.selection-culture-actions button[data-selection-culture] { color:#315c57; background:rgba(255,253,248,.78); }
.selection-culture-note { margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(76,118,95,.24); color:#6d786f; font-size:9px; line-height:1.4; }
.selection-evidence { margin-top:8px; padding:9px 10px; border:1px solid rgba(110,139,127,.48); background:rgba(235,241,232,.68); }
.selection-evidence-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.selection-evidence-head > div { min-width:0; }
.selection-evidence-head span { display:block; color:#527b85; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-evidence-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-evidence-head p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.selection-evidence-status { flex:0 0 auto; padding:5px 7px; border:1px solid #b9cdbd; border-radius:999px; color:#356c69; background:#e6f0e8; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.selection-evidence-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:10px; }
.selection-evidence-step { min-width:0; min-height:145px; padding:9px; border:1px solid #c7d4c9; border-radius:8px; background:rgba(255,253,248,.74); }
.selection-evidence-step.check { border-color:#d8c69e; background:#fff9e9; }
.selection-evidence-step.missing { border-color:#d8c4bd; background:#fbf0ec; }
.selection-evidence-top { display:flex; align-items:center; justify-content:space-between; gap:5px; color:#897c67; font:700 8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.selection-evidence-top b { padding:3px 5px; border-radius:999px; color:#356c69; background:#e6f0e8; font-size:7px; }
.selection-evidence-step.check .selection-evidence-top b { color:#8b6419; background:#fff0cc; }
.selection-evidence-step.missing .selection-evidence-top b { color:#a04e40; background:#f8e3dd; }
.selection-evidence-step h4 { min-height:31px; margin:10px 0 0; color:var(--deep); font:700 14px/1.08 Georgia,serif; letter-spacing:-.03em; }
.selection-evidence-step > strong { display:block; margin-top:7px; overflow-wrap:anywhere; color:var(--deep); font:700 11px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-evidence-step p { min-height:38px; margin:6px 0 0; color:#69766e; font-size:8px; line-height:1.35; }
.selection-evidence-links { display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }
.selection-evidence-links a { color:#315c57; font-size:8px; font-weight:800; text-decoration:none; }
.selection-evidence-links a:hover { color:var(--red); text-decoration:underline; }
.selection-evidence-links small { color:#897c68; font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-evidence-note { margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(110,139,127,.28); color:#6d786f; font-size:9px; line-height:1.4; }
.selection-context { margin-top:8px; padding:9px 10px; border:1px solid rgba(91,119,132,.46); background:linear-gradient(135deg,rgba(232,239,237,.84),rgba(247,240,222,.72)); }
.selection-context-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.selection-context-head > div { min-width:0; }
.selection-context-head span { display:block; color:#527b85; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-context-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-context-head p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.selection-context-status { flex:0 0 auto; padding:5px 7px; border:1px solid #b9c9ce; border-radius:999px; color:#315c67; background:#e1ecec; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.selection-context-grid { display:grid; grid-template-columns:minmax(190px,.72fr) minmax(0,1.28fr); gap:7px; margin-top:10px; }
.selection-context-plot-frame { min-width:0; padding:7px; border:1px solid rgba(91,119,132,.26); background:rgba(255,253,248,.64); }
.selection-context-canvas { display:block; width:100%; height:150px; border:1px solid #c6d1cf; background:#f2f0e8; }
.selection-context-plot-note { display:block; margin-top:5px; color:#68776f; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-context-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; align-content:start; }
.selection-context-card { min-width:0; min-height:93px; padding:8px; border:1px solid #c5d1cd; border-radius:7px; color:var(--deep); background:rgba(255,253,248,.76); text-align:left; cursor:pointer; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.selection-context-card:hover, .selection-context-card:focus-visible { border-color:#527b85; background:#f7fbf7; box-shadow:0 5px 14px rgba(49,92,103,.11); transform:translateY(-1px); }
.selection-context-card-top { display:flex; align-items:center; justify-content:space-between; gap:5px; color:#897c67; font:700 8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; text-transform:uppercase; }
.selection-context-card-top b { color:#527b85; font-size:8px; }
.selection-context-card h4 { min-height:26px; margin:8px 0 0; overflow:hidden; color:var(--deep); font:700 13px/1.08 Georgia,serif; letter-spacing:-.03em; text-overflow:ellipsis; white-space:nowrap; }
.selection-context-card p { min-height:24px; margin:5px 0 0; overflow:hidden; color:#69766e; font-size:8px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }
.selection-context-card small { display:block; margin-top:6px; overflow:hidden; color:#315c57; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.selection-context-note { margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(91,119,132,.25); color:#6d786f; font-size:9px; line-height:1.4; }
.selection-fingerprint { display:grid; grid-template-columns:minmax(0,1fr) minmax(180px,.48fr); gap:8px; margin-top:8px; padding:9px 10px; border:1px solid rgba(207,192,156,.75); background:rgba(255,253,248,.72); }
.selection-fingerprint-head { min-width:0; }
.selection-fingerprint-head span { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-fingerprint-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-fingerprint-head p { margin:6px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.selection-fingerprint-canvas { display:block; width:100%; min-width:0; height:132px; border:1px solid #d8cdb8; background:#fbf7ee; }
.selection-fingerprint-note { display:block; margin-top:5px; color:#897c68; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-weave { display:grid; grid-template-columns:minmax(0,1fr) minmax(180px,.48fr); gap:8px; margin-top:8px; padding:9px 10px; border:1px solid rgba(110,139,127,.48); background:rgba(235,241,232,.68); }
.selection-weave-head { min-width:0; }
.selection-weave-head span { display:block; color:#527b85; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-weave-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-weave-head p { margin:6px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.selection-weave-canvas { display:block; width:100%; min-width:0; height:132px; border:1px solid #c6d2c8; background:#eef2e8; }
.selection-weave-note { display:block; margin-top:5px; color:#6d786f; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.selection-passport { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:8px; padding:10px; border:1px solid rgba(191,91,69,.46); background:linear-gradient(135deg,rgba(255,247,230,.82),rgba(239,242,231,.82)); }
.selection-passport > div:first-child { min-width:0; }
.selection-passport span { display:block; color:#bf5b45; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.selection-passport strong { display:block; margin-top:5px; color:var(--deep); font:700 14px/1.12 Georgia,serif; letter-spacing:-.03em; }
.selection-passport p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.selection-passport-actions { display:flex; align-items:center; flex:0 0 auto; flex-wrap:wrap; justify-content:flex-end; gap:7px; }
.selection-passport-actions button { min-height:29px; padding:5px 9px; border:1px solid #bf5b45; border-radius:7px; color:#fff8eb; background:#bf5b45; font-size:10px; font-weight:800; cursor:pointer; }
.selection-passport-actions button:hover, .selection-passport-actions button:focus-visible { border-color:#173b3a; background:#173b3a; }
.selection-passport-status { color:#6c786f; font-size:9px; }
.selection-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:10px; }
.selection-actions a, .selection-actions button { min-height:27px; padding:4px 8px; border:1px solid #c9b995; border-radius:7px; color:#315c57; background:rgba(255,253,248,.78); font-size:10px; font-weight:750; text-decoration:none; }
.selection-actions a:hover, .selection-actions button:hover { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.selection-actions button:disabled { cursor:not-allowed; opacity:.5; }
.selection-share-status { color:#6c786f; font-size:9px; }
.comparison-tray { margin:0 18px 10px; padding:14px; border:1px solid #bbaa7d; border-radius:14px; background:linear-gradient(135deg,#f3ead8 0%,#e8f0e8 100%); box-shadow:0 7px 18px rgba(31,63,59,.06); scroll-margin-top:54px; }
.comparison-tray[hidden] { display:none; }
.comparison-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.comparison-head h2 { margin:4px 0 0; color:var(--deep); font:700 21px/1.05 Georgia,serif; letter-spacing:-.04em; }
.comparison-head p { max-width:650px; margin:5px 0 0; color:#617069; font-size:10px; line-height:1.4; }
.comparison-head-actions { display:flex; align-items:flex-start; flex-wrap:wrap; justify-content:flex-end; gap:6px; }
.comparison-clear { flex:0 0 auto; min-height:27px; padding:4px 8px; color:#65726a; background:rgba(255,253,248,.72); font-size:10px; }
.comparison-copy { flex:0 0 auto; min-height:27px; padding:4px 8px; color:#315c57; background:rgba(255,253,248,.86); font-size:10px; }
.comparison-copy:disabled { cursor:not-allowed; opacity:.45; }
.comparison-share-status { flex-basis:100%; color:#6c786f; font-size:9px; text-align:right; }
.comparison-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; margin-top:11px; }
.comparison-target { min-width:0; padding:10px; border:1px solid rgba(187,170,125,.78); border-radius:10px; background:rgba(255,253,248,.72); }
.comparison-target-head { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
.comparison-target-head span { color:#897c67; font-size:8px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.comparison-target-head button { min-height:24px; padding:3px 6px; color:#65726a; background:rgba(255,253,248,.76); font-size:9px; }
.comparison-target h3 { margin:7px 0 0; overflow-wrap:anywhere; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.03em; }
.comparison-target > p { margin:4px 0 0; color:#6b776f; font-size:9px; line-height:1.35; }
.comparison-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; margin-top:9px; }
.comparison-metric { min-width:0; padding:6px; border:1px solid #ddd2bd; background:#fbf7ee; }
.comparison-metric span { display:block; color:#897c67; font:700 8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.comparison-metric strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:var(--deep); font:700 12px/1.05 Georgia,serif; }
.comparison-signals { margin-top:7px; padding:7px 8px; border-left:3px solid var(--gold); color:#6b776f; font-size:9px; line-height:1.4; }
.comparison-signals b { color:var(--deep); }
.comparison-delta { margin-top:8px; padding:10px 11px; border:1px solid #bbaa7d; background:rgba(255,253,248,.6); }
.comparison-delta > span { color:#897c67; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.comparison-delta strong { display:block; margin-top:6px; color:var(--deep); font:700 15px/1.08 Georgia,serif; }
.comparison-delta p { margin:5px 0 0; color:#68766e; font-size:9px; line-height:1.45; }
.comparison-awaiting { margin-top:10px; padding:10px; border:1px dashed #bbaa7d; color:#68766e; background:rgba(255,253,248,.5); font-size:10px; line-height:1.45; }
.comparison-relation { margin-top:8px; padding:11px 12px; border:1px solid #9ab1aa; border-radius:11px; background:linear-gradient(135deg,#e8f0ec 0%,#f4ead9 100%); }
.comparison-relation[hidden] { display:none; }
.comparison-relation-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.comparison-relation-head > div { min-width:0; }
.comparison-relation-head span { display:block; color:#527b85; font-size:9px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.comparison-relation-head strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.035em; }
.comparison-relation-head p { max-width:660px; margin:5px 0 0; color:#68766e; font-size:9px; line-height:1.45; }
.comparison-relation-status { flex:0 0 auto; padding:5px 7px; border:1px solid #b1c6bb; border-radius:999px; color:#315c57; background:#e2eee5; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.comparison-relation-grid { display:grid; grid-template-columns:minmax(190px,.75fr) minmax(0,1.25fr); gap:7px; margin-top:10px; }
.comparison-relation-plot-frame { min-width:0; padding:7px; border:1px solid rgba(82,123,133,.25); background:rgba(255,253,248,.66); }
.comparison-relation-canvas { display:block; width:100%; height:132px; border:1px solid #c6d1cf; background:#f2f0e8; }
.comparison-relation-plot-note { display:block; margin-top:5px; color:#68776f; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.comparison-relation-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; align-content:start; }
.comparison-relation-metric { min-width:0; min-height:69px; padding:8px; border:1px solid #c5d1cd; border-radius:7px; background:rgba(255,253,248,.74); }
.comparison-relation-metric span { display:block; color:#897c67; font:700 8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.06em; text-transform:uppercase; }
.comparison-relation-metric strong { display:block; margin-top:6px; overflow-wrap:anywhere; color:var(--deep); font:700 13px/1.08 Georgia,serif; letter-spacing:-.025em; }
.comparison-relation-metric small { display:block; margin-top:4px; color:#68766e; font-size:8px; line-height:1.3; }
.comparison-relation-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:9px; }
.comparison-relation-actions button { min-height:27px; padding:4px 8px; border-color:#9ab1aa; color:#315c57; background:#fffaf0; font-size:10px; font-weight:750; }
.comparison-relation-actions button:hover { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.comparison-relation-actions span { color:#68766e; font-size:9px; }
.comparison-relation-note { margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(82,123,133,.24); color:#6d786f; font-size:9px; line-height:1.4; }
.section { padding:10px 12px; border-bottom:1px solid var(--line); }
.section h2 { margin:0 0 7px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:#465467; }
.section-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.section-intro { margin:0 0 9px; color:var(--muted); font-size:11px; }
.pattern-summary { margin:0 0 8px; color:var(--muted); font-size:11px; }
.pattern-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; }
.pattern-card { display:flex; flex-direction:column; align-items:stretch; gap:5px; min-height:132px; padding:9px; border:1px solid var(--line);
                border-radius:9px; background:#fff; color:var(--ink); text-align:left; }
.pattern-card:hover, .pattern-card.active { border-color:var(--blue); box-shadow:0 0 0 2px rgba(37,99,235,.12); color:var(--ink); }
.pattern-card.active { background:#f5f9ff; }
.pattern-card-top { display:flex; align-items:flex-start; justify-content:space-between; gap:6px; }
.pattern-card-label { font-size:12px; line-height:1.2; }
.pattern-category { color:var(--muted); font-size:9px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; }
.pattern-count { color:var(--blue); font-size:15px; line-height:1; }
.pattern-meter { height:6px; border-radius:99px; background:#eef1f5; overflow:hidden; }
.pattern-meter-fill { height:100%; min-width:0; border-radius:99px; background:linear-gradient(90deg,#60a5fa,#2563eb); }
.pattern-card-meta { color:#526071; font-size:10px; }
.pattern-description { margin:0; color:var(--muted); font-size:10px; line-height:1.35; }
.bars { display:grid; gap:5px; }
.bar-row { display:grid; grid-template-columns:100px 1fr 58px; align-items:center; gap:6px; font-size:11px; }
.bar-track { height:10px; border-radius:20px; background:#eef1f5; overflow:hidden; }
.bar-fill { height:100%; border-radius:20px; background:var(--blue); }
.bar-fill.control { background:#98a2b3; }
.interpretation-headline { margin:0 0 8px; padding:7px 8px; border-left:3px solid var(--blue); background:#f7faff; }
.interpretation-grid { display:grid; gap:6px; }
.interpretation-card { padding:7px 8px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.interpretation-card header { display:flex; align-items:center; justify-content:space-between; gap:7px; padding:0; border:0; }
.interpretation-card p { margin:4px 0 0; }
.interpretation-status { display:inline-block; padding:1px 5px; border-radius:4px; background:#eef1f5; color:#526071; font-size:10px; white-space:nowrap; }
.interpretation-status.signal { background:#fff1f0; color:#a5322e; font-weight:700; }
.interpretation-status.suggestive, .interpretation-status.not_confirmed { background:#fff7e8; color:#a05a00; font-weight:700; }
.interpretation-status.pass, .interpretation-status.supportive { background:#e8f5ee; color:#166534; font-weight:700; }
.interpretation-status.fail { background:#fff1f0; color:#a5322e; font-weight:700; }
.interpretation-caveats { margin:7px 0 0; padding-left:18px; color:var(--muted); font-size:11px; }
.table-wrap { flex:1; overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:11px; }
th { position:sticky; top:0; z-index:2; padding:7px 6px; background:#f7f8fa; color:#526071; text-align:left; cursor:pointer; }
td { padding:6px; border-top:1px solid #eef0f3; vertical-align:top; }
tr:hover td { background:#f8fbff; }
tr[data-id] { cursor:pointer; }
tr[data-id].selected td { background:#f4ebd5; box-shadow:inset 3px 0 0 var(--gold); }
.score { color:var(--red); font-weight:700; }
.flag { display:inline-block; margin:1px 2px 1px 0; padding:1px 4px; border-radius:4px; background:#eef4ff; color:#2456a6; font-size:10px; }
.verdict-SIGNAL { color:#a5322e; font-weight:700; }
.verdict-suggestive { color:#a05a00; font-weight:700; }
.pagination { display:flex; justify-content:center; gap:7px; padding:8px; border-top:1px solid var(--line); }
.pagination button { min-width:74px; }
.empty { padding:18px; color:var(--muted); text-align:center; }
.footnote { color:var(--muted); font-size:11px; }
#map.offline-map { background:linear-gradient(145deg,#eef5f8,#dce8ee); }
#map.offline-map .offline-map-svg { width:100%; height:100%; display:block; }
#mapLoading { position:fixed; z-index:650; top:50%; left:50%; display:inline-flex; align-items:center; gap:8px; transform:translate(-50%,-50%); padding:9px 12px; border:1px solid rgba(232,218,184,.38); border-radius:999px; color:#f7f2e6; background:rgba(16,53,55,.86); box-shadow:0 12px 34px rgba(4,20,23,.22); backdrop-filter:blur(12px); font-size:11px; }
#mapLoading[hidden] { display:none; }
.map-loading-dot { width:8px; height:8px; border-radius:50%; background:#e1bd66; box-shadow:0 0 0 3px rgba(225,189,102,.15); animation:map-pulse 1.2s ease-in-out infinite; }
@keyframes map-pulse { 0%,100% { opacity:.42; transform:scale(.82); } 50% { opacity:1; transform:scale(1); } }
.offline-grid { stroke:#b7c8d2; stroke-width:1; stroke-dasharray:4 8; opacity:.75; }
.offline-outline { fill:rgba(37,99,235,.08); stroke:#526f80; stroke-width:1.2; }
.offline-selection-outline { fill:rgba(224,189,110,.18); stroke:#bf5b45; stroke-width:3; stroke-linejoin:round; vector-effect:non-scaling-stroke; pointer-events:none; }
.offline-selection-ring { fill:#e0bd6e; stroke:#fffaf0; stroke-width:2.5; vector-effect:non-scaling-stroke; pointer-events:none; }
.offline-selection-label { fill:#173f40; font:700 10px ui-monospace,SFMono-Regular,Menlo,monospace; paint-order:stroke; stroke:#fffaf0; stroke-width:3px; stroke-linejoin:round; }
.offline-route { fill:none; stroke:#1d4ed8; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; opacity:.9; pointer-events:none; }
.offline-route-focus { fill:#f59e0b; stroke:#fff; stroke-width:2.5; }
.offline-comparison-chord { pointer-events:none; }
.offline-comparison-chord line { stroke:#bf5b45; stroke-width:3; stroke-linecap:round; stroke-dasharray:10 7; opacity:.92; }
.offline-comparison-end { stroke:#fffaf0; stroke-width:2.5; }
.offline-comparison-end-a { fill:#bf5b45; }
.offline-comparison-end-b { fill:#527b85; }
.offline-comparison-label { fill:#173f40; font:700 11px ui-monospace,SFMono-Regular,Menlo,monospace; paint-order:stroke; stroke:#fffaf0; stroke-width:3px; stroke-linejoin:round; }
.offline-point { stroke:#17324d; stroke-width:1; cursor:pointer; opacity:.82; }
.offline-point:hover, .offline-point.selected { stroke:#111827; stroke-width:2.5; opacity:1; }
.selected-footprint-outline { stroke:#fffaf0; stroke-width:3; stroke-linejoin:round; vector-effect:non-scaling-stroke; filter:drop-shadow(0 0 4px rgba(191,91,69,.8)); }
.offline-map-note, .offline-selection { position:absolute; z-index:2; left:14px; max-width:350px; padding:7px 9px;
  border:1px solid #cbd5df; border-radius:8px; background:rgba(255,255,255,.94); box-shadow:0 2px 8px rgba(15,23,42,.12); }
.offline-map-note { top:14px; color:#526071; font-size:11px; }
.offline-selection { bottom:14px; color:var(--ink); font-size:11px; }
.quality-details { margin-top:8px; padding:6px 8px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.quality-details summary { cursor:pointer; color:var(--blue); font-weight:600; }
.quality-table { max-height:300px; margin-top:6px; overflow:auto; border:1px solid #eef0f3; border-radius:6px; }
.quality-table table { min-width:610px; }
.quality-table th { position:sticky; top:0; z-index:1; }
.quality-member { display:inline-flex; align-items:center; gap:3px; margin:1px 5px 1px 0; white-space:nowrap; }
.quality-member button { min-height:24px; padding:2px 5px; color:var(--blue); font-size:10px; }
.quality-member a { color:var(--muted); font-size:10px; text-decoration:none; }
.quality-member a:hover { color:var(--blue); text-decoration:underline; }
.quality-status { margin-top:6px; }
.quality-audit-controls { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:7px; }
.quality-audit-controls select { min-height:27px; padding:3px 6px; font-size:11px; }
.quality-audit-count { margin-left:auto; }
.quality-audit-table { max-height:310px; margin-top:6px; overflow:auto; border:1px solid #eef0f3; border-radius:6px; }
.quality-audit-table table { min-width:800px; }
.quality-audit-table th { position:sticky; top:0; z-index:1; }
.quality-badge { display:inline-block; padding:1px 4px; border-radius:4px; font-size:10px; white-space:nowrap; }
.quality-badge.ok, .quality-badge.provided { background:#e8f5ee; color:#166534; }
.quality-badge.missing, .quality-badge.check { background:#fff1f0; color:#a5322e; }
#mapLabel { position:fixed; z-index:2; left:30px; bottom:30px; width:260px; color:#f2e8d1; pointer-events:none; }
#mapLabel .map-label-kicker { display:block; margin-bottom:8px; color:#dfb75d; font-size:10px; font-weight:750; letter-spacing:.16em; text-transform:uppercase; }
#mapLabel strong { display:block; font:700 30px/.95 Georgia,serif; letter-spacing:-.04em; }
#mapLabel small { display:block; margin-top:10px; max-width:220px; color:rgba(242,232,209,.62); font-size:11px; line-height:1.45; }
#mapStamp { position:fixed; z-index:4; left:320px; bottom:30px; width:min(235px,calc(100vw - 350px)); padding:10px 12px; border:1px solid rgba(232,218,184,.27); border-radius:12px; color:#f7f2e6; background:rgba(16,53,55,.56); box-shadow:0 10px 26px rgba(4,20,23,.16); backdrop-filter:blur(12px); pointer-events:none; }
#mapStamp .map-stamp-kicker { display:block; color:#dfb75d; font-size:9px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
#mapStamp strong { display:block; margin-top:5px; color:#f7f0dc; font:700 18px/1 Georgia,serif; letter-spacing:-.04em; }
#mapStamp small { display:block; margin-top:5px; color:rgba(247,242,230,.58); font:9px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace; }
#mapHud { position:fixed; z-index:700; top:86px; left:24px; width:min(330px,calc(100vw - 48px)); color:#f7f2e6; }
.map-hud-card { padding:12px 13px 11px; border:1px solid rgba(232,218,184,.38); border-radius:16px; background:rgba(16,53,55,.86); box-shadow:0 12px 34px rgba(4,20,23,.24); backdrop-filter:blur(12px); }
.map-hud-topline { display:flex; align-items:center; justify-content:space-between; gap:10px; color:#d9c58d; font-size:9px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
.map-live-state { display:inline-flex; align-items:center; gap:5px; color:#dcebd9; font-size:9px; letter-spacing:.04em; white-space:nowrap; text-transform:none; }
.map-live-state::before { content:""; width:7px; height:7px; border-radius:50%; background:#7fd19a; box-shadow:0 0 0 3px rgba(127,209,154,.15); }
.map-live-state.loading::before { background:#e1bd66; box-shadow:0 0 0 3px rgba(225,189,102,.15); }
.map-live-state.offline::before, .map-live-state.error::before { background:#de866e; box-shadow:0 0 0 3px rgba(222,134,110,.15); }
.map-hud-title { margin-top:7px; font:700 21px/1 Georgia,serif; letter-spacing:-.04em; }
.map-hud-subtitle { margin-top:6px; color:rgba(247,242,230,.68); font-size:10px; line-height:1.4; }
.map-layer-switcher { display:flex; gap:4px; margin-top:10px; padding:3px; border:1px solid rgba(247,242,230,.13); border-radius:10px; background:rgba(7,29,32,.42); }
.map-layer-button { flex:1; min-height:28px; padding:4px 6px; border:0; border-radius:7px; color:rgba(247,242,230,.72); background:transparent; font-size:10px; font-weight:750; }
.map-layer-button:hover { border:0; color:#f7f2e6; }
.map-layer-button.active { color:var(--deep); background:#f1d893; }
.map-hud-meta { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:9px; color:rgba(247,242,230,.58); font-size:9px; }
.map-hud-meta span:last-child { color:#e0bd6e; font-weight:700; text-align:right; }
.map-constellation { margin-top:10px; padding-top:9px; border-top:1px solid rgba(247,242,230,.13); }
.map-constellation-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.map-constellation-head span { color:#d9c58d; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.map-constellation-head small { overflow:hidden; color:rgba(247,242,230,.47); font:9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.map-signal-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; margin-top:6px; }
.map-signal-cell { min-width:0; padding:6px 5px; border:1px solid rgba(247,242,230,.12); border-radius:7px; background:rgba(7,29,32,.34); }
.map-signal-cell b { display:block; color:#e0bd6e; font:700 13px/1 Georgia,serif; }
.map-signal-cell strong { display:block; margin-top:3px; overflow:hidden; color:#f7f2e6; font:700 10px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.map-signal-cell small { display:block; margin-top:3px; overflow:hidden; color:rgba(247,242,230,.47); font-size:8px; text-overflow:ellipsis; white-space:nowrap; }
.map-hud-actions { display:grid; grid-template-columns:1fr 1fr; gap:5px; margin-top:9px; }
.map-hud-action { width:100%; min-height:28px; margin-top:9px; padding:4px 8px; border:1px solid rgba(247,242,230,.2); border-radius:8px; color:#f7f2e6; background:rgba(247,242,230,.08); font-size:10px; }
.map-hud-actions .map-hud-action { margin-top:0; }
.map-hud-action:hover { border-color:#e0bd6e; color:#f1d893; }
.map-hud-action:disabled { cursor:wait; opacity:.52; }
.map-hud-note { margin-top:8px; color:rgba(247,242,230,.5); font-size:9px; line-height:1.35; }
.popup-focus { min-height:25px; margin-top:7px; padding:3px 7px; border-color:#d8c89e; color:#315c57; background:#fbf6e8; font-size:10px; }
.popup-focus:hover { border-color:#315c57; color:#173637; }
.studio-section { padding:0; border-bottom:1px solid var(--line); background:var(--paper); }
.studio-head { padding:22px 24px 16px; }
.studio-kicker { display:flex; align-items:center; gap:8px; margin-bottom:12px; color:var(--blue); font-size:10px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
.studio-kicker::before { content:""; width:24px; height:1px; background:var(--gold); }
.studio-head h2 { max-width:620px; margin:0; color:var(--deep); font:700 clamp(26px,3vw,36px)/1.04 Georgia,serif; letter-spacing:-.045em; }
.studio-head h2 em { color:var(--red); font-style:normal; }
.studio-head p { max-width:630px; margin:12px 0 0; color:#586764; font-size:12px; line-height:1.6; }
.studio-disclaimer { display:inline-flex; align-items:center; gap:6px; margin-top:12px; padding:5px 8px; border-radius:999px; color:#756c5b; background:#ede5d5; font-size:10px; }
.studio-disclaimer::before { content:"✳"; color:var(--red); }
.studio-share { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:12px; }
.studio-share button { min-height:28px; padding:5px 9px; border-color:#b9aa7c; color:#315c57; background:#fffaf0; font-size:10px; font-weight:750; }
.studio-share button:hover { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.studio-share-status { color:#6c786f; font-size:9px; }
.equation-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; padding:0 24px 18px; }
.equation-card { min-height:150px; padding:12px; border:1px solid #e1d8c7; border-radius:15px; color:var(--ink); background:#fbf8f1; text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.equation-card:hover { transform:translateY(-2px); border-color:#c8b07b; box-shadow:0 7px 18px rgba(31,63,59,.08); }
.equation-card.is-active { border-color:var(--deep-2); background:var(--deep); color:#f8f2e5; box-shadow:0 8px 20px rgba(16,53,55,.18); }
.equation-symbol { display:grid; width:34px; height:34px; place-items:center; margin-bottom:12px; border-radius:50%; color:var(--deep); background:#e4bd64; font:700 21px/1 Georgia,serif; }
.equation-card.is-active .equation-symbol { color:var(--deep); background:#f1d893; }
.equation-card b { display:block; min-height:29px; font-size:12px; line-height:1.2; }
.equation { margin-top:6px; color:var(--red); font:700 13px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:-.04em; }
.equation-card.is-active .equation { color:#e0bd6e; }
.equation-card p { margin:8px 0 0; color:#6d776f; font-size:10px; line-height:1.45; }
.equation-card.is-active p { color:rgba(248,242,229,.7); }
.studio-reference { margin:0 24px 15px; padding:13px 14px 14px; border:1px solid #b9aa7c; border-radius:15px; background:linear-gradient(135deg,#e8efe7 0%,#f4edde 100%); box-shadow:0 6px 16px rgba(31,63,59,.06); }
.studio-reference[hidden] { display:none; }
.studio-reference-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.studio-reference-head h3 { margin:3px 0 0; color:var(--deep); font:700 19px/1.05 Georgia,serif; letter-spacing:-.04em; }
.studio-reference-head p { margin:5px 0 0; color:#607068; font-size:10px; line-height:1.4; }
.studio-reference-clear { flex:0 0 auto; min-height:27px; padding:4px 8px; color:#65726a; background:rgba(255,253,248,.72); font-size:10px; }
.studio-reference-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:11px; }
.studio-reference-fact { min-width:0; padding:8px; border:1px solid rgba(185,170,124,.68); border-radius:8px; background:rgba(255,253,248,.64); }
.studio-reference-fact span { display:block; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.studio-reference-fact strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:var(--deep); font:700 13px/1.15 Georgia,serif; }
.studio-reference-fact small { display:block; margin-top:4px; overflow-wrap:anywhere; color:#6c786f; font-size:9px; line-height:1.25; }
.studio-reference-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:10px; }
.studio-reference-actions button { min-height:28px; padding:4px 9px; border-color:#b9aa7c; color:#315c57; background:#fffaf0; font-size:10px; font-weight:750; }
.studio-reference-actions button:hover { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.studio-reference-actions button:disabled { cursor:not-allowed; opacity:.48; }
.studio-reference-status { color:#6c786f; font-size:9px; }
.studio-reference-note { margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(185,170,124,.56); color:#6d6254; font-size:9px; line-height:1.4; }
.studio-pair-reference { border-color:#89a9a0; background:linear-gradient(135deg,#e3efeb 0%,#f3e8d7 100%); }
.studio-pair-reference .studio-kicker { color:#527b85; }
.studio-pair-reference .studio-reference-fact { border-color:rgba(137,169,160,.7); }
.studio-pair-reference .studio-reference-note { border-top-color:rgba(137,169,160,.56); }
.lab { margin:0 24px 22px; border:1px solid #ded3bf; border-radius:20px; background:#eee7d9; overflow:hidden; }
.lab-toolbar { display:flex; flex-wrap:wrap; align-items:end; gap:14px; padding:13px 15px; border-bottom:1px solid #ddd1bc; background:rgba(255,252,244,.68); }
.lab-toolbar label { display:flex; flex-direction:column; gap:5px; color:#6b756d; font-size:10px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
.lab-toolbar select, .lab-toolbar input { min-width:0; min-height:32px; border-color:#d6c9b2; border-radius:8px; background:#fffaf0; color:var(--deep); font-size:12px; text-transform:none; }
.lab-toolbar select { width:190px; }
.lab-toolbar input[type=range] { width:160px; padding:0; accent-color:var(--red); }
.module-readout { color:var(--red); font:700 12px ui-monospace,SFMono-Regular,Menlo,monospace; text-transform:none; }
.scenario-controls { padding:13px 15px 14px; border-bottom:1px solid #ddd1bc; background:#f4edde; }
.scenario-heading { display:flex; align-items:end; justify-content:space-between; gap:12px; margin-bottom:11px; color:#55645d; font-size:10px; line-height:1.4; }
.scenario-heading b { color:var(--deep); font-size:10px; letter-spacing:.08em; text-transform:uppercase; }
.scenario-heading small { color:#8b7c66; font-size:10px; text-align:right; }
.scenario-control-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }
.scenario-control-grid label { display:flex; flex-direction:column; gap:5px; color:#6b756d; font-size:9px; font-weight:750; letter-spacing:.05em; text-transform:uppercase; }
.scenario-control-grid output { color:var(--red); font:700 11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; text-transform:none; }
.scenario-control-grid input[type=range] { width:100%; min-height:18px; padding:0; accent-color:var(--red); }
.performance-controls { padding:13px 15px 14px; border-bottom:1px solid #d7ded3; background:#e8eee8; }
.performance-heading { display:flex; align-items:end; justify-content:space-between; gap:12px; margin-bottom:11px; color:#55645d; font-size:10px; line-height:1.4; }
.performance-heading b { color:var(--deep); font-size:10px; letter-spacing:.08em; text-transform:uppercase; }
.performance-heading small { color:#6f806f; font-size:10px; text-align:right; }
.performance-control-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }
.water-event-control { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:10px; padding-top:9px; border-top:1px solid rgba(73,104,94,.16); }
.water-event-control label { display:flex; align-items:center; gap:7px; color:#52675e; font-size:10px; font-weight:750; }
.water-event-control select { min-height:28px; padding:5px 8px; border:1px solid #cfc4b0; border-radius:6px; color:var(--deep); background:#f8f2e6; font:10px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.water-event-control p { margin:0; color:#718078; font-size:9px; line-height:1.35; }
.performance-control-grid label { display:flex; flex-direction:column; gap:5px; color:#5f7065; font-size:9px; font-weight:750; letter-spacing:.05em; text-transform:uppercase; }
.performance-control-grid output { color:var(--blue); font:700 11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:0; text-transform:none; }
.performance-control-grid input[type=range] { width:100%; min-height:18px; padding:0; accent-color:var(--blue); }
.lab-grid { display:grid; grid-template-columns:minmax(0,1.45fr) minmax(210px,.75fr); min-height:285px; }
.diagram-frame { position:relative; min-height:285px; padding:12px; background:linear-gradient(150deg,#e7ddc9,#f4edde); }
#designDiagram { display:block; width:100%; height:100%; min-height:260px; }
.diagram-caption { position:absolute; right:15px; bottom:12px; left:15px; color:#756e60; font-size:10px; }
.lab-copy { display:flex; flex-direction:column; justify-content:center; padding:20px; border-left:1px solid #ddd1bc; background:#f8f2e6; }
.lab-copy .lab-index { color:var(--red); font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.lab-copy h3 { margin:8px 0 0; color:var(--deep); font:700 23px/1.06 Georgia,serif; letter-spacing:-.04em; }
.lab-copy .lab-equation { margin:12px 0 0; color:var(--blue); font:700 12px ui-monospace,SFMono-Regular,Menlo,monospace; }
.lab-copy p { margin:11px 0 0; color:#64716b; font-size:11px; line-height:1.55; }
.lab-copy .lab-move { margin-top:13px; padding-top:11px; border-top:1px solid #e0d6c6; color:#4e625b; font-size:10px; line-height:1.45; }
.lab-copy .lab-move b { color:var(--deep); }
.lab-copy .lab-tags { display:flex; flex-wrap:wrap; gap:5px; margin-top:12px; }
.lab-copy .lab-tags span { padding:4px 6px; border-radius:999px; color:#52675e; background:#e6eee7; font-size:9px; }
.design-spec { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; border-top:1px solid #ddd1bc; background:#ddd1bc; }
.spec-card { min-height:95px; padding:12px; background:#f8f2e6; }
.spec-card span { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.spec-card strong { display:block; margin-top:6px; color:var(--deep); font:700 15px/1.08 Georgia,serif; letter-spacing:-.02em; }
.spec-card p { margin:6px 0 0; color:#69756e; font-size:10px; line-height:1.35; }
.spec-card small { display:block; margin-top:7px; color:var(--red); font-size:9px; }
.programme-controls { border-top:1px solid #d7ded3; background:#edf1e9; }
.design-schedule { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; border-top:1px solid #ddd1bc; background:#ddd1bc; }
.schedule-card { min-height:98px; padding:12px; background:#f8f2e6; }
.schedule-card span { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.schedule-card strong { display:block; margin-top:6px; color:var(--deep); font:700 18px/1.05 Georgia,serif; letter-spacing:-.03em; }
.schedule-card p { margin:6px 0 0; color:#69756e; font-size:10px; line-height:1.35; }
.schedule-note { grid-column:1 / -1; padding:9px 12px; color:#786b59; background:#f1e9db; font-size:10px; line-height:1.4; }
.sky-field { margin:14px 24px 0; padding:14px; border:1px solid #d9cfbd; border-radius:14px; background:linear-gradient(135deg,#eef1e8 0%,#f3ead7 100%); }
.sky-field-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; }
.sky-field-head h3 { margin:0; color:var(--deep); font:700 21px/1.08 Georgia,serif; letter-spacing:-.04em; }
.sky-field-head p { max-width:570px; margin:6px 0 0; color:#64736a; font-size:10px; line-height:1.45; }
.sky-field-kicker { display:block; margin-bottom:6px; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.sky-field-scope { flex:0 0 auto; padding:5px 8px; border:1px solid #c8c4aa; border-radius:999px; color:#49685e; background:#f8f2e6; font:700 9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-transform:uppercase; }
.sky-field-grid { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(250px,.75fr); gap:8px; margin-top:12px; }
.sky-plot { position:relative; min-height:170px; overflow:hidden; border:1px solid rgba(73,104,94,.24); background:linear-gradient(180deg,#c4d8d0 0%,#eef0dc 63%,#d2c39b 63%,#c6b68d 100%); }
.sky-plot::before { content:""; position:absolute; inset:15px 13% 37px; border:1px solid rgba(53,108,105,.38); border-bottom:0; border-radius:50% 50% 0 0; transform:perspective(260px) rotateX(8deg); }
.sky-plot::after { content:""; position:absolute; right:10%; bottom:31px; left:10%; height:1px; background:rgba(16,53,55,.45); box-shadow:0 -33px 0 rgba(16,53,55,.07),0 -66px 0 rgba(16,53,55,.07); }
.sky-sun { position:absolute; z-index:1; left:50%; bottom:var(--sky-sun-y,58%); width:19px; height:19px; border:2px solid #f4d783; border-radius:50%; background:#e1bd66; box-shadow:0 0 0 7px rgba(225,189,102,.19),0 0 24px rgba(225,189,102,.52); transform:translateX(-50%); }
.sky-plot-label { position:absolute; z-index:2; color:rgba(16,53,55,.62); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; }
.sky-plot-label.north { top:10px; left:11px; }
.sky-plot-label.south { right:11px; bottom:10px; }
.sky-sun-label { position:absolute; z-index:2; top:10px; right:11px; color:#64522f; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-align:right; text-transform:uppercase; }
.sky-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; border:1px solid #d1c7b5; background:#d1c7b5; }
.sky-fact { min-height:77px; padding:10px; background:#f8f2e6; }
.sky-fact span { display:block; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; }
.sky-fact strong { display:block; margin-top:7px; color:var(--deep); font:700 17px/1 Georgia,serif; }
.sky-fact small { display:block; margin-top:5px; color:#718078; font-size:9px; line-height:1.25; }
.sky-field-note { margin:10px 0 0; padding-top:9px; border-top:1px solid rgba(73,104,94,.2); color:#6d786f; font-size:9px; line-height:1.45; }
.water-field { margin:14px 24px 0; padding:14px; border:1px solid #c9d2d0; border-radius:14px; background:linear-gradient(135deg,#e5efed 0%,#f2eadb 100%); }
.water-field-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; }
.water-field-head h3 { margin:0; color:var(--deep); font:700 21px/1.08 Georgia,serif; letter-spacing:-.04em; }
.water-field-head p { max-width:570px; margin:6px 0 0; color:#64736a; font-size:10px; line-height:1.45; }
.water-field-kicker { display:block; margin-bottom:6px; color:#527b85; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.water-field-badge { flex:0 0 auto; padding:5px 8px; border:1px solid #b8c9c5; border-radius:999px; color:#315c57; background:#f8f2e6; font:700 9px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-transform:uppercase; }
.water-field-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(300px,1fr); gap:8px; margin-top:12px; }
.water-equation { min-height:166px; padding:14px; border:1px solid rgba(82,123,133,.24); background:rgba(248,242,230,.78); }
.water-equation > span { display:block; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.water-equation strong { display:block; margin-top:14px; color:var(--deep); font:700 clamp(22px,3vw,34px)/1 Georgia,serif; letter-spacing:-.06em; }
.water-equation p { margin:10px 0 0; color:#65766f; font-size:10px; line-height:1.45; }
.water-route { display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-top:15px; padding-top:9px; border-top:1px solid #d7d8cd; }
.water-route span { color:#897c67; font-size:8px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; }
.water-route b { color:#315c57; font-size:10px; text-align:right; }
.water-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; border:1px solid #c8d0c9; background:#c8d0c9; }
.water-fact { min-height:77px; padding:10px; background:#f8f2e6; }
.water-fact span { display:block; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; }
.water-fact strong { display:block; margin-top:7px; color:var(--deep); font:700 17px/1 Georgia,serif; }
.water-fact small { display:block; margin-top:5px; color:#718078; font-size:9px; line-height:1.25; }
.water-field-note { margin:10px 0 0; padding-top:9px; border-top:1px solid rgba(82,123,133,.2); color:#6d786f; font-size:9px; line-height:1.45; }
.brief-panel { padding:16px; border-top:1px solid #ddd1bc; background:#e9e4d8; }
.brief-head { display:flex; align-items:start; justify-content:space-between; gap:14px; }
.brief-head .studio-kicker { margin-bottom:7px; }
.brief-head h3 { margin:0; color:var(--deep); font:700 22px/1.05 Georgia,serif; letter-spacing:-.04em; }
.brief-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:6px; }
.brief-actions button { min-height:29px; padding:5px 8px; border-color:#c9c0ae; color:var(--deep); background:#f8f2e6; font-size:10px; }
.brief-actions button:hover { border-color:var(--blue); color:var(--blue); }
.design-brief { max-height:275px; margin:13px 0 0; padding:12px; overflow:auto; border:1px solid #d5cbb9; color:#425750; background:#f8f2e6; font:10px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap; }
.brief-status { margin:9px 0 0; color:#786b59; font-size:10px; line-height:1.4; }
.typology-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; padding:0 24px 22px; }
.typology-card { min-height:118px; padding:13px; border-top:2px solid var(--gold); background:#f1ebdf; }
.typology-card:nth-child(2) { border-top-color:var(--red); }
.typology-card:nth-child(3) { border-top-color:var(--blue); }
.typology-card:nth-child(4) { border-top-color:var(--green); }
.typology-card span { color:#897c67; font-size:9px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.typology-card h3 { margin:7px 0 0; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.03em; }
.typology-card p { margin:7px 0 0; color:#66736d; font-size:10px; line-height:1.45; }
.typology-move { display:block; margin-top:8px; color:var(--red); font:700 10px ui-monospace,SFMono-Regular,Menlo,monospace; }
.studio-metrics { display:grid; grid-template-columns:1.1fr 1fr 1fr; gap:8px; margin:0 24px 22px; padding:13px; border:1px solid #d9cfbd; border-radius:14px; background:#f0e9da; }
.studio-metric { display:flex; flex-direction:column; gap:4px; padding-right:10px; border-right:1px solid #d8cdb9; }
.studio-metric:last-child { border-right:0; }
.studio-metric strong { color:var(--deep); font:700 21px/1 Georgia,serif; }
.studio-metric span { color:#6d776e; font-size:10px; line-height:1.25; }
.studio-metric small { color:#8b7c66; font-size:9px; }
.evidence-bridge { margin:0 24px 24px; padding:16px; border:1px solid #d9cfbd; border-radius:16px; background:#eee7d9; }
.evidence-bridge-head { display:flex; align-items:start; justify-content:space-between; gap:14px; }
.evidence-bridge-head .studio-kicker { margin-bottom:7px; }
.evidence-bridge-head h3 { margin:0; color:var(--deep); font:700 22px/1.05 Georgia,serif; letter-spacing:-.04em; }
.evidence-badge { padding:5px 8px; border:1px solid #cbbda5; border-radius:999px; color:#6e6456; background:#f8f2e6; font-size:9px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; white-space:nowrap; }
.evidence-badge.pass { color:#356c69; border-color:#a7c1b0; background:#e6f0e8; }
.evidence-badge.incomplete { color:#9b632d; border-color:#e2bf8b; background:#fff3dc; }
.evidence-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:13px; }
.evidence-grid article { min-height:150px; padding:12px; border:1px solid #ddd2c0; background:#f8f2e6; }
.evidence-index { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.evidence-grid article b { display:block; margin-top:9px; color:var(--deep); font-size:12px; line-height:1.25; }
.evidence-grid article p { min-height:45px; margin:7px 0 0; color:#6a766e; font-size:10px; line-height:1.45; }
.evidence-grid article a { color:var(--blue); font-size:10px; font-weight:750; text-decoration:none; }
.evidence-grid article a:hover { text-decoration:underline; }
.evidence-caveat { margin:12px 0 0; padding:9px 10px; border-left:3px solid var(--red); color:#6d6254; background:#f4e9d8; font-size:10px; line-height:1.45; }
.culture-section { padding:24px; border-bottom:1px solid #d9cfbd; background:linear-gradient(135deg,#f5eee1 0%,#e9efe8 100%); }
.culture-head { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; }
.culture-head h2 { max-width:560px; margin:0; color:var(--deep); font:700 clamp(26px,3vw,38px)/1.02 Georgia,serif; letter-spacing:-.05em; }
.culture-head p { max-width:650px; margin:11px 0 0; color:#5d6d66; font-size:12px; line-height:1.6; }
.culture-reading-context { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:12px; padding:11px 13px; border:1px solid #b9cdbd; border-radius:12px; background:linear-gradient(135deg,#e6efe7 0%,#f5eddd 100%); }
.culture-reading-context[hidden] { display:none; }
.culture-reading-context > div { min-width:0; }
.culture-reading-context span { display:block; color:#527b85; font-size:8px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.culture-reading-context strong { display:block; margin-top:5px; overflow-wrap:anywhere; color:var(--deep); font:700 17px/1.05 Georgia,serif; letter-spacing:-.035em; }
.culture-reading-context p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.culture-reading-context button { flex:0 0 auto; min-height:29px; padding:5px 9px; border:1px solid #4c765f; border-radius:7px; color:#fff8eb; background:#4c765f; font-size:10px; font-weight:800; }
.culture-reading-context button:hover, .culture-reading-context button:focus-visible { border-color:var(--deep); background:var(--deep); }
.culture-mark { flex:0 0 142px; display:flex; flex-direction:column; align-items:center; justify-content:center; width:142px; height:142px; border:1px solid #c3b58f; border-radius:50%; color:#f8f2e6; background:var(--deep); box-shadow:0 8px 24px rgba(16,53,55,.13); transform:rotate(5deg); }
.culture-mark span { color:#e1c276; font-size:9px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
.culture-mark b { margin-top:5px; color:#f7f0dc; font:700 31px/1 Georgia,serif; letter-spacing:-.07em; }
.culture-mark small { margin-top:7px; color:rgba(247,240,220,.62); font-size:9px; }
.culture-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-top:20px; }
.culture-card { min-height:236px; padding:14px; border:1px solid #d6cdbd; background:rgba(255,252,244,.7); box-shadow:0 5px 14px rgba(31,63,59,.04); }
.culture-card:nth-child(2) { border-top:2px solid var(--red); }
.culture-card:nth-child(3) { border-top:2px solid var(--blue); }
.culture-card:nth-child(4) { border-top:2px solid var(--green); }
.culture-card-top { display:flex; align-items:center; justify-content:space-between; gap:8px; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.culture-card-top small { color:#6e8177; font-size:9px; font-weight:650; letter-spacing:.02em; text-transform:none; }
.culture-card strong { display:block; margin-top:25px; color:var(--deep); font:700 30px/.95 Georgia,serif; letter-spacing:-.06em; }
.culture-card h3 { margin:10px 0 0; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.03em; }
.culture-card p { min-height:54px; margin:8px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.culture-card p b { color:var(--red); }
.culture-focus { display:flex; align-items:center; justify-content:space-between; width:100%; min-height:29px; margin-top:14px; padding:5px 8px; border:1px solid #cfc4b0; border-radius:7px; color:#49685e; background:#f8f2e6; font-size:10px; font-weight:750; text-align:left; }
.culture-focus:hover, .culture-focus[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.culture-focus span { color:var(--red); font-size:14px; line-height:1; }
.culture-focus[aria-pressed="true"] span { color:#e5c874; }
.spectrum-field { margin-top:10px; padding:15px; border:1px solid #355d59; border-radius:15px; color:#f7f0dc; background:radial-gradient(circle at 86% 10%,rgba(225,194,118,.16),transparent 30%),linear-gradient(135deg,#173b3d 0%,#23544f 100%); box-shadow:0 8px 22px rgba(31,63,59,.08); }
.spectrum-head { display:grid; grid-template-columns:minmax(0,1.12fr) minmax(250px,.88fr); gap:16px; align-items:start; }
.spectrum-field .culture-mosaic-label { color:#e1c276; }
.spectrum-field h3 { max-width:560px; margin:7px 0 0; color:#f7f0dc; font:700 24px/1.04 Georgia,serif; letter-spacing:-.045em; }
.spectrum-head p { max-width:620px; margin:8px 0 0; color:rgba(247,240,220,.65); font-size:10px; line-height:1.5; }
.spectrum-readout { min-height:104px; padding:11px 12px; border:1px solid rgba(225,194,118,.4); background:rgba(7,29,32,.3); }
.spectrum-readout > span { color:#e1c276; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.spectrum-readout strong { display:block; margin-top:8px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.spectrum-readout p { margin:5px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.spectrum-readout-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:9px; }
.spectrum-readout-metric { min-width:0; padding:6px; border:1px solid rgba(247,240,220,.13); background:rgba(7,29,32,.22); }
.spectrum-readout-metric b, .spectrum-readout-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.spectrum-readout-metric b { color:#e1c276; font:700 11px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.spectrum-readout-metric small { margin-top:3px; color:rgba(247,240,220,.48); font:7px/1.15 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.spectrum-plot-wrap { margin-top:13px; padding:8px 8px 5px; overflow-x:auto; border:1px solid rgba(247,240,220,.16); background:rgba(7,29,32,.28); scrollbar-color:#c6a85d rgba(247,240,220,.12); }
.spectrum-plot { display:block; width:100%; min-width:560px; height:auto; }
.spectrum-grid-line { stroke:rgba(247,240,220,.14); stroke-width:1; }
.spectrum-axis { stroke:rgba(247,240,220,.42); stroke-width:1; }
.spectrum-axis-label { fill:rgba(247,240,220,.56); font:700 9px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; }
.spectrum-guide { stroke:#e1c276; stroke-width:1.3; stroke-dasharray:5 5; opacity:.8; }
.spectrum-guide-label { fill:#e1c276; font:700 9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.spectrum-median-guide { stroke:#7aa5a1; stroke-width:1; stroke-dasharray:2 5; opacity:.7; }
.spectrum-dot { cursor:pointer; opacity:.78; stroke:#fff4d6; stroke-width:.7; transition:opacity .14s ease,stroke-width .14s ease,r .14s ease; }
.spectrum-dot:hover, .spectrum-dot:focus { opacity:1; stroke-width:1.8; outline:none; }
.spectrum-legend { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; }
.spectrum-legend-chip { display:inline-flex; align-items:center; gap:5px; color:rgba(247,240,220,.58); font-size:8px; }
.spectrum-legend-chip i { width:7px; height:7px; border-radius:50%; }
.spectrum-notation { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:9px; }
.spectrum-notation button { min-width:0; padding:7px 8px; border:1px solid rgba(225,194,118,.22); border-radius:7px; color:#f7f0dc; background:rgba(7,29,32,.22); text-align:left; }
.spectrum-notation button:hover, .spectrum-notation button:focus-visible { border-color:#e1c276; background:rgba(7,29,32,.5); }
.spectrum-notation b, .spectrum-notation small { display:block; }
.spectrum-notation b { color:#e1c276; font:700 13px/1 Georgia,serif; }
.spectrum-notation small { margin-top:4px; overflow:hidden; color:rgba(247,240,220,.52); font:7px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.spectrum-note { margin:11px 0 0; color:rgba(247,240,220,.46); font-size:9px; line-height:1.45; }
.culture-timeline { margin-top:10px; padding:15px; border:1px solid #355d59; border-radius:15px; color:#f7f0dc; background:linear-gradient(135deg,#173b3d 0%,#23544f 100%); box-shadow:0 8px 22px rgba(31,63,59,.08); }
.culture-timeline-head { display:grid; grid-template-columns:minmax(0,1.12fr) minmax(250px,.88fr); gap:16px; align-items:start; }
.culture-timeline .culture-mosaic-label { color:#e1c276; }
.culture-timeline h3 { max-width:530px; margin:7px 0 0; color:#f7f0dc; font:700 24px/1.04 Georgia,serif; letter-spacing:-.045em; }
.culture-timeline-head p { max-width:620px; margin:8px 0 0; color:rgba(247,240,220,.65); font-size:10px; line-height:1.5; }
.heritage-timeline-readout { min-height:104px; padding:11px 12px; border:1px solid rgba(225,194,118,.4); background:rgba(7,29,32,.3); }
.heritage-timeline-readout > span { color:#e1c276; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.heritage-timeline-readout strong { display:block; margin-top:8px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.heritage-timeline-readout p { margin:5px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.heritage-timeline { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(84px,1fr); gap:5px; margin-top:15px; padding:1px 1px 5px; overflow-x:auto; scrollbar-color:#c6a85d rgba(247,240,220,.12); }
.heritage-era { position:relative; min-height:116px; padding:8px 8px 17px; border:1px solid rgba(247,240,220,.18); border-radius:8px; color:#f7f0dc; background:rgba(7,29,32,.28); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.heritage-era:hover, .heritage-era:focus-visible { border-color:#e1c276; background:rgba(7,29,32,.5); box-shadow:0 6px 16px rgba(4,20,23,.18); transform:translateY(-2px); }
.heritage-era[aria-selected="true"] { border-color:#e1c276; background:rgba(7,29,32,.62); box-shadow:0 0 0 2px rgba(225,194,118,.16); }
.heritage-era-top { display:flex; align-items:center; justify-content:space-between; gap:5px; color:rgba(247,240,220,.58); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; }
.heritage-era-top small { max-width:47px; overflow:hidden; color:#e1c276; font:700 7px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; text-transform:uppercase; }
.heritage-era strong { display:block; margin-top:13px; color:#f7f0dc; font:700 20px/1 Georgia,serif; letter-spacing:-.05em; }
.heritage-era > small { display:block; min-height:26px; margin-top:5px; color:rgba(247,240,220,.55); font-size:8px; line-height:1.3; }
.heritage-era-meter { position:absolute; right:8px; bottom:8px; left:8px; height:4px; overflow:hidden; border-radius:99px; background:rgba(247,240,220,.13); }
.heritage-era-meter i { display:block; height:100%; min-width:2px; border-radius:inherit; background:linear-gradient(90deg,#e1c276,#bf6d52); }
.heritage-timeline-caveat { margin:12px 0 0; color:rgba(247,240,220,.46); font-size:9px; line-height:1.45; }
.heritage-type-field { margin-top:10px; padding:15px; border:1px solid #c4b58c; border-radius:15px; color:#f7f0dc; background:linear-gradient(135deg,#213f40 0%,#315b51 55%,#806d48 100%); box-shadow:0 8px 22px rgba(31,63,59,.08); }
.heritage-type-head { display:grid; grid-template-columns:minmax(0,1.12fr) minmax(250px,.88fr); gap:16px; align-items:start; }
.heritage-type-field .culture-mosaic-label { color:#e1c276; }
.heritage-type-field h3 { max-width:560px; margin:7px 0 0; color:#f7f0dc; font:700 24px/1.04 Georgia,serif; letter-spacing:-.045em; }
.heritage-type-head p { max-width:620px; margin:8px 0 0; color:rgba(247,240,220,.65); font-size:10px; line-height:1.5; }
.heritage-type-readout { min-height:104px; padding:11px 12px; border:1px solid rgba(225,194,118,.4); background:rgba(7,29,32,.3); }
.heritage-type-readout > span { color:#e1c276; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.heritage-type-readout strong { display:block; margin-top:8px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.heritage-type-readout p { margin:5px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.heritage-type-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:15px; }
.heritage-type-card { min-width:0; min-height:183px; padding:10px; border:1px solid rgba(247,240,220,.18); border-radius:8px; color:#f7f0dc; background:rgba(7,29,32,.28); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.heritage-type-card:hover, .heritage-type-card:focus-visible { border-color:#e1c276; background:rgba(7,29,32,.5); box-shadow:0 6px 16px rgba(4,20,23,.18); transform:translateY(-2px); }
.heritage-type-card[aria-pressed="true"] { border-color:#e1c276; background:rgba(7,29,32,.62); box-shadow:0 0 0 2px rgba(225,194,118,.16); }
.heritage-type-card-top { display:flex; align-items:baseline; justify-content:space-between; gap:5px; color:rgba(247,240,220,.58); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.heritage-type-card-top small { color:#e1c276; font-size:7px; }
.heritage-type-card h4 { min-height:34px; margin:12px 0 0; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.035em; }
.heritage-type-card > strong { display:block; margin-top:8px; color:#f7f0dc; font:700 19px/1 Georgia,serif; letter-spacing:-.05em; }
.heritage-type-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:10px; }
.heritage-type-metrics span { min-width:0; padding:6px; border:1px solid rgba(247,240,220,.13); background:rgba(7,29,32,.22); }
.heritage-type-metrics small { display:block; color:rgba(247,240,220,.48); font:700 7px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.06em; text-transform:uppercase; }
.heritage-type-metrics b { display:block; margin-top:4px; overflow:hidden; color:#e1c276; font:700 10px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.heritage-type-track { display:block; height:4px; margin-top:9px; overflow:hidden; border-radius:99px; background:rgba(247,240,220,.13); }
.heritage-type-track i { display:block; height:100%; min-width:2px; border-radius:inherit; background:linear-gradient(90deg,#8fbe9c,#e1c276); }
.heritage-type-card em { display:block; margin-top:7px; overflow:hidden; color:rgba(247,240,220,.46); font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.heritage-type-caveat { margin:12px 0 0; color:rgba(247,240,220,.46); font-size:9px; line-height:1.45; }
.place-braid-field { margin-top:10px; padding:15px; border:1px solid #cbd2bf; border-radius:14px; background:linear-gradient(135deg,#e7eee8 0%,#f2eadb 56%,#e7e9df 100%); }
.place-braid-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.place-braid-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.place-braid-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.place-braid-readout { flex:0 0 235px; min-height:104px; padding:11px 12px; border:1px solid #bdcbbd; background:rgba(248,242,230,.72); }
.place-braid-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.place-braid-readout strong { display:block; margin-top:8px; color:var(--deep); font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.place-braid-readout p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.place-braid-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:5px; margin-top:13px; }
.place-braid-card { min-width:0; padding:10px; border:1px solid #c6d1c5; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.84); }
.place-braid-card:has(.place-braid-county[aria-pressed="true"]) { border-color:var(--deep-2); box-shadow:0 0 0 2px rgba(31,81,79,.1); }
.place-braid-county { display:block; width:100%; padding:0 0 8px; border:0; border-bottom:1px solid #d7dfd5; color:var(--deep); background:transparent; text-align:left; }
.place-braid-county:hover, .place-braid-county[aria-pressed="true"] { color:var(--blue); }
.place-braid-county-top { display:flex; align-items:baseline; justify-content:space-between; gap:6px; color:#897c67; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.07em; text-transform:uppercase; }
.place-braid-county-top small { color:#6c806e; font-size:7px; }
.place-braid-county strong { display:block; margin-top:7px; color:var(--deep); font:700 20px/1 Georgia,serif; letter-spacing:-.05em; }
.place-braid-county > small { display:block; margin-top:5px; color:#69766e; font:8px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
.place-braid-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:9px; }
.place-braid-metrics span { min-width:0; padding:6px; border:1px solid #d9e0d7; background:rgba(255,252,244,.62); }
.place-braid-metrics small { display:block; color:#897c68; font:700 7px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.06em; text-transform:uppercase; }
.place-braid-metrics b { display:block; margin-top:4px; overflow:hidden; color:var(--deep); font:700 11px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.place-braid-types { display:grid; gap:4px; margin-top:9px; }
.place-braid-type { display:flex; align-items:center; justify-content:space-between; gap:7px; width:100%; min-height:25px; padding:4px 6px; border:1px solid #d3ddcf; border-radius:6px; color:#49685e; background:#f8f2e6; font-size:8px; text-align:left; }
.place-braid-type:hover, .place-braid-type[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.place-braid-type span { overflow:hidden; font-weight:750; text-overflow:ellipsis; white-space:nowrap; }
.place-braid-type small { flex:0 0 auto; color:#897c68; font:8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.place-braid-type:hover small, .place-braid-type[aria-pressed="true"] small { color:#e1c276; }
.place-braid-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid #71977b; color:#6d6254; background:rgba(248,242,230,.75); font-size:9px; line-height:1.4; }
.land-field { margin-top:10px; padding:15px; border:1px solid #416b64; border-radius:15px; color:#f7f0dc; background:linear-gradient(135deg,#12383b 0%,#1f514d 58%,#3e6955 100%); box-shadow:0 8px 22px rgba(31,63,59,.08); }
.land-field-head { display:grid; grid-template-columns:minmax(0,1.12fr) minmax(250px,.88fr); gap:16px; align-items:start; }
.land-field .culture-mosaic-label { color:#e1c276; }
.land-field h3 { max-width:560px; margin:7px 0 0; color:#f7f0dc; font:700 24px/1.04 Georgia,serif; letter-spacing:-.045em; }
.land-field-head p { max-width:620px; margin:8px 0 0; color:rgba(247,240,220,.65); font-size:10px; line-height:1.5; }
.land-field-readout { min-height:104px; padding:11px 12px; border:1px solid rgba(225,194,118,.4); background:rgba(7,29,32,.3); }
.land-field-readout > span, .land-density-panel > span, .land-source-panel > span { color:#e1c276; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.land-field-readout strong { display:block; margin-top:8px; color:#f7f0dc; font:700 16px/1.05 Georgia,serif; letter-spacing:-.03em; }
.land-field-readout p { margin:5px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.land-group-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(126px,1fr)); gap:5px; margin-top:15px; }
.land-group { min-width:0; min-height:132px; padding:9px; border:1px solid rgba(247,240,220,.18); border-radius:8px; color:#f7f0dc; background:rgba(7,29,32,.28); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.land-group:hover, .land-group:focus-visible { border-color:#e1c276; background:rgba(7,29,32,.5); box-shadow:0 6px 16px rgba(4,20,23,.18); transform:translateY(-2px); }
.land-group[aria-pressed="true"] { border-color:#e1c276; background:rgba(7,29,32,.62); box-shadow:0 0 0 2px rgba(225,194,118,.16); }
.land-group:disabled { cursor:default; opacity:.64; }
.land-group-top { display:flex; align-items:center; justify-content:space-between; gap:6px; color:rgba(247,240,220,.58); font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.05em; text-transform:uppercase; }
.land-group-top small { color:#e1c276; font-size:7px; }
.land-group strong { display:block; margin-top:13px; color:#f7f0dc; font:700 20px/1 Georgia,serif; letter-spacing:-.05em; }
.land-group > small { display:block; min-height:24px; margin-top:5px; color:rgba(247,240,220,.55); font-size:8px; line-height:1.3; }
.land-group-track { height:4px; margin-top:9px; overflow:hidden; border-radius:99px; background:rgba(247,240,220,.13); }
.land-group-track i { display:block; height:100%; min-width:2px; border-radius:inherit; background:linear-gradient(90deg,#8fbe9c,#e1c276); }
.land-group-meta { display:block; margin-top:6px; color:rgba(247,240,220,.44); font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.land-field-bottom { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,.9fr); gap:1px; margin-top:10px; border:1px solid rgba(225,194,118,.22); background:rgba(225,194,118,.22); }
.land-density-panel, .land-source-panel { min-height:126px; padding:12px; background:rgba(7,29,32,.28); }
.land-density-panel strong { display:block; margin-top:8px; color:#f7f0dc; font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.land-density-panel p, .land-source-panel p { margin:6px 0 0; color:rgba(247,240,220,.58); font-size:9px; line-height:1.4; }
.land-density-bars { display:grid; gap:5px; margin-top:10px; }
.land-density-row { display:grid; grid-template-columns:54px minmax(0,1fr) auto; gap:6px; align-items:center; color:rgba(247,240,220,.58); font:8px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.land-density-track { height:4px; overflow:hidden; border-radius:99px; background:rgba(247,240,220,.13); }
.land-density-track i { display:block; height:100%; min-width:2px; border-radius:inherit; background:#8fbe9c; }
.land-source-panel p b { color:#e1c276; }
.culture-mosaic { display:grid; grid-template-columns:1.15fr .85fr; gap:1px; margin-top:10px; border:1px solid #d6cdbd; background:#d6cdbd; }
.culture-mosaic > div { min-height:118px; padding:15px; background:rgba(255,252,244,.78); }
.culture-mosaic h3 { margin:8px 0 0; color:var(--deep); font:700 19px/1.08 Georgia,serif; letter-spacing:-.035em; }
.culture-mosaic h3 b { color:var(--red); }
.culture-mosaic p { margin:8px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.county-field { margin-top:13px; padding-top:10px; border-top:1px solid #ddd2c0; }
.county-field-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.county-field-head span { color:#52675e; font-size:9px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.county-field-head small { color:#8a7c68; font-size:9px; }
.county-chips { display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }
.county-chip { min-height:25px; padding:4px 7px; border:1px solid #d1c4aa; border-radius:999px; color:#49685e; background:#f8f2e6; font-size:9px; font-weight:750; }
.county-chip:hover, .county-chip[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.county-field-note { margin-top:9px; padding:8px 9px; border:1px solid #d8cdb8; border-radius:8px; background:rgba(247,240,223,.7); }
.county-field-note > span { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.county-field-note p { margin:5px 0 0; color:#65736b; font-size:10px; line-height:1.4; }
.county-field-metrics { display:flex; flex-wrap:wrap; gap:5px 12px; margin-top:7px; color:#49685e; font:700 9px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
.county-pulse-field { margin-top:10px; padding:15px; border:1px solid #c8d5cb; border-radius:14px; background:linear-gradient(135deg,#e9f0e8 0%,#f4ead9 100%); }
.county-pulse-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.county-pulse-head h3 { max-width:540px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.county-pulse-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.county-pulse-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:5px; margin-top:13px; }
.county-pulse { min-width:0; padding:9px; border:1px solid #cbd4c8; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.82); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.county-pulse:hover, .county-pulse:focus-visible, .county-pulse[aria-pressed="true"] { border-color:var(--deep-2); background:#f8f2e6; box-shadow:0 5px 13px rgba(31,63,59,.09); transform:translateY(-2px); }
.county-pulse-top { display:flex; align-items:baseline; justify-content:space-between; gap:5px; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.county-pulse strong { display:block; margin-top:7px; color:var(--deep); font:700 18px/1 Georgia,serif; letter-spacing:-.04em; }
.county-pulse small { display:block; margin-top:5px; color:#49685e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.county-pulse-form { color:#6c806e !important; font-size:7px !important; letter-spacing:.02em; }
.county-pulse-track { display:block; height:4px; margin-top:8px; overflow:hidden; border-radius:99px; background:#dce3d8; }
.county-pulse-track i { display:block; height:100%; min-width:2px; border-radius:inherit; background:linear-gradient(90deg,#527b85,#d0a34c); }
.county-pulse em { display:block; margin-top:6px; overflow:hidden; color:#897c68; font-size:8px; font-style:normal; line-height:1.2; text-overflow:ellipsis; white-space:nowrap; }
.county-pulse-note { margin:10px 0 0; padding-top:9px; border-top:1px solid rgba(73,104,94,.2); color:#6d786f; font-size:9px; line-height:1.45; }
.makers-field { margin-top:10px; padding:15px; border:1px solid #d6c3a0; border-radius:14px; background:linear-gradient(135deg,#f3eadb 0%,#e8efea 100%); }
.makers-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.makers-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.makers-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.makers-stat { flex:0 0 142px; padding:10px; border:1px solid #d8c29b; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.makers-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.makers-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.makers-binary { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; margin-top:13px; border:1px solid #d5c9b6; background:#d5c9b6; }
.makers-binary-card { min-height:104px; padding:11px; background:rgba(248,242,230,.85); }
.makers-binary-card span { display:block; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.makers-binary-card strong { display:block; margin-top:7px; color:var(--deep); font:700 20px/1 Georgia,serif; }
.makers-binary-card p { margin:6px 0 0; color:#69766e; font-size:9px; line-height:1.35; }
.makers-grid { display:flex; flex-wrap:wrap; gap:5px; margin-top:12px; }
.maker-chip { display:flex; flex-direction:column; align-items:flex-start; min-width:132px; padding:7px 8px; border:1px solid #d1c4aa; border-radius:8px; color:#315c57; background:#f8f2e6; text-align:left; }
.maker-chip:hover, .maker-chip:focus-visible { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.maker-chip strong { max-width:180px; overflow:hidden; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.maker-chip small { margin-top:4px; color:#897c68; font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.maker-chip:hover small, .maker-chip:focus-visible small { color:#e1c276; }
.makers-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid var(--gold); color:#6d6254; background:rgba(248,242,230,.75); font-size:9px; line-height:1.4; }
.rhythm-field { margin-top:10px; padding:15px; border:1px solid #bdcfc3; border-radius:14px; background:linear-gradient(135deg,#e8efea 0%,#edf1e7 56%,#f3e8d6 100%); }
.rhythm-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.rhythm-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.rhythm-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.rhythm-stat { flex:0 0 142px; padding:10px; border:1px solid #b9cdbd; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.rhythm-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.rhythm-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.rhythm-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:13px; }
.rhythm-card { min-width:0; padding:10px; border:1px solid #c5d2c6; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.84); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.rhythm-card:hover, .rhythm-card:focus-visible, .rhythm-card[aria-pressed="true"] { border-color:var(--deep-2); background:#f8f2e6; box-shadow:0 5px 13px rgba(31,63,59,.09); transform:translateY(-2px); }
.rhythm-card-top { display:flex; align-items:baseline; justify-content:space-between; gap:5px; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.rhythm-card strong { display:block; margin-top:10px; color:var(--deep); font:700 21px/1 Georgia,serif; letter-spacing:-.05em; }
.rhythm-card small { display:block; margin-top:5px; color:#49685e; font-size:9px; line-height:1.25; }
.rhythm-track { display:block; height:5px; margin-top:10px; overflow:hidden; border-radius:99px; background:linear-gradient(90deg,#c8d9cd 49.5%,#d6bd80 50%,#d9c8a4 50.5%); }
.rhythm-track i { display:block; height:100%; min-width:3px; border-radius:inherit; background:linear-gradient(90deg,#6d9a84,#d0a34c); }
.rhythm-card em { display:block; margin-top:7px; color:#897c68; font-size:8px; font-style:normal; line-height:1.25; }
.rhythm-card-meta { color:#897c68 !important; font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace !important; }
.rhythm-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:11px; padding:10px 11px; border:1px solid rgba(73,104,94,.24); background:rgba(248,242,230,.62); }
.rhythm-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.rhythm-readout strong { display:block; color:var(--deep); font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.rhythm-readout p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.rhythm-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid #6d9a84; color:#6d6254; background:rgba(248,242,230,.72); font-size:9px; line-height:1.4; }
.scale-field { margin-top:10px; padding:15px; border:1px solid #c9b98e; border-radius:14px; background:linear-gradient(135deg,#f2eadb 0%,#e9efe7 58%,#e3eee9 100%); }
.scale-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.scale-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.scale-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.scale-stat { flex:0 0 142px; padding:10px; border:1px solid #d8c29b; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.scale-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.scale-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.scale-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:13px; }
.scale-card { min-width:0; padding:10px; border:1px solid #d3c6a9; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.86); text-align:left; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }
.scale-card:hover, .scale-card:focus-visible, .scale-card[aria-pressed="true"] { border-color:var(--deep-2); background:#f8f2e6; box-shadow:0 5px 13px rgba(31,63,59,.09); transform:translateY(-2px); }
.scale-card-top { display:flex; align-items:baseline; justify-content:space-between; gap:5px; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.scale-card strong { display:block; margin-top:9px; color:var(--deep); font:700 18px/1 Georgia,serif; letter-spacing:-.05em; }
.scale-steps { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); align-items:end; gap:3px; height:100px; margin-top:10px; padding:6px 3px 0; border-bottom:1px solid #c9c0ab; background:repeating-linear-gradient(0deg,transparent 0,transparent 24px,rgba(73,104,94,.1) 25px); }
.scale-step { display:flex; flex-direction:column; align-items:stretch; justify-content:end; min-width:0; height:100%; }
.scale-step i { display:block; min-height:4px; border-radius:3px 3px 0 0; background:linear-gradient(180deg,#d0a34c,#6b9680); }
.scale-step small { display:block; margin-top:5px; overflow:hidden; color:#897c68; font:7px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.scale-step b { display:block; margin-top:3px; overflow:hidden; color:#49685e; font:700 7px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.scale-card-meta { display:block; margin-top:8px; color:#897c68; font:8px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace; }
.scale-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:11px; padding:10px 11px; border:1px solid rgba(73,104,94,.24); background:rgba(248,242,230,.62); }
.scale-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.scale-readout strong { display:block; color:var(--deep); font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.scale-readout p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.scale-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid var(--gold); color:#6d6254; background:rgba(248,242,230,.72); font-size:9px; line-height:1.4; }
.alignment-field { margin-top:10px; padding:15px; border:1px solid #c8b9c5; border-radius:14px; background:linear-gradient(135deg,#eee6df 0%,#e9efeb 58%,#e8e7ef 100%); }
.alignment-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.alignment-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.alignment-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.alignment-stat { flex:0 0 142px; padding:10px; border:1px solid #c9bac7; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.alignment-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.alignment-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.alignment-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; margin-top:13px; }
.alignment-card { display:grid; grid-template-columns:auto minmax(0,1fr); gap:12px; min-width:0; padding:11px; border:1px solid #cfc5d0; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.86); }
.alignment-card:hover, .alignment-card:focus-within { border-color:var(--deep-2); box-shadow:0 5px 13px rgba(31,63,59,.09); }
.alignment-compass { position:relative; width:82px; height:82px; border:1px solid #b8a8b6; border-radius:50%; background:radial-gradient(circle at center,#f7f0e3 0 5px,transparent 6px),repeating-conic-gradient(from 0deg,rgba(73,104,94,.16) 0 1deg,transparent 1deg 45deg); box-shadow:inset 0 0 0 7px rgba(73,104,94,.04); }
.alignment-compass::before { content:""; position:absolute; top:8px; left:50%; width:2px; height:33px; border-radius:99px; background:linear-gradient(#bf5b45,#d0a34c); transform:translateX(-50%) rotate(var(--needle-angle,0deg)); transform-origin:50% 33px; }
.alignment-compass::after { content:""; position:absolute; top:50%; left:50%; width:7px; height:7px; border:1px solid #f8f2e6; border-radius:50%; background:#49685e; transform:translate(-50%,-50%); }
.alignment-compass-label { display:block; margin-top:6px; color:#897c68; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; text-align:center; }
.alignment-card-body { min-width:0; }
.alignment-card-top { display:flex; align-items:baseline; justify-content:space-between; gap:6px; color:#897c67; font-size:8px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.alignment-card-top small { color:#6c806e; font-size:8px; letter-spacing:0; text-transform:none; }
.alignment-card h4 { margin:7px 0 0; color:var(--deep); font:700 17px/1.04 Georgia,serif; letter-spacing:-.04em; }
.alignment-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; margin-top:10px; }
.alignment-metric { min-width:0; padding:7px; border:1px solid #d8ced8; background:rgba(255,252,244,.58); }
.alignment-metric span { display:block; min-height:18px; color:#897c68; font-size:7px; font-weight:800; line-height:1.2; text-transform:uppercase; }
.alignment-metric strong { display:block; margin-top:4px; color:var(--deep); font:700 14px/1 Georgia,serif; letter-spacing:-.04em; }
.alignment-metric small { display:block; margin-top:4px; overflow:hidden; color:#69766e; font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; text-overflow:ellipsis; white-space:nowrap; }
.alignment-meta { display:block; margin-top:8px; color:#897c68; font:8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }
.alignment-action { display:flex; align-items:center; justify-content:space-between; width:100%; min-height:27px; margin-top:8px; padding:5px 7px; border:1px solid #cbbdcc; border-radius:7px; color:#49685e; background:#f8f2e6; font-size:9px; font-weight:750; text-align:left; }
.alignment-action:hover, .alignment-action[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.alignment-action span { color:var(--red); font-size:13px; line-height:1; }
.alignment-action[aria-pressed="true"] span { color:#e5c874; }
.alignment-reference { display:block; margin-top:8px; color:#897c68; font-size:8px; line-height:1.25; }
.alignment-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:11px; padding:10px 11px; border:1px solid rgba(73,104,94,.24); background:rgba(248,242,230,.62); }
.alignment-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.alignment-readout strong { display:block; color:var(--deep); font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.alignment-readout p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.alignment-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid #8b7190; color:#6d6254; background:rgba(248,242,230,.72); font-size:9px; line-height:1.4; }
.source-field { margin-top:10px; padding:15px; border:1px solid #b8c9c7; border-radius:14px; background:linear-gradient(135deg,#e8efec 0%,#f2eadb 58%,#e8ece8 100%); }
.source-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.source-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.source-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.source-stat { flex:0 0 142px; padding:10px; border:1px solid #b7c9c1; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.source-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.source-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.source-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:13px; }
.source-root-card { min-width:0; min-height:164px; padding:10px; border:1px solid #c7d2cc; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.84); }
.source-root-top { display:flex; align-items:center; justify-content:space-between; gap:6px; }
.source-root-top > span:first-child { color:#897c67; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; }
.source-status { padding:4px 6px; border:1px solid #c5d3c8; border-radius:999px; color:#356c69; background:#e6f0e8; font-size:7px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.source-status.fallback { border-color:#d8c69e; color:#8b6419; background:#fff3dc; }
.source-status.missing { border-color:#d5bdb5; color:#a04e40; background:#f8e9e4; }
.source-root-card h4 { min-height:34px; margin:12px 0 0; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.03em; }
.source-root-card p { min-height:39px; margin:7px 0 0; color:#69766e; font-size:9px; line-height:1.35; }
.source-root-card small { display:block; margin-top:8px; color:#897c68; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.source-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:11px; padding:10px 11px; border:1px solid rgba(73,104,94,.24); background:rgba(248,242,230,.62); }
.source-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.source-readout strong { display:block; color:var(--deep); font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.source-readout p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.source-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid #527b85; color:#6d6254; background:rgba(248,242,230,.72); font-size:9px; line-height:1.4; }
.trust-field { margin-top:10px; padding:15px; border:1px solid #c8b997; border-radius:14px; background:radial-gradient(circle at 92% 12%,rgba(208,163,76,.2),transparent 30%),linear-gradient(135deg,#f1eadb 0%,#e7efea 58%,#eee6dc 100%); }
.trust-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.trust-head h3 { max-width:560px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.trust-head p { max-width:650px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.trust-stat { flex:0 0 142px; padding:10px; border:1px solid #cbbd9d; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.trust-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.trust-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.trust-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:13px; }
.trust-card { min-width:0; min-height:166px; padding:10px; border:1px solid #d2c7b3; border-radius:8px; color:var(--deep); background:rgba(248,242,230,.86); }
.trust-card.pass { border-color:#b7cbbd; background:rgba(239,247,239,.82); }
.trust-card.available { border-color:#d5c29a; background:rgba(255,248,228,.86); }
.trust-card.missing { border-color:#d8c0b8; background:rgba(251,239,232,.8); }
.trust-card-top { display:flex; align-items:center; justify-content:space-between; gap:6px; }
.trust-card-top > span:first-child { color:#897c67; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; }
.trust-status { padding:4px 6px; border:1px solid #b7cbbd; border-radius:999px; color:#356c69; background:#e6f0e8; font-size:7px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.trust-status.available { border-color:#d8c69e; color:#8b6419; background:#fff3dc; }
.trust-status.missing { border-color:#d5bdb5; color:#a04e40; background:#f8e9e4; }
.trust-status.check { border-color:#c9c2ae; color:#756a57; background:#f1ebdf; }
.trust-card h4 { min-height:34px; margin:12px 0 0; color:var(--deep); font:700 16px/1.08 Georgia,serif; letter-spacing:-.03em; }
.trust-card strong { display:block; margin-top:8px; color:var(--deep); font:700 17px/1.05 Georgia,serif; letter-spacing:-.04em; }
.trust-card p { min-height:38px; margin:7px 0 0; color:#69766e; font-size:9px; line-height:1.35; }
.trust-card small { display:block; margin-top:8px; color:#897c68; font:8px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; }
.trust-readout { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; align-items:start; margin-top:11px; padding:10px 11px; border:1px solid rgba(73,104,94,.24); background:rgba(248,242,230,.62); }
.trust-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.trust-readout strong { display:block; color:var(--deep); font:700 15px/1.05 Georgia,serif; letter-spacing:-.03em; }
.trust-readout p { margin:4px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.trust-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid var(--red); color:#6d6254; background:rgba(248,242,230,.72); font-size:9px; line-height:1.4; }
.culture-mosaic-label { color:#897c67; font-size:9px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.culture-word-links { display:flex; flex-wrap:wrap; gap:6px; margin-top:15px; }
.culture-word-links a { display:flex; flex-direction:column; min-width:74px; padding:7px 8px; border:1px solid #d8cdb8; border-radius:7px; color:var(--deep); background:#f7f0e3; font:700 12px/1 Georgia,serif; text-decoration:none; }
.culture-word-links a:hover { border-color:var(--blue); color:var(--blue); }
.culture-word-links small { margin-top:4px; color:#8a7c68; font:600 9px/1.1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.place-name-field { margin-top:10px; padding:15px; border:1px solid #d6c3a0; border-radius:14px; background:rgba(255,252,244,.76); }
.place-name-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.place-name-head h3 { max-width:540px; margin:7px 0 0; color:var(--deep); font:700 22px/1.04 Georgia,serif; letter-spacing:-.04em; }
.place-name-head p { max-width:620px; margin:7px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.place-name-stat { flex:0 0 142px; padding:10px; border:1px solid #d8c29b; border-radius:10px; color:#49685e; background:#f7f0e3; text-align:right; }
.place-name-stat strong { display:block; color:var(--deep); font:700 22px/1 Georgia,serif; letter-spacing:-.05em; }
.place-name-stat small { display:block; margin-top:4px; color:#897c68; font-size:8px; line-height:1.25; }
.place-name-chips { display:flex; flex-wrap:wrap; gap:5px; margin-top:13px; }
.place-name-chip { display:flex; align-items:center; gap:7px; min-height:27px; padding:4px 7px; border:1px solid #d1c4aa; border-radius:999px; color:#49685e; background:#f8f2e6; font-size:9px; font-weight:750; }
.place-name-chip small { color:#897c68; font:700 8px/1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.place-name-chip:hover, .place-name-chip[aria-pressed="true"] { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
.place-name-chip:hover small, .place-name-chip[aria-pressed="true"] small { color:#e1c276; }
.place-name-readout { margin-top:10px; padding:10px 11px; border:1px solid #d8c29b; background:linear-gradient(135deg,#f1eadb 0%,#e8efe8 100%); }
.place-name-readout > span { color:#6c806e; font:700 8px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; text-transform:uppercase; }
.place-name-readout strong { display:block; margin-top:6px; color:var(--deep); font:700 15px/1.08 Georgia,serif; letter-spacing:-.03em; }
.place-name-readout p { margin:5px 0 0; color:#69766e; font-size:9px; line-height:1.4; }
.place-name-readout-metrics { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:5px; margin-top:9px; }
.place-name-readout-metric { min-width:0; padding:6px 7px; border:1px solid #d8dcca; background:rgba(255,253,248,.68); }
.place-name-readout-metric b, .place-name-readout-metric small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.place-name-readout-metric b { color:var(--deep); font:700 11px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace; }
.place-name-readout-metric small { margin-top:3px; color:#897c68; font-size:7px; letter-spacing:.05em; text-transform:uppercase; }
.place-name-readout-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:9px; }
.place-name-readout-actions button { min-height:27px; padding:5px 8px; border:1px solid #4c765f; border-radius:7px; color:#fff8eb; background:#4c765f; font-size:9px; font-weight:800; }
.place-name-readout-actions button:hover, .place-name-readout-actions button:focus-visible { border-color:var(--deep); background:var(--deep); }
.place-name-readout-actions button[hidden] { display:none; }
.place-name-share-status { color:#69766e; font-size:8px; line-height:1.3; }
.place-name-note { margin:11px 0 0; padding:8px 9px; border-left:3px solid var(--gold); color:#6d6254; background:rgba(248,242,230,.75); font-size:9px; line-height:1.4; }
.culture-caveat { margin:12px 0 0; padding:9px 10px; border-left:3px solid var(--gold); color:#6d6254; background:rgba(248,242,230,.75); font-size:10px; line-height:1.45; }
.diagram-kicker { fill:#857962; font:700 9px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; }
.diagram-baseline { stroke:#c7bba6; stroke-width:1; }
.diagram-outer { fill:rgba(53,108,105,.08); stroke:#356c69; stroke-width:2; }
.diagram-inner { fill:rgba(191,91,69,.16); stroke:#bf5b45; stroke-width:2; }
.diagram-ring { fill:none; stroke:#356c69; stroke-width:2; stroke-dasharray:6 7; }
.diagram-arc { fill:none; stroke:#d0a34c; stroke-width:2; stroke-dasharray:4 5; }
.diagram-center { fill:#d0a34c; stroke:#103537; stroke-width:2; }
.diagram-center-label { fill:#103537; font:700 10px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.diagram-ray { fill:none; stroke-width:1.3; opacity:.5; stroke-dasharray:3 5; }
.diagram-axis { fill:none; stroke:#bf5b45; stroke-width:1.4; opacity:.58; stroke-dasharray:7 5; }
.diagram-grid { fill:none; stroke:#356c69; stroke-width:1; opacity:.32; }
.diagram-crux { fill:rgba(191,91,69,.08); stroke:#bf5b45; stroke-width:1.8; opacity:.72; }
.diagram-route { fill:none; stroke:#103537; stroke-width:4; stroke-linecap:round; stroke-dasharray:2 7; opacity:.58; }
.diagram-wind { fill:none; stroke:#527b85; stroke-width:1.4; stroke-linecap:round; opacity:.42; marker-end:url(#windArrow); }
.diagram-rain { fill:none; stroke:#7aa5a1; stroke-width:1.5; stroke-linecap:round; opacity:.55; }
.diagram-phase { fill:none; stroke:#d0a34c; stroke-width:1; stroke-dasharray:2 4; opacity:.55; }
.diagram-step { fill:rgba(53,108,105,.08); stroke-width:1.5; }
.diagram-step-label { fill:#103537; font:700 9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.diagram-bay { fill:rgba(208,163,76,.14); stroke:#356c69; stroke-width:1.5; }
.diagram-bay-dot { fill:#bf5b45; opacity:.8; }
.diagram-canopy { fill:none; stroke:#d0a34c; stroke-width:3; stroke-linecap:round; }
.diagram-spiral { fill:none; stroke:#4c765f; stroke-width:2.4; stroke-linecap:round; stroke-linejoin:round; }
.diagram-water { fill:none; stroke:#7aa5a1; stroke-width:2; stroke-dasharray:8 7; }
.section { padding:14px 18px; border-bottom:1px solid var(--line); }
.section h2 { color:var(--deep); }
.kpis { padding:12px 18px; border-bottom:1px solid var(--line); background:#f1eadc; }
.kpi { border-color:#dfd4c2; background:#fbf8f1; }
.kpi b { color:var(--deep); }
.filters { padding:12px 18px 10px; border-bottom:1px solid var(--line); background:#f5efe4; }
input,select,button { border-color:#d5cbbd; border-radius:8px; background:#fffdf8; color:var(--ink); }
button:hover { border-color:var(--blue); color:var(--blue); }
.pattern-card { border-color:#dfd5c6; background:#fbf8f1; }
.pattern-card:hover, .pattern-card.active { border-color:var(--blue); box-shadow:0 0 0 2px rgba(53,108,105,.13); }
.pattern-card.active { background:#eef5ef; }
.pattern-meter-fill { background:linear-gradient(90deg,#d4a84c,#bf5b45); }
.bar-track { background:#e5ddcf; }
.bar-fill { background:var(--blue); }
.interpretation-headline { border-left-color:var(--red); background:#f1e8d9; }
.interpretation-card { border-color:#dfd5c6; background:#fbf8f1; }
.table-wrap { background:#f9f4eb; }
th { background:#eee7da; color:#5b6c65; }
td { border-top-color:#ebe3d7; }
tr:hover td { background:#f1f6f1; }
.flag { background:#e7f0ea; color:#2d615b; }
.runtime-status { background:#ece7dd; }
.offline-map-note, .offline-selection { border-color:#d7ccb9; background:rgba(250,246,237,.94); }
@media (max-width:900px) {
  #mapHud { top:78px; left:16px; }
  #mapLabel { left:22px; bottom:22px; }
  #mapStamp { left:22px; bottom:112px; width:220px; }
  .equation-grid, .typology-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .culture-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .culture-timeline-head { grid-template-columns:1fr; }
  .heritage-type-head { grid-template-columns:1fr; }
  .heritage-type-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .place-braid-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .land-field-head, .land-field-bottom { grid-template-columns:1fr; }
  .intro-shell { grid-template-columns:minmax(0,1fr) minmax(220px,.75fr); gap:28px; }
  .intro-main h2 { font-size:clamp(46px,8vw,82px); }
  .intro-orbit { width:min(38vw,340px); }
  .field-content { grid-template-columns:1fr; }
  .field-coordinate { min-height:0; }
  .field-sequence { grid-template-columns:repeat(4,minmax(0,1fr)); row-gap:18px; }
  .field-walk-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .field-journey-head { grid-template-columns:1fr; }
  .maths-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .measure-ledger { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .lab-toolbar { align-items:stretch; }
  .lab-toolbar select { width:100%; }
  .lab-toolbar label { min-width:calc(50% - 10px); }
  .scenario-control-grid, .performance-control-grid, .design-spec, .design-schedule { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .studio-reference-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .sky-field-grid { grid-template-columns:1fr; }
  .water-field-grid { grid-template-columns:1fr; }
  .county-pulse-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .evidence-grid { grid-template-columns:1fr; }
  .lab-grid { grid-template-columns:1fr; }
  .lab-copy { border-top:1px solid #ddd1bc; border-left:0; }
  .selection-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .selection-evidence-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .selection-context-grid { grid-template-columns:1fr; }
  .comparison-relation-grid { grid-template-columns:1fr; }
  .comparison-grid { grid-template-columns:1fr; }
}
@media (max-width:720px) {
  #mapHud { top:70px; left:12px; width:min(300px,calc(100vw - 24px)); }
  #mapLabel { display:none; }
  #mapStamp { left:12px; bottom:calc(72vh + 12px); width:calc(100vw - 24px); }
  #panel { top:auto; right:0; bottom:0; left:0; width:100%; max-height:72vh; border-radius:14px 14px 0 0; }
  .atlas-nav { padding:6px 12px; }
  .atlas-nav-status { display:none; }
  .site-intro { place-items:end center; padding:17px; }
  .site-intro::before { inset:12px; border-radius:22px; }
  .intro-shell { display:block; width:100%; padding:22px 18px 64px; }
  .intro-topline { margin-bottom:48px; }
  .intro-main h2 { font-size:clamp(48px,15vw,78px); }
  .intro-main p { font-size:13px; }
  .intro-aside { position:absolute; top:19%; right:5%; min-height:0; opacity:.3; pointer-events:none; }
  .intro-orbit { width:235px; }
  .intro-foot { right:18px; bottom:14px; left:18px; display:block; }
  .intro-proof { position:static; gap:3px 9px; font-size:7px; }
  .intro-proof span { white-space:normal; }
  .intro-proof strong { font-size:13px; }
  .intro-foot > span:last-child { display:block; margin-top:7px; margin-left:0; text-align:right; }
  .hero-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); row-gap:12px; }
  .field-section { padding:22px 18px 21px; }
  .field-sequence { grid-template-columns:repeat(2,minmax(0,1fr)); row-gap:18px; }
  .field-signals { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .field-walk-head { display:block; }
  .field-walk-count { display:inline-block; margin-top:10px; text-align:left; }
  .field-walk-grid { grid-template-columns:1fr; }
  .field-journey { padding:11px; }
  .field-journey-track { margin-right:-4px; padding-right:4px; }
  .field-journey-node { flex:0 0 142px; }
  .field-journey-segment { flex-basis:72px; }
  .field-walk-controls { align-items:stretch; flex-wrap:wrap; }
  .field-walk-nav { flex:1 1 calc(50% - 4px); }
  .field-walk-progress { order:-1; flex-basis:100%; }
  .field-signal-detail { grid-template-columns:1fr; }
  .maths-section { padding:18px; }
  .maths-head { display:block; }
  .maths-notation { display:none; }
  .maths-reading-context { display:block; }
  .maths-reading-context button { margin-top:9px; }
  .maths-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .maths-readout { grid-template-columns:1fr; }
  .measure-ledger { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .kpis { grid-template-columns:repeat(3,1fr); }
  .kpi b { font-size:15px; }
  .filters { grid-template-columns:1fr 1fr; }
  .filters input[type=text] { grid-column:1 / -1; }
  .route-grid { grid-template-columns:1fr 1fr; }
  .route-compare-grid { grid-template-columns:1fr; }
  .route-matrix-grid { grid-template-columns:1fr; }
  .pattern-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .brief-head { flex-direction:column; }
  .brief-actions { justify-content:flex-start; }
  .culture-section { padding:18px; }
  .studio-reference { margin:0 18px 12px; }
  .studio-reference-head { display:block; }
  .studio-reference-clear { margin-top:9px; }
  .culture-head { display:block; }
  .culture-reading-context { display:block; }
  .culture-reading-context button { margin-top:9px; }
  .culture-mark { display:none; }
  .spectrum-head { grid-template-columns:1fr; }
  .spectrum-field h3 { max-width:none; }
  .spectrum-notation { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .culture-grid { grid-template-columns:1fr; }
  .culture-card { min-height:0; }
  .heritage-type-grid { grid-template-columns:1fr; }
  .place-braid-head { display:block; }
  .place-braid-readout { margin-top:10px; }
  .place-braid-grid { grid-template-columns:1fr; }
  .place-name-field { padding:13px; }
  .place-name-readout-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .county-pulse-head { display:block; }
  .county-pulse-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .makers-head { display:block; }
  .makers-stat { margin-top:10px; text-align:left; }
  .makers-binary { grid-template-columns:1fr; }
  .rhythm-head { display:block; }
  .rhythm-stat { margin-top:10px; text-align:left; }
  .rhythm-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .rhythm-readout { grid-template-columns:1fr; }
  .scale-head { display:block; }
  .scale-stat { margin-top:10px; text-align:left; }
  .scale-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .scale-readout { grid-template-columns:1fr; }
  .alignment-head { display:block; }
  .alignment-stat { margin-top:10px; text-align:left; }
  .alignment-grid { grid-template-columns:1fr; }
  .alignment-readout { grid-template-columns:1fr; }
  .source-head { display:block; }
  .source-stat { margin-top:10px; text-align:left; }
  .source-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .source-readout { grid-template-columns:1fr; }
  .trust-head { display:block; }
  .trust-stat { margin-top:10px; text-align:left; }
  .trust-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .trust-readout { grid-template-columns:1fr; }
  .place-name-head { display:block; }
  .place-name-stat { margin-top:10px; text-align:left; }
  .culture-timeline { padding:13px; }
  .land-field { padding:13px; }
  .land-group-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .culture-mosaic { grid-template-columns:1fr; }
  .toolbar { align-items:flex-start; flex-direction:column; }
  .toolbar-actions { width:100%; justify-content:flex-start; }
  .selection-card { margin:0 12px 10px; }
  .selection-focus-bar { display:block; }
  .selection-focus-bar button { width:100%; margin-top:8px; }
  .selection-evidence-head { display:block; }
  .selection-evidence-status { display:inline-block; margin-top:8px; }
  .selection-evidence-grid { grid-template-columns:1fr; }
  .selection-culture-head { display:block; }
  .selection-culture-facts { grid-template-columns:1fr; }
  .selection-context-head { display:block; }
  .selection-context-status { display:inline-block; margin-top:8px; }
  .selection-context-list { grid-template-columns:1fr; }
  .selection-fingerprint { grid-template-columns:1fr; }
  .selection-weave { grid-template-columns:1fr; }
  .selection-passport { display:block; }
  .selection-passport-actions { justify-content:flex-start; margin-top:9px; }
  .comparison-tray { margin:0 12px 10px; }
  .comparison-head { display:block; }
  .comparison-head-actions { justify-content:flex-start; margin-top:9px; }
  .comparison-share-status { text-align:left; }
  .comparison-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .comparison-relation-head { display:block; }
  .comparison-relation-status { display:inline-block; margin-top:8px; }
  .comparison-relation-metrics { grid-template-columns:1fr; }
}
@media (prefers-reduced-motion:reduce) {
  *, *::before, *::after { scroll-behavior:auto !important; animation-duration:.01ms !important; animation-iteration-count:1 !important; transition-duration:.01ms !important; }
}
</style>
</head>
<body class="intro-open">
<section id="siteIntro" class="site-intro" aria-labelledby="introTitle">
  <div class="intro-shell">
    <div class="intro-main">
      <div class="intro-topline"><span>CRUTH / FIELD ATLAS V3</span><span>Land · line · memory</span></div>
      <div class="intro-kicker">An Irish geometry of place</div>
      <h2 id="introTitle">Every stone has a <em>ratio.</em><br/>Every place has a memory.</h2>
      <p>Enter a living map of Irish land, buildings and shared space. Follow the evidence from footprint to equation, from equation to threshold, and from threshold back to the people and places that give it meaning.</p>
      <div class="intro-actions"><button id="enterAtlas" type="button">Enter the field →</button><button id="skipIntro" class="secondary" type="button">Skip opening</button></div>
    </div>
    <div class="intro-aside" aria-hidden="true">
      <div class="intro-orbit"><span class="intro-orbit-line"></span><span class="intro-orbit-line second"></span><span class="intro-orbit-core">φ</span><span class="intro-orbit-label north">north / 55°</span><span class="intro-orbit-label east">shore / edge</span><span class="intro-orbit-label south">south / 51°</span><span class="intro-orbit-label west">field / trace</span></div>
    </div>
  </div>
  <div class="intro-foot"><div class="intro-proof" aria-label="Current atlas snapshot"><span><strong id="introTargetCount">—</strong> footprints</span><span><strong id="introNiahCount">—</strong> NIAH joins</span><span><strong id="introSignalCount">—</strong> signal families <em>φ · θ · ↔ · □</em></span></div><span>scroll / click to begin</span></div>
</section>
<div id="map" aria-label="Map of analysed Irish buildings"></div>
<div id="mapLoading" role="status" aria-live="polite"><span class="map-loading-dot" aria-hidden="true"></span><span id="mapLoadingText">Loading live basemap…</span></div>
<div id="mapLabel" aria-hidden="true"><span class="map-label-kicker">Live satellite / measured Ireland</span><strong>Shape makes place.</strong><small>Explore the map as a field of building footprints, then translate the patterns into civic rooms, thresholds and shared space.</small></div>
<div id="mapStamp" aria-live="polite"><span class="map-stamp-kicker">Field coordinate</span><strong id="mapCenterText">53.2° N / 7.7° W</strong><small id="mapContextText">Ireland field · awaiting map context</small></div>
<div id="mapHud" aria-label="Live map controls">
  <div class="map-hud-card">
    <div class="map-hud-topline"><span>Live cartography</span><span id="mapLiveState" class="map-live-state">Connecting…</span></div>
    <div class="map-hud-title">Satellite fieldwork</div>
    <div class="map-hud-subtitle">Streamed imagery for place context · the analysis below remains a dated research snapshot.</div>
    <div class="map-layer-switcher" role="group" aria-label="Map layer">
      <button class="map-layer-button active" type="button" data-map-layer="satellite" aria-pressed="true">Satellite</button>
      <button class="map-layer-button" type="button" data-map-layer="hybrid" aria-pressed="false">Hybrid</button>
      <button class="map-layer-button" type="button" data-map-layer="streets" aria-pressed="false">Streets</button>
    </div>
    <div class="map-hud-meta"><span id="mapTileStatus">Waiting for imagery…</span><span id="mapVisibleCount">— targets in view</span></div>
    <div class="map-constellation" aria-live="polite" aria-label="Signal intensity in the current map field"><div class="map-constellation-head"><span>Signal constellation</span><small id="mapConstellationScope">active field · awaiting rows</small></div><div class="map-signal-grid"><div class="map-signal-cell"><b>φ</b><strong id="mapRatioSignal">—</strong><small>ratio</small></div><div class="map-signal-cell"><b>θ</b><strong id="mapAngleSignal">—</strong><small>angle</small></div><div class="map-signal-cell"><b>↔</b><strong id="mapSymmetrySignal">—</strong><small>mirror</small></div><div class="map-signal-cell"><b>□</b><strong id="mapOrthogonalSignal">—</strong><small>orthogonal</small></div></div></div>
    <div class="map-hud-actions">
      <button id="mapFit" class="map-hud-action" type="button">Fit visible targets</button>
      <button id="mapReset" class="map-hud-action" type="button">Reset view</button>
    </div>
    <button id="mapRefresh" class="map-hud-action" type="button">Refresh imagery</button>
    <div class="map-hud-note">Map points follow the active filters. Satellite tiles are live; analytical rows are the dated research snapshot shown in the panel.</div>
  </div>
</div>
<div id="panel">
  <header>
    <div class="hero-topline"><span>IRELAND / 02—FIELD ATLAS</span><span class="hero-tag">Mathematics · memory · land</span></div>
    <h1><em>Cruth</em>: Ireland<br/>in proportion</h1>
    <div class="subtitle">A data-backed field atlas where Irish land, building footprints, heritage records and civic imagination meet. Read the island as coordinates, the building as geometry, and culture as the context that keeps both honest.</div>
    <p class="hero-note">The scan finds geometric signals. The studio translates them into contemporary possibilities; it does not claim historic intent or reduce Irish culture to a formula.</p>
    <div class="header-actions"><a href="#field">Enter the field</a><a href="#studio">Open the design studio</a><a href="#culture">Read the cultural lens</a><a href="#patterns">Browse measured patterns</a><button id="replayIntro" type="button">Replay opening</button><a href="review.html" target="_blank" rel="noopener">Open expert review queue</a></div>
    <div class="hero-metrics" aria-label="Atlas at a glance"><div class="hero-metric"><strong id="heroTargetCount">—</strong><span>target footprints</span></div><div class="hero-metric"><strong id="heroNiahCount">—</strong><span>NIAH-linked joins</span></div><div class="hero-metric"><strong id="heroSignalCount">—</strong><span>geometry signals</span></div><div class="hero-metric"><strong id="heroSnapshot">V3</strong><span>field atlas release</span></div></div>
  </header>
  <nav id="atlasNav" class="atlas-nav" aria-label="Atlas sections">
    <div class="atlas-nav-links"><a href="#field" data-nav-section="field" data-nav-label="The Irish field" aria-current="page">Field</a><a href="#maths" data-nav-section="maths" data-nav-label="Mathematical grammar">Maths</a><a href="#studio" data-nav-section="studio" data-nav-label="Design studio">Studio</a><a href="#culture" data-nav-section="culture" data-nav-label="Cultural lens">Culture</a><a href="#filters" data-nav-section="filters" data-nav-label="Explore targets">Explore</a><a href="#evidence" data-nav-section="evidence" data-nav-label="Evidence and findings">Evidence</a></div>
    <span id="atlasNavStatus" class="atlas-nav-status" aria-live="polite">Design studio</span>
  </nav>
  <section id="field" class="field-section atlas-section" aria-labelledby="fieldTitle">
    <div class="field-content">
      <div>
        <div class="field-kicker">The Irish field / a measured island</div>
        <h2 id="fieldTitle">Start with the land.<br/><em>Then let the building speak.</em></h2>
        <p>Coordinates give us the first precision: a footprint belongs somewhere, in a county, beside a road, under a particular light. The mathematics here is a lens for noticing—ratios, angles, symmetry, circles—not a story that replaces memory, craft, ecology or lived culture.</p>
        <div class="field-principles" aria-label="Field principles"><button type="button" data-field-principle="named" aria-controls="filters" aria-pressed="false" aria-label="Filter the atlas by Ainm, named places">ainm / name <b aria-hidden="true">→</b></button><button type="button" data-field-principle="heritage" aria-controls="filters" aria-pressed="false" aria-label="Filter the atlas by Oidhreacht, heritage joins">oidhreacht / heritage <b aria-hidden="true">→</b></button><button type="button" data-field-principle="form" aria-controls="maths" aria-pressed="false" aria-label="Open Cruth, the mathematical grammar">cruth / form <b aria-hidden="true">→</b></button><button type="button" data-field-principle="pobal" aria-controls="filters" aria-pressed="false" aria-label="Filter the atlas by Pobal, shared life">pobal / shared life <b aria-hidden="true">→</b></button></div>
      </div>
      <div class="field-coordinate" aria-label="Coordinate field diagram"><div class="field-coordinate-top"><span>Coordinate field</span><small>WGS84 / snapshot</small></div><div id="coordinatePlot" class="coordinate-plot" aria-label="Ireland field coordinate marker with latitude and longitude axes"><span id="coordinatePlotReadout" class="coordinate-plot-readout">Ireland field centre</span><div class="coordinate-axis coordinate-latitude-axis" aria-hidden="true"><span>55° N</span><span>54°</span><span>53°</span><span>52°</span><span>51° N</span></div><div class="coordinate-axis coordinate-longitude-axis" aria-hidden="true"><span>10.7° W</span><span>9° W</span><span>8° W</span><span>7° W</span><span>5.3° W</span></div></div><p id="coordinateNote" class="coordinate-note">A schematic WGS84 field for the current report pack: latitude rises vertically and longitude runs west to east. The live map carries the actual points; every measurement stays situated.</p><div id="fieldLight" class="field-light" aria-live="polite" aria-label="Approximate seasonal light field"><div class="field-light-top"><span>Solas / light field</span><small id="fieldLightScope">Ireland field centre</small></div><strong id="fieldLightHeadline">Seasonal light is loading.</strong><div class="field-light-metrics" aria-label="Seasonal light measurements"><span class="field-light-metric"><b id="fieldLightLatitude">—</b><small>latitude</small></span><span class="field-light-metric"><b id="fieldLightSummer">—</b><small>midsummer daylight</small></span><span class="field-light-metric"><b id="fieldLightWinter">—</b><small>midwinter daylight</small></span></div><p id="fieldLightNote" class="field-light-note">Approximate horizon geometry will appear when the field loads.</p></div><div id="fieldShape" class="field-shape" aria-live="polite" aria-label="Measured footprint shape field"><div class="field-shape-top"><span>Cruth / shape field</span><small id="fieldShapeScope">Waiting for a footprint</small></div><strong id="fieldShapeHeadline">Read a footprint as measured form.</strong><div class="field-shape-grid"><div id="fieldShapeVisual" class="field-shape-visual" role="img" aria-label="No footprint selected"><svg viewBox="0 0 150 72" aria-hidden="true"><path d="M10 36 H140 M75 8 V64" fill="none" stroke="#e1bd66" stroke-opacity=".24" stroke-width=".8" stroke-dasharray="3 4"/><path d="M53 20 H97 V52 H53 Z" fill="#6d9b8f" fill-opacity=".22" stroke="#e1bd66" stroke-width="1.4"/><circle cx="75" cy="36" r="3" fill="#e1bd66" stroke="#103537" stroke-width="1"/></svg><small class="field-shape-visual-note" id="fieldShapeVisualNote">select a field stop or map point</small></div><div class="field-shape-metrics" aria-label="Measured footprint properties"><span class="field-shape-metric"><b id="fieldShapeArea">—</b><small>area</small></span><span class="field-shape-metric"><b id="fieldShapePerimeter">—</b><small>perimeter</small></span><span class="field-shape-metric"><b id="fieldShapeScale">—</b><small>length × width</small></span><span class="field-shape-metric"><b id="fieldShapeAspect">—</b><small>aspect r</small></span></div></div><p id="fieldShapeNote" class="field-shape-note">Select a field walk stop or map point to draw its mapped outline and translate area, boundary and proportion into visible form.</p></div><div id="fieldRoots" class="field-roots" aria-live="polite" aria-label="Source-linked Irish roots field"><div class="field-roots-top"><span>Fréamh / roots field</span><small id="fieldRootsScope">Irish field context</small></div><strong id="fieldRootsHeadline">Irish land is a network of names, records and shared places.</strong><div class="field-roots-grid" aria-label="Irish roots context"><span class="field-roots-metric"><b id="fieldRootsPlace">—</b><small>Áit / place</small></span><span class="field-roots-metric"><b id="fieldRootsHeritage">—</b><small>Oidhreacht / heritage</small></span><span class="field-roots-metric"><b id="fieldRootsGroup">—</b><small>Pobal / shared life</small></span></div><p id="fieldRootsNote" class="field-roots-note">Source-linked place and heritage context will appear when the field loads.</p></div></div>
    </div>
    <div class="field-sequence" aria-label="Atlas narrative sequence"><button class="field-sequence-step" type="button" data-sequence-target="field" data-sequence-section="field"><span>01</span><strong>Land</strong><small>shore · weather · ground</small></button><button class="field-sequence-step" type="button" data-sequence-target="field" data-sequence-section="field"><span>02</span><strong>Coordinate</strong><small>where the point belongs</small></button><button class="field-sequence-step" type="button" data-sequence-target="fieldWalk" data-sequence-section="field"><span>03</span><strong>Footprint</strong><small>area · edge · scale</small></button><button class="field-sequence-step" type="button" data-sequence-target="maths" data-sequence-section="maths"><span>04</span><strong>Maths</strong><small>ratio · angle · symmetry</small></button><button class="field-sequence-step" type="button" data-sequence-target="culture" data-sequence-section="culture"><span>05</span><strong>Heritage</strong><small>record · name · time</small></button><button class="field-sequence-step" type="button" data-sequence-target="culture" data-sequence-section="culture"><span>06</span><strong>Culture</strong><small>Áit · Pobal · Oidhreacht</small></button><button class="field-sequence-step" type="button" data-sequence-target="studio" data-sequence-section="studio"><span>07</span><strong>Civic possibility</strong><small>the shared room ahead</small></button></div>
    <div id="fieldWalk" class="field-walk" aria-labelledby="fieldWalkTitle">
      <div class="field-walk-head"><div><span class="field-walk-kicker">Wander the field / four measured invitations</span><h3 id="fieldWalkTitle">No route required.<br/><em>Start where the signal catches you.</em></h3><p class="field-walk-intro">These waypoints are selected from the current research snapshot to give a first visit a human scale. Open one to bring its real footprint, map position, source chain and mathematical dossier into view.</p></div><div class="field-walk-count"><strong id="fieldWalkCount">—</strong><small>curated waypoints</small></div></div>
      <div id="fieldWalkGrid" class="field-walk-grid" aria-label="Curated field walk waypoints"></div>
      <div id="fieldJourney" class="field-journey" aria-labelledby="fieldJourneyHeading">
        <div class="field-journey-head">
          <div><span class="field-walk-kicker">Slí / journey field</span><h4 id="fieldJourneyHeading">Not a route.<br/><em>A line of attention.</em></h4><p>The curated stops are threaded by their source coordinates. Read the straight-line span, the next leg and its bearing as a geographic rhythm—not as a walking route or a historical itinerary.</p></div>
          <div class="field-journey-readout" aria-live="polite"><span id="fieldJourneyStatus">Coordinate journey</span><strong id="fieldJourneyReadoutTitle">Choose a field stop to set your position.</strong><p id="fieldJourneyReadoutText">The journey field will connect the curated stops when the report pack loads.</p><div class="field-journey-actions"><button id="fieldJourneyCarry" type="button" hidden>Carry active leg to studio →</button><span id="fieldJourneyActionStatus" class="field-journey-action-status" role="status" aria-live="polite"></span></div></div>
        </div>
        <div id="fieldJourneyTrack" class="field-journey-track" role="list" aria-label="Straight-line journey between curated field stops"></div>
        <div class="field-journey-metrics" aria-label="Journey geometry measurements"><span class="field-journey-metric"><b id="fieldJourneyTotal">—</b><small>first → last span</small></span><span class="field-journey-metric"><b id="fieldJourneyLeg">—</b><small>active leg</small></span><span class="field-journey-metric"><b id="fieldJourneyBearing">—</b><small id="fieldJourneyBearingLabel">first → last bearing</small></span></div>
        <p id="fieldJourneyNote" class="field-journey-note">Coordinates will remain separate from route, walking, and historical claims.</p>
      </div>
      <div id="fieldWalkControls" class="field-walk-controls" aria-live="polite" hidden><button id="fieldWalkPrevious" class="field-walk-nav" type="button" data-field-walk-nav="previous" aria-label="Go to the previous field walk stop">← Previous stop</button><span id="fieldWalkProgress" class="field-walk-progress">Choose a stop to begin</span><button id="fieldWalkNext" class="field-walk-nav" type="button" data-field-walk-nav="next" aria-label="Go to the next field walk stop">Next stop →</button></div>
      <p id="fieldWalkNote" class="field-walk-note">The walk is a reproducible starting sample, not a ranking of Irish buildings or evidence of historic mathematical intention.</p>
    </div>
    <div class="field-signals" aria-label="Measured mathematical signals"><button class="field-signal" type="button" data-field-signal="golden_ratio" aria-controls="patterns"><div class="field-signal-top"><span>φ / proportion</span><b id="fieldRatioCount">—</b></div><strong>Golden ratio screens</strong><p id="fieldRatioText">Loading measured footprint signals.</p><div class="field-meter"><i id="fieldRatioMeter"></i></div><span class="field-signal-action">Trace this signal →</span></button><button class="field-signal" type="button" data-field-signal="golden_angle" aria-controls="patterns"><div class="field-signal-top"><span>θ / rotation</span><b id="fieldAngleCount">—</b></div><strong>Golden-angle screens</strong><p id="fieldAngleText">Loading measured footprint signals.</p><div class="field-meter"><i id="fieldAngleMeter"></i></div><span class="field-signal-action">Trace this signal →</span></button><button class="field-signal" type="button" data-field-signal="reflective_symmetry" aria-controls="patterns"><div class="field-signal-top"><span>↔ / symmetry</span><b id="fieldSymmetryCount">—</b></div><strong>Reflective symmetry</strong><p id="fieldSymmetryText">Loading measured footprint signals.</p><div class="field-meter"><i id="fieldSymmetryMeter"></i></div><span class="field-signal-action">Trace this signal →</span></button><button class="field-signal" type="button" data-field-signal="orthogonal" aria-controls="patterns"><div class="field-signal-top"><span>□ / order</span><b id="fieldOrthogonalCount">—</b></div><strong>Orthogonal traces</strong><p id="fieldOrthogonalText">Loading measured footprint signals.</p><div class="field-meter"><i id="fieldOrthogonalMeter"></i></div><span class="field-signal-action">Trace this signal →</span></button></div>
    <div class="field-signal-detail" aria-live="polite"><span>Signal lens</span><div><strong id="fieldSignalDetailTitle">Choose a signal to trace it.</strong><p id="fieldSignalDetailText">Select a mathematical signal to filter the building footprints, focus the map, and carry the question into the heritage and culture layers below.</p></div></div>
    <div class="measure-ledger" aria-label="Shape measurement ledger"><article><span>Area</span><strong>A = footprint</strong><small>m² · surface enclosed</small></article><article><span>Perimeter</span><strong>P = boundary</strong><small>m · edge length</small></article><article><span>Scale</span><strong>l × w</strong><small>length · width in metres</small></article><article><span>Aspect</span><strong>r = l / w</strong><small>elongation ratio</small></article><article><span>Compactness</span><strong>C = 4πA / P²</strong><small>circle-normalised form</small></article><article><span>Radial field</span><strong>σᵣ / μᵣ</strong><small>variation from centre</small></article></div>
    <p class="field-footnote">Signal counts are descriptive screens in the current data pack. They show where to look next—not evidence that a historical builder consciously used a named mathematical system.</p>
  </section>
  <section id="maths" class="maths-section atlas-section" aria-labelledby="mathsTitle">
    <div class="maths-head">
      <div>
        <div class="maths-kicker">Mathematical grammar / measured signals</div>
        <h2 id="mathsTitle">Measure first.<br/><em>Interpret carefully.</em></h2>
        <p>This index names the mathematical properties used to read Irish building footprints: proportion, angle, symmetry, compactness and boundary shape. Select a card to trace a screening signal into the catalogue and map, or to read the descriptor inside a selected building’s geometry dossier.</p>
      </div>
      <div class="maths-notation" aria-hidden="true">A / P<br/>φ · θ · Fₙ</div>
    </div>
    <div id="mathsReadingContext" class="maths-reading-context" aria-live="polite" hidden>
      <div>
        <span>Tracing from the dossier / selected footprint</span>
        <strong id="mathsReadingContextTitle">—</strong>
        <p id="mathsReadingContextText">The Maths field stays attached to the measured footprint that opened it.</p>
      </div>
      <button id="mathsReadingReturn" type="button">Return to building dossier →</button>
    </div>
    <div id="mathsIndex" class="maths-grid" aria-label="Interactive mathematical property index"></div>
    <div class="maths-readout" aria-live="polite"><span>Maths lens</span><div><strong id="mathsReadoutTitle">Choose a property to trace it.</strong><p id="mathsReadoutText">Each card connects a named mathematical idea to a measured descriptor or screening flag in this report.</p></div></div>
    <p class="maths-caveat">The index names measurements and screens used by this atlas. It does not turn a mathematical resemblance into evidence of historic intention or a single Irish architectural tradition.</p>
  </section>
  <section id="studio" class="studio-section atlas-section">
    <div class="studio-head">
      <div class="studio-kicker">Irish civic geometry / design hypothesis</div>
      <h2>Four equations. <em>Four ways</em> to make a public room.</h2>
      <p>Irish places are interesting when landscape, weather, craft and social ritual meet. This design grammar treats mathematics as a legible tool for making space—not as a shortcut to explain culture. Select an equation, then test it against a civic typology.</p>
      <span class="studio-disclaimer">Contemporary translation inspired by Irish/Celtic visual language · not a historical reconstruction</span>
      <div class="studio-share"><button id="copyStudioLink" type="button">Copy studio link →</button><span id="studioShareStatus" class="studio-share-status" role="status" aria-live="polite">Share the current equation, field reference and test-fit settings.</span></div>
    </div>
    <div class="equation-grid" role="tablist" aria-label="Civic geometry equations">
      <button class="equation-card is-active" type="button" data-equation="phi" role="tab" aria-selected="true">
        <span class="equation-symbol">φ</span><b>Golden proportion</b><div class="equation">φ = (1 + √5) / 2 ≈ 1.618</div><p>Sequence chamber, foyer and threshold as a calm-to-active civic gradient.</p>
      </button>
      <button class="equation-card" type="button" data-equation="theta" role="tab" aria-selected="false">
        <span class="equation-symbol">θ</span><b>Golden angle</b><div class="equation">θ = 360° / φ² ≈ 137.5°</div><p>Rotate trees, lanterns or rain gardens to make a porous, non-linear route.</p>
      </button>
      <button class="equation-card" type="button" data-equation="fib" role="tab" aria-selected="false">
        <span class="equation-symbol">F</span><b>Fibonacci modules</b><div class="equation">Fₙ = Fₙ₋₁ + Fₙ₋₂</div><p>Build a kit of 3, 5, 8, 13 and 21 metre bays for adaptable public use.</p>
      </button>
      <button class="equation-card" type="button" data-equation="spiral" role="tab" aria-selected="false">
        <span class="equation-symbol">↻</span><b>Spiral / triskele curve</b><div class="equation">r(θ) = a · e<sup>bθ</sup></div><p>Turn a growing curve into a ramp, canopy edge or gallery that keeps unfolding.</p>
      </button>
    </div>
    <div id="studioReference" class="studio-reference" aria-live="polite" hidden>
      <div class="studio-reference-head"><div><span class="studio-kicker">Field reference / carried place</span><h3 id="studioReferenceTitle">Selected footprint</h3><p id="studioReferenceContext">A measured Irish footprint is now the reference for this contemporary test-fit.</p></div><button id="clearStudioReference" class="studio-reference-clear" type="button">Clear reference</button></div>
      <div class="studio-reference-grid" aria-label="Selected footprint reference measurements"><div class="studio-reference-fact"><span>Place</span><strong id="studioReferencePlace">—</strong><small id="studioReferenceSource">—</small></div><div class="studio-reference-fact"><span>Footprint</span><strong id="studioReferenceDimensions">—</strong><small id="studioReferenceArea">—</small></div><div class="studio-reference-fact"><span>Proportion</span><strong id="studioReferenceAspect">—</strong><small id="studioReferenceCompactness">—</small></div><div class="studio-reference-fact"><span>Signals</span><strong id="studioReferenceSignals">—</strong><small id="studioReferenceHeritage">—</small></div></div>
      <div class="studio-reference-actions"><button id="studioUseScale" type="button">Use measured width as module</button><span id="studioReferenceStatus" class="studio-reference-status" role="status" aria-live="polite">Reference only · controls remain editable.</span></div>
      <p class="studio-reference-note">This is a contemporary translation: the selected building supplies measured scale and context, while the studio proposes a new civic possibility. It does not reconstruct historic intent.</p>
    </div>
    <div id="studioPairReference" class="studio-reference studio-pair-reference" aria-live="polite" hidden>
      <div class="studio-reference-head"><div><span class="studio-kicker">Between reference / carried relationship</span><h3 id="studioPairTitle">Two places in one civic field</h3><p id="studioPairContext">A comparison relationship can become a contemporary spatial starting point.</p></div><button id="clearStudioPairReference" class="studio-reference-clear" type="button">Clear pair</button></div>
      <div class="studio-reference-grid" aria-label="Paired place relationship reference"><div class="studio-reference-fact"><span>Field A</span><strong id="studioPairPlaceA">—</strong><small id="studioPairSourceA">—</small></div><div class="studio-reference-fact"><span>Field B</span><strong id="studioPairPlaceB">—</strong><small id="studioPairSourceB">—</small></div><div class="studio-reference-fact"><span>Between</span><strong id="studioPairSpan">—</strong><small id="studioPairBearing">—</small></div><div class="studio-reference-fact"><span>Bridge</span><strong id="studioPairBridge">—</strong><small id="studioPairSignals">—</small></div></div>
      <div class="studio-reference-actions"><button id="studioUsePairBearing" type="button">Use A→B bearing as path rotation</button><span id="studioPairStatus" class="studio-reference-status" role="status" aria-live="polite">Reference only · controls remain editable.</span></div>
      <p class="studio-reference-note">The pair supplies a measured relationship, not a historic pattern. Using its orientation in the studio is a contemporary design choice; it does not claim that the buildings were designed as a pair.</p>
    </div>
    <div class="lab" aria-label="Interactive civic test-fit">
      <div class="lab-toolbar">
        <label>Prototype typology<select id="typology" aria-label="Prototype typology"><option value="parliament">Parliament / assembly</option><option value="forum">Public forum square</option><option value="market">Market canopy</option><option value="harbour">Harbour garden</option><option value="library">Library courtyard</option><option value="museum">Museum loop</option></select></label>
        <label>Primary module <span class="module-readout" id="moduleValue">13 m</span><input id="moduleScale" type="range" min="5" max="34" step="1" value="13" aria-label="Primary module in metres"/></label>
        <label>Seasonal lens<select id="season" aria-label="Seasonal lens"><option value="midsummer">Long-light / midsummer</option><option value="equinox">Equinox / changeover</option><option value="midwinter">Low-light / midwinter</option></select></label>
        <label>Material study<select id="material" aria-label="Material study"><option value="stone">Stone + lime</option><option value="timber">Timber + cork</option><option value="slate">Slate + rain chain</option><option value="copper">Copper + planted roof</option></select></label>
        <label>Spatial grammar<select id="grammar" aria-label="Spatial geometry grammar"><option value="radial">Radial field</option><option value="symmetry">Mirror symmetry</option><option value="orthogonal">Orthogonal grid</option><option value="circle">Circular court</option><option value="cruciform">Cruciform plan</option></select></label>
      </div>
      <div class="scenario-controls" aria-label="Public space scenario controls">
        <div class="scenario-heading"><span><b>02 / Public-realm test-fit</b><br/>Tune the shared ground around the building.</span><small id="scenarioSummary">38% court · 8 bays · 137.5° path · 60% public density</small></div>
        <div class="scenario-control-grid">
          <label>Courtyard void <output id="courtyardValue">38%</output><input id="courtyardScale" type="range" min="10" max="72" step="1" value="38" aria-label="Courtyard void percentage"/></label>
          <label>Bay count <output id="bayValue">8</output><input id="bayCount" type="range" min="3" max="13" step="1" value="8" aria-label="Number of public bays"/></label>
          <label>Path rotation <output id="pathValue">137.5°</output><input id="pathAngle" type="range" min="0" max="180" step="0.5" value="137.5" aria-label="Public path rotation in degrees"/></label>
          <label>Public density <output id="densityValue">60%</output><input id="publicDensity" type="range" min="20" max="100" step="1" value="60" aria-label="Public-space density percentage"/></label>
        </div>
      </div>
      <div class="performance-controls" aria-label="Climate accessibility and phasing controls">
        <div class="performance-heading"><span><b>03 / Performance + delivery</b><br/>Make the architectural idea answer to weather, access and time.</span><small id="performanceSummary">64% shelter · 72% rain capture · 1.8 m clear route · 2 phases</small></div>
        <div class="performance-control-grid">
          <label>Wind shelter <output id="windValue">64%</output><input id="windShelter" type="range" min="0" max="100" step="1" value="64" aria-label="Wind shelter emphasis percentage"/></label>
          <label>Rain capture <output id="rainValue">72%</output><input id="rainCapture" type="range" min="0" max="100" step="1" value="72" aria-label="Rainwater capture emphasis percentage"/></label>
          <label>Accessible route <output id="accessValue">1.8 m</output><input id="accessWidth" type="range" min="1.2" max="2.4" step="0.1" value="1.8" aria-label="Clear accessible route width in metres"/></label>
          <label>Future phases <output id="phaseValue">2 phases</output><input id="futurePhases" type="range" min="1" max="3" step="1" value="2" aria-label="Number of future building phases"/></label>
        </div>
        <div class="water-event-control"><label>Rain event<select id="rainEvent" aria-label="Indicative rainfall event"><option value="light">5 mm / light pulse</option><option value="design" selected>10 mm / design pulse</option><option value="heavy">20 mm / heavy pulse</option></select></label><p id="rainEventNote">Choose one transparent rainfall pulse to make the catchment arithmetic visible.</p></div>
      </div>
      <div class="performance-controls programme-controls" aria-label="Programme and building scale controls">
        <div class="performance-heading"><span><b>04 / Programme + scale</b><br/>Balance public rooms, support space and vertical growth.</span><small id="programmeSummary">70% public programme · 2 levels</small></div>
        <div class="performance-control-grid">
          <label>Public programme <output id="publicMixValue">70%</output><input id="publicMix" type="range" min="30" max="95" step="1" value="70" aria-label="Public programme percentage"/></label>
          <label>Building levels <output id="levelValue">2 levels</output><input id="buildingLevels" type="range" min="1" max="5" step="1" value="2" aria-label="Number of building levels"/></label>
        </div>
      </div>
      <div class="lab-grid">
        <div class="diagram-frame"><svg id="designDiagram" viewBox="0 0 600 330" role="img" aria-label="Schematic civic building geometry"></svg><div class="diagram-caption" id="diagramCaption">Schematic only · dimensions are a test-fit, not a construction drawing.</div></div>
        <div class="lab-copy" id="labCopy"></div>
      </div>
      <div id="designSpec" class="design-spec" aria-live="polite"></div>
      <div id="designSchedule" class="design-schedule" aria-live="polite"></div>
      <section id="skyField" class="sky-field" aria-labelledby="skyFieldTitle">
        <div class="sky-field-head"><div><span class="sky-field-kicker">Solas / sky geometry</span><h3 id="skyFieldTitle">Let the place set the light.</h3><p id="skyFieldIntro">A seasonal light reading derived from the field latitude and the selected studio lens.</p></div><span id="skyFieldScope" class="sky-field-scope">Ireland field</span></div>
        <div class="sky-field-grid">
          <div id="skyPlot" class="sky-plot" role="img" aria-label="Indicative seasonal solar geometry"><span class="sky-plot-label north">N / horizon</span><span class="sky-plot-label south">S / noon axis</span><span id="skySunLabel" class="sky-sun-label">solar noon</span><i id="skySun" class="sky-sun" aria-hidden="true"></i></div>
          <div class="sky-facts" aria-label="Derived seasonal light measurements"><div class="sky-fact"><span>Latitude</span><strong id="skyLatitude">53.35° N</strong><small id="skyLatitudeNote">Ireland field centre</small></div><div class="sky-fact"><span>Day length</span><strong id="skyDaylight">—</strong><small id="skyDaylightNote">approximate horizon-to-horizon light</small></div><div class="sky-fact"><span>Solar noon</span><strong id="skyNoonAltitude">—</strong><small>maximum sun altitude</small></div><div class="sky-fact"><span>Sunrise / sunset</span><strong id="skyBearings">—</strong><small>compass bearings from north</small></div></div>
        </div>
        <p id="skyFieldNote" class="sky-field-note">Indicative geometry only: latitude and a standard seasonal solar declination are used to make the light question visible; this is not a site-specific daylight, glare or energy model.</p>
      </section>
      <section id="waterField" class="water-field" aria-labelledby="waterFieldTitle">
        <div class="water-field-head"><div><span class="water-field-kicker">Uisce / water geometry</span><h3 id="waterFieldTitle">Make the rain legible.</h3><p id="waterFieldIntro">A one-event catchment reading turns the covered civic field into a visible relationship between roof, rain and public ground.</p></div><span id="waterFieldBadge" class="water-field-badge">10 mm pulse</span></div>
        <div class="water-field-grid">
          <div class="water-equation"><span>Catchment arithmetic</span><strong id="waterEquation">—</strong><p id="waterEquationNote">Covered field × rainfall depth = one transparent event volume before losses.</p><div class="water-route"><span>Typology water route</span><b id="waterRoute">—</b></div></div>
          <div class="water-facts" aria-label="Derived rainwater catchment measurements"><div class="water-fact"><span>Covered field</span><strong id="waterRoofArea">—</strong><small>enclosed module area</small></div><div class="water-fact"><span>Rain pulse</span><strong id="waterRainDepth">—</strong><small>one indicative event</small></div><div class="water-fact"><span>Event volume</span><strong id="waterEventVolume">—</strong><small>before capture losses</small></div><div class="water-fact"><span>Capture setting</span><strong id="waterCapturedVolume">—</strong><small id="waterOverflow">scenario emphasis · verify locally</small></div></div>
        </div>
        <p id="waterFieldNote" class="water-field-note">Indicative arithmetic only: 1 m² × 1 mm = 1 litre. This is not a hydrological, drainage, storage, flooding, water-quality or compliance model.</p>
      </section>
      <div class="brief-panel" aria-label="Exportable design brief">
        <div class="brief-head"><div><span class="studio-kicker">Concept brief</span><h3>Carry the geometry into a review.</h3></div><div class="brief-actions"><button id="copyBrief" type="button">Copy brief</button><button id="downloadBrief" type="button">Download .txt</button></div></div>
        <pre id="designBrief" class="design-brief">Select a typology to generate a concept brief.</pre>
        <p id="briefStatus" class="brief-status" role="status" aria-live="polite">Indicative design arithmetic only · verify standards, site conditions, structure, fire, cost and planning requirements separately.</p>
      </div>
    </div>
    <div class="typology-grid" aria-label="Civic typology translations">
      <article class="typology-card"><span>01 / Representation</span><h3>Parliament</h3><p>A central chamber opens to a public foyer, gallery and planted threshold.</p><b class="typology-move">13 × φ ≈ 21 m</b></article>
      <article class="typology-card"><span>02 / Encounter</span><h3>Forum square</h3><p>A shared center is framed by a rotated path so no edge becomes a dead end.</p><b class="typology-move">12 turns × 137.5°</b></article>
      <article class="typology-card"><span>03 / Exchange</span><h3>Market canopy</h3><p>Small stalls aggregate into a weather-ready civic room with flexible edges.</p><b class="typology-move">3 · 5 · 8 · 13 bays</b></article>
      <article class="typology-card"><span>04 / Belonging</span><h3>Harbour garden</h3><p>A spiral walk slows the threshold between town, water, wind and gathering.</p><b class="typology-move">r = a · e<sup>bθ</sup></b></article>
      <article class="typology-card"><span>05 / Learning</span><h3>Library courtyard</h3><p>Quiet reading rooms gather around a daylit court with a visible public route.</p><b class="typology-move">grid × circle × pause</b></article>
      <article class="typology-card"><span>06 / Interpretation</span><h3>Museum loop</h3><p>A sequence of galleries, thresholds and rest points turns evidence into shared memory.</p><b class="typology-move">ring + spiral + threshold</b></article>
    </div>
    <div class="studio-metrics" aria-label="Data signals supporting the design studio">
      <div class="studio-metric"><strong id="studioSignalRate">7.09%</strong><span>golden-angle flag in worship targets</span><small id="studioSignalCompare">vs 0.69% in controls</small></div>
      <div class="studio-metric"><strong id="studioGovernmentRate">6.65%</strong><span>golden-angle flag in government targets</span><small>public buildings deserve a closer look</small></div>
      <div class="studio-metric"><strong id="studioTargetCount">33,416</strong><span>target footprints in the current pack</span><small>screening evidence, not proof of intent</small></div>
    </div>
    <div class="evidence-bridge" aria-label="Evidence bridge for the design studio">
      <div class="evidence-bridge-head"><div><span class="studio-kicker">Evidence bridge</span><h3>Design from signals, then check the source.</h3></div><span id="studioValidation" class="evidence-badge">validation pending</span></div>
      <div class="evidence-grid">
        <article><span class="evidence-index">01 / measured signal</span><b id="studioEvidenceSignal">Golden-angle flags are exploratory geometry screens.</b><p id="studioEvidenceText">A signal can suggest where to look; it cannot establish who designed a building or why.</p><a href="#patterns">Inspect the pattern catalogue →</a></article>
        <article><span class="evidence-index">02 / heritage inventory</span><b id="studioEvidenceNiah">NIAH joins connect footprints to named places.</b><p>Use the inventory to form questions about date, type, rating and context—not to infer a single Irish design tradition.</p><a href="https://www.buildingsofireland.ie/niah-data-download/" target="_blank" rel="noopener">Open Buildings of Ireland →</a></article>
        <article><span class="evidence-index">03 / open map</span><b id="studioEvidenceMap">OSM footprints keep the test-fit grounded in place.</b><p>Open the source geometry, review the map context, then adapt the proposal to roads, water, weather and access.</p><a href="https://www.openstreetmap.org/" target="_blank" rel="noopener">OpenStreetMap contributors →</a></article>
      </div>
      <p id="studioCaveat" class="evidence-caveat">Evidence caveats will appear here once the report pack is loaded.</p>
    </div>
  </section>
  <section id="culture" class="culture-section atlas-section" aria-labelledby="cultureTitle">
    <div class="culture-head">
      <div>
        <div class="studio-kicker">Living Ireland / cultural lens</div>
        <h2 id="cultureTitle">A place is more than a pattern.</h2>
        <p>Read the atlas through names, inherited fabric, shared life and county difference. These lenses use the current OSM, NIAH and spatial-context data; they do not claim that one geometry explains Irish culture.</p>
      </div>
      <div class="culture-mark" aria-hidden="true"><span>Cruth</span><b>Áit</b><small>shape → place</small></div>
    </div>
    <div id="cultureReadingContext" class="culture-reading-context" aria-live="polite" hidden>
      <div>
        <span>Reading from the dossier / selected place</span>
        <strong id="cultureReadingContextTitle">—</strong>
        <p id="cultureReadingContextText">The cultural field stays attached to the measured footprint that opened it.</p>
      </div>
      <button id="cultureReadingReturn" type="button">Return to building dossier →</button>
    </div>
    <div class="spectrum-field" aria-labelledby="spectrumHeading">
      <div class="spectrum-head"><div><span class="culture-mosaic-label">Cruth / measured constellation</span><h3 id="spectrumHeading">The island becomes a cloud of forms.</h3><p>Read aspect ratio <b>r</b> against circularity <b>C</b>. The φ guide marks the golden-ratio screen; colours keep building-group context in view. Select a point to return to the source row, map and dossier.</p></div><div class="spectrum-readout" aria-live="polite"><span id="spectrumStatus">Measured field / r × C</span><strong id="spectrumReadoutTitle">The measured field is ready to explore.</strong><p id="spectrumReadoutText">Each point is a source-linked footprint descriptor; the cloud is a diagnostic surface, not a map of cultural meaning.</p><div class="spectrum-readout-metrics"><span class="spectrum-readout-metric"><b id="spectrumCount">—</b><small>plotted / visible</small></span><span class="spectrum-readout-metric"><b id="spectrumMedian">—</b><small>field median r / C</small></span><span class="spectrum-readout-metric"><b id="spectrumSignals">—</b><small>φ / θ screens</small></span></div></div></div>
      <div class="spectrum-plot-wrap"><svg id="geometrySpectrumPlot" class="spectrum-plot" viewBox="0 0 760 260" role="img" aria-label="Measured building footprint constellation by aspect ratio and circularity"></svg></div>
      <div id="spectrumLegend" class="spectrum-legend" aria-label="Building group colours"></div>
      <div class="spectrum-notation" aria-label="Mathematical notation key"><button type="button" data-maths-read="maths" aria-label="Read aspect ratio r in the maths section"><b>r</b><small>L / W · proportion</small></button><button type="button" data-maths-read="maths" aria-label="Read circularity C in the maths section"><b>C</b><small>4πA / P² · compactness</small></button><button type="button" data-maths-read="maths" aria-label="Read golden ratio phi in the maths section"><b>φ</b><small>1.618… · ratio screen</small></button><button type="button" data-maths-read="maths" aria-label="Read golden angle theta in the maths section"><b>θ</b><small>137.5° · angle screen</small></button></div>
      <p id="spectrumNote" class="spectrum-note">The constellation will appear when the report pack loads. r is a footprint aspect ratio and C is 4πA/P²; both are mapped descriptors, not evidence of design intent.</p>
    </div>
    <div class="culture-grid" aria-label="Data-derived Irish cultural lenses">
      <article class="culture-card">
        <div class="culture-card-top"><span>01 / Ainm</span><small>name</small></div>
        <strong id="cultureNamedCount">—</strong>
        <h3>Names keep place specific.</h3>
        <p><b id="cultureNamedShare">—</b> of target rows sit in a named settlement context in this snapshot.</p>
        <button class="culture-focus" type="button" data-culture-focus="named" aria-pressed="false">Explore named places <span>→</span></button>
      </article>
      <article class="culture-card">
        <div class="culture-card-top"><span>02 / Oidhreacht</span><small>heritage</small></div>
        <strong id="cultureHeritageCount">—</strong>
        <h3>Memory has an inventory.</h3>
        <p>NIAH-linked target rows connect footprints to named places, types, counties and ratings where the inventory reaches them.</p>
        <button class="culture-focus" type="button" data-culture-focus="heritage" aria-pressed="false">Explore heritage joins <span>→</span></button>
      </article>
      <article class="culture-card">
        <div class="culture-card-top"><span>03 / Pobal</span><small>shared life</small></div>
        <strong id="cultureSharedLifeCount">—</strong>
        <h3>Public life has many rooms.</h3>
        <p>Worship, government and civic cohorts stay distinct so “community” is not flattened into one visual style.</p>
        <button class="culture-focus" type="button" data-culture-focus="pobal" aria-pressed="false">Explore shared life <span>→</span></button>
      </article>
      <article class="culture-card">
        <div class="culture-card-top"><span>04 / Civic ground</span><small>public institutions</small></div>
        <strong id="cultureCivicLifeCount">—</strong>
        <h3>Commons need civic edges.</h3>
        <p>Government and civic targets form a smaller public-institution lens for testing access, welcome and shared ground.</p>
        <button class="culture-focus" type="button" data-culture-focus="civic" aria-pressed="false">Explore civic ground <span>→</span></button>
      </article>
    </div>
    <div class="culture-timeline" aria-labelledby="heritageTimelineHeading">
      <div class="culture-timeline-head"><div><span class="culture-mosaic-label">Oidhreacht / time field</span><h3 id="heritageTimelineHeading">Memory moves through decades.</h3><p>Read the dated NIAH screen as a sequence, not a single story. Each marker compares the golden-angle flag rate in dated worship targets with its era-matched controls.</p></div><div class="heritage-timeline-readout" aria-live="polite"><span id="heritageTimelineStatus">NIAH decade screen</span><strong id="heritageTimelineReadoutTitle">Choose a decade to read the evidence.</strong><p id="heritageTimelineReadoutText">The timeline is a measured comparison surface. Select a decade to carry its century lens into the target table and map.</p></div></div>
      <div id="heritageTimeline" class="heritage-timeline" role="tablist" aria-label="NIAH construction decade screens"></div>
      <p class="heritage-timeline-caveat">Era rates describe the dated subset reached by the NIAH inventory. They are screening results with uncertainty, not proof that a period, building type, or Irish tradition intended a named mathematical system.</p>
    </div>
    <div class="heritage-type-field" aria-labelledby="heritageTypeHeading">
      <div class="heritage-type-head"><div><span class="culture-mosaic-label">Cineál / heritage typology</span><h3 id="heritageTypeHeading">Read the pattern against the building type.</h3><p>Use the NIAH’s own type vocabulary to keep the geometry attached to Irish buildings: churches, houses, mills, schools, bridges and more. Select a type to carry it into Explore.</p></div><div class="heritage-type-readout" aria-live="polite"><span id="heritageTypeStatus">NIAH type screen</span><strong id="heritageTypeReadoutTitle">Choose a building type to read the evidence.</strong><p id="heritageTypeReadoutText">The typology field will compare measured φ/θ screens and compactness inside the visible NIAH-linked rows.</p></div></div>
      <div id="heritageTypeGrid" class="heritage-type-grid" aria-label="NIAH building types and measured geometry screens"></div>
      <p class="heritage-type-caveat">Cards are ranked by NIAH-linked rows in the current view; φ and θ remain target-level geometry screens within each type. A type label gives the building a source context, not a claim that its makers intended a named mathematical system.</p>
    </div>
    <div class="place-braid-field" aria-labelledby="placeBraidHeading">
      <div class="place-braid-head"><div><span class="culture-mosaic-label">Fite / county–type braid</span><h3 id="placeBraidHeading">County and building type share a field.</h3><p>Follow the NIAH-linked building types into their county contexts. Each braid keeps row count, φ/θ screens, and the most represented types together, so place is read as a relationship rather than a label.</p></div><div class="place-braid-readout" aria-live="polite"><span id="placeBraidStatus">County/type field</span><strong id="placeBraidReadoutTitle">Choose a county to read its braid.</strong><p id="placeBraidReadoutText">The report pack will connect county context, NIAH type, and measured geometry here.</p></div></div>
      <div id="placeBraidGrid" class="place-braid-grid" aria-label="County and NIAH building-type relationships"></div>
      <p class="place-braid-note">The braid ranks the current report view’s NIAH-linked rows; φ and θ are within-county geometry screens, not evidence of county-wide architectural identity. County and type controls return to the existing Explore state.</p>
    </div>
    <div class="land-field" aria-labelledby="landFieldHeading">
      <div class="land-field-head"><div><span class="culture-mosaic-label">Talamh / land threshold</span><h3 id="landFieldHeading">Buildings do not float above the island.</h3><p>Nearest mapped drivable-road proximity gives the footprint one measurable relationship to movement and access. Read the sample by building group, then keep the boundary visible: a centroid distance is not a walking route, a topographic model, or a complete account of landscape.</p></div><div id="landFieldReadout" class="land-field-readout" aria-live="polite"><span>Current field / road proximity</span><strong>Loading land context.</strong><p>The report pack will place the building groups beside their nearest mapped-road sample.</p></div></div>
      <div id="landGroupGrid" class="land-group-grid" aria-label="Nearest mapped road proximity by building group"></div>
      <div class="land-field-bottom"><div id="landDensityPanel" class="land-density-panel"><span>Mapping density / field texture</span><strong>Loading spatial context.</strong><p>Density bins describe the mapped snapshot, not settlement quality or landscape value.</p></div><div id="landSourcePanel" class="land-source-panel"><span>Evidence boundary</span><p>Routing and spatial source status will appear when the report pack loads.</p></div></div>
    </div>
    <div class="culture-mosaic">
      <div>
        <span class="culture-mosaic-label">Contae / county mosaic</span>
        <h3><b id="cultureCountyCount">—</b> county contexts in the heritage-linked rows.</h3>
        <p id="cultureTopCounties">County context will appear when the report pack loads.</p>
        <div class="county-field"><div class="county-field-head"><span>Choose a county lens</span><small id="countyFieldStatus">All counties</small></div><div id="countyChips" class="county-chips" aria-label="County filters"></div><div id="countyFieldNote" class="county-field-note" aria-live="polite"><span>Field note</span><p>Choose a county to open its measured field note.</p></div></div>
      </div>
      <div class="culture-wordbank">
        <span class="culture-mosaic-label">Words to carry into the studio</span>
        <div class="culture-word-links">
          <a href="https://www.teanglann.ie/en/eid/h%C3%A1it" target="_blank" rel="noopener">Áit <small>place</small></a>
          <a href="https://www.teanglann.ie/en/eid/NAME" target="_blank" rel="noopener">Ainm <small>name</small></a>
          <a href="https://www.teanglann.ie/en/eid/oidhreacht" target="_blank" rel="noopener">Oidhreacht <small>heritage</small></a>
          <a href="https://www.teanglann.ie/en/eid/pobal" target="_blank" rel="noopener">Pobal <small>community</small></a>
        </div>
      </div>
    </div>
    <div class="county-pulse-field" aria-labelledby="countyPulseHeading">
      <div class="county-pulse-head"><div><span class="culture-mosaic-label">Contae / geometry pulse</span><h3 id="countyPulseHeading">Every county carries a different field texture.</h3><p id="countyPulseIntro">Read target count, heritage reach, named-place context, and measured φ/θ screens together. Select a county to carry the same lens into Explore.</p></div></div>
      <div id="countyPulseGrid" class="county-pulse-grid" aria-label="County-level geometry and cultural pulse"></div>
      <p id="countyPulseNote" class="county-pulse-note">The pulse will appear when the report pack loads. φ and θ are target-level screening rates, not evidence of a county-wide architectural identity.</p>
    </div>
    <div class="rhythm-field" aria-labelledby="rhythmHeading">
      <div class="rhythm-head"><div><span class="culture-mosaic-label">Pátrún / spatial rhythm</span><h3 id="rhythmHeading">Patterns have neighbours.</h3><p>Move from the footprint to its surrounding field. These cards pair an eight-neighbour Moran’s I screen with county-preserving and spatial-block comparisons, so a mathematical signal stays situated in Irish land.</p></div><div class="rhythm-stat"><strong id="rhythmCount">—</strong><small>cohort screens in the spatial audit</small></div></div>
      <div id="rhythmGrid" class="rhythm-grid" aria-label="Spatial rhythm by building cohort"></div>
      <div class="rhythm-readout" aria-live="polite"><span>Field note</span><div><strong id="rhythmReadoutTitle">Choose a cohort to read its spatial rhythm.</strong><p id="rhythmReadoutText">The spatial audit will place each cohort beside its neighbourhood statistic and county-preserving comparison.</p></div></div>
      <p class="rhythm-note">Moran’s I describes local similarity in a sampled neighbour graph; county Δ and block intervals are separate sensitivity screens. These are descriptive diagnostics, not proof of regional style, historic intent, or a single Irish architectural tradition.</p>
    </div>
    <div class="scale-field" aria-labelledby="scaleHeading">
      <div class="scale-head"><div><span class="culture-mosaic-label">Ciorcal / field scale</span><h3 id="scaleHeading">Place changes when the circle grows.</h3><p>Follow the reported L(r) − r statistic from 100 m to 5 km. Each cohort keeps its own scale rail so the surrounding Irish land enters the reading without being collapsed into one building story.</p></div><div class="scale-stat"><strong id="scaleCount">—</strong><small>cohort radius observations shown</small></div></div>
      <div id="scaleGrid" class="scale-grid" aria-label="Multi-distance spatial scale by building cohort"></div>
      <div class="scale-readout" aria-live="polite"><span>Scale note</span><div><strong id="scaleReadoutTitle">Choose a cohort to read its field scale.</strong><p id="scaleReadoutText">The six-radius spatial summary will appear when the report pack loads.</p></div></div>
      <p class="scale-note">The rails show the reported Ripley L(r) − r values with translation correction on a sampled bounding rectangle. They are scale diagnostics, not a significance envelope, route model, settlement-quality score, or proof of historic design intent.</p>
    </div>
    <div class="alignment-field" aria-labelledby="alignmentHeading">
      <div class="alignment-head"><div><span class="culture-mosaic-label">Ailíniú / orientation field</span><h3 id="alignmentHeading">Edges carry a direction before they carry a story.</h3><p>Read bearings, turns and nearest neighbours together. The point-pattern audit keeps worship geometry beside its control field so an angular resemblance stays a question to test, not a cultural shortcut.</p></div><div class="alignment-stat"><strong id="alignmentCount">—</strong><small>point-pattern cohorts shown</small></div></div>
      <div id="alignmentGrid" class="alignment-grid" aria-label="Directional and nearest-neighbour point-pattern screens"></div>
      <div class="alignment-readout" aria-live="polite"><span>Orientation note</span><div><strong id="alignmentReadoutTitle">Read the edge field beside its reference.</strong><p id="alignmentReadoutText">The point-pattern summary will appear when the report pack loads.</p></div></div>
      <p class="alignment-note">Bearing and turn screens describe mapped edge geometry; nearest-neighbour comparisons use the reported Fibonacci, sham, and Poisson references. They are exploratory diagnostics, not proof of conscious angle selection, cultural origin, or historic intent.</p>
    </div>
    <div class="source-field" aria-labelledby="sourceHeading">
      <div class="source-head"><div><span class="culture-mosaic-label">Foinse / source roots</span><h3 id="sourceHeading">Every claim has a lineage.</h3><p>Keep the island’s evidence layers named: community mapping, heritage inventory, bounded attribution text, and the historical material that is still to be supplied.</p></div><div class="source-stat"><strong id="sourceCount">—</strong><small>source families present</small></div></div>
      <div id="sourceGrid" class="source-grid" aria-label="Source register and evidence lineage"></div>
      <div class="source-readout" aria-live="polite"><span>Evidence ledger</span><div><strong id="sourceReadoutTitle">Read the source roots beside the measurements.</strong><p id="sourceReadoutText">The report pack will summarize its source register and geometry quality here.</p></div></div>
      <p class="source-note">Source status describes what this snapshot actually carries. A missing curated-history register is kept visible rather than silently replaced by a mathematical or heritage inference; local knowledge and source review remain part of the work.</p>
    </div>
    <div class="trust-field" aria-labelledby="trustHeading">
      <div class="trust-head"><div><span class="culture-mosaic-label">Fíorú / validation field</span><h3 id="trustHeading">Make the pattern earn its next question.</h3><p>Let the atlas show its working: independent gates, a deterministic holdout, and the human review step that still needs expert labels. Precision is part of the culture of the map.</p></div><div class="trust-stat"><strong id="trustCount">—</strong><small>core gates passing</small></div></div>
      <div id="trustGrid" class="trust-grid" aria-label="Validation, holdout and review calibration evidence"></div>
      <div class="trust-readout" aria-live="polite"><span>Trust note</span><div><strong id="trustReadoutTitle">Read the validation field beside the signal.</strong><p id="trustReadoutText">The report pack will place its independent checks beside its mathematical screens.</p></div></div>
      <p class="trust-note">A passing artifact gate means the report is internally checked, not that a geometric resemblance proves design intent or cultural origin. Holdout p-values are shown unadjusted; expert review labels remain an explicit next step.</p>
    </div>
    <div class="makers-field" aria-labelledby="makersHeading">
      <div class="makers-head"><div><span class="culture-mosaic-label">Makers / named design evidence</span><h3 id="makersHeading">People enter the record carefully.</h3><p>Where the source record carries a design, architect-role, or bounded attribution phrase, the atlas keeps that name visible beside the geometry. Select a maker to search the current field.</p></div><div class="makers-stat"><strong id="makersCount">—</strong><small>attributed names in the report pack</small></div></div>
      <div id="makersBinary" class="makers-binary" aria-label="Named versus unattributed comparison"></div>
      <div id="makersGrid" class="makers-grid" aria-label="Named design attributions"></div>
      <p id="makersNote" class="makers-note">Attribution rows are source-linked evidence and remain exploratory; a name does not prove sole authorship, design intent, or a shared architectural tradition.</p>
    </div>
    <div class="place-name-field" aria-labelledby="placeNameHeading">
      <div class="place-name-head"><div><span class="culture-mosaic-label">Ainm / named-place field</span><h3 id="placeNameHeading">Names anchor the geometry.</h3><p>These are the named settlement contexts carried by the report data. Choose one to bring its label into Explore and read the buildings beside their measured form, heritage joins, and source trail.</p></div><div id="placeNameStat" class="place-name-stat" aria-live="polite"><strong>—</strong><small>named contexts in the current field</small></div></div>
      <div id="placeNameChips" class="place-name-chips" aria-label="Named settlement contexts"></div>
      <div id="placeNameReadout" class="place-name-readout" aria-live="polite"><span id="placeNameReadoutStatus">Named-place field</span><strong id="placeNameReadoutTitle">Choose a name to read its measured field.</strong><p id="placeNameReadoutText">The selected settlement context will keep its source coverage and geometry screens visible together.</p><div class="place-name-readout-metrics" aria-label="Selected named-place measurements"><span class="place-name-readout-metric"><b id="placeNameRows">—</b><small>visible rows</small></span><span class="place-name-readout-metric"><b id="placeNameHeritage">—</b><small>NIAH joins</small></span><span class="place-name-readout-metric"><b id="placeNameSignals">—</b><small>φ / θ screens</small></span><span class="place-name-readout-metric"><b id="placeNameForm">—</b><small>median r / C</small></span><span class="place-name-readout-metric"><b id="placeNameGroups">—</b><small>group mix</small></span></div><div class="place-name-readout-actions"><button id="copyPlaceNameLink" type="button" hidden>Copy named-place link →</button><span id="placeNameShareStatus" class="place-name-share-status" role="status" aria-live="polite"></span></div></div>
      <p id="placeNameNote" class="place-name-note">Place labels are context cues from the report’s settlement field; they are not an etymological dictionary or a substitute for local knowledge.</p>
    </div>
    <p class="culture-caveat">The Irish labels are language cues, not a claim that the dashboard can stand in for lived culture. Follow the evidence from place name to source record, then bring local knowledge into the design conversation.</p>
  </section>
  <div class="kpis" aria-live="polite">
    <div class="kpi"><b id="kTargets">—</b><span>visible targets</span></div>
    <div class="kpi"><b id="kControls">—</b><span>controls</span></div>
    <div class="kpi"><b id="kGolden">—</b><span>golden-angle</span></div>
    <div class="kpi"><b id="kNiah">—</b><span>NIAH matched</span></div>
    <div class="kpi"><b id="kPatterns">—</b><span>pattern types</span></div>
  </div>
  <div id="filters" class="filters atlas-section">
    <div class="quick-views" aria-label="Quick exploration views">
      <span class="quick-views-label">Start with</span>
      <button class="quick-view" type="button" data-quick-view="all" aria-pressed="true">All targets</button>
      <button class="quick-view" type="button" data-quick-view="named" aria-pressed="false">Named places</button>
      <button class="quick-view" type="button" data-quick-view="heritage" aria-pressed="false">Heritage joins</button>
      <button class="quick-view" type="button" data-quick-view="civic" aria-pressed="false">Civic ground</button>
      <button class="quick-view" type="button" data-quick-view="signals" aria-pressed="false">Geometry signals</button>
    </div>
    <input id="query" type="text" placeholder="Search name, OSM id, flags, county…" aria-label="Search analyzed targets"/>
    <select id="group" aria-label="Filter by group"><option value="">All groups</option></select>
    <select id="century" aria-label="Filter by century"><option value="">All centuries</option></select>
    <select id="rating" aria-label="Filter by NIAH rating"><option value="">All ratings</option></select>
    <select id="niahType" aria-label="Filter by NIAH class"><option value="">All NIAH classes</option></select>
    <select id="county" aria-label="Filter by county"><option value="">All counties</option></select>
    <select id="cultureLens" aria-label="Filter by cultural lens"><option value="">All cultural lenses</option><option value="named">Ainm / named places</option><option value="heritage">Oidhreacht / heritage joins</option><option value="pobal">Pobal / shared life</option><option value="civic">Civic / public institutions</option></select>
    <select id="pattern" aria-label="Filter by geometric pattern"><option value="">All geometric patterns</option></select>
    <select id="reviewState" aria-label="Filter by review state"><option value="">All review states</option><option value="not_queued">Not in current queue</option><option value="not_reviewed">Not reviewed</option><option value="supportive">Supportive</option><option value="ambiguous">Ambiguous</option><option value="not_supportive">Not supportive</option></select>
    <label class="filter-wide" for="score"><span>Minimum score <b id="scoreValue">0</b></span><input id="score" type="range" min="0" max="100" value="0" aria-label="Minimum score"/></label>
    <div class="checks">
      <label><input id="onlyAngle" type="checkbox"/> golden angle</label>
      <label><input id="onlyRatio" type="checkbox"/> golden ratio</label>
      <label><input id="onlyCircular" type="checkbox"/> circular</label>
      <label><input id="onlyMulti" type="checkbox"/> multipart/repaired</label>
    </div>
  </div>
  <div class="toolbar"><div class="toolbar-left"><small id="count">Loading…</small><span id="activeCounty" class="active-filter" aria-live="polite"></span><span id="activeCulture" class="active-filter" aria-live="polite"></span><span id="activePattern" class="active-filter" aria-live="polite"></span><span id="runtimeStatus" class="runtime-status" role="status" aria-live="polite"></span></div><div class="toolbar-actions"><button id="clearFilters" class="clear-button" type="button">Reset filters</button><div class="actions"><button id="downloadCsv" type="button">CSV</button><button id="downloadGeo" type="button">GeoJSON</button></div></div></div>
  <div id="runtimeReloadNotice" class="runtime-reload" role="alert" hidden><span id="runtimeReloadText">The served data changed while this report was open.</span><button id="runtimeReload" type="button">Reload current report</button></div>
  <div id="reportLoadError" class="report-error" role="alert" hidden><span id="reportLoadErrorText">The report request failed.</span><button id="reportRetry" type="button">Retry report request</button></div>
  <section id="selectionCard" class="selection-card" aria-labelledby="selectionTitle" aria-live="polite" hidden>
    <div class="selection-head">
      <div><span class="selection-kicker">Selected place / field note</span><h2 id="selectionTitle">Target detail</h2><p id="selectionSubtitle">Select a point or table row to bring its evidence into view.</p></div>
      <button id="clearSelection" class="selection-close" type="button">Close</button>
    </div>
    <div class="selection-grid">
      <div class="selection-fact"><span>Place context</span><strong id="selectionPlace">—</strong></div>
      <div class="selection-fact"><span>Heritage record</span><strong id="selectionHeritage">—</strong></div>
      <div class="selection-fact"><span>Geometry signals</span><strong id="selectionGeometry">—</strong></div>
      <div class="selection-fact"><span>Review state</span><strong id="selectionReview">—</strong></div>
    </div>
    <div class="selection-focus-bar"><div><span>Map trace / source footprint</span><p>The selected outline follows this dossier on the Irish field map.</p></div><button id="focusSelectionMap" type="button">Show on map →</button></div>
    <div class="selection-math"><span>Measured geometry / snapshot descriptors</span><strong id="selectionMath">—</strong><small>Area, boundary, dimensions, compactness, radial variation, and Fourier terms describe the mapped footprint; they do not establish historical intent.</small><button id="selectionMathRead" class="selection-math-action" type="button" data-maths-read="maths">Trace this geometry through Maths →</button></div>
    <div id="selectionCultureTrace" class="selection-culture" aria-labelledby="selectionCultureTraceTitle" hidden>
      <div class="selection-culture-head"><div><span>Áit / cultural trace</span><strong id="selectionCultureTraceTitle">Place context beside the measurement.</strong><p id="selectionCultureTraceIntro">The selected footprint will be read through the place, heritage, shared-life, or civic context that the source row actually carries.</p></div></div>
      <div class="selection-culture-facts"><div class="selection-culture-fact"><span>Lens</span><strong id="selectionCultureLens">—</strong></div><div class="selection-culture-fact"><span>Source context</span><strong id="selectionCultureContext">—</strong></div></div>
      <div class="selection-culture-actions"><button id="selectionCultureRead" type="button" data-culture-read="culture">Read the cultural field →</button><button id="selectionCulture" type="button" data-selection-culture="" hidden>Filter this lens →</button></div>
      <p id="selectionCultureNote" class="selection-culture-note">Culture is a context layer here, not a formula or a claim about historic intent.</p>
    </div>
    <div class="selection-evidence" aria-labelledby="selectionEvidenceHeading">
      <div class="selection-evidence-head"><div><span>Rian / evidence trail</span><strong id="selectionEvidenceHeading">Follow this building across the source layers.</strong><p id="selectionEvidenceIntro">The selected footprint will place geometry, heritage, historical evidence, review, and mapping history beside one another.</p></div><span id="selectionEvidenceStatus" class="selection-evidence-status">Waiting for selection</span></div>
      <div id="selectionEvidenceGrid" class="selection-evidence-grid" aria-label="Evidence trail for selected building"></div>
      <p id="selectionEvidenceNote" class="selection-evidence-note">Source availability will remain explicit; missing historical or mapping material is not replaced by mathematical inference.</p>
    </div>
    <div class="selection-context" aria-labelledby="selectionContextHeading">
      <div class="selection-context-head"><div><span>Timpeall / surrounding field</span><strong id="selectionContextHeading">Place one building inside its nearby field.</strong><p id="selectionContextIntro">The nearest mapped footprints will show how this place sits beside other buildings, without turning distance into a walking route or cultural claim.</p></div><span id="selectionContextStatus" class="selection-context-status">Waiting for selection</span></div>
      <div class="selection-context-grid"><div class="selection-context-plot-frame"><canvas id="selectionContextPlot" class="selection-context-canvas" width="520" height="210" role="img" aria-label="Nearby mapped building context plot">Nearby mapped context appears here when coordinates are available.</canvas><small id="selectionContextPlotNote" class="selection-context-plot-note">Coordinate field: waiting for selection.</small></div><div id="selectionContextList" class="selection-context-list" aria-label="Nearest mapped buildings"></div></div>
      <p id="selectionContextNote" class="selection-context-note">Distances are straight-line centroid estimates from the current report view; they are not road routes, walking distances, or evidence of shared historical design.</p>
    </div>
    <div class="selection-fingerprint"><div class="selection-fingerprint-head"><span>Boundary fingerprint / mapped shape</span><strong id="selectionFingerprintLabel">Select a footprint to draw its boundary.</strong><p id="selectionFingerprintText">The atlas will normalize the mapped outline to show its measured proportions, axis, and centre without changing the source geometry.</p></div><div><canvas id="selectionFingerprint" class="selection-fingerprint-canvas" width="520" height="200" role="img" aria-label="Selected footprint boundary fingerprint">Mapped footprint fingerprint appears here when geometry is available.</canvas><small id="selectionFingerprintNote" class="selection-fingerprint-note">Geometry source status: waiting for selection.</small></div></div>
    <div class="selection-weave"><div class="selection-weave-head"><span>Cruth / derived field print</span><strong id="selectionWeaveLabel">Select a footprint to translate its signals.</strong><p id="selectionWeaveText">A contemporary visual study will combine the selected descriptors and screening flags into a repeatable field—not a historic ornament or a claim about cultural origin.</p></div><div><canvas id="selectionWeave" class="selection-weave-canvas" width="520" height="200" role="img" aria-label="Derived geometry field print">Derived field print appears here when geometry is available.</canvas><small id="selectionWeaveNote" class="selection-weave-note">Descriptor-led study: waiting for selection.</small></div></div>
    <div class="selection-passport" aria-label="Downloadable visual field passport"><div><span>Field passport / take the place with you</span><strong>One measured Irish footprint, one visual record.</strong><p>Download a self-contained SVG card with the place context, source trail, geometry descriptors, and an honest evidence boundary.</p></div><div class="selection-passport-actions"><button id="downloadSelectionPassport" type="button">Download SVG passport →</button><span id="selectionPassportStatus" class="selection-passport-status" role="status" aria-live="polite"></span></div></div>
    <div class="selection-actions"><a id="selectionOsm" href="#" target="_blank" rel="noopener">Open source geometry →</a><button id="copySelectionLink" type="button">Copy place link →</button><button id="carrySelectionToStudio" type="button" data-carry-studio="">Carry geometry to studio →</button><button id="addSelectionCompare" type="button" data-compare-target="">Add to comparison →</button><span id="selectionShareStatus" class="selection-share-status" role="status" aria-live="polite"></span></div>
  </section>
  <section id="comparisonTray" class="comparison-tray atlas-section" aria-labelledby="comparisonTitle" aria-live="polite" hidden>
    <div class="comparison-head"><div><span class="selection-kicker">Field comparison / two places</span><h2 id="comparisonTitle">Read two footprints together.</h2><p id="comparisonIntro">Add a selected target to begin a side-by-side comparison of place context, geometry and screening signals.</p></div><div class="comparison-head-actions"><button id="copyComparisonLink" class="comparison-copy" type="button" disabled>Copy comparison link →</button><button id="clearComparison" class="comparison-clear" type="button">Clear comparison</button><span id="comparisonShareStatus" class="comparison-share-status" role="status" aria-live="polite"></span></div></div>
    <div id="comparisonContent"></div>
    <div id="comparisonRelation" class="comparison-relation" aria-labelledby="comparisonRelationHeading" hidden>
      <div class="comparison-relation-head"><div><span>Idir / between places</span><strong id="comparisonRelationHeading">Read the space between Field A and Field B.</strong><p id="comparisonRelationIntro">The relationship layer will place two selected footprints beside their geographic, cultural, and mathematical context.</p></div><span id="comparisonRelationStatus" class="comparison-relation-status">Awaiting two places</span></div>
      <div class="comparison-relation-grid"><div class="comparison-relation-plot-frame"><canvas id="comparisonRelationPlot" class="comparison-relation-canvas" width="520" height="185" role="img" aria-label="Relationship plot between two selected buildings">The between-places plot appears when two footprints are selected.</canvas><small id="comparisonRelationPlotNote" class="comparison-relation-plot-note">Centroid field: awaiting two places.</small></div><div id="comparisonRelationMetrics" class="comparison-relation-metrics" aria-label="Relationship measurements between selected buildings"></div></div>
      <div class="comparison-relation-actions"><button id="carryComparisonToStudio" type="button">Carry relationship to studio →</button><span id="comparisonRelationActionStatus" role="status" aria-live="polite">Turn the measured relationship into a contemporary civic test-fit.</span></div>
      <p id="comparisonRelationNote" class="comparison-relation-note">Relationships remain descriptive: straight-line centroid distance is not a route, and shared mathematical flags do not establish shared authorship or historical intent.</p>
    </div>
  </section>
  <div id="patterns" class="section pattern-section atlas-section"><div class="section-heading"><div><h2>Geometric pattern catalogue</h2><p class="section-intro">Every screening flag in this report is listed below. Select a card to filter the table and map.</p></div><button id="clearPattern" class="clear-button" type="button">Show all</button></div><div id="patternSummary" class="pattern-summary"></div><div id="patternCatalog" class="pattern-grid"></div></div>
  <div class="section"><h2>Local route query</h2>
    <div class="route-grid">
      <label>Start latitude<input id="routeStartLat" inputmode="decimal" placeholder="53.3498"/></label>
      <label>Start longitude<input id="routeStartLon" inputmode="decimal" placeholder="-6.2603"/></label>
      <label>Goal latitude<input id="routeGoalLat" inputmode="decimal" placeholder="53.3438"/></label>
      <label>Goal longitude<input id="routeGoalLon" inputmode="decimal" placeholder="-6.2546"/></label>
      <label>Fallback speed km/h<input id="routeSpeed" inputmode="decimal" value="50"/></label>
      <label>Vehicle weight t (optional)<input id="routeWeight" inputmode="decimal" placeholder="7.5"/></label>
      <label>HGV permitted rating t (optional)<input id="routeRating" inputmode="decimal" placeholder="18"/></label>
      <label>Vehicle height m (optional)<input id="routeHeight" inputmode="decimal" placeholder="3.8"/></label>
      <label>Vehicle width m (optional)<input id="routeWidth" inputmode="decimal" placeholder="2.5"/></label>
      <label>Vehicle length m (optional)<input id="routeLength" inputmode="decimal" placeholder="12"/></label>
      <label>Vehicle axle load t (optional)<input id="routeAxleload" inputmode="decimal" placeholder="10"/></label>
      <label>Vehicle class<select id="routeVehicleClass"><option value="general">General traffic</option><option value="delivery">Delivery</option><option value="hgv">Heavy goods vehicle</option><option value="psv">Public service vehicle</option><option value="taxi">Taxi</option></select></label>
      <label>Departure (optional)<input id="routeDeparture" placeholder="2026-08-17T08:00:00+00:00"/></label>
      <label>Objective<select id="routeObjective"><option value="distance">Shortest distance</option><option value="duration">Fastest duration</option></select></label>
      <label>Response<select id="routeFormat"><option value="json">JSON</option><option value="geojson">GeoJSON</option></select></label>
      <div class="route-actions"><button id="routeRun" type="button">Route</button><label class="route-check"><input id="routeIncludePath" type="checkbox" checked/> include path</label><label class="route-check"><input id="routeIncludeFerries" type="checkbox"/> include static ferries</label><label class="route-check"><input id="routeAllowHgvDestination" type="checkbox"/> allow HGV destination access</label><button id="routeCopyLink" type="button">Copy link</button><button id="routeDownloadJson" type="button" hidden>JSON</button><button id="routeDownloadGeojson" type="button" hidden>GeoJSON</button><span id="routeShareStatus" class="footnote route-share-status" role="status" aria-live="polite"></span></div>
    </div>
    <div id="routeStatus" class="footnote route-status" role="status" aria-live="polite">Serve this dashboard with ireland-geometry-serve to enable routing.</div>
    <div class="route-compare" aria-labelledby="routeCompareHeading">
      <div class="route-compare-heading"><strong id="routeCompareHeading">Compare vehicle profiles</strong><small>2–8 lines · first profile is the baseline</small></div>
      <div class="route-compare-grid">
        <label>Profiles (one per line; NAME;key=value)<textarea id="routeCompareProfiles" class="route-compare-profiles" rows="3" spellcheck="false">general
hgv;vehicle_class=hgv;weight_t=7.5</textarea></label>
        <div class="route-compare-actions"><button id="routeCompareRun" type="button">Compare</button><label class="route-check"><input id="routeCompareIncludePath" type="checkbox" checked/> include profile paths</label><label class="route-check"><input id="routeCompareIncludeFerries" type="checkbox"/> include static ferries</label><button id="routeCompareDownloadJson" type="button" hidden>JSON</button></div>
      </div>
      <div id="routeCompareStatus" class="footnote route-compare-status" role="status" aria-live="polite">Compare profiles against the same coordinates and route options.</div>
      <div id="routeCompareResults" class="route-compare-results" hidden>
        <div class="route-compare-results-heading"><strong>Profile comparison</strong><small id="routeCompareSummary"></small></div>
        <div class="route-compare-table-wrap">
          <table class="route-compare-table"><caption class="sr-only">Vehicle profile route comparison</caption>
            <thead><tr><th scope="col">Profile</th><th scope="col">Status</th><th scope="col">Distance</th><th scope="col">Δ distance</th><th scope="col">Duration</th><th scope="col">Δ duration</th><th scope="col">Ferry wait</th><th scope="col">Path</th></tr></thead>
            <tbody id="routeCompareList"></tbody>
          </table>
        </div>
      </div>
      <pre id="routeCompareResult" class="route-result route-compare-result" aria-live="polite" hidden></pre>
    </div>
    <div class="route-matrix" aria-labelledby="routeMatrixHeading">
      <div class="route-matrix-heading"><strong id="routeMatrixHeading">Route matrix</strong><small>1–25 origin × destination pairs · uses the route form's vehicle options</small></div>
      <div class="route-matrix-grid">
        <label>Origins (one lat,lon per line)<textarea id="routeMatrixOrigins" class="route-matrix-points" rows="3" spellcheck="false" placeholder="53.3498,-6.2603
53.3438,-6.2672"></textarea></label>
        <label>Destinations (one lat,lon per line)<textarea id="routeMatrixDestinations" class="route-matrix-points" rows="3" spellcheck="false" placeholder="53.3445,-6.2408
53.3500,-6.2600"></textarea></label>
        <div class="route-matrix-actions"><button id="routeMatrixRun" type="button">Run matrix</button><label class="route-check"><input id="routeMatrixIncludePath" type="checkbox"/> include pair paths</label><label class="route-check"><input id="routeMatrixIncludeFerries" type="checkbox"/> include static ferries</label><button id="routeMatrixDownloadJson" type="button" hidden>JSON</button></div>
      </div>
      <div id="routeMatrixStatus" class="footnote route-matrix-status" role="status" aria-live="polite">Run up to 25 ordered origin–destination pairs against the local graph.</div>
      <div id="routeMatrixResults" class="route-matrix-results" hidden>
        <div class="route-matrix-results-heading"><strong>Matrix results</strong><small id="routeMatrixSummary"></small></div>
        <div class="route-matrix-table-wrap">
          <table class="route-matrix-table"><caption class="sr-only">Origin and destination route matrix</caption>
            <thead><tr><th scope="col">Pair</th><th scope="col">Status</th><th scope="col">Distance</th><th scope="col">Duration</th><th scope="col">Ferry wait</th><th scope="col">Arrival</th><th scope="col">Path</th></tr></thead>
            <tbody id="routeMatrixList"></tbody>
          </table>
        </div>
      </div>
      <pre id="routeMatrixResult" class="route-result route-matrix-result" aria-live="polite" hidden></pre>
    </div>
    <div id="routeManeuvers" class="route-maneuvers" aria-live="polite" hidden>
      <div class="route-maneuver-heading"><strong>Route guidance</strong><small id="routeManeuverSummary"></small></div>
      <ol id="routeManeuverList" class="route-maneuver-list"></ol>
    </div>
    <div id="routeSegments" class="route-segments" aria-live="polite" hidden>
      <div class="route-segment-heading"><strong>Path detail</strong><small id="routeSegmentSummary"></small></div>
      <div class="route-segment-table-wrap">
        <table class="route-segment-table"><caption class="sr-only">Detailed route segments and applied restriction provenance</caption>
          <thead><tr><th scope="col">#</th><th scope="col">Mapped road</th><th scope="col">Distance</th><th scope="col">Time</th><th scope="col">Mode</th><th scope="col">Checks</th></tr></thead>
          <tbody id="routeSegmentList"></tbody>
        </table>
      </div>
    </div>
    <pre id="routeResult" class="route-result" aria-live="polite" hidden></pre>
  </div>
  <div id="evidence" class="section atlas-section"><h2>Data-derived interpretation</h2><div id="interpretation"></div></div>
  <div class="section"><h2>Observed target vs control rates</h2><div id="groupBars" class="bars"></div></div>
  <div class="section"><h2>Construction-era golden-angle rates</h2><div id="eraBars" class="bars"></div></div>
  <div class="table-wrap"><table><caption class="sr-only">Analyzed target results</caption><thead><tr>
    <th data-sort="name" aria-sort="none"><button class="sort-button" type="button">Name</button></th><th data-sort="group" aria-sort="none"><button class="sort-button" type="button">Group</button></th><th data-sort="area_m2" aria-sort="none"><button class="sort-button" type="button">Area</button></th><th data-sort="score" aria-sort="descending"><button class="sort-button" type="button">Score</button></th><th data-sort="flags" aria-sort="none"><button class="sort-button" type="button">Evidence</button></th><th scope="col">Review</th>
  </tr></thead><tbody id="tbody"></tbody></table><div id="empty" class="empty" hidden>No buildings match these filters.</div></div>
  <div class="pagination"><button id="prev" type="button">Previous</button><span id="page" aria-live="polite">1 / 1</span><button id="next" type="button">Next</button></div>
  <div class="section"><h2>Method and provenance</h2><div id="method" class="footnote"></div><div id="reviewCoverage" class="footnote"></div></div>
  <div class="section"><h2>Data quality</h2><div id="quality" class="footnote"></div><div id="qualityFindings"></div><div id="qualityAudit"></div></div>
  <div class="section"><h2>Statistical results</h2><div id="statsTable"></div></div>
</div>
<script>
const PACK = __DATA__;
const OFFLINE_REQUESTED = new URLSearchParams(location.search).get('offline') === '1';
const SERVER_MODE = Boolean(PACK && PACK.paged) && !OFFLINE_REQUESTED;
let DATA = PACK.targets || [];
const OUTLINES = PACK.outlines || [];
let SUMMARY = PACK.summary || {};
const BASE_INTERPRETATION = PACK.interpretation || {};
const ARCHITECTS = PACK.architects || [];
const ARCHITECTS_BINARY = PACK.architects_binary || [];
let INTERPRETATION = {...BASE_INTERPRETATION};
let REPORT_RUNTIME = PACK.runtime || null;
const SIG = PACK.significance || [];
const NEGATIVE = PACK.negative_controls || [];
const NIAH_SIG = PACK.niah_significance || [];
const DECADES = PACK.decades || [];
const ROAD_PROXIMITY = PACK.road_proximity || [];
const SPATIAL_COVARIATES = PACK.spatial_covariates_summary || [];
const MATCHED = PACK.matched_significance || [];
const HIER = PACK.hierarchical_model || [];
const MORAN = PACK.moran || [];
const COUNTY_PERM = PACK.county_permutation || [];
const BOOT = PACK.spatial_bootstrap || [];
const RIPLEY = PACK.ripley || [];
const POINT_PATTERN = PACK.point_pattern || [];
const SOURCE_REGISTER = PACK.historical_source_register || [];
const HOLDOUT = PACK.holdout || [];
const REVIEW_CALIBRATION = PACK.review_calibration || [];
const QUALITY_SUMMARY = PACK.data_quality_summary || {};
const QUALITY_DUPLICATES = PACK.data_quality_duplicates || [];
const QUALITY_AUDIT = PACK.data_quality || [];
const GEOJSON = PACK.geojson || {type:'FeatureCollection',features:[]};
const GEOJSON_BY_ID = new Map((GEOJSON.features||[]).map(feature=>[String(feature.properties?.osm_id||''),feature]).filter(([id])=>id));
const PATTERN_CATALOG = PACK.pattern_catalog || [];
const PATTERN_BY_KEY = new Map(PATTERN_CATALOG.map(item=>[item.key,item]));
const FIELD_WALK = PACK.field_walk || [];
const MATHS_INDEX = [
  {key:'golden_ratio',symbol:'φ',category:'ratio',title:'Golden ratio',equation:'φ = (1 + √5) / 2 ≈ 1.618',pattern:'golden_ratio',description:'Aspect-ratio screening compares a footprint’s measured length-to-width relationship with φ. It is a geometric screen, not evidence of intent.'},
  {key:'fib_ratio',symbol:'Fₙ',category:'ratio',title:'Fibonacci ratio',equation:'Fₙ / Fₙ₋₁ → φ',pattern:'fib_ratio',description:'Non-trivial Fibonacci ratios offer a second proportion screen for footprint dimensions.'},
  {key:'fib_dimension',symbol:'F',category:'dimension',title:'Fibonacci dimensions',equation:'l,w ∈ {1,2,3,5,8,13…}',pattern:'fib_dimension',description:'Length or width near a Fibonacci-number dimension is a prompt to inspect scale and construction context.'},
  {key:'golden_angle',symbol:'θ',category:'angle',title:'Golden angle',equation:'θ = 360° / φ² ≈ 137.5°',pattern:'golden_angle',description:'Vertex-angle screening tests an exploratory rotation relation; it is not a cultural proof.'},
  {key:'reflective_symmetry',symbol:'↔',category:'symmetry',title:'Reflective symmetry',equation:'S(x,y) ≈ S(−x,y)',pattern:'reflective_symmetry',description:'Mirror-overlap screening compares a footprint with a reflected copy across a candidate axis.'},
  {key:'rot180_symmetry',symbol:'R₁₈₀',category:'symmetry',title:'180° rotation',equation:'R₁₈₀(x,y) = S(−x,−y)',pattern:'rot180_symmetry',description:'Rotational overlap tests whether the footprint repeats after a half-turn.'},
  {key:'orthogonal',symbol:'□',category:'angle',title:'Orthogonal structure',equation:'α ≈ 90°',pattern:'orthogonal',description:'Near-right-angle edges are common construction outcomes; the signal needs place and source review.'},
  {key:'circular',symbol:'○',category:'shape',title:'Circularity',equation:'C = 4πA / P²',pattern:'circular',description:'Circle-normalised area and perimeter show how compact a footprint is; C = 1 is the ideal circle.'},
  {key:'rectangularity',symbol:'R',category:'shape',title:'Rectangularity',equation:'R = A / A_bbox',pattern:null,description:'Rectangularity compares enclosed area with the footprint’s axis-aligned bounding rectangle; it is a descriptor, not a history claim.'},
  {key:'aspect_ratio',symbol:'r',category:'dimension',title:'Aspect ratio',equation:'r = l / w',pattern:null,description:'Aspect ratio records the relationship between measured length and width for every mapped footprint.'},
  {key:'radial_cv',symbol:'σᵣ',category:'field',title:'Radial variation',equation:'σᵣ / μᵣ',pattern:null,description:'Radial coefficient of variation describes how much distance from the footprint centre changes around the boundary.'},
  {key:'fourier',symbol:'F₁…₄',category:'field',title:'Fourier descriptors',equation:'ρ(θ) = Σ Fₙ eⁱⁿθ',pattern:null,description:'Radial Fourier terms retain compact harmonic descriptors of boundary shape for comparison.'},
];
const PAGE_SIZE = 50;
let filtered = DATA.slice();
let page = 1;
let pageStats = PACK.page || {total: DATA.length, matching_golden_angle: 0, matching_niah: 0};
let serverPageReady = Boolean(PACK.initial);
let serverRequestId = 0;
let runtimePollTimer = null;
let runtimePollInFlight = false;
let runtimeRefreshError = '';
let runtimeReloadRequired = false;
let reportRuntimeIdentity = null;
const RUNTIME_REFRESH_MS = 30000;
let filterTimer = null;
let sortKey = 'score';
let sortDesc = true;
let map = null;
let markerLayer = null;
let mapBaseLayers = {};
let mapReferenceLayer = null;
let activeMapLayer = 'satellite';
let lastTileLoadedAt = 0;
let mapTileErrorCount = 0;
let mapLiveTimer = null;
let offlineMap = false;
let offlineSelection = null;
let offlineMapFocus = false;
let routeGeometry = null;
let routeLine = null;
let comparisonLine = null;
let comparisonEndpointLayer = null;
let routeManeuverData = [];
let routeManeuverFocus = null;
let routeManeuverMarker = null;
const ROUTE_SEGMENT_DISPLAY_LIMIT = 250;
let routePayloadData = null;
let routeComparisonPayloadData = null;
let routeComparisonSelectedIndex = null;
let routeMatrixPayloadData = null;
let routeMatrixSelectedIndex = null;
const markerById = new Map();
const DEFAULT_MAP_CENTER = [53.35,-8.05];
const DEFAULT_MAP_ZOOM = 7;
let selectedMarkerId = null;
let fieldWalkFocusMarker = null;
let selectionOutlineLayer = null;
let studioReferenceData = null;
let studioReferenceId = '';
let studioPairData = [];
let studioPairIds = [];
let heritageEraKey = '';
let comparisonData = [];

const DESIGN_EQUATIONS = {
  phi: {
    index: '01 / proportion',
    title: 'A chamber that opens outward',
    equation: 'φ = (1 + √5) / 2 ≈ 1.618',
    description: 'Use φ as a proportion guide: a 13 m chamber can open into a foyer of roughly 21 m. The changing scale gives representation a visible civic gradient—from focused debate to public welcome.',
    move: 'Architecture move: 13 m chamber → 21 m foyer → planted threshold.',
    caption: 'Nested rooms use a golden-proportion relationship to move from debate to welcome.'
  },
  theta: {
    index: '02 / orientation',
    title: 'A route that keeps unfolding',
    equation: 'θ = 360° / φ² ≈ 137.5°',
    description: 'Rotate trees, lanterns, rain gardens or seating pockets by the golden angle. The result is a porous path with repeated encounters rather than one front door and one back door.',
    move: 'Architecture move: 12 small turns frame wind, light and social pause.',
    caption: 'Golden-angle rays become a sequence of thresholds, shade and sightlines.'
  },
  fib: {
    index: '03 / kit of parts',
    title: 'A building that can grow',
    equation: 'Fₙ = Fₙ₋₁ + Fₙ₋₂',
    description: 'Treat 3, 5, 8, 13 and 21 m as a kit of civic bays. A market, library or assembly hall can expand without losing its rhythm, making the public realm adaptable over time.',
    move: 'Architecture move: 3 m threshold + 5 m stall + 8 m hall + 13 m canopy.',
    caption: 'Fibonacci steps turn one large hall into a legible family of public bays.'
  },
  spiral: {
    index: '04 / continuity',
    title: 'A threshold that remembers the landscape',
    equation: 'r(θ) = a · eᵇᶿ',
    description: 'A logarithmic spiral can shape a ramp, gallery or canopy edge. Its changing radius makes movement feel continuous, like a path between town, water, weather and gathering.',
    move: 'Architecture move: slow the edge; widen the turn; keep the horizon visible.',
    caption: 'A growing spiral becomes a civic promenade rather than a decorative motif.'
  }
};
const DESIGN_TYPOLOGIES = {
  parliament: {
    label: 'Parliament / assembly', kind: 'chamber',
    tags: ['representation', 'public gallery', 'rain court'],
    access: '1.8 m continuous public loop',
    water: 'Roof → rill → planted court',
    wind: 'Sheltered foyer + cross-ventilated chamber',
    public: 'debate · welcome · visible threshold'
  },
  forum: {
    label: 'Public forum square', kind: 'forum',
    tags: ['encounter', 'shared ground', 'all-weather edge'],
    access: '1.8 m level route around the court',
    water: 'Perimeter swale → shared rain garden',
    wind: 'Porous edge + sheltered sitting pockets',
    public: 'pause · play · gathering'
  },
  market: {
    label: 'Market canopy', kind: 'market',
    tags: ['exchange', 'adaptable bays', 'weather canopy'],
    access: '1.8 m clear market aisle',
    water: 'Canopy gutters → visible rain chain',
    wind: 'Lifted canopy + protected stall backs',
    public: 'trade · food · informal meeting'
  },
  harbour: {
    label: 'Harbour garden', kind: 'harbour',
    tags: ['belonging', 'water edge', 'wind garden'],
    access: '1.8 m readable promenade to the water',
    water: 'Rain garden → tidal-edge planting study',
    wind: 'Layered planting + low sheltered pauses',
    public: 'walk · look · linger'
  },
  library: {
    label: 'Library courtyard', kind: 'library',
    tags: ['learning', 'reading court', 'quiet threshold'],
    access: '1.8 m level route through the court',
    water: 'Roof garden → planted reading court',
    wind: 'Deep reveal + calm, filtered ventilation',
    public: 'read · learn · exchange'
  },
  museum: {
    label: 'Museum loop', kind: 'museum',
    tags: ['interpretation', 'gallery loop', 'rest points'],
    access: '1.8 m loop with frequent rest points',
    water: 'Roof valley → visible collection garden',
    wind: 'Buffered gallery edge + controlled daylight',
    public: 'encounter · interpret · return'
  }
};
const DESIGN_SEASONS = {
  midsummer: { label: 'Long-light / midsummer', sky: '#f1d893', light: 'High sun · shade and filtered glare', weather: 'Cross-ventilated court · seasonal canopy' },
  equinox: { label: 'Equinox / changeover', sky: '#c9d8c5', light: 'Balanced light · test both orientations', weather: 'Adjustable threshold · rain-ready edge' },
  midwinter: { label: 'Low-light / midwinter', sky: '#aabec5', light: 'Low sun · south-facing warmth', weather: 'Sheltered route · wind-buffered court' }
};
const SKY_DECLINATION = { midsummer: 23.44, equinox: 0, midwinter: -23.44 };
const RAIN_EVENTS = {
  light: { label: '5 mm / light pulse', mm: 5, note: 'A small rainfall pulse for testing roof-to-ground visibility.' },
  design: { label: '10 mm / design pulse', mm: 10, note: 'A transparent one-event pulse for comparing the civic catchment.' },
  heavy: { label: '20 mm / heavy pulse', mm: 20, note: 'A heavier pulse for stress-testing overflow and public-ground sequencing.' }
};
const DESIGN_MATERIALS = {
  stone: { label: 'Stone + lime', tone: '#356c69', note: 'Durable civic base; verify local quarry, repair and carbon data.' },
  timber: { label: 'Timber + cork', tone: '#987044', note: 'Warm interior structure; verify moisture, fire and carbon data.' },
  slate: { label: 'Slate + rain chain', tone: '#334c62', note: 'Rain-ready roof language; verify sourcing, runoff and maintenance.' },
  copper: { label: 'Copper + planted roof', tone: '#a66b4e', note: 'Patinating roof study; verify runoff, biodiversity and material impacts.' }
};
const DESIGN_GRAMMARS = {
  radial: { label: 'Radial field', equation: 'rᵢ = r₀ + i·Δr', description: 'Distribute entries, trees, lights or seats around a shared centre so the public realm has more than one address.' },
  symmetry: { label: 'Mirror symmetry', equation: 'f(x,y) = f(−x,y)', description: 'Use a readable axis for arrival and ceremony, then let the landscape soften the balance on either side.' },
  orthogonal: { label: 'Orthogonal grid', equation: 'G = {(i·m, j·m)}', description: 'Set out bays, paving and furniture on a clear module so the building can be built, repaired and extended.' },
  circle: { label: 'Circular court', equation: '(x−h)² + (y−k)² = r²', description: 'Make a shared centre legible through a circular room, court, canopy or ring of public pause.' },
  cruciform: { label: 'Cruciform plan', equation: 'C = axes + shared centre', description: 'Cross two civic routes at a common room so representation, learning, water and public life meet visibly.' }
};
let selectedEquation = 'phi';

function scenarioNumber(id, fallback) {
  const value=Number($(id)?.value);
  return Number.isFinite(value) ? value : fallback;
}
function scenarioValues() {
  return {
    module: scenarioNumber('moduleScale',13),
    courtyard: scenarioNumber('courtyardScale',38),
    bays: Math.round(scenarioNumber('bayCount',8)),
    angle: scenarioNumber('pathAngle',137.5),
    density: scenarioNumber('publicDensity',60),
    wind: scenarioNumber('windShelter',64),
    rain: scenarioNumber('rainCapture',72),
    rainEvent: $('rainEvent')?.value || 'design',
    accessWidth: scenarioNumber('accessWidth',1.8),
    phases: Math.round(scenarioNumber('futurePhases',2)),
    publicMix: scenarioNumber('publicMix',70),
    levels: Math.round(scenarioNumber('buildingLevels',2)),
    season: $('season')?.value || 'midsummer',
    material: $('material')?.value || 'stone',
    grammar: $('grammar')?.value || 'radial'
  };
}
function skyClamp(value,min,max) { return Math.max(min,Math.min(max,value)); }
function skyLatitudeText(value) { return `${fmt(Math.abs(value),2)}° ${value>=0?'N':'S'}`; }
function skyLongitudeText(value) { return `${fmt(Math.abs(value),2)}° ${value>=0?'E':'W'}`; }
function solarHorizonMetrics(latitude,declination) {
  const lat=skyClamp(Number(latitude),-66,66), declinationValue=Number(declination)||0, latitudeRadians=lat*Math.PI/180, declinationRadians=declinationValue*Math.PI/180;
  const hourAngle=Math.acos(skyClamp(-Math.tan(latitudeRadians)*Math.tan(declinationRadians),-1,1));
  const daylightHours=24*hourAngle/Math.PI, noonAltitude=skyClamp(90-Math.abs(lat-declinationValue),0,90), sunrise=Math.acos(skyClamp(Math.sin(declinationRadians)/Math.cos(latitudeRadians),-1,1))*180/Math.PI;
  return {daylightHours,noonAltitude,sunrise,sunset:360-sunrise};
}
function renderFieldLight(row) {
  const root=$('fieldLight');
  if(!root) return;
  const rawLat=Number(row?.lat), selected=Number.isFinite(rawLat), lat=selected?skyClamp(rawLat,-66,66):DEFAULT_MAP_CENTER[0], summer=solarHorizonMetrics(lat,SKY_DECLINATION.midsummer), winter=solarHorizonMetrics(lat,SKY_DECLINATION.midwinter), place=selected?selectionPlaceText(row):'Ireland field centre', title=selected?contextTitle(row):'the island field';
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  set('fieldLightScope',selected?place:'Ireland field centre');
  set('fieldLightHeadline',`${fmt(summer.daylightHours,1)} h midsummer · ${fmt(winter.daylightHours,1)} h midwinter`);
  set('fieldLightLatitude',skyLatitudeText(lat));
  set('fieldLightSummer',`${fmt(summer.daylightHours,1)} h`);
  set('fieldLightWinter',`${fmt(winter.daylightHours,1)} h`);
  set('fieldLightNote',`Approximate solar geometry for ${title} at ${skyLatitudeText(lat)}: noon altitude ${fmt(summer.noonAltitude,1)}° in midsummer and ${fmt(winter.noonAltitude,1)}° in midwinter. This is a light question, not a site-specific energy or daylight model.`);
  root.setAttribute('aria-label',`Approximate seasonal light field for ${title}: ${fmt(summer.daylightHours,1)} hours of midsummer daylight and ${fmt(winter.daylightHours,1)} hours of midwinter daylight.`);
}
function renderFieldShape(row) {
  const root=$('fieldShape'), visual=$('fieldShapeVisual');
  if(!root||!visual) return;
  const selected=Boolean(row?.osm_id), set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  if(!selected) {
    const total=Number(SUMMARY.targets||DATA.length||0);
    set('fieldShapeScope',`${total.toLocaleString()} target footprints`);
    set('fieldShapeHeadline','Read a footprint as measured form.');
    ['fieldShapeArea','fieldShapePerimeter','fieldShapeScale','fieldShapeAspect'].forEach(id=>set(id,'—'));
    visual.setAttribute('aria-label','No footprint selected; choose a field walk stop or map point to draw a measured shape');
    set('fieldShapeVisualNote','select a field stop or map point');
    set('fieldShapeNote','Select a field walk stop or map point to draw its mapped outline and translate area, boundary and proportion into visible form.');
    root.setAttribute('aria-label',`Measured footprint shape field for ${total.toLocaleString()} target footprints; no footprint selected.`);
    return;
  }
  const shape=fieldWalkShapeSvg(row), flags=contextSignalText(row), aspect=Number(row.aspect_ratio), circularity=Number(row.circularity), title=contextTitle(row), place=selectionPlaceText(row), signalText=flags==='no core signal'?'no core signal':`${flags} signal${flags.includes(' · ')?'s':''}`;
  set('fieldShapeScope',place||title);
  set('fieldShapeHeadline',`r ${fmt(aspect,3)} · C ${fmt(circularity,3)} · ${signalText}`);
  set('fieldShapeArea',`${fmt(row.area_m2,0)} m²`);
  set('fieldShapePerimeter',`${fmt(row.perimeter_m,1)} m`);
  set('fieldShapeScale',`${fmt(row.length_m,1)} × ${fmt(row.width_m,1)} m`);
  set('fieldShapeAspect',`r ${fmt(aspect,3)}`);
  visual.innerHTML=`${shape.svg}<small class="field-shape-visual-note" id="fieldShapeVisualNote">${esc(shape.source)} · ${esc(shape.detail)}</small>`;
  visual.setAttribute('aria-label',`Measured ${shape.source} for ${title}; ${shape.detail}`);
  set('fieldShapeNote',`${title} sits at ${coordinateLabel(row.lat,'N','S')} / ${coordinateLabel(row.lon,'E','W')}. Area and boundary are mapped descriptors; ${signalText} remains a screening prompt, not evidence of historic intent.`);
  root.setAttribute('aria-label',`Measured footprint shape field for ${title}: ${fmt(row.area_m2,0)} square metres, perimeter ${fmt(row.perimeter_m,1)} metres, aspect ratio ${fmt(aspect,3)}.`);
}
function renderFieldRoots(row) {
  const root=$('fieldRoots');
  if(!root) return;
  const culture=SUMMARY.culture||{}, set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; }, selected=Boolean(row?.osm_id), count=value=>Number(value||0).toLocaleString();
  if(!selected) {
    set('fieldRootsScope','Irish field context');
    set('fieldRootsHeadline','Irish land is a network of names, records and shared places.');
    set('fieldRootsPlace',`${count(culture.county_contexts)} counties`);
    set('fieldRootsHeritage',`${count(culture.heritage_joins)} NIAH joins`);
    set('fieldRootsGroup',`${count(culture.shared_life)} shared-life rows`);
    set('fieldRootsNote','Snapshot counts describe source coverage: a named place is not an etymological reading, and a heritage join is context rather than proof of historic mathematical intent.');
    root.setAttribute('aria-label',`Source-linked Irish roots field: ${count(culture.county_contexts)} county contexts, ${count(culture.heritage_joins)} NIAH joins, and ${count(culture.shared_life)} shared-life rows.`);
    return;
  }
  const niah=row.niah||{}, lens=culturalLensForRow(row), lensLabel=lens?CULTURE_LENS_LABELS[lens]:'Áit / place context', place=selectionPlaceText(row), title=contextTitle(row), type=String(niah.type||'').trim(), heritage=niah.reg_no?[niah.name||'NIAH record',niah.reg_no,niah.rating,niah.century].filter(Boolean).join(' · '):'No NIAH join';
  const group=[spatialGroupLabel(row.group),type?heritageTypeLabel(type):String(row.subtype||'').trim()].filter(Boolean).join(' · ');
  set('fieldRootsScope',place||'context not reported');
  set('fieldRootsHeadline',`${lensLabel} · ${title}`);
  set('fieldRootsPlace',place||'Context not reported');
  set('fieldRootsHeritage',heritage);
  set('fieldRootsGroup',group||'Mapped group not reported');
  set('fieldRootsNote',`Source-linked context for ${title}: the row carries ${place||'no named place'} beside its mapped geometry. Read the place, record and group together without treating the connection as a cultural proof.`);
  root.setAttribute('aria-label',`Source-linked Irish roots field for ${title}: ${place||'context not reported'}; ${heritage}; ${group||'mapped group not reported'}.`);
}
function skyFieldMetrics() {
  const values=scenarioValues(), reference=studioReferenceData;
  const rawLat=Number(reference?.lat), rawLon=Number(reference?.lon);
  const lat=Number.isFinite(rawLat)?skyClamp(rawLat,-66,66):DEFAULT_MAP_CENTER[0];
  const lon=Number.isFinite(rawLon)?rawLon:DEFAULT_MAP_CENTER[1];
  const declination=Number(SKY_DECLINATION[values.season] ?? 0);
  const solar=solarHorizonMetrics(lat,declination);
  return {values,reference,lat,lon,declination,...solar};
}
function renderSkyField() {
  const root=$('skyField');
  if(!root) return;
  const metrics=skyFieldMetrics(), season=DESIGN_SEASONS[metrics.values.season] || DESIGN_SEASONS.midsummer;
  const reference=metrics.reference, place=reference ? selectionPlaceText(reference) : 'Ireland field centre';
  const scope=reference ? 'selected place' : 'island field';
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const sunY=skyClamp(31+(metrics.noonAltitude/90)*48,35,82);
  root.style.setProperty('--sky-sun-y',`${sunY}%`);
  set('skyFieldScope',scope);
  set('skyFieldIntro',reference ? `For ${place}, the seasonal light field becomes another design constraint: orient the civic room to welcome, shade or shelter the people who use it.` : `At Ireland's field centre, the seasonal light field becomes another design constraint: orient the civic room to welcome, shade or shelter the people who use it.`);
  set('skyLatitude',skyLatitudeText(metrics.lat));
  set('skyLatitudeNote',reference ? place : 'Ireland field centre');
  set('skyDaylight',`${fmt(metrics.daylightHours,1)} h`);
  set('skyDaylightNote',`${season.label.split('/')[0].trim()} horizon estimate`);
  set('skyNoonAltitude',`${fmt(metrics.noonAltitude,1)}°`);
  set('skyBearings',`${fmt(metrics.sunrise,0)}° / ${fmt(metrics.sunset,0)}°`);
  set('skySunLabel',`${fmt(metrics.noonAltitude,1)}° solar noon`);
  set('skyFieldNote',`Indicative geometry only: ${skyLatitudeText(metrics.lat)} at ${skyLongitudeText(metrics.lon)} with a ${season.label.toLowerCase()} declination of ${metrics.declination>=0?'+':''}${fmt(metrics.declination,2)}° produces this horizon estimate; it is not a site-specific daylight, glare or energy model.`);
  const plot=$('skyPlot');
  if(plot) plot.setAttribute('aria-label',`Indicative ${season.label.toLowerCase()} solar geometry for ${place}: ${fmt(metrics.daylightHours,1)} hours of daylight and ${fmt(metrics.noonAltitude,1)} degrees solar-noon altitude.`);
}
function waterFieldMetrics(values=scenarioValues()) {
  const event=RAIN_EVENTS[values.rainEvent] || RAIN_EVENTS.design;
  const metrics=designMetrics(values), coveredArea=metrics.enclosedFootprint;
  const eventLitres=coveredArea*event.mm, capturedLitres=eventLitres*(values.rain/100), overflowLitres=Math.max(0,eventLitres-capturedLitres);
  return {event,metrics,coveredArea,eventLitres,capturedLitres,overflowLitres};
}
function litresText(value) { return `${Math.round(value).toLocaleString()} L`; }
function renderWaterField() {
  const root=$('waterField');
  if(!root) return;
  const values=scenarioValues(), water=waterFieldMetrics(values), typology=DESIGN_TYPOLOGIES[$('typology')?.value || 'parliament'] || DESIGN_TYPOLOGIES.parliament;
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  set('rainEventNote',water.event.note);
  set('waterFieldBadge',water.event.label);
  set('waterFieldIntro',`For the ${typology.label.toLowerCase()}, a ${water.event.label.toLowerCase()} turns the covered civic field into a visible relationship between roof, rain and public ground.`);
  set('waterEquation',`${metricM2(water.coveredArea)} × ${fmt(water.event.mm,0)} mm = ${litresText(water.eventLitres)}`);
  set('waterEquationNote',`1 m² × 1 mm = 1 litre · ${fmt(values.rain,0)}% capture setting retains ${litresText(water.capturedLitres)} before storage, conveyance and surface losses.`);
  set('waterRoute',typology.water);
  set('waterRoofArea',metricM2(water.coveredArea));
  set('waterRainDepth',`${fmt(water.event.mm,0)} mm`);
  set('waterEventVolume',litresText(water.eventLitres));
  set('waterCapturedVolume',litresText(water.capturedLitres));
  set('waterOverflow',`${litresText(water.overflowLitres)} outside capture setting · verify locally`);
  set('waterFieldNote',`Indicative arithmetic only: the covered area is the studio’s enclosed module field after the courtyard void, multiplied by one rainfall pulse. Capture is a scenario emphasis, not a certified efficiency; test drainage, storage, flooding, water quality, maintenance and compliance separately.`);
}
function scenarioSummaryText(values) {
  return `${fmt(values.courtyard,0)}% court · ${fmt(values.bays,0)} bays · ${fmt(values.angle,1)}° path · ${fmt(values.density,0)}% public density`;
}
function performanceSummaryText(values) {
  return `${fmt(values.wind,0)}% shelter · ${fmt(values.rain,0)}% rain capture · ${fmt(values.accessWidth,1)} m clear route · ${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'}`;
}
function programmeSummaryText(values) {
  return `${fmt(values.publicMix,0)}% public programme · ${fmt(values.levels,0)} ${values.levels===1?'level':'levels'}`;
}
function radialNodes(count, radius, angleOffset, tone, opacity, className='diagram-bay-dot') {
  const safeCount=Math.max(1,Math.round(count));
  const safeRadius=Number(radius)||0;
  const safeOpacity=Number(opacity)||.5;
  return Array.from({length:safeCount},(_,i)=>{
    const angle=(-90+angleOffset+i*(360/safeCount))*Math.PI/180;
    const nodeRadius=3.1+safeOpacity*2.8;
    const x=300+Math.cos(angle)*safeRadius;
    const y=156+Math.sin(angle)*safeRadius*.72;
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${nodeRadius.toFixed(1)}" class="${className}" style="fill:${tone};opacity:${safeOpacity.toFixed(2)}"/>`;
  }).join('');
}
function renderGrammarOverlay(key, values, tone, accent) {
  if(key==='symmetry') {
    return `<line x1="300" y1="48" x2="300" y2="264" class="diagram-axis"/><line x1="196" y1="156" x2="404" y2="156" class="diagram-axis"/><path d="M214 80 Q258 114 300 80 Q342 114 386 80" class="diagram-axis" style="stroke:${tone}"/>`;
  }
  if(key==='orthogonal') {
    const lines=Array.from({length:7},(_,i)=>{
      const x=174+i*42, y=62+i*31;
      return `<line x1="${x}" y1="48" x2="${x}" y2="264" class="diagram-grid" style="stroke:${tone}"/><line x1="164" y1="${y}" x2="436" y2="${y}" class="diagram-grid" style="stroke:${tone}"/>`;
    }).join('');
    return `<g aria-label="orthogonal grid">${lines}</g>`;
  }
  if(key==='circle') {
    const radii=[42,68,94].map((radius,index)=>`<circle cx="300" cy="156" r="${radius}" class="diagram-ring" style="stroke:${index===1?accent:tone};opacity:${index===1?.82:.5}"/>`).join('');
    return `${radii}<circle cx="300" cy="156" r="7" class="diagram-center" style="fill:${tone}"/>`;
  }
  if(key==='cruciform') {
    return `<path d="M260 48 H340 V116 H408 V196 H340 V264 H260 V196 H192 V116 H260 Z" class="diagram-crux" style="stroke:${accent}"/><line x1="300" y1="48" x2="300" y2="264" class="diagram-axis" style="stroke:${tone}"/><line x1="192" y1="156" x2="408" y2="156" class="diagram-axis" style="stroke:${tone}"/>`;
  }
  const rings=[52,82,112].map(radius=>`<circle cx="300" cy="156" r="${radius}" class="diagram-ring" style="stroke:${tone};opacity:.45"/>`).join('');
  return `${rings}${radialNodes(Math.max(5,values.bays),104,values.angle,tone,.56)}`;
}
function renderPerformanceOverlay(values, tone) {
  const exposure=(100-Math.max(0,Math.min(100,values.wind)))/100;
  const windLines=Array.from({length:4},(_,i)=>{
    const y=72+i*34, length=34+exposure*88, bend=8+(i%2)*6;
    return `<path d="M${(62-i*4).toFixed(1)} ${y} Q ${(62+length*.45).toFixed(1)} ${(y-bend).toFixed(1)} ${(62+length).toFixed(1)} ${y}" class="diagram-wind" style="opacity:${(.16+exposure*.55).toFixed(2)}"/>`;
  }).join('');
  const rainCount=Math.max(2,Math.round(values.rain/18));
  const rainLines=Array.from({length:rainCount},(_,i)=>{
    const x=424+i*13, length=10+(values.rain/100)*18;
    return `<path d="M${x} 42 l-4 ${length.toFixed(1)}" class="diagram-rain" style="opacity:${(.15+values.rain/150).toFixed(2)}"/>`;
  }).join('');
  const routeWidth=Math.max(3,Math.min(8,values.accessWidth*2.4));
  const route=`<path d="M92 278 Q300 ${(286-values.density*.08).toFixed(1)} 508 278" class="diagram-route" style="stroke:${tone};stroke-width:${routeWidth.toFixed(1)}"/>`;
  const phaseLines=Array.from({length:Math.max(0,values.phases-1)},(_,i)=>{
    const x=210+i*90;
    return `<line x1="${x}" y1="278" x2="${x}" y2="304" class="diagram-phase"/>`;
  }).join('');
  return `${windLines}${rainLines}${route}${phaseLines}`;
}

function designSvgText(x, y, text, className='diagram-label', anchor='start') {
  return `<text x="${x}" y="${y}" class="${className}" text-anchor="${anchor}">${esc(text)}</text>`;
}
function renderDesignDiagram() {
  const svg=$('designDiagram');
  if(!svg) return;
  const equation=DESIGN_EQUATIONS[selectedEquation] || DESIGN_EQUATIONS.phi;
  const type=DESIGN_TYPOLOGIES[$('typology')?.value || 'parliament'] || DESIGN_TYPOLOGIES.parliament;
  const values=scenarioValues();
  const season=DESIGN_SEASONS[values.season] || DESIGN_SEASONS.midsummer;
  const material=DESIGN_MATERIALS[values.material] || DESIGN_MATERIALS.stone;
  const grammar=DESIGN_GRAMMARS[values.grammar] || DESIGN_GRAMMARS.radial;
  const module=Math.max(5,values.module);
  const accent=selectedEquation==='theta'?'#bf5b45':selectedEquation==='fib'?'#356c69':selectedEquation==='spiral'?'#4c765f':'#d0a34c';
  const phi=1.618;
  const cx=300, cy=156;
  const rays=Array.from({length:12},(_,i)=>{
    const angle=(-90+i*values.angle)*Math.PI/180;
    const length=94+values.density*.35+(i%3)*12;
    return `<line x1="${cx}" y1="${cy}" x2="${(cx+Math.cos(angle)*length).toFixed(1)}" y2="${(cy+Math.sin(angle)*length).toFixed(1)}" class="diagram-ray" style="stroke:${accent}"/>`;
  }).join('');
  const steps=[3,5,8,13,21].map((value,index)=>{
    const width=36+value*3.2, height=18+value*1.8;
    const x=38+index*30, y=238-index*14;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${width.toFixed(1)}" height="${height.toFixed(1)}" class="diagram-step" style="stroke:${accent}"/><text x="${(x+width/2).toFixed(1)}" y="${(y+height/2+3).toFixed(1)}" class="diagram-step-label" text-anchor="middle">${value}</text>`;
  }).join('');
  const groundNodes=radialNodes(values.bays,78,values.angle,material.tone,.32+values.density/150);
  let drawing='';
  if(type.kind==='chamber') {
    const chamber=70+module*1.2, foyer=chamber*phi;
    const courtRx=Math.min(91,38+values.courtyard*.66);
    const courtRy=Math.min(55,22+values.courtyard*.34);
    const seats=radialNodes(Math.max(4,Math.min(13,Math.round(values.bays*(.55+values.density/240)))),Math.min(118,foyer*.48),values.angle,material.tone,.45+values.density/180);
    drawing=`<rect x="${(cx-foyer/2).toFixed(1)}" y="${(cy-foyer*.28).toFixed(1)}" width="${foyer.toFixed(1)}" height="${(foyer*.56).toFixed(1)}" rx="16" class="diagram-outer" style="stroke:${material.tone}"/><ellipse cx="${cx}" cy="${(cy+6).toFixed(1)}" rx="${courtRx.toFixed(1)}" ry="${courtRy.toFixed(1)}" class="diagram-ring" style="stroke:${material.tone}"/><ellipse cx="${cx}" cy="${cy}" rx="${(chamber*.63).toFixed(1)}" ry="${(chamber*.46).toFixed(1)}" class="diagram-inner"/><path d="M${cx-foyer/2} ${cy-foyer*.28} Q ${cx} ${cy-foyer*.54} ${cx+foyer/2} ${cy-foyer*.28}" class="diagram-arc"/><circle cx="${cx}" cy="${cy}" r="${(chamber*.13).toFixed(1)}" class="diagram-center"/>${seats}${selectedEquation==='phi'?designSvgText(cx,cy+4,'public chamber','diagram-center-label','middle'):''}${groundNodes}`;
  } else if(type.kind==='forum') {
    const frameW=340+Math.min(80,module*1.5);
    const frameX=cx-frameW/2;
    const courtRadius=Math.min(90,32+values.courtyard*.8);
    const forumNodes=radialNodes(values.bays,78,values.angle,material.tone,.42+values.density/170);
    drawing=`<rect x="${frameX.toFixed(1)}" y="48" width="${frameW.toFixed(1)}" height="216" rx="8" class="diagram-outer" style="stroke:${material.tone}"/><circle cx="${cx}" cy="${cy}" r="90" class="diagram-ring"/><circle cx="${cx}" cy="${cy}" r="${courtRadius.toFixed(1)}" class="diagram-inner"/><circle cx="${cx}" cy="${cy}" r="15" class="diagram-center"/>${forumNodes}${designSvgText(cx,cy+4,'shared ground','diagram-center-label','middle')}`;
  } else if(type.kind==='market') {
    const columns=Math.ceil(Math.sqrt(values.bays));
    const rows=Math.ceil(values.bays/columns);
    const baySize=Math.min(52,Math.max(24,230/Math.max(columns,rows)));
    const gap=Math.min(13,Math.max(6,baySize*.18));
    const totalW=columns*baySize+(columns-1)*gap;
    const totalH=rows*baySize+(rows-1)*gap;
    const startX=cx-totalW/2, startY=154-totalH/2;
    const modules=Array.from({length:values.bays},(_,i)=>{
      const x=startX+(i%columns)*(baySize+gap), y=startY+Math.floor(i/columns)*(baySize+gap);
      const dot=6+values.density*.045;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${baySize.toFixed(1)}" height="${baySize.toFixed(1)}" rx="7" class="diagram-bay" style="stroke:${material.tone}"/><circle cx="${(x+baySize/2).toFixed(1)}" cy="${(y+baySize/2).toFixed(1)}" r="${dot.toFixed(1)}" class="diagram-bay-dot" style="fill:${accent}"/>`;
    }).join('');
    drawing=`<path d="M82 210 Q300 26 518 210" class="diagram-canopy" style="stroke:${material.tone}"/>${modules}${designSvgText(cx,285,'adaptable bay field','diagram-center-label','middle')}`;
  } else if(type.kind==='library') {
    const columns=4, rows=Math.ceil(values.bays/columns);
    const bayW=64, bayH=Math.min(42,Math.max(28,124/rows));
    const totalW=columns*bayW+(columns-1)*8, totalH=rows*bayH+(rows-1)*8;
    const startX=cx-totalW/2, startY=154-totalH/2;
    const shelves=Array.from({length:values.bays},(_,i)=>{
      const x=startX+(i%columns)*(bayW+8), y=startY+Math.floor(i/columns)*(bayH+8);
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bayW}" height="${bayH.toFixed(1)}" rx="5" class="diagram-bay" style="stroke:${material.tone}"/><line x1="${(x+8).toFixed(1)}" y1="${(y+bayH*.66).toFixed(1)}" x2="${(x+bayW-8).toFixed(1)}" y2="${(y+bayH*.66).toFixed(1)}" class="diagram-grid" style="stroke:${accent}"/>`;
    }).join('');
    const courtRadius=Math.min(52,28+values.courtyard*.42);
    drawing=`<rect x="92" y="48" width="416" height="216" rx="9" class="diagram-outer" style="stroke:${material.tone}"/>${shelves}<circle cx="${cx}" cy="${cy}" r="${courtRadius.toFixed(1)}" class="diagram-inner"/><circle cx="${cx}" cy="${cy}" r="13" class="diagram-center"/>${designSvgText(cx,cy+4,'reading court','diagram-center-label','middle')}`;
  } else if(type.kind==='museum') {
    const outerRadius=Math.min(112,84+values.courtyard*.28);
    const innerRadius=Math.max(30,outerRadius*.46);
    const exhibitNodes=radialNodes(values.bays,Math.max(48,outerRadius*.72),values.angle,material.tone,.45+values.density/190);
    const loopPoints=Array.from({length:38},(_,i)=>{ const t=i*.22, radius=innerRadius+i*.8, angle=t+values.angle*Math.PI/180-1.8; return `${(cx+Math.cos(angle)*radius).toFixed(1)},${(cy+Math.sin(angle)*radius*.72).toFixed(1)}`; }).join(' ');
    drawing=`<circle cx="${cx}" cy="${cy}" r="${outerRadius.toFixed(1)}" class="diagram-outer" style="stroke:${material.tone}"/><circle cx="${cx}" cy="${cy}" r="${innerRadius.toFixed(1)}" class="diagram-inner"/><polyline points="${loopPoints}" class="diagram-spiral" style="stroke:${accent}"/><circle cx="${cx}" cy="${cy}" r="13" class="diagram-center"/>${exhibitNodes}${designSvgText(cx,cy+4,'shared memory','diagram-center-label','middle')}`;
  } else {
    const spiralCount=Math.round(42+values.density*.34);
    const points=Array.from({length:spiralCount},(_,i)=>{ const t=i*.25, radius=8+i*(.82+values.courtyard/230), angle=t+values.angle*Math.PI/180-2.7; return `${(cx+Math.cos(angle)*radius).toFixed(1)},${(cy+Math.sin(angle)*radius*.74).toFixed(1)}`; }).join(' ');
    drawing=`<polyline points="${points}" class="diagram-spiral" style="stroke:${accent}"/><circle cx="${cx}" cy="${cy}" r="13" class="diagram-center"/>${designSvgText(cx,cy+4,'pause','diagram-center-label','middle')}<path d="M96 246 Q300 284 510 240" class="diagram-water" style="stroke:${material.tone}"/>${groundNodes}`;
  }
  const equationOverlay=selectedEquation==='theta'?rays:selectedEquation==='fib'?steps:selectedEquation==='spiral'?`<path d="M134 242 Q245 ${42+values.courtyard*.25} 450 ${98-values.density*.08} Q536 131 480 230" class="diagram-spiral" style="stroke:${accent}"/>`:'';
  const grammarOverlay=renderGrammarOverlay(values.grammar,values,material.tone,accent);
  const performanceOverlay=renderPerformanceOverlay(values,material.tone);
  svg.innerHTML=`<defs><pattern id="diagramGrid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#d7ccb8" stroke-width=".7"/></pattern><marker id="windArrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6Z" fill="#527b85"/></marker></defs><rect width="600" height="330" fill="${season.sky}" opacity=".16"/><rect width="600" height="330" fill="url(#diagramGrid)"/><text x="26" y="27" class="diagram-kicker">SCHEMATIC / ${esc(type.label).toUpperCase()}</text><text x="574" y="27" class="diagram-kicker" text-anchor="end">${esc(equation.equation)}</text><text x="574" y="42" class="diagram-kicker" text-anchor="end">${esc(grammar.label.toUpperCase())} · ${esc(grammar.equation)}</text>${drawing}${grammarOverlay}${equationOverlay}${performanceOverlay}<line x1="28" y1="305" x2="572" y2="305" class="diagram-baseline"/>`;
  if($('diagramCaption')) $('diagramCaption').textContent=`${equation.caption} ${grammar.label.toLowerCase()} · ${scenarioSummaryText(values)} · ${season.label}.`;
}
function renderScenarioReadout(values=scenarioValues()) {
  const outputs=[['moduleValue',`${fmt(values.module,0)} m`],['courtyardValue',`${fmt(values.courtyard,0)}%`],['bayValue',fmt(values.bays,0)],['pathValue',`${fmt(values.angle,1)}°`],['densityValue',`${fmt(values.density,0)}%`],['windValue',`${fmt(values.wind,0)}%`],['rainValue',`${fmt(values.rain,0)}%`],['accessValue',`${fmt(values.accessWidth,1)} m`],['phaseValue',`${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'}`],['publicMixValue',`${fmt(values.publicMix,0)}%`],['levelValue',`${fmt(values.levels,0)} ${values.levels===1?'level':'levels'}`]];
  outputs.forEach(([id,text])=>{ if($(id)) $(id).textContent=text; });
  if($('scenarioSummary')) $('scenarioSummary').textContent=scenarioSummaryText(values);
  if($('performanceSummary')) $('performanceSummary').textContent=performanceSummaryText(values);
  if($('programmeSummary')) $('programmeSummary').textContent=programmeSummaryText(values);
}
function renderDesignSpec(typology, season, material, values) {
  const grammar=DESIGN_GRAMMARS[values.grammar] || DESIGN_GRAMMARS.radial;
  const specs=[
    {label:'Universal access', strong:`${fmt(values.accessWidth,1)} m clear route`, copy:`${typology.access}. Verify gradients, door clearances, tactile cues and resting places locally.`, note:`Concept setting · ${fmt(values.module,0)} m module rhythm`},
    {label:'Rain + water', strong:`${fmt(values.rain,0)}% capture emphasis`, copy:`${typology.water}. Keep roof, rill and planted court readable as one public sequence.`, note:'Design setting · test overflow, storage and maintenance'},
    {label:'Weather + season', strong:`${fmt(values.wind,0)}% shelter emphasis`, copy:`${season.light}. ${typology.wind}.`, note:`${season.weather} · verify with local wind/daylight studies`},
    {label:'Material + carbon', strong:material.label, copy:material.note, note:'Concept palette · LCA, sourcing and maintenance check required'},
    {label:'Future capacity', strong:`${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'} · ${fmt(values.bays,0)} bays · ${fmt(values.levels,0)} levels`, copy:'Keep the civic room legible while allowing the public edge, planting and services to grow in stages.', note:`${fmt(values.density,0)}% public-density setting · test a future phase`},
    {label:'Spatial grammar', strong:grammar.label, copy:grammar.description, note:`${grammar.equation} · contemporary hypothesis`}
  ];
  if($('designSpec')) $('designSpec').innerHTML=specs.map(spec=>`<article class="spec-card"><span>${esc(spec.label)}</span><strong>${esc(spec.strong)}</strong><p>${esc(spec.copy)}</p><small>${esc(spec.note)}</small></article>`).join('');
}
function designMetrics(values) {
  const bayArea=Math.max(1,values.module*values.module);
  const footprint=bayArea*Math.max(1,values.bays);
  const courtArea=footprint*(values.courtyard/100);
  const enclosedFootprint=Math.max(0,footprint-courtArea);
  const grossFloor=enclosedFootprint*Math.max(1,values.levels);
  const publicFloor=grossFloor*(values.publicMix/100);
  const supportFloor=Math.max(0,grossFloor-publicFloor);
  const equivalentCourtRadius=Math.sqrt(Math.max(0,courtArea)/Math.PI);
  const loopLength=2*Math.PI*equivalentCourtRadius;
  const phaseFloor=grossFloor/Math.max(1,values.phases);
  return {bayArea,footprint,courtArea,enclosedFootprint,grossFloor,publicFloor,supportFloor,loopLength,phaseFloor};
}
function metricM2(value) { return `${Math.round(value).toLocaleString()} m²`; }
function metricM(value) { return `${Math.round(value).toLocaleString()} m`; }
function renderDesignSchedule(typology, values) {
  const metrics=designMetrics(values);
  const cards=[
    {label:'Module field',strong:metricM2(metrics.footprint),copy:`${fmt(values.module,0)} m × ${fmt(values.module,0)} m bay × ${fmt(values.bays,0)} bays`},
    {label:'Indicative floor area',strong:metricM2(metrics.grossFloor),copy:`${fmt(values.levels,0)} ${values.levels===1?'level':'levels'} after the courtyard void`},
    {label:'Courtyard / porous void',strong:metricM2(metrics.courtArea),copy:`${fmt(values.courtyard,0)}% of the module field kept open`},
    {label:'Public programme',strong:metricM2(metrics.publicFloor),copy:`${fmt(values.publicMix,0)}% of indicative enclosed floor area`},
    {label:'Civic loop',strong:metricM(metrics.loopLength),copy:`Equivalent circular route around the ${typology.label.toLowerCase()} court`},
    {label:'Phase 1 share',strong:metricM2(metrics.phaseFloor),copy:`Indicative floor area per stage across ${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'}`}
  ];
  if($('designSchedule')) $('designSchedule').innerHTML=cards.map(card=>`<article class="schedule-card"><span>${esc(card.label)}</span><strong>${esc(card.strong)}</strong><p>${esc(card.copy)}</p></article>`).join('')+'<p class="schedule-note">Indicative geometry only: the calculation uses the selected module, bay count, courtyard void, level count, public-programme mix and phases. It is not a planning, occupancy, structural, cost or compliance model.</p>';
}
function buildDesignBrief() {
  const values=scenarioValues();
  const typology=DESIGN_TYPOLOGIES[$('typology')?.value || 'parliament'] || DESIGN_TYPOLOGIES.parliament;
  const equation=DESIGN_EQUATIONS[selectedEquation] || DESIGN_EQUATIONS.phi;
  const grammar=DESIGN_GRAMMARS[values.grammar] || DESIGN_GRAMMARS.radial;
  const season=DESIGN_SEASONS[values.season] || DESIGN_SEASONS.midsummer;
  const material=DESIGN_MATERIALS[values.material] || DESIGN_MATERIALS.stone;
  const metrics=designMetrics(values);
  const sky=skyFieldMetrics();
  const water=waterFieldMetrics(values);
  const reference=studioReferenceData;
  const pairRows=studioPairData.filter(row=>row&&row.osm_id).slice(0,2), pairDistance=pairRows.length===2?contextDistanceMeters(pairRows[0],pairRows[1]):NaN, pairBearing=pairRows.length===2?comparisonBearingDegrees(pairRows[0],pairRows[1]):NaN, pairShared=pairRows.length===2?[...(new Set((pairRows[0].flags||[]).map(patternLabel)))].filter(flag=>new Set((pairRows[1].flags||[]).map(patternLabel)) .has(flag)):[], pairLines=pairRows.length===2?[`Paired places: ${contextTitle(pairRows[0])} ↔ ${contextTitle(pairRows[1])}`,`Between: ${contextDistanceLabel(pairDistance)} straight-line centroid span · bearing ${Number.isFinite(pairBearing)?fmt(pairBearing,1):'not reported'}° A → B`, `Context bridge: ${selectionPlaceText(pairRows[0])} ↔ ${selectionPlaceText(pairRows[1])}`, `Shared measured screens: ${pairShared.length?pairShared.join(' · '):'none'}`]:['No paired relationship carried from the comparison field.'];
  const worship=SIG.find(row=>row.signal==='golden_angle'&&row.group==='worship') || {};
  const caveat=(Array.isArray(INTERPRETATION.caveats)&&INTERPRETATION.caveats[0]) || 'Geometry flags are screening evidence, not evidence of design intent.';
  const validation=String(SUMMARY.validation?.status || (SUMMARY.analysis_ready?'pass':'incomplete'));
  return [
    'CRUTH / IRISH CIVIC GEOMETRY ATLAS',
    'CONCEPT DESIGN BRIEF',
    'Generated from the current interactive test-fit · contemporary design hypothesis · not a historical reconstruction',
    '',
    '0 / FIELD REFERENCE',
    reference ? `Selected footprint: ${contextTitle(reference)} · ${reference.osm_id||'OSM target'}` : 'No selected footprint carried from the field.',
    reference ? `Place context: ${selectionPlaceText(reference)}` : 'Use “Carry geometry to studio” on a selected map target to add a measured field reference.',
    reference ? `Measured geometry: ${fmt(reference.length_m,1)} × ${fmt(reference.width_m,1)} m · ${fmt(reference.area_m2,0)} m² · aspect ${fmt(reference.aspect_ratio,3)}` : 'The studio remains a standalone contemporary test-fit until a field reference is chosen.',
    reference ? `Source trail: ${reference.osm_url||`https://www.openstreetmap.org/${encodeURIComponent(reference.osm_id||'')}`}` : '',
    ...pairLines,
    '',
    '1 / POSITION',
    `Typology: ${typology.label}`,
    `Public life: ${typology.public}`,
    `Spatial sequence: ${equation.move}`,
    '',
    '2 / GEOMETRY',
    `Equation: ${equation.equation}`,
    `Geometry grammar: ${grammar.label} — ${grammar.equation}`,
    grammar.description,
    `Selected material study: ${material.label}`,
    '',
    '3 / INDICATIVE TEST-FIT ARITHMETIC',
    `Module field: ${metricM2(metrics.footprint)} = ${fmt(values.module,0)} m module × ${fmt(values.bays,0)} bays`,
    `Courtyard / porous void: ${metricM2(metrics.courtArea)} (${fmt(values.courtyard,0)}%)`,
    `Indicative floor area: ${metricM2(metrics.grossFloor)} across ${fmt(values.levels,0)} ${values.levels===1?'level':'levels'}`,
    `Public programme: ${metricM2(metrics.publicFloor)} (${fmt(values.publicMix,0)}% of indicative enclosed floor area)`,
    `Support / service allowance: ${metricM2(metrics.supportFloor)}`,
    `Civic loop: ${metricM(metrics.loopLength)} equivalent circular route`,
    `Phase 1 share: ${metricM2(metrics.phaseFloor)} across ${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'}`,
    '',
    '4 / CLIMATE, ACCESS + DELIVERY',
    `Seasonal lens: ${season.label} — ${season.light}`,
    `Wind setting: ${fmt(values.wind,0)}% shelter emphasis — ${typology.wind}`,
    `Rain setting: ${fmt(values.rain,0)}% capture emphasis — ${typology.water}`,
    `Rain pulse: ${water.event.label}`,
    `Catchment arithmetic: ${metricM2(water.coveredArea)} × ${fmt(water.event.mm,0)} mm = ${litresText(water.eventLitres)} event volume`,
    `Indicative retained volume: ${litresText(water.capturedLitres)} before storage, conveyance and surface losses`,
    `Accessible route setting: ${fmt(values.accessWidth,1)} m clear route — ${typology.access}`,
    `Future delivery: ${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'} with ${fmt(values.bays,0)} bays available for adaptation`,
    `Material note: ${material.note}`,
    '',
    '5 / SKY + PLACE',
    `Field reference coordinate: ${skyLatitudeText(sky.lat)} · ${skyLongitudeText(sky.lon)}${reference?` · ${selectionPlaceText(reference)}`:' · Ireland field centre'}`,
    `Seasonal light lens: ${season.label} · declination ${sky.declination>=0?'+':''}${fmt(sky.declination,2)}°`,
    `Approximate day length: ${fmt(sky.daylightHours,1)} hours`,
    `Solar-noon altitude: ${fmt(sky.noonAltitude,1)}°`,
    `Sunrise / sunset bearings: ${fmt(sky.sunrise,0)}° / ${fmt(sky.sunset,0)}° from north`,
    'Use this as an orientation conversation; it is not a site-specific daylight, glare or energy model.',
    '',
    '6 / EVIDENCE POSITION',
    `Validation status: ${validation}`,
    `Measured signal: ${fmt(worship.observed_rate,2)}% worship targets vs ${fmt(worship.control_rate,2)}% controls for the golden-angle flag.`,
    'Use the measured pack, NIAH records and OSM geometry to form questions about place; do not infer historic intent from a geometric match.',
    `Caveat: ${caveat}`,
    '',
    '7 / SOURCE TRAIL',
    'OpenStreetMap contributors: https://www.openstreetmap.org/',
    'Buildings of Ireland / NIAH data: https://www.buildingsofireland.ie/niah-data-download/',
    'Report evidence catalogue: use the measured pattern catalogue in this dashboard.',
    '',
    'Review note: verify planning, fire, structure, accessibility, daylight, wind, drainage, ecology, cost, maintenance and procurement requirements with the relevant professionals.'
  ].join('\n');
}
function renderDesignBrief() {
  if($('designBrief')) $('designBrief').textContent=buildDesignBrief();
}
async function copyDesignBrief() {
  const status=$('briefStatus');
  try {
    if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(buildDesignBrief());
    if(status) status.textContent='Brief copied to the clipboard · indicative design arithmetic only.';
  } catch(error) {
    if(status) status.textContent='Copy is unavailable in this browser; use Download .txt instead · indicative design arithmetic only.';
  }
}
function downloadDesignBrief() {
  const blob=new Blob([buildDesignBrief()],{type:'text/plain;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download='cruth-concept-design-brief.txt';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  if($('briefStatus')) $('briefStatus').textContent='Brief downloaded · indicative design arithmetic only.';
}
function renderStudioReference() {
  const panel=$('studioReference');
  if(!panel) return;
  const row=studioReferenceData;
  if(!row) { panel.hidden=true; return; }
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const length=Number(row.length_m), width=Number(row.width_m), area=Number(row.area_m2), aspect=Number(row.aspect_ratio), circularity=Number(row.circularity);
  const dimensions=Number.isFinite(length)&&Number.isFinite(width) ? `${fmt(length,1)} × ${fmt(width,1)} m` : 'Dimensions not reported';
  const source=row.osm_id||'OSM target';
  const heritage=row.niah?.reg_no ? [row.niah.name||'NIAH-linked record',row.niah.reg_no].filter(Boolean).join(' · ') : 'No NIAH join in this snapshot';
  const signals=(row.flags||[]).slice(0,3).map(patternLabel).join(' · ') || 'No screening flags';
  set('studioReferenceTitle',contextTitle(row));
  set('studioReferenceContext',`${row.osm_id||'Target'} · ${row.group||'other'} · carried from the measured field`);
  set('studioReferencePlace',selectionPlaceText(row));
  set('studioReferenceSource',source);
  set('studioReferenceDimensions',dimensions);
  set('studioReferenceArea',Number.isFinite(area)?`${fmt(area,0)} m² enclosed area`:'Area not reported');
  set('studioReferenceAspect',Number.isFinite(aspect)?`r = ${fmt(aspect,3)}`:'r = not reported');
  set('studioReferenceCompactness',Number.isFinite(circularity)?`C = ${fmt(circularity,3)}`:'C = not reported');
  set('studioReferenceSignals',signals);
  set('studioReferenceHeritage',heritage);
  const use=$('studioUseScale');
  if(use) use.disabled=!Number.isFinite(width)||width<=0;
  panel.hidden=false;
}
function renderStudioPairReference() {
  const panel=$('studioPairReference');
  if(!panel) return;
  const rows=studioPairData.filter(row=>row&&row.osm_id).slice(0,2);
  if(rows.length<2) { panel.hidden=true; return; }
  const [a,b]=rows, set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; }, distance=contextDistanceMeters(a,b), bearing=comparisonBearingDegrees(a,b), axis=Number.isFinite(bearing)?((bearing%180)+180)%180:NaN, countyA=String(a.spatial?.county||a.niah?.county||'').trim(), countyB=String(b.spatial?.county||b.niah?.county||'').trim(), sameCounty=Boolean(countyA&&countyB&&countyA===countyB), shared=[...(new Set((a.flags||[]).map(patternLabel)))].filter(flag=>new Set((b.flags||[]).map(patternLabel)).has(flag));
  set('studioPairTitle',`${contextTitle(a)} ↔ ${contextTitle(b)}`);
  set('studioPairContext',`${a.osm_id||'Field A'} and ${b.osm_id||'Field B'} · carried from the measured relationship`);
  set('studioPairPlaceA',selectionPlaceText(a)); set('studioPairSourceA',`${a.group||'other'} · ${a.niah?.reg_no?'NIAH joined':'NIAH not joined'}`);
  set('studioPairPlaceB',selectionPlaceText(b)); set('studioPairSourceB',`${b.group||'other'} · ${b.niah?.reg_no?'NIAH joined':'NIAH not joined'}`);
  set('studioPairSpan',contextDistanceLabel(distance)); set('studioPairBearing',Number.isFinite(bearing)?`bearing ${fmt(bearing,1)}° · axis ${fmt(axis,1)}°`:'bearing not reported');
  set('studioPairBridge',sameCounty?countyA:'cross-county'); set('studioPairSignals',shared.length?`${shared.length} shared · ${shared.join(' · ')}`:'no shared core screens');
  const use=$('studioUsePairBearing'); if(use) use.disabled=!Number.isFinite(axis);
  panel.hidden=false;
}
function carrySelectionToStudio(id) {
  const row=targetRowForId(id);
  if(!row) return;
  studioReferenceData=row;
  studioReferenceId=String(row.osm_id||'');
  syncStudioState();
  renderStudioReference();
  setAtlasNavActive('studio');
  window.setTimeout(()=>{
    $('studio')?.scrollIntoView({behavior:'smooth',block:'start'});
    revealPanelTarget($('studioReference'));
  },120);
}
function carryComparisonToStudio() {
  const rows=comparisonData.filter(row=>row&&row.osm_id).slice(0,2), status=$('comparisonRelationActionStatus');
  if(rows.length<2) { if(status) status.textContent='Add two places before carrying a relationship to the studio.'; return; }
  studioPairData=rows.slice(); studioPairIds=rows.map(row=>String(row.osm_id));
  syncStudioState(true); renderStudioPairReference(); renderStudio(); setAtlasNavActive('studio');
  if(status) status.textContent='Relationship carried to the studio · controls remain editable.';
  window.setTimeout(()=>{
    $('studio')?.scrollIntoView({behavior:'smooth',block:'start'});
    revealPanelTarget($('studioPairReference'));
  },120);
}
function clearStudioReference() {
  studioReferenceData=null;
  studioReferenceId='';
  syncStudioState();
  renderStudioReference();
  renderStudio();
}
function clearStudioPairReference() {
  studioPairData=[]; studioPairIds=[];
  syncStudioState(); renderStudioPairReference(); renderStudio();
}
function useStudioReferenceScale() {
  const width=Number(studioReferenceData?.width_m);
  if(!Number.isFinite(width)||width<=0) return;
  const module=Math.max(5,Math.min(34,Math.round(width)));
  const input=$('moduleScale');
  if(input) input.value=String(module);
  renderStudio();
  if($('studioReferenceStatus')) $('studioReferenceStatus').textContent=`Module set to ${module} m from the measured width · reference remains editable.`;
}
function useStudioPairBearing() {
  const [a,b]=studioPairData.filter(row=>row&&row.osm_id).slice(0,2), bearing=comparisonBearingDegrees(a,b), axis=Number.isFinite(bearing)?((bearing%180)+180)%180:NaN;
  if(!Number.isFinite(axis)) return;
  const input=$('pathAngle'), value=Math.max(0,Math.min(180,Math.round(axis*2)/2));
  if(input) input.value=String(value);
  renderStudio();
  if($('studioPairStatus')) $('studioPairStatus').textContent=`Path rotation set to ${fmt(value,1)}° from the A→B bearing · pair remains editable.`;
}
function renderEvidenceBridge() {
  const validation=String(SUMMARY.validation?.status || (SUMMARY.analysis_ready?'pass':'incomplete')).toLowerCase();
  const badge=$('studioValidation');
  if(badge) {
    badge.textContent=`validation ${validation}`;
    badge.classList.toggle('pass',validation==='pass');
    badge.classList.toggle('incomplete',validation!=='pass');
  }
  const worship=SIG.find(row=>row.signal==='golden_angle'&&row.group==='worship') || {};
  const worshipRate=Number.isFinite(Number(worship.observed_rate))?fmt(worship.observed_rate,2):'7.09';
  const controlRate=Number.isFinite(Number(worship.control_rate))?fmt(worship.control_rate,2):'0.69';
  const adjusted=Number(worship.p_adjusted);
  const adjustedText=Number.isFinite(adjusted)?(adjusted<.0001?'Holm-adjusted p <0.0001':`Holm-adjusted p ${fmt(adjusted,4)}`):'adjusted significance reported in the pack';
  const verdict=String(worship.verdict||'screening result').toLowerCase();
  if($('studioEvidenceSignal')) $('studioEvidenceSignal').textContent=`${worshipRate}% worship targets vs ${controlRate}% controls`;
  if($('studioEvidenceText')) $('studioEvidenceText').textContent=`The pack calls this a ${verdict} (${adjustedText}); use it to choose questions for review, never as proof of historic intent.`;
  if($('studioEvidenceNiah')) $('studioEvidenceNiah').textContent=`${Number(SUMMARY.niah_matches||0).toLocaleString()} NIAH-linked joins`;
  if($('studioEvidenceMap')) $('studioEvidenceMap').textContent=`${Number(SUMMARY.targets||0).toLocaleString()} target footprints in context`;
  const caveats=Array.isArray(INTERPRETATION.caveats)?INTERPRETATION.caveats:[];
  const caveat=caveats[0] || 'Geometry flags are screening evidence, not evidence of design intent.';
  if($('studioCaveat')) $('studioCaveat').textContent=`Evidence caveat: ${caveat} Design outputs remain conceptual test-fits; verify standards and site conditions separately.`;
}
function renderStudio() {
  const equation=DESIGN_EQUATIONS[selectedEquation] || DESIGN_EQUATIONS.phi;
  document.querySelectorAll('.equation-card').forEach(card=>{ const active=card.dataset.equation===selectedEquation; card.classList.toggle('is-active',active); card.setAttribute('aria-selected',String(active)); });
  const typology=DESIGN_TYPOLOGIES[$('typology')?.value || 'parliament'] || DESIGN_TYPOLOGIES.parliament;
  const values=scenarioValues();
  const season=DESIGN_SEASONS[values.season] || DESIGN_SEASONS.midsummer;
  const material=DESIGN_MATERIALS[values.material] || DESIGN_MATERIALS.stone;
  const grammar=DESIGN_GRAMMARS[values.grammar] || DESIGN_GRAMMARS.radial;
  renderStudioReference();
  renderStudioPairReference();
  renderScenarioReadout(values);
  const tags=[...(typology.tags||[]),grammar.label,season.label.split('/')[0].trim(),material.label,`${fmt(values.levels,0)} ${values.levels===1?'level':'levels'}`];
  if($('labCopy')) $('labCopy').innerHTML=`<span class="lab-index">${esc(equation.index)} · ${esc(typology.label)}</span><h3>${esc(equation.title)}</h3><div class="lab-equation">${esc(equation.equation)}</div><p>${esc(equation.description)}</p><div class="lab-move"><b>Spatial translation</b><br/>${esc(equation.move)}<br/><b>Geometry grammar</b><br/>${esc(grammar.description)}<br/><b>Public edge</b><br/>${esc(typology.public)}</div><div class="lab-tags">${tags.map(tag=>`<span>${esc(tag)}</span>`).join('')}</div>`;
  const worship=SIG.find(row=>row.signal==='golden_angle'&&row.group==='worship') || {};
  const government=SIG.find(row=>row.signal==='golden_angle'&&row.group==='government') || {};
  if($('studioSignalRate')) $('studioSignalRate').textContent=Number.isFinite(Number(worship.observed_rate))?`${fmt(worship.observed_rate,2)}%`:'7.09%';
  if($('studioSignalCompare')) $('studioSignalCompare').textContent=Number.isFinite(Number(worship.control_rate))?`vs ${fmt(worship.control_rate,2)}% in controls`:'vs 0.69% in controls';
  if($('studioGovernmentRate')) $('studioGovernmentRate').textContent=Number.isFinite(Number(government.observed_rate))?`${fmt(government.observed_rate,2)}%`:'6.65%';
  if($('studioTargetCount')) $('studioTargetCount').textContent=Number(SUMMARY.targets||33416).toLocaleString();
  renderDesignSpec(typology,season,material,values);
  renderDesignSchedule(typology,values);
  renderSkyField();
  renderWaterField();
  renderDesignBrief();
  renderEvidenceBridge();
  renderDesignDiagram();
  syncStudioState();
}
async function copyStudioLink() {
  const status=$('studioShareStatus');
  syncStudioState(true);
  const url=location.href;
  try {
    if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(url);
    if(status) status.textContent='Studio link copied · equation, place reference, pair relationship and test-fit settings are encoded.';
  } catch(error) {
    if(status) status.textContent='Studio state saved in the address bar · copy the URL manually.';
  }
}
function initStudio() {
  restoreStudioState();
  document.querySelectorAll('.equation-card').forEach(card=>card.addEventListener('click',()=>{ selectedEquation=card.dataset.equation || 'phi'; renderStudio(); }));
  $('typology')?.addEventListener('change',renderStudio);
  $('season')?.addEventListener('change',renderStudio);
  $('material')?.addEventListener('change',renderStudio);
  $('grammar')?.addEventListener('change',renderStudio);
  $('rainEvent')?.addEventListener('change',renderStudio);
  ['moduleScale','courtyardScale','bayCount','pathAngle','publicDensity','windShelter','rainCapture','accessWidth','futurePhases','publicMix','buildingLevels'].forEach(id=>$(id)?.addEventListener('input',renderStudio));
  $('copyBrief')?.addEventListener('click',copyDesignBrief);
  $('downloadBrief')?.addEventListener('click',downloadDesignBrief);
  $('copyStudioLink')?.addEventListener('click',copyStudioLink);
  $('clearStudioReference')?.addEventListener('click',clearStudioReference);
  $('studioUseScale')?.addEventListener('click',useStudioReferenceScale);
  $('clearStudioPairReference')?.addEventListener('click',clearStudioPairReference);
  $('studioUsePairBearing')?.addEventListener('click',useStudioPairBearing);
  $('carryComparisonToStudio')?.addEventListener('click',carryComparisonToStudio);
  renderStudio();
}

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits=1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const medianValue = values => { const sorted=values.filter(value=>Number.isFinite(Number(value))).map(Number).sort((a,b)=>a-b); if(!sorted.length) return NaN; const middle=Math.floor(sorted.length/2); return sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2; };
const pFmt = value => { const p=Number(value); if (!Number.isFinite(p)) return 'n/a'; if (p<1e-4) return '&lt;0.0001'; if (p<.001) return '&lt;0.001'; return p.toFixed(4).replace(/0+$/,'').replace(/\.$/,''); };
const flagsText = row => row.flags.join(', ');
const hasFlag = (row, flag) => row.flags.includes(flag);
const patternLabel = key => PATTERN_BY_KEY.get(key)?.label || String(key||'').replaceAll('_',' ');
const patternNamesText = row => row.flags.map(patternLabel).join(', ');
const CULTURE_LENS_LABELS = {named:'Ainm / named places',heritage:'Oidhreacht / heritage joins',pobal:'Pobal / shared life',civic:'Civic / public institutions'};
const ATLAS_NAV_LABELS = {field:'The Irish field',maths:'Mathematical grammar',studio:'Design studio',culture:'Cultural lens',filters:'Explore targets',evidence:'Evidence and findings'};
let atlasNavFocusTimer=null;
function atlasNavStatusText(key) {
  const base=ATLAS_NAV_LABELS[key]||'Atlas';
  if(key!=='filters') return base;
  const culture=$('cultureLens')?.value, type=$('niahType')?.value, pattern=$('pattern')?.value, century=$('century')?.value;
  if(culture) return `${base} · ${CULTURE_LENS_LABELS[culture]||culture}`;
  if(type) return `${base} · ${heritageTypeLabel(type)}`;
  if(pattern) return `${base} · ${patternLabel(pattern)}`;
  if(century) return `${base} · ${century}`;
  return base;
}
const FIELD_SIGNAL_META = {
  golden_ratio: {
    title: 'Golden ratio / φ',
    detail: 'Aspect-ratio screening compares a footprint’s measured length-to-width relationship with φ ≈ 1.618. It is a geometric screen, not evidence of a builder’s intent.'
  },
  golden_angle: {
    title: 'Golden angle / θ',
    detail: 'Golden-angle screening uses θ = 360° / φ² ≈ 137.5° as an exploratory angular relation. It is a prompt for inspection, not a cultural proof.'
  },
  reflective_symmetry: {
    title: 'Reflective symmetry / mirror axis',
    detail: 'Reflective symmetry compares balance across a candidate axis. The measured footprint still needs source review and a place-based reading.'
  },
  orthogonal: {
    title: 'Orthogonal structure / right angles',
    detail: 'Orthogonal screening looks for near-right-angle edge relationships. Construction constraints can produce this signal without a named mathematical system.'
  }
};
function rowHasSignal(row,key) {
  if(key==='golden_ratio') return Boolean(row.has_golden_ratio||hasFlag(row,key));
  if(key==='golden_angle') return Boolean(row.has_golden_angle||hasFlag(row,key));
  return hasFlag(row,key);
}
function fieldSignalCount(key) {
  const catalogue=PATTERN_BY_KEY.get(key);
  if(catalogue && Number.isFinite(Number(catalogue.count))) return Number(catalogue.count);
  return DATA.filter(row=>rowHasSignal(row,key)).length;
}
function fieldWalkIndexForId(id) {
  return FIELD_WALK.findIndex(item=>String(item?.row?.osm_id||'')===String(id||''));
}
function renderFieldJourney() {
  const track=$('fieldJourneyTrack'), status=$('fieldJourneyStatus'), title=$('fieldJourneyReadoutTitle'), text=$('fieldJourneyReadoutText'), totalValue=$('fieldJourneyTotal'), legValue=$('fieldJourneyLeg'), bearingValue=$('fieldJourneyBearing'), bearingLabel=$('fieldJourneyBearingLabel'), carry=$('fieldJourneyCarry'), actionStatus=$('fieldJourneyActionStatus'), note=$('fieldJourneyNote');
  if(!track) return;
  const items=FIELD_WALK.filter(item=>item?.row?.osm_id), rows=items.map(item=>item.row||{}), selectedIndex=fieldWalkIndexForId(selectedMarkerId), distanceText=value=>Number.isFinite(Number(value))?contextDistanceLabel(value):'n/a', bearingText=value=>Number.isFinite(Number(value))?`${fmt(value,0)}°`:'n/a';
  if(carry) { carry.hidden=true; carry.disabled=true; carry.dataset.carryJourney=''; }
  if(actionStatus) actionStatus.textContent='';
  if(!rows.length) {
    track.innerHTML='<span class="footnote">No coordinate-linked stops are available in this report pack.</span>';
    if(status) status.textContent='Coordinate journey unavailable';
    if(title) title.textContent='The journey field has no stops.';
    if(text) text.textContent='Use the measured catalogue or map to begin a place reading.';
    if(totalValue) totalValue.textContent='n/a';
    if(legValue) legValue.textContent='n/a';
    if(bearingValue) bearingValue.textContent='n/a';
    if(note) note.textContent='No coordinate sequence is inferred when the source rows do not carry usable positions.';
    return;
  }
  const legs=rows.slice(0,-1).map((row,index)=>contextDistanceMeters(row,rows[index+1])), finiteLegs=legs.filter(value=>Number.isFinite(value)), maxLeg=Math.max(1,...finiteLegs), totalDistance=contextDistanceMeters(rows[0],rows[rows.length-1]);
  track.innerHTML=rows.map((row,index)=>{
    const item=items[index], id=String(row.osm_id||''), active=selectedIndex===index, titleText=contextTitle(row), place=selectionPlaceText(row), label=`${item.title||'Field waypoint'} · ${titleText} · ${place}`;
    const node=`<button class="field-journey-node" type="button" data-field-walk-id="${esc(id)}" aria-pressed="${active}" aria-label="${esc(label)}"><span class="field-journey-node-top"><span>${esc(item.step||String(index+1).padStart(2,'0'))} / ${esc(item.symbol||'·')}</span><b aria-hidden="true">${esc(String(index+1).padStart(2,'0'))}</b></span><strong>${esc(titleText)}</strong><small>${esc(place)}</small></button>`;
    if(index>=rows.length-1) return node;
    const leg=legs[index], weight=Number.isFinite(leg)?Math.max(.28,Math.min(1,.32+.68*leg/maxLeg)):.28;
    return `${node}<span class="field-journey-segment" aria-hidden="true"><i style="--journey-weight:${weight.toFixed(2)}"></i><small>${esc(distanceText(leg))}</small></span>`;
  }).join('');
  const current=selectedIndex>=0?rows[selectedIndex]:null, next=selectedIndex>=0?rows[selectedIndex+1]:null, previous=selectedIndex>0?rows[selectedIndex-1]:null, legFrom=current&&next?current:previous, legTo=current&&next?next:current, activeLeg=legFrom&&legTo?contextDistanceMeters(legFrom,legTo):NaN, activeBearing=legFrom&&legTo?comparisonBearingDegrees(legFrom,legTo):NaN, firstToLastBearing=rows.length>1?comparisonBearingDegrees(rows[0],rows[rows.length-1]):NaN;
  if(totalValue) totalValue.textContent=distanceText(totalDistance);
  if(legValue) legValue.textContent=distanceText(activeLeg);
  if(bearingValue) bearingValue.textContent=bearingText(selectedIndex>=0?activeBearing:firstToLastBearing);
  if(bearingLabel) bearingLabel.textContent=selectedIndex>=0?'active leg bearing':'first → last bearing';
  if(selectedIndex<0) {
    if(status) status.textContent=`${rows.length} coordinate-linked stops`;
    if(title) title.textContent='Choose a field stop to set your position.';
    if(text) text.textContent=`${distanceText(totalDistance)} first-to-last span · initial bearing ${bearingText(firstToLastBearing)} · straight-line coordinate reading.`;
  } else {
    const currentItem=items[selectedIndex], currentTitle=contextTitle(current), currentPlace=selectionPlaceText(current);
    if(status) status.textContent=`Stop ${String(selectedIndex+1).padStart(2,'0')} / ${String(rows.length).padStart(2,'0')} · ${currentItem.title||'field waypoint'}`;
    if(title) title.textContent=`${currentTitle}${currentPlace&&currentPlace!=='Context not reported'?` · ${currentPlace}`:''}`;
    if(text) text.textContent=next?`Next: ${contextTitle(next)} · ${distanceText(activeLeg)} straight-line · bearing ${bearingText(activeBearing)} from this coordinate.`:previous?`Final stop · from ${contextTitle(previous)} · ${distanceText(activeLeg)} straight-line · bearing ${bearingText(activeBearing)}.`:'The first stop sets the coordinate origin for the journey field.';
    if(carry&&(next||previous)) { carry.hidden=false; carry.disabled=false; carry.dataset.carryJourney='active'; }
  }
  if(note) note.textContent=`${rows.length} curated source rows · ${distanceText(totalDistance)} first-to-last span. Distances use source coordinates; no walking route, road itinerary, or historic journey is inferred.`;
}
function carryJourneyToStudio() {
  const selectedIndex=fieldWalkIndexForId(selectedMarkerId), rows=FIELD_WALK.filter(item=>item?.row?.osm_id).map(item=>item.row||{}), status=$('fieldJourneyActionStatus');
  if(selectedIndex<0||selectedIndex>=rows.length) { if(status) status.textContent='Choose a journey stop before carrying its active leg.'; return; }
  const current=rows[selectedIndex], next=rows[selectedIndex+1], previous=rows[selectedIndex-1], from=next?current:previous, to=next||current;
  if(!from||!to||String(from.osm_id||'')===String(to.osm_id||'')) { if(status) status.textContent='The active leg needs two coordinate-linked stops.'; return; }
  studioPairData=[from,to];
  studioPairIds=[String(from.osm_id||''),String(to.osm_id||'')];
  syncStudioState(true); renderStudioPairReference(); renderStudio(); setAtlasNavActive('studio');
  if(status) status.textContent='Active leg carried to the studio · controls remain editable.';
  window.setTimeout(()=>{
    $('studio')?.scrollIntoView({behavior:'smooth',block:'start'});
    revealPanelTarget($('studioPairReference'));
  },120);
}
function moveFieldWalk(delta) {
  if(!FIELD_WALK.length) return;
  const current=fieldWalkIndexForId(selectedMarkerId);
  const targetIndex=current<0 ? (delta>0?0:FIELD_WALK.length-1) : current+delta;
  if(targetIndex<0||targetIndex>=FIELD_WALK.length) return;
  const id=String(FIELD_WALK[targetIndex]?.row?.osm_id||'');
  if(id) focusRow(id,{scroll:true,openPopup:true});
}
function fieldWalkShapeSvg(row) {
  const width=150, height=72, pad=10, feature=GEOJSON_BY_ID.get(String(row?.osm_id||'')), ring=geometryOuterRing(feature), points=ring.filter(pair=>Array.isArray(pair)&&pair.length>=2&&Number.isFinite(Number(pair[0]))&&Number.isFinite(Number(pair[1]))).map(pair=>[Number(pair[0]),Number(pair[1])]);
  let path='', source='descriptor guide', detail='aspect only';
  if(points.length>=3) {
    const xs=points.map(pair=>pair[0]), ys=points.map(pair=>pair[1]), minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys), rangeX=Math.max(1e-12,maxX-minX), rangeY=Math.max(1e-12,maxY-minY), scale=Math.min((width-pad*2)/rangeX,(height-pad*2)/rangeY), usedW=rangeX*scale, usedH=rangeY*scale, left=(width-usedW)/2, top=(height-usedH)/2;
    path=points.map((pair,index)=>`${index?'L':'M'} ${(left+(pair[0]-minX)*scale).toFixed(1)} ${(top+(maxY-pair[1])*scale).toFixed(1)}`).join(' ')+' Z';
    source='mapped outline'; detail=`${Math.max(0,points.length-1)} vertices`;
  } else {
    const aspect=Math.max(.3,Math.min(3.6,Number(row?.aspect_ratio)||1)), boxW=aspect>=1?Math.min(width*.72,38+aspect*20):Math.max(28,48*aspect), boxH=aspect>=1?Math.max(26,42/aspect):Math.min(52,42/aspect), left=(width-boxW)/2, top=(height-boxH)/2;
    path=`M ${left.toFixed(1)} ${top.toFixed(1)} H ${(left+boxW).toFixed(1)} V ${(top+boxH).toFixed(1)} H ${left.toFixed(1)} Z`;
  }
  const cx=(width/2).toFixed(1), cy=(height/2).toFixed(1);
  return {source,detail,svg:`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><path d="M ${pad} ${cy} H ${width-pad} M ${cx} ${pad} V ${height-pad}" fill="none" stroke="#e1bd66" stroke-opacity=".22" stroke-width=".8" stroke-dasharray="3 4"/><path d="${path}" fill="#6d9b8f" fill-opacity=".42" stroke="#e1bd66" stroke-width="1.7"/><circle cx="${cx}" cy="${cy}" r="3" fill="#e1bd66" stroke="#103537" stroke-width="1"/></svg>`};
}
function renderFieldWalk() {
  const grid=$('fieldWalkGrid'), count=$('fieldWalkCount'), note=$('fieldWalkNote'), controls=$('fieldWalkControls'), previous=$('fieldWalkPrevious'), next=$('fieldWalkNext'), progress=$('fieldWalkProgress');
  if(!grid) return;
  renderFieldJourney();
  if(count) count.textContent=FIELD_WALK.length.toLocaleString();
  if(!FIELD_WALK.length) {
    grid.innerHTML='<p class="footnote">No curated waypoints are available in this report pack; use the signal cards or map to begin.</p>';
    if(controls) controls.hidden=true;
    if(note) note.textContent='The walk needs at least one named target in the current snapshot. The measured catalogue remains available below.';
    return;
  }
  grid.innerHTML=FIELD_WALK.map(item=>{
    const row=item.row||{}, id=String(row.osm_id||''), active=selectedMarkerId===id, title=contextTitle(row), place=selectionPlaceText(row), signals=contextSignalText(row), source=row.niah?.reg_no?`NIAH ${row.niah.reg_no}`:`${spatialGroupLabel(row.group)} record`, label=`${item.title||'Field waypoint'} · ${title} · ${place}`;
    const shape=fieldWalkShapeSvg(row);
    return `<button class="field-walk-stop${active?' is-active':''}" type="button" data-field-walk-id="${esc(id)}" aria-pressed="${active}" aria-label="${esc(label)}"><span class="field-walk-stop-top"><span>${esc(item.step||'—')} / ${esc(item.eyebrow||'field waypoint')}</span><b aria-hidden="true">${esc(item.symbol||'·')}</b></span><div class="field-walk-shape" role="img" aria-label="${esc(`${shape.source}; ${shape.detail}`)}">${shape.svg}<span aria-hidden="true">${esc(shape.source)}<br/>${esc(shape.detail)}</span></div><h4>${esc(item.title||'Field waypoint')}</h4><p>${esc(item.description||'Open this measured place to read its source and geometry.')}</p><div class="field-walk-record"><strong>${esc(title)}</strong><small>${esc(place)} · ${esc(source)}</small></div><div class="field-walk-metrics"><span class="field-walk-metric"><small>score</small><b>${fmt(row.score)}</b></span><span class="field-walk-metric"><small>signals</small><b>${esc(signals)}</b></span><span class="field-walk-metric"><small>area</small><b>${fmt(row.area_m2,0)} m²</b></span><span class="field-walk-metric"><small>aspect</small><b>r ${fmt(row.aspect_ratio,2)}</b></span></div><span class="field-walk-action">${active?'Current dossier ✓':'Visit dossier →'}</span></button>`;
  }).join('');
  const activeIndex=fieldWalkIndexForId(selectedMarkerId), activeItem=activeIndex>=0?FIELD_WALK[activeIndex]:null;
  if(controls) controls.hidden=activeIndex<0;
  if(previous) { previous.disabled=activeIndex<=0; previous.title=activeIndex>0?`Previous: ${FIELD_WALK[activeIndex-1].title||'field stop'}`:'This is the first stop'; }
  if(next) { next.disabled=activeIndex<0||activeIndex>=FIELD_WALK.length-1; next.title=activeIndex>=0&&activeIndex< FIELD_WALK.length-1?`Next: ${FIELD_WALK[activeIndex+1].title||'field stop'}`:'This is the final stop'; }
  if(progress) progress.textContent=activeItem?`Stop ${String(activeIndex+1).padStart(2,'0')} / ${String(FIELD_WALK.length).padStart(2,'0')} · ${activeItem.title||'field waypoint'}`:'Choose a stop to begin';
  if(note) note.textContent=activeItem
    ? `Current stop: ${activeItem.title}. The dossier keeps measured geometry, heritage context and review boundaries separate; use the sequence rail to continue the walk.`
    : 'The walk is a reproducible starting sample, not a ranking of Irish buildings or evidence of historic mathematical intention.';
}
function renderFieldCoordinate(row) {
  const plot=$('coordinatePlot'), readout=$('coordinatePlotReadout'), note=$('coordinateNote');
  if(!plot) return;
  renderFieldLight(row);
  renderFieldShape(row);
  renderFieldRoots(row);
  const lat=Number(row?.lat), lon=Number(row?.lon), focused=Number.isFinite(lat)&&Number.isFinite(lon);
  const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
  if(focused) {
    const x=clamp((lon+10.7)/5.4*100,6,94), y=100-clamp((lat-51.3)/4.2*100,6,94), coordinate=`${coordinateLabel(lat,'N','S')} / ${coordinateLabel(lon,'E','W')}`;
    plot.style.setProperty('--coordinate-x',`${x.toFixed(2)}%`);
    plot.style.setProperty('--coordinate-y',`${y.toFixed(2)}%`);
    plot.classList.add('is-focused');
    if(readout) readout.textContent=`${contextTitle(row)} · ${coordinate}`;
    if(note) note.textContent=`Selected footprint · ${coordinate}. The live map carries the same point into source imagery; the descriptor remains situated in county, road and place context.`;
    plot.setAttribute('aria-label',`Selected coordinate ${coordinate}`);
    return;
  }
  plot.style.setProperty('--coordinate-x','59%');
  plot.style.setProperty('--coordinate-y','14%');
  plot.classList.remove('is-focused');
  if(readout) readout.textContent='Ireland field centre';
  if(note) note.textContent='A schematic WGS84 field for the current report pack: latitude rises vertically and longitude runs west to east. The live map carries the actual points; every measurement stays situated.';
  plot.setAttribute('aria-label','Ireland field coordinate marker with latitude and longitude axes');
}
function renderFieldAtlas() {
  const total=Math.max(1,Number(SUMMARY.targets||DATA.length||0));
  const activePattern=$('pattern')?.value||'';
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const setSignal=(key,countId,textId,meterId,label)=>{
    const count=fieldSignalCount(key), rate=count/total*100;
    set(countId,count.toLocaleString());
    set(textId,`${fmt(rate,2)}% of target rows carry this ${label} screening flag.`);
    const meter=$(meterId);
    if(meter) meter.style.width=`${Math.max(2,Math.min(100,rate))}%`;
  };
  set('heroTargetCount',Number(SUMMARY.targets||DATA.length||0).toLocaleString());
  set('heroNiahCount',Number(SUMMARY.niah_matches||0).toLocaleString());
  set('heroSignalCount',PATTERN_CATALOG.filter(item=>Number(item.count||0)>0).length.toLocaleString());
  set('introTargetCount',Number(SUMMARY.targets||DATA.length||0).toLocaleString());
  set('introNiahCount',Number(SUMMARY.niah_matches||0).toLocaleString());
  set('introSignalCount',PATTERN_CATALOG.filter(item=>Number(item.count||0)>0).length.toLocaleString());
  renderFieldCoordinate(targetRowForId(selectedMarkerId||offlineSelection));
  setSignal('golden_ratio','fieldRatioCount','fieldRatioText','fieldRatioMeter','proportion');
  setSignal('golden_angle','fieldAngleCount','fieldAngleText','fieldAngleMeter','rotation');
  setSignal('reflective_symmetry','fieldSymmetryCount','fieldSymmetryText','fieldSymmetryMeter','symmetry');
  setSignal('orthogonal','fieldOrthogonalCount','fieldOrthogonalText','fieldOrthogonalMeter','order');
  document.querySelectorAll('[data-field-signal]').forEach(button=>{
    const active=button.dataset.fieldSignal===activePattern;
    button.classList.toggle('is-active',active);
    button.setAttribute('aria-pressed',String(active));
  });
  document.querySelectorAll('[data-field-principle]').forEach(button=>{
    const key=button.dataset.fieldPrinciple, active=key!=='form'&&key===$('cultureLens')?.value;
    button.classList.toggle('is-active',active);
    button.setAttribute('aria-pressed',String(active));
  });
  const meta=FIELD_SIGNAL_META[activePattern];
  set('fieldSignalDetailTitle',meta?.title||'Choose a signal to trace it.');
  set('fieldSignalDetailText',meta?.detail||'Select a mathematical signal to filter the building footprints, focus the map, and carry the question into the heritage and culture layers below.');
}
function renderMathsIndex() {
  const index=$('mathsIndex');
  if(!index) return;
  const activePattern=$('pattern')?.value||'';
  const total=Math.max(1,Number(SUMMARY.targets||DATA.length||0));
  index.innerHTML=MATHS_INDEX.map((item,indexNumber)=>{
    const catalogue=item.pattern?PATTERN_BY_KEY.get(item.pattern):null;
    const count=catalogue && Number.isFinite(Number(catalogue.count)) ? Number(catalogue.count) : null;
    const meta=count===null ? 'Measured descriptor · all footprint rows' : `${count.toLocaleString()} screens · ${fmt(count/total*100,1)}% of targets`;
    const active=Boolean(item.pattern)&&item.pattern===activePattern;
    const action=item.pattern?'Trace this signal →':'Read this descriptor →';
    return `<button class="maths-card" type="button" data-math-key="${esc(item.key)}" aria-pressed="${active}"><span class="maths-card-top"><span>${String(indexNumber+1).padStart(2,'0')} / ${esc(item.category)}</span><small>${item.pattern?'screen':'descriptor'}</small></span><span class="maths-card-symbol" aria-hidden="true">${esc(item.symbol)}</span><h3>${esc(item.title)}</h3><div class="maths-card-equation">${esc(item.equation)}</div><p>${esc(item.description)}</p><span class="maths-card-meta">${meta}</span><span class="maths-card-action">${action}</span></button>`;
  }).join('');
  const active=MATHS_INDEX.find(item=>item.pattern===activePattern);
  const readoutTitle=active?.title||'Choose a property to trace it.';
  const readoutText=active ? `${active.description} ${active.pattern?`The current atlas filter is ${active.pattern}; follow the trace into the pattern catalogue and map.`:'The descriptor is available in each selected building’s geometry dossier and measured target row.'}` : 'Each card connects a named mathematical idea to a measured descriptor or screening flag in this report.';
  const title=$('mathsReadoutTitle'), text=$('mathsReadoutText');
  if(title) title.textContent=readoutTitle;
  if(text) text.textContent=readoutText;
  renderMathsReadingContext();
}
function renderMathsReadingContext() {
  const panel=$('mathsReadingContext');
  if(!panel) return;
  const id=selectedMarkerId||offlineSelection, row=targetRowForId(id);
  if(!row) { panel.hidden=true; return; }
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const area=Number(row.area_m2), length=Number(row.length_m), width=Number(row.width_m), aspect=Number(row.aspect_ratio), circularity=Number(row.circularity), rectangularity=Number(row.rectangularity), radial=Number(row.radial_cv);
  const metrics=[
    Number.isFinite(area)?`A ${fmt(area,0)} m²`:'',
    Number.isFinite(length)&&Number.isFinite(width)?`l×w ${fmt(length,1)} × ${fmt(width,1)} m`:'',
    Number.isFinite(aspect)?`r ${fmt(aspect,3)}`:'',
    Number.isFinite(circularity)?`C ${fmt(circularity,3)}`:'',
    Number.isFinite(rectangularity)?`R ${fmt(rectangularity,3)}`:'',
    Number.isFinite(radial)?`σᵣ/μᵣ ${fmt(radial,3)}`:''
  ].filter(Boolean).join(' · ');
  const flags=(row.flags||[]).slice(0,3).map(patternLabel), place=selectionPlaceText(row), placeText=place&&place!=='Context not reported'?`${place} · `:'';
  set('mathsReadingContextTitle',contextTitle(row));
  set('mathsReadingContextText',`${placeText}${metrics||'Measured descriptors are not reported'}.${flags.length?` Screens: ${flags.join(' · ')}.`:''} Trace the descriptors below; screening flags remain exploratory and do not establish historic intent.`);
  panel.hidden=false;
}
function selectMathCard(key) {
  const item=MATHS_INDEX.find(candidate=>candidate.key===key);
  if(!item) return;
  if(item.pattern && $('pattern')) {
    $('pattern').value=item.pattern;
    if($('cultureLens')) $('cultureLens').value='';
    setAtlasNavActive('filters');
    applyFilters();
    renderMathsIndex();
    window.setTimeout(()=>{
      $('patterns')?.scrollIntoView({behavior:'smooth',block:'start'});
      fitMapToResults();
    },120);
    return;
  }
  setAtlasNavActive('filters');
  renderMathsIndex();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function selectFieldSignal(key) {
  const pattern=$('pattern');
  if(!pattern || ![...pattern.options].some(option=>option.value===key)) return;
  pattern.value=key;
  if($('cultureLens')) $('cultureLens').value='';
  setAtlasNavActive('filters');
  applyFilters();
  renderFieldAtlas();
  window.setTimeout(()=>{
    $('patterns')?.scrollIntoView({behavior:'smooth',block:'start'});
    fitMapToResults();
  },120);
}
function selectFieldPrinciple(key) {
  if(key==='form') { focusAtlasSection('maths'); return; }
  if(['named','heritage','pobal'].includes(key)) setCultureFilter(key);
}
function initFieldAtlas() {
  document.querySelectorAll('[data-field-signal]').forEach(button=>button.addEventListener('click',()=>selectFieldSignal(button.dataset.fieldSignal)));
  renderFieldAtlas();
  renderFieldWalk();
  renderMathsIndex();
}
function dismissSiteIntro(remember=true,focusField=false) {
  const intro=$('siteIntro');
  if(!intro) return;
  intro.classList.add('is-dismissed');
  document.body.classList.remove('intro-open');
  if(remember) { try { sessionStorage.setItem('cruth-intro-seen','1'); } catch(error) {} }
  window.setTimeout(()=>{ intro.hidden=true; },760);
  if(focusField) window.setTimeout(()=>$('field')?.scrollIntoView({behavior:'smooth',block:'start'}),180);
}
function showSiteIntro() {
  const intro=$('siteIntro');
  if(!intro) return;
  intro.hidden=false;
  intro.classList.remove('is-dismissed');
  document.body.classList.add('intro-open');
}
function initSiteIntro() {
  const intro=$('siteIntro');
  if(!intro) return;
  let seen=false;
  try { seen=sessionStorage.getItem('cruth-intro-seen')==='1'; } catch(error) {}
  if(seen) { intro.hidden=true; document.body.classList.remove('intro-open'); }
  $('enterAtlas')?.addEventListener('click',()=>dismissSiteIntro(true,true));
  $('skipIntro')?.addEventListener('click',()=>dismissSiteIntro(true,false));
  $('replayIntro')?.addEventListener('click',()=>showSiteIntro());
  intro.addEventListener('click',event=>{ if(event.target===intro) dismissSiteIntro(true,false); });
  intro.addEventListener('keydown',event=>{ if(event.key==='Escape') dismissSiteIntro(true,false); });
  intro.addEventListener('pointermove',event=>{ const rect=intro.getBoundingClientRect(); const x=(event.clientX-rect.left)/rect.width-.5; const y=(event.clientY-rect.top)/rect.height-.5; intro.style.setProperty('--intro-x',x.toFixed(3)); intro.style.setProperty('--intro-y',y.toFixed(3)); });
}
function culturalLensMatches(row,lens) {
  if(lens==='named') return row.spatial?.settlement_class==='named_place';
  if(lens==='heritage') return Boolean(row.niah?.reg_no);
  if(lens==='pobal') return ['worship','government','civic'].includes(row.group);
  if(lens==='civic') return ['government','civic'].includes(row.group);
  return true;
}
const unique = key => [...new Set(DATA.map(row => key(row)).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));

function fillSelect(id, values) { for (const value of values) { const option=document.createElement('option'); option.value=value; option.textContent=value; $(id).appendChild(option); } }
function fillPatternSelect() { for (const item of PATTERN_CATALOG) { const option=document.createElement('option'); option.value=item.key; option.textContent=`${item.label} (${Number(item.count||0).toLocaleString()})`; $('pattern').appendChild(option); } }
const FILTER_OPTIONS = PACK.filter_options || {};
fillSelect('group', FILTER_OPTIONS.group || unique(row=>row.group));
fillSelect('century', FILTER_OPTIONS.century || unique(row=>row.niah.century));
fillSelect('rating', FILTER_OPTIONS.rating || unique(row=>row.niah.rating));
fillSelect('niahType', FILTER_OPTIONS.type || unique(row=>row.niah.type));
fillSelect('county', FILTER_OPTIONS.county || unique(row=>row.spatial?.county||row.niah?.county));
fillPatternSelect();

const VIEW_SELECTS = [['group','group'],['century','century'],['rating','rating'],['niahType','type'],['county','county'],['pattern','pattern'],['reviewState','review'],['cultureLens','culture']];
const VIEW_CHECKS = [['onlyAngle','angle'],['onlyRatio','ratio'],['onlyCircular','circular'],['onlyMulti','multi']];
const ROUTE_STATE_TEXT = [['route_start_lat','routeStartLat'],['route_start_lon','routeStartLon'],['route_goal_lat','routeGoalLat'],['route_goal_lon','routeGoalLon'],['route_speed','routeSpeed'],['route_weight','routeWeight'],['route_rating','routeRating'],['route_height','routeHeight'],['route_width','routeWidth'],['route_length','routeLength'],['route_axleload','routeAxleload'],['route_departure','routeDeparture']];
const ROUTE_STATE_SELECTS = [['route_vehicle_class','routeVehicleClass'],['route_objective','routeObjective'],['route_response','routeFormat']];
const ROUTE_STATE_CHECKS = [['route_path','routeIncludePath'],['route_ferries','routeIncludeFerries'],['route_hgv_destination','routeAllowHgvDestination']];
const ROUTE_STATE_KEYS = ['route',...ROUTE_STATE_TEXT.map(([key])=>key),...ROUTE_STATE_SELECTS.map(([key])=>key),...ROUTE_STATE_CHECKS.map(([key])=>key)];
const ROUTE_COMPARE_STATE_KEYS = ['compare','compare_start_lat','compare_start_lon','compare_goal_lat','compare_goal_lon','compare_speed','compare_departure','compare_objective','compare_profiles','compare_path','compare_ferries'];
const ROUTE_MATRIX_STATE_KEYS = ['matrix','matrix_origins','matrix_destinations','matrix_speed','matrix_departure','matrix_objective','matrix_weight','matrix_rating','matrix_height','matrix_width','matrix_length','matrix_axleload','matrix_vehicle_class','matrix_hgv_destination','matrix_path','matrix_ferries'];
const STUDIO_STATE_KEYS = ['studio','studio_ref','studio_pair_a','studio_pair_b','studio_equation','studio_typology','studio_season','studio_material','studio_grammar','studio_module','studio_courtyard','studio_bays','studio_angle','studio_density','studio_wind','studio_rain','studio_rain_event','studio_access','studio_phases','studio_public_mix','studio_levels'];
const SORT_KEYS = new Set(['name','group','area_m2','score','flags']);
function restoreViewState() {
  const params=new URLSearchParams(location.search);
  if(params.has('q')) $('query').value=params.get('q');
  const focus=params.get('focus');
  if(focus && SERVER_MODE && !params.has('q')) $('query').value=focus;
  for(const [id,key] of VIEW_SELECTS) { const value=params.get(key); if(value!==null && [...$(id).options].some(option=>option.value===value)) $(id).value=value; }
  if(params.has('score')) { const value=Number(params.get('score')); if(Number.isFinite(value)) $('score').value=String(Math.max(0,Math.min(100,Math.round(value)))); }
  for(const [id,key] of VIEW_CHECKS) $(id).checked=params.get(key)==='1';
  const requestedSort=params.get('sort'); if(requestedSort && SORT_KEYS.has(requestedSort)) sortKey=requestedSort;
  sortDesc=params.get('desc') !== '0';
  $('scoreValue').textContent=$('score').value;
  restoreComparisonState();
}
function restoreComparisonState() {
  const params=new URLSearchParams(location.search);
  const requested=[...new Set([params.get('compare_a'),params.get('compare_b')].filter(Boolean))];
  const restored=[];
  requested.forEach(id=>{
    const row=DATA.find(item=>String(item.osm_id)===String(id));
    if(row && !restored.some(item=>String(item.osm_id)===String(row.osm_id))) restored.push(row);
  });
  comparisonData=restored.slice(0,2);
  renderComparisonTray();
  const missing=requested.filter(id=>!restored.some(item=>String(item.osm_id)===String(id)));
  const status=$('comparisonShareStatus');
  if(status) status.textContent=missing.length
    ? `Comparison link found ${restored.length}/${requested.length} place${requested.length===1?'':'s'} in the current view · select the missing place${missing.length===1?'':'s'} again to complete it.`
    : '';
}
function restoreStudioState() {
  const params=new URLSearchParams(location.search), hasState=params.get('studio')==='1'||STUDIO_STATE_KEYS.some(key=>key!=='studio'&&params.has(key));
  if(!hasState) return;
  const equation=params.get('studio_equation');
  if(equation && Object.prototype.hasOwnProperty.call(DESIGN_EQUATIONS,equation)) selectedEquation=equation;
  const setSelect=(key,id,values)=>{ const value=params.get(key); if(value&&$(id)&&values.includes(value)) $(id).value=value; };
  setSelect('studio_typology','typology',Object.keys(DESIGN_TYPOLOGIES));
  setSelect('studio_season','season',Object.keys(DESIGN_SEASONS));
  setSelect('studio_material','material',Object.keys(DESIGN_MATERIALS));
  setSelect('studio_grammar','grammar',Object.keys(DESIGN_GRAMMARS));
  setSelect('studio_rain_event','rainEvent',Object.keys(RAIN_EVENTS));
  const setRange=(key,id)=>{ const input=$(id), value=Number(params.get(key)); if(!input||!Number.isFinite(value)) return; const min=Number(input.min), max=Number(input.max); input.value=String(Math.max(Number.isFinite(min)?min:value,Math.min(Number.isFinite(max)?max:value,value))); };
  [['studio_module','moduleScale'],['studio_courtyard','courtyardScale'],['studio_bays','bayCount'],['studio_angle','pathAngle'],['studio_density','publicDensity'],['studio_wind','windShelter'],['studio_rain','rainCapture'],['studio_access','accessWidth'],['studio_phases','futurePhases'],['studio_public_mix','publicMix'],['studio_levels','buildingLevels']].forEach(([key,id])=>setRange(key,id));
  studioReferenceId=params.get('studio_ref')||'';
  if(studioReferenceId) {
    const row=targetRowForId(studioReferenceId);
    if(row) studioReferenceData=row;
    else if($('studioShareStatus')) $('studioShareStatus').textContent='Studio settings restored · the field reference is not in the current data view.';
  }
  studioPairIds=[params.get('studio_pair_a'),params.get('studio_pair_b')].filter(Boolean).slice(0,2);
  const pairRows=studioPairIds.map(id=>DATA.find(item=>String(item.osm_id)===String(id))).filter(Boolean);
  if(pairRows.length===2) studioPairData=pairRows;
  else if(studioPairIds.length===2 && $('studioShareStatus')) $('studioShareStatus').textContent='Studio settings restored · the paired relationship is not fully in the current data view.';
}
function restoreRouteState() {
  const params=new URLSearchParams(location.search);
  for(const [key,id] of ROUTE_STATE_TEXT) if(params.has(key)) $(id).value=params.get(key);
  for(const [key,id] of ROUTE_STATE_SELECTS) {
    const value=params.get(key);
    if(value!==null && [...$(id).options].some(option=>option.value===value)) $(id).value=value;
  }
  for(const [key,id] of ROUTE_STATE_CHECKS) if(params.has(key)) $(id).checked=params.get(key)==='1';
  return params.get('route')==='1' && Boolean($('routeStartLat').value.trim()&&$('routeStartLon').value.trim()&&$('routeGoalLat').value.trim()&&$('routeGoalLon').value.trim());
}
function syncRouteState(fields) {
  const params=new URLSearchParams(location.search);
  ROUTE_STATE_KEYS.forEach(key=>params.delete(key));
  const set=(key,value,defaultValue='')=>{ if(value!==undefined&&value!==null&&String(value)!==''&&String(value)!==defaultValue) params.set(key,String(value)); };
  set('route_start_lat',fields.start_lat); set('route_start_lon',fields.start_lon); set('route_goal_lat',fields.goal_lat); set('route_goal_lon',fields.goal_lon);
  set('route_speed',fields.speed_kmh,'50'); set('route_weight',fields.weight_t); set('route_rating',fields.rating_t); set('route_height',fields.height_m); set('route_width',fields.width_m); set('route_length',fields.length_m); set('route_axleload',fields.axleload_t); set('route_departure',fields.departure);
  set('route_vehicle_class',fields.vehicle_class,'general'); set('route_objective',fields.objective,'distance'); set('route_response',fields.format,'json');
  if(fields.include_path==='1') params.set('route_path','1'); else params.set('route_path','0');
  if(fields.include_ferries==='1') params.set('route_ferries','1');
  if(fields.allow_hgv_destination==='1') params.set('route_hgv_destination','1');
  params.set('route','1');
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}
function restoreRouteComparisonState() {
  const params=new URLSearchParams(location.search), profiles=params.get('compare_profiles');
  [['compare_start_lat','routeStartLat'],['compare_start_lon','routeStartLon'],['compare_goal_lat','routeGoalLat'],['compare_goal_lon','routeGoalLon'],['compare_speed','routeSpeed'],['compare_departure','routeDeparture']].forEach(([key,id])=>{ if(params.has(key)) $(id).value=params.get(key); });
  if(params.has('compare_objective') && [...$('routeObjective').options].some(option=>option.value===params.get('compare_objective'))) $('routeObjective').value=params.get('compare_objective');
  if(profiles!==null) $('routeCompareProfiles').value=profiles;
  if(params.has('compare_path')) $('routeCompareIncludePath').checked=params.get('compare_path')==='1';
  if(params.has('compare_ferries')) $('routeCompareIncludeFerries').checked=params.get('compare_ferries')==='1';
  const lines=$('routeCompareProfiles').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
  return params.get('compare')==='1' && lines.length>=2 && lines.length<=8 && Boolean($('routeStartLat').value.trim()&&$('routeStartLon').value.trim()&&$('routeGoalLat').value.trim()&&$('routeGoalLon').value.trim());
}
function syncRouteComparisonState(fields) {
  const params=new URLSearchParams(location.search);
  ROUTE_COMPARE_STATE_KEYS.forEach(key=>params.delete(key));
  params.set('compare','1');
  params.set('compare_start_lat',fields.start_lat); params.set('compare_start_lon',fields.start_lon); params.set('compare_goal_lat',fields.goal_lat); params.set('compare_goal_lon',fields.goal_lon);
  if(fields.speed_kmh) params.set('compare_speed',fields.speed_kmh);
  if(fields.departure) params.set('compare_departure',fields.departure);
  if(fields.objective) params.set('compare_objective',fields.objective);
  params.set('compare_profiles',fields.profiles.join('\n'));
  params.set('compare_path',fields.include_path==='1'?'1':'0');
  if(fields.include_ferries==='1') params.set('compare_ferries','1');
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}
function restoreRouteMatrixState() {
  const params=new URLSearchParams(location.search), origins=params.get('matrix_origins'), destinations=params.get('matrix_destinations');
  if(origins!==null) $('routeMatrixOrigins').value=origins;
  if(destinations!==null) $('routeMatrixDestinations').value=destinations;
  const textFields=[['matrix_speed','routeSpeed'],['matrix_departure','routeDeparture'],['matrix_weight','routeWeight'],['matrix_rating','routeRating'],['matrix_height','routeHeight'],['matrix_width','routeWidth'],['matrix_length','routeLength'],['matrix_axleload','routeAxleload']];
  textFields.forEach(([key,id])=>{ if(params.has(key)) $(id).value=params.get(key); });
  if(params.has('matrix_objective') && [...$('routeObjective').options].some(option=>option.value===params.get('matrix_objective'))) $('routeObjective').value=params.get('matrix_objective');
  if(params.has('matrix_vehicle_class') && [...$('routeVehicleClass').options].some(option=>option.value===params.get('matrix_vehicle_class'))) $('routeVehicleClass').value=params.get('matrix_vehicle_class');
  if(params.has('matrix_hgv_destination')) $('routeAllowHgvDestination').checked=params.get('matrix_hgv_destination')==='1';
  if(params.has('matrix_path')) $('routeMatrixIncludePath').checked=params.get('matrix_path')==='1';
  if(params.has('matrix_ferries')) $('routeMatrixIncludeFerries').checked=params.get('matrix_ferries')==='1';
  const originLines=$('routeMatrixOrigins').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean), destinationLines=$('routeMatrixDestinations').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
  const firstOrigin=originLines[0]?.split(',').map(value=>value.trim()), firstDestination=destinationLines[0]?.split(',').map(value=>value.trim());
  if(firstOrigin?.length===2) { $('routeStartLat').value=firstOrigin[0]; $('routeStartLon').value=firstOrigin[1]; }
  if(firstDestination?.length===2) { $('routeGoalLat').value=firstDestination[0]; $('routeGoalLon').value=firstDestination[1]; }
  return params.get('matrix')==='1' && originLines.length>=1 && destinationLines.length>=1 && originLines.length*destinationLines.length<=25;
}
function syncRouteMatrixState(fields) {
  const params=new URLSearchParams(location.search);
  ROUTE_MATRIX_STATE_KEYS.forEach(key=>params.delete(key));
  const set=(key,value)=>{ if(value!==undefined&&value!==null&&String(value)!=='') params.set(key,String(value)); };
  params.set('matrix','1'); set('matrix_origins',fields.origins.join('\n')); set('matrix_destinations',fields.destinations.join('\n'));
  set('matrix_speed',fields.speed_kmh); set('matrix_departure',fields.departure); set('matrix_objective',fields.objective); set('matrix_weight',fields.weight_t); set('matrix_rating',fields.rating_t); set('matrix_height',fields.height_m); set('matrix_width',fields.width_m); set('matrix_length',fields.length_m); set('matrix_axleload',fields.axleload_t); set('matrix_vehicle_class',fields.vehicle_class); set('matrix_hgv_destination',fields.allow_hgv_destination==='1'?'1':'0'); set('matrix_path',fields.include_path==='1'?'1':'0');
  if(fields.include_ferries==='1') params.set('matrix_ferries','1');
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}
function syncViewState() {
  const params=new URLSearchParams(location.search);
  const update=(key,value,defaultValue='')=>{ if(value && value!==defaultValue) params.set(key,value); else params.delete(key); };
  update('q',$('query').value.trim());
  for(const [id,key] of VIEW_SELECTS) update(key,$(id).value);
  update('score',Number($('score').value)>0?String(Number($('score').value)):'');
  for(const [id,key] of VIEW_CHECKS) { if($(id).checked) params.set(key,'1'); else params.delete(key); }
  if(sortKey!=='score') params.set('sort',sortKey); else params.delete('sort');
  if(!sortDesc) params.set('desc','0'); else params.delete('desc');
  params.delete('compare_a'); params.delete('compare_b');
  const compareRows=comparisonData.filter(row=>row&&row.osm_id).slice(0,2);
  if(compareRows[0]) params.set('compare_a',String(compareRows[0].osm_id));
  if(compareRows[1]) params.set('compare_b',String(compareRows[1].osm_id));
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}
function syncStudioState(force=false) {
  const params=new URLSearchParams(location.search), hadStudio=params.get('studio')==='1';
  STUDIO_STATE_KEYS.forEach(key=>params.delete(key));
  const values=scenarioValues(), typology=$('typology')?.value||'parliament', season=$('season')?.value||'midsummer', material=$('material')?.value||'stone', grammar=$('grammar')?.value||'radial', rainEvent=values.rainEvent||'design', referenceId=String(studioReferenceData?.osm_id||studioReferenceId||''), pairRows=studioPairData.filter(row=>row&&row.osm_id).slice(0,2), pairA=String(pairRows[0]?.osm_id||studioPairIds[0]||''), pairB=String(pairRows[1]?.osm_id||studioPairIds[1]||'');
  const active=force||hadStudio||Boolean(referenceId)||Boolean(pairA&&pairB)||selectedEquation!=='phi'||typology!=='parliament'||season!=='midsummer'||material!=='stone'||grammar!=='radial'||rainEvent!=='design'||values.module!==13||values.courtyard!==38||values.bays!==8||values.angle!==137.5||values.density!==60||values.wind!==64||values.rain!==72||values.accessWidth!==1.8||values.phases!==2||values.publicMix!==70||values.levels!==2;
  if(active) {
    params.set('studio','1');
    if(referenceId) params.set('studio_ref',referenceId);
    if(pairA&&pairB) { params.set('studio_pair_a',pairA); params.set('studio_pair_b',pairB); }
    params.set('studio_equation',selectedEquation); params.set('studio_typology',typology); params.set('studio_season',season); params.set('studio_material',material); params.set('studio_grammar',grammar); params.set('studio_rain_event',rainEvent);
    params.set('studio_module',String(values.module)); params.set('studio_courtyard',String(values.courtyard)); params.set('studio_bays',String(values.bays)); params.set('studio_angle',String(values.angle)); params.set('studio_density',String(values.density)); params.set('studio_wind',String(values.wind)); params.set('studio_rain',String(values.rain)); params.set('studio_access',String(values.accessWidth)); params.set('studio_phases',String(values.phases)); params.set('studio_public_mix',String(values.publicMix)); params.set('studio_levels',String(values.levels));
  }
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}
function syncFocusState(id) {
  const params=new URLSearchParams(location.search);
  if(id) params.set('focus',String(id)); else params.delete('focus');
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}

function color(score) { return score >= 60 ? '#b42318' : score >= 35 ? '#d97706' : score >= 15 ? '#2563eb' : '#3f8f65'; }
function matches(row) {
  const q=$('query').value.trim().toLowerCase();
  const hay=[row.name,row.osm_id,row.group,row.subtype,row.address_city,flagsText(row),patternNamesText(row),row.niah.name,row.niah.county,row.niah.type,row.history.status,row.history.architect].join(' ').toLowerCase();
  return (!q || hay.includes(q)) && (!$('group').value || row.group===$('group').value) &&
    (!$('century').value || row.niah.century===$('century').value) && (!$('rating').value || row.niah.rating===$('rating').value) &&
    (!$('niahType').value || row.niah.type===$('niahType').value) && (!$('county').value || (row.spatial?.county||row.niah?.county)===$('county').value) && (!$('pattern').value || hasFlag(row,$('pattern').value)) && (!$('reviewState').value || reviewFilterState(row)===$('reviewState').value) &&
    culturalLensMatches(row,$('cultureLens').value) && row.score >= Number($('score').value) &&
    (!$('onlyAngle').checked || row.has_golden_angle) && (!$('onlyRatio').checked || row.has_golden_ratio) &&
    (!$('onlyCircular').checked || hasFlag(row,'circular')) && (!$('onlyMulti').checked || row.multipart || row.repaired);
}
function sortRows(rows) {
  return rows.sort((a,b) => { let av=a[sortKey], bv=b[sortKey]; if(sortKey==='flags') { av=flagsText(a); bv=flagsText(b); } if(sortKey==='name'||sortKey==='group') return (String(av||'').localeCompare(String(bv||'')))*(sortDesc?-1:1); return ((Number(bv)||0)-(Number(av)||0))*(sortDesc?1:-1); });
}
function updateSortHeaders() { document.querySelectorAll('th[data-sort]').forEach(th=>{ const active=th.dataset.sort===sortKey; const direction=active?(sortDesc?'descending':'ascending'):'none'; th.setAttribute('aria-sort',direction); const button=th.querySelector('.sort-button'); if(button) button.setAttribute('aria-label',`${button.textContent.trim()}; ${active?`sorted ${direction}`:'activate to sort'}`); }); }
function currentFilterParameters() {
  const params=new URLSearchParams();
  const add=(key,value)=>{ if(value!==undefined && value!==null && String(value)!=='') params.set(key,String(value)); };
  add('q',$('query').value.trim()); add('group',$('group').value); add('century',$('century').value);
  add('rating',$('rating').value); add('type',$('niahType').value); add('county',$('county').value); add('review',$('reviewState').value); add('culture',$('cultureLens').value);
  if(Number($('score').value)>0) add('score',Number($('score').value));
  for(const [id,key] of VIEW_CHECKS) if($(id).checked) params.set(key,'1');
  add('sort',sortKey==='score'?'':sortKey); if(!sortDesc) params.set('desc','0');
  return params;
}
function hasActiveViewState() { const params=currentFilterParameters(); return !sortDesc || [...params.keys()].some(key=>key!=='desc'); }
function sortBy(key) { sortDesc=sortKey===key?!sortDesc:key==='score'; sortKey=key; applyFilters(); }
function runtimeDataIdentity(runtime) {
  const snapshot=runtime?.snapshot;
  if(!snapshot || snapshot.available!==true) return '';
  const manifest=String(snapshot.manifest_sha256||'').trim();
  if(manifest) return `manifest:${manifest}`;
  const generated=String(snapshot.generated_at||'').trim();
  const revision=String(snapshot.git_revision||'').trim();
  return generated||revision ? `build:${revision}:${generated}` : '';
}
function applyRuntime(runtime) {
  if(!runtime || typeof runtime!=='object') return false;
  const previousRuntime=REPORT_RUNTIME;
  const previousIdentity=reportRuntimeIdentity || runtimeDataIdentity(previousRuntime);
  const nextIdentity=runtimeDataIdentity(runtime);
  if(SERVER_MODE && ((previousIdentity && nextIdentity && previousIdentity!==nextIdentity) || (previousRuntime?.analysis_ready===true && runtime.analysis_ready!==true) || (previousRuntime?.manifest_alignment?.status==='pass' && runtime.manifest_alignment?.status && runtime.manifest_alignment.status!=='pass'))) runtimeReloadRequired=true;
  reportRuntimeIdentity=nextIdentity || previousIdentity;
  REPORT_RUNTIME=runtime;
  const validation=runtime.validation && typeof runtime.validation==='object' ? runtime.validation : {};
  const alignment=runtime.manifest_alignment && typeof runtime.manifest_alignment==='object' ? runtime.manifest_alignment : {};
  const sourceAlignment=runtime.source_alignment && typeof runtime.source_alignment==='object' ? runtime.source_alignment : {};
  SUMMARY={...SUMMARY,analysis_ready:runtime.analysis_ready===true,validation,manifest_alignment:alignment,source_alignment:sourceAlignment};
  if(runtime.analysis_ready===true) {
    INTERPRETATION={...BASE_INTERPRETATION};
    return true;
  }
  const validationStatus=String(validation.status||'incomplete');
  const alignmentStatus=String(alignment.status||'incomplete');
  const sourceStatus=String(sourceAlignment.status||'not_reported');
  const validationText=sourceStatus==='fail'
    ? 'Current input source alignment is '+sourceStatus+'; numerical findings should be treated as provisional.'
    : alignmentStatus!=='pass'
    ? 'Current output artifact alignment is '+alignmentStatus+'; numerical findings should be treated as provisional.'
    : 'Analytical validation is '+validationStatus+'; numerical findings should be treated as provisional.';
  const interpretation={...BASE_INTERPRETATION};
  const findings=Array.isArray(interpretation.findings)?interpretation.findings:[];
  const patchedFindings=findings.map(item=>{
    if(!item || typeof item!=='object' || item.id!=='validation') return item;
    return {...item,status:sourceStatus==='fail'?sourceStatus:alignmentStatus!=='pass'?alignmentStatus:validationStatus,text:validationText};
  });
  const caveats=Array.isArray(interpretation.caveats)?[...interpretation.caveats]:[];
  const caveat='Current output artifacts are not aligned with the manifest; numerical findings should be treated as provisional.';
  if(alignmentStatus!=='pass' && !caveats.includes(caveat)) caveats.push(caveat);
  const sourceCaveat='Current input sources are not aligned with the output manifest; numerical findings should be treated as provisional.';
  if(sourceStatus==='fail' && !caveats.includes(sourceCaveat)) caveats.push(sourceCaveat);
  INTERPRETATION={...interpretation,findings:patchedFindings,caveats};
  return true;
}
function applyRuntimeHeaders(headers) {
  if(!headers || typeof headers.get!=='function') return false;
  const runtimeStatus=headers.get('X-Ireland-Geometry-Runtime-Status');
  if(!runtimeStatus) return false;
  const validationStatus=headers.get('X-Ireland-Geometry-Validation')||runtimeStatus;
  const alignmentStatus=headers.get('X-Ireland-Geometry-Manifest-Alignment')||runtimeStatus;
  const sourceStatus=headers.get('X-Ireland-Geometry-Source-Alignment')||runtimeStatus;
  return applyRuntime({
    ...(REPORT_RUNTIME||{}),
    contract:headers.get('X-Ireland-Geometry-Runtime-Contract')||'ireland-geometry.report-runtime.v1',
    status:runtimeStatus,
    analysis_ready:headers.get('X-Ireland-Geometry-Analysis-Ready')==='true',
    validation:{...(REPORT_RUNTIME?.validation||{}),status:validationStatus,passed:validationStatus==='pass'},
    manifest_alignment:{...(REPORT_RUNTIME?.manifest_alignment||{}),status:alignmentStatus,passed:alignmentStatus==='pass'},
    source_alignment:{...(REPORT_RUNTIME?.source_alignment||{}),status:sourceStatus,passed:sourceStatus==='pass'}
  });
}
function runtimeIdentityText() {
  const snapshot=REPORT_RUNTIME?.snapshot;
  if(!snapshot || snapshot.available!==true) return '';
  const revision=String(snapshot.git_revision||'').trim();
  if(!revision) return '';
  const dirty=snapshot.git_dirty===true?' · dirty':'';
  return ` · build ${revision.slice(0,12)}${dirty}`;
}
function renderRuntimeReloadNotice() {
  const notice=$('runtimeReloadNotice');
  if(!notice) return;
  notice.hidden=!SERVER_MODE || !runtimeReloadRequired;
  if(!notice.hidden) {
    const text=$('runtimeReloadText');
    if(text) text.textContent='The served data changed or became provisional while this report was open. Reload to fetch the current build.';
  }
}
function renderRuntimeStatus() {
  renderRuntimeReloadNotice();
  const element=$('runtimeStatus');
  if(!element) return;
  if(!SERVER_MODE || !REPORT_RUNTIME) { element.hidden=true; element.textContent=''; element.className='runtime-status'; return; }
  const status=String(REPORT_RUNTIME.status||'incomplete');
  const validation=String(REPORT_RUNTIME.validation?.status||'not_reported');
  const alignment=String(REPORT_RUNTIME.manifest_alignment?.status||'not_reported');
  const source=String(REPORT_RUNTIME.source_alignment?.status||'not_reported');
  element.hidden=false;
  if(runtimeRefreshError) {
    element.className='runtime-status incomplete';
    element.textContent=`Runtime check unavailable · last known ${status}`;
    element.title=runtimeRefreshError;
    return;
  }
  element.removeAttribute('title');
  element.className='runtime-status '+status;
  const identity=runtimeIdentityText();
  element.textContent=REPORT_RUNTIME.analysis_ready===true
    ? 'Validated runtime · current output'+identity
    : 'Provisional runtime · '+status+'; validation '+validation+'; manifest '+alignment+'; sources '+source+identity;
}
function showReportError(error, prefix='Report request unavailable') {
  const notice=$('reportLoadError'), text=$('reportLoadErrorText');
  if(!notice||!text) return;
  revealReportError();
  const message=error?.message || String(error || 'unknown error');
  text.textContent=`${prefix}: ${message}`;
  notice.hidden=false;
}
function revealReportError() {
  const intro=$('siteIntro');
  if(intro) { intro.hidden=true; intro.classList.add('is-dismissed'); }
  document.body.classList.remove('intro-open');
}
function hideReportError() {
  const notice=$('reportLoadError');
  if(notice) notice.hidden=true;
}
function retryReportRequest() {
  location.reload();
}
async function refreshRuntime() {
  if(!SERVER_MODE || runtimePollInFlight) return;
  runtimePollInFlight=true;
  try {
    const endpoint=PACK.endpoints?.runtime || '/api/report/runtime';
    const response=await fetch(endpoint,{cache:'no-cache'});
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Runtime request failed (${response.status})`);
    runtimeRefreshError='';
    const runtimeChanged=applyRuntime(payload);
    if(runtimeChanged) { renderSummary(); renderMethod(); renderRuntimeStatus(); renderInterpretation(); renderStudio(); }
  } catch(error) {
    runtimeRefreshError=error.message;
    renderRuntimeStatus();
  } finally { runtimePollInFlight=false; }
}
function startRuntimeRefresh() {
  if(!SERVER_MODE || runtimePollTimer!==null) return;
  runtimePollTimer=setInterval(refreshRuntime,RUNTIME_REFRESH_MS);
}
function reportRequestBody(params, extra={}) {
  const body={...extra};
  params.forEach((value,key)=>{
    if(key==='score'||key==='limit'||key==='offset') body[key]=Number(value);
    else if(['angle','ratio','circular','multi','desc','initial'].includes(key)) body[key]=value==='1';
    else body[key]=value;
  });
  return body;
}
async function fetchServerPage() {
  const requestId=++serverRequestId;
  const params=currentFilterParameters(); params.set('limit',String(PAGE_SIZE)); params.set('offset',String((page-1)*PAGE_SIZE));
  try {
    const endpoint=PACK.endpoints?.page || '/api/report/page';
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(reportRequestBody(params,{initial:false}))});
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Report page request failed (${response.status})`);
    if(requestId!==serverRequestId) return;
    hideReportError();
    const runtimeChanged=applyRuntime(payload.runtime);
    DATA=payload.targets || []; filtered=DATA.slice(); pageStats=payload.page || {total:0}; renderAll();
    if(runtimeChanged) { renderInterpretation(); renderStudio(); }
    restoreFocusedTarget();
  } catch(error) {
    if(requestId!==serverRequestId) return;
    showReportError(error);
    $('count').textContent=`Report API unavailable: ${error.message}`;
    $('tbody').innerHTML=''; $('empty').textContent='No rows are available until the report request succeeds.'; $('empty').hidden=false;
  }
}
function applyFilters() {
  page=1; syncViewState(); updateSortHeaders();
  if(SERVER_MODE) {
    if(serverPageReady && !hasActiveViewState()) { serverPageReady=false; filtered=DATA.slice(); renderAll(); return; }
    serverPageReady=false; return fetchServerPage();
  }
  filtered=sortRows(DATA.filter(matches)); renderAll();
}
function queueFilters() { clearTimeout(filterTimer); filterTimer=setTimeout(()=>applyFilters(), $('query')===document.activeElement ? 180 : 0); }
function renderMethod() {
  const element=$('method');
  if(!element) return;
  const paragraphs=element.querySelectorAll('p');
  if(paragraphs.length<3) return;
  paragraphs[2].innerHTML=`Analytical readiness: <b>${SUMMARY.analysis_ready?'pass':'incomplete'}</b>; validation records: <b>${esc(SUMMARY.validation?.status||'not reported')}</b>.`;
}
function renderAll() {
  renderFieldAtlas(); renderFieldWalk(); renderMathsIndex(); renderCultureAtlas(); renderComparisonTray(); renderSummary(); renderMethod(); renderPatternCatalog(); renderTable(); renderMap(); renderRuntimeStatus();
  const active=document.querySelector('[data-nav-section][aria-current="page"]')?.dataset.navSection||'field', status=$('atlasNavStatus');
  if(status) status.textContent=atlasNavStatusText(active);
}

function rowCounty(row) { return String(row.spatial?.county||row.niah?.county||'').trim(); }
function renderCountyFieldNote() {
  const note=$('countyFieldNote'), county=$('county')?.value||'';
  if(!note) return;
  if(!county) {
    note.innerHTML='<span>Field note</span><p>Choose a county to open its measured field note. The selector contains every reported county; the chips show the most represented heritage-linked contexts.</p>';
    return;
  }
  const rows=DATA.filter(row=>rowCounty(row)===county);
  const total=SERVER_MODE?Number(pageStats.total||0):rows.length;
  const niah=SERVER_MODE?Number(pageStats.matching_niah||0):rows.filter(row=>Boolean(row.niah?.reg_no)).length;
  const named=rows.filter(row=>row.spatial?.settlement_class==='named_place').length;
  const ratio=rows.filter(row=>rowHasSignal(row,'golden_ratio')).length;
  const angle=rows.filter(row=>rowHasSignal(row,'golden_angle')).length;
  const medianRatio=medianValue(rows.map(row=>row.aspect_ratio)), medianCircularity=medianValue(rows.map(row=>row.circularity)), form=Number.isFinite(medianRatio)&&Number.isFinite(medianCircularity)?`median r ${fmt(medianRatio,3)} · C ${fmt(medianCircularity,3)}`:'median form not reported';
  const scope=SERVER_MODE?`The lazy view has returned ${rows.length.toLocaleString()} records on this page; the filtered total is ${total.toLocaleString()}.`:`The embedded snapshot contains ${total.toLocaleString()} target footprints in this county.`;
  const metrics=SERVER_MODE
    ? `<b>${total.toLocaleString()}</b> targets · <b>${niah.toLocaleString()}</b> NIAH joins`
    : `<b>${total.toLocaleString()}</b> targets · <b>${niah.toLocaleString()}</b> NIAH · <b>${named.toLocaleString()}</b> named-place contexts · φ ${fmt(ratio/Math.max(1,total)*100,1)}% · θ ${fmt(angle/Math.max(1,total)*100,1)}% · <b>${form}</b>`;
  note.innerHTML=`<span>Field note / ${esc(county)}</span><p>${scope} Use this as a place-specific reading of the snapshot, not a claim that a county has one architectural identity.</p><div class="county-field-metrics">${metrics}</div>`;
}
function renderCountyPulse() {
  const grid=$('countyPulseGrid'), note=$('countyPulseNote');
  if(!grid) return;
  const groups=new Map();
  DATA.forEach(row=>{
    const county=rowCounty(row);
    if(!county) return;
    const item=groups.get(county)||{county,target:0,niah:0,named:0,ratio:0,angle:0,aspects:[],circularities:[]};
    item.target++;
    if(row.niah?.reg_no) item.niah++;
    if(row.spatial?.settlement_class==='named_place') item.named++;
    if(rowHasSignal(row,'golden_ratio')) item.ratio++;
    if(rowHasSignal(row,'golden_angle')) item.angle++;
    item.aspects.push(row.aspect_ratio);
    item.circularities.push(row.circularity);
    groups.set(county,item);
  });
  const rows=[...groups.values()].sort((a,b)=>b.target-a.target||a.county.localeCompare(b.county));
  const scope=SERVER_MODE?'current lazy page':'full embedded snapshot', selected=$('county')?.value||'';
  if(!rows.length) {
    grid.innerHTML='<span class="footnote">No county context is available in this report view.</span>';
    if(note) note.textContent=`No county pulse can be drawn from the ${scope}. Clear filters or widen the report view to restore the field.`;
    return;
  }
  const max=Math.max(1,...rows.map(row=>row.target));
  grid.innerHTML=rows.map(row=>{
    const ratio=row.ratio/Math.max(1,row.target)*100, angle=row.angle/Math.max(1,row.target)*100, medianRatio=medianValue(row.aspects), medianCircularity=medianValue(row.circularities), form=Number.isFinite(medianRatio)&&Number.isFinite(medianCircularity)?`r ${fmt(medianRatio,3)} · C ${fmt(medianCircularity,3)}`:'form n/a', active=row.county===selected, width=Math.max(3,Math.min(100,row.target/max*100)), label=`${row.county}: ${row.target.toLocaleString()} targets; φ ${fmt(ratio,1)}%; θ ${fmt(angle,1)}%; median form ${form}; ${row.niah.toLocaleString()} NIAH joins; ${row.named.toLocaleString()} named-place contexts`;
    return `<button class="county-pulse" type="button" data-county-focus="${esc(row.county)}" aria-pressed="${active}" aria-label="${esc(label)}"><span class="county-pulse-top"><span>${esc(row.county)}</span><small>${active?'selected':'field'}</small></span><strong>${row.target.toLocaleString()}</strong><small>φ ${fmt(ratio,1)}% · θ ${fmt(angle,1)}%</small><small class="county-pulse-form">${esc(form)}</small><span class="county-pulse-track" aria-hidden="true"><i style="width:${width}%"></i></span><em>${row.niah.toLocaleString()} NIAH · ${row.named.toLocaleString()} Ainm</em></button>`;
  }).join('');
  if(note) note.textContent=`Showing ${rows.length.toLocaleString()} county contexts from the ${scope}; card bars scale to the largest visible target field. φ and θ are target-level screening rates, not evidence of a county-wide architectural identity.`;
}
function spatialGroupLabel(value) {
  const labels={historic:'Historic fabric',worship:'Worship',government:'Government',civic:'Civic'};
  return labels[String(value||'').toLowerCase()] || String(value||'Other');
}
function renderSpatialRhythm() {
  const grid=$('rhythmGrid'), stat=$('rhythmCount'), title=$('rhythmReadoutTitle'), text=$('rhythmReadoutText');
  if(!grid) return;
  const rows=MORAN.filter(row=>String(row.group||'').trim()&&String(row.group)!=='controls'), countyByGroup=new Map(COUNTY_PERM.filter(row=>String(row.signal)==='golden_angle').map(row=>[String(row.group),row])), bootByGroup=new Map(BOOT.filter(row=>String(row.signal)==='golden_angle').map(row=>[String(row.target_group),row])), active=String($('group')?.value||'');
  if(stat) stat.textContent=rows.length.toLocaleString();
  if(!rows.length) {
    grid.innerHTML='<span class="footnote">No spatial rhythm screens are available in this report pack.</span>';
    if(title) title.textContent='Spatial rhythm is not reported.';
    if(text) text.textContent='The report pack does not contain a usable neighbour or county-preserving screen.';
    return;
  }
  grid.innerHTML=rows.map(row=>{
    const group=String(row.group), label=spatialGroupLabel(group), moran=Number(row.moran_i), p=Number(row.p_adjusted||row.p_value), county=countyByGroup.get(group)||{}, boot=bootByGroup.get(group)||{}, delta=Number(county.observed_difference_pp), bootDelta=Number(boot.observed_difference_pp), low=Number(boot.ci_low_pp), high=Number(boot.ci_high_pp), verdict=heritageVerdictLabel(row.verdict), direction=moran>0.005?'neighbour echoes':moran<-.005?'neighbour contrast':'near null', width=Number.isFinite(moran)?Math.max(4,Math.min(100,50+moran*280)):50, countyText=Number.isFinite(delta)?`county Δ ${delta>=0?'+':''}${fmt(delta,2)} pp`:'county Δ n/a', blockText=Number.isFinite(bootDelta)&&Number.isFinite(low)&&Number.isFinite(high)?`block Δ ${bootDelta>=0?'+':''}${fmt(bootDelta,2)} pp [${fmt(low,2)}, ${fmt(high,2)}]`:'block interval n/a', sample=Number(row.n);
    const aria=`${label}: Moran's I ${fmt(moran,3)}; ${direction}; ${countyText}; ${verdict} screen`;
    return `<button class="rhythm-card" type="button" data-rhythm-group="${esc(group)}" aria-pressed="${group===active}" aria-label="${esc(aria)}"><span class="rhythm-card-top"><span>${esc(label)}</span><small>${esc(verdict)}</small></span><strong>I ${fmt(moran,3)}</strong><small>${esc(direction)} · adjusted p ${fmt(p,3)}</small><span class="rhythm-track" aria-hidden="true"><i style="width:${width}%"></i></span><em>${esc(countyText)} · ${esc(blockText)}</em><small class="rhythm-card-meta">k=${esc(row.k_neighbours||'—')} · ${Number.isFinite(sample)?sample.toLocaleString():'—'} sampled footprints</small></button>`;
  }).join('');
  const focus=rows.find(row=>String(row.group)===active);
  if(!focus) {
    if(title) title.textContent=`${rows.length.toLocaleString()} cohorts sit inside the spatial audit.`;
    if(text) text.textContent='Choose a card to carry its building-group filter into Explore. The cards compare neighbourhood similarity with county-preserving and spatial-block sensitivity screens.';
    return;
  }
  const group=String(focus.group), county=countyByGroup.get(group)||{}, boot=bootByGroup.get(group)||{}, moran=Number(focus.moran_i), p=Number(focus.p_adjusted||focus.p_value), delta=Number(county.observed_difference_pp), bootDelta=Number(boot.observed_difference_pp), low=Number(boot.ci_low_pp), high=Number(boot.ci_high_pp), countyText=Number.isFinite(delta)?`County-preserving golden-angle difference ${delta>=0?'+':''}${fmt(delta,2)} percentage points.`:'County-preserving comparison is not reported.';
  if(title) title.textContent=`${spatialGroupLabel(group)} / neighbouring field`;
  if(text) text.textContent=`Moran's I is ${fmt(moran,3)} across the sampled 8-neighbour graph (adjusted p ${fmt(p,3)}). ${countyText} ${Number.isFinite(bootDelta)&&Number.isFinite(low)&&Number.isFinite(high)?`The spatial-block screen is ${bootDelta>=0?'+':''}${fmt(bootDelta,2)} pp with interval ${fmt(low,2)} to ${fmt(high,2)} pp.`:'The spatial-block sensitivity interval is not reported.'} Read this as a situated diagnostic, not as proof of regional style or historical intent.`;
}
function scaleRadiusLabel(value) {
  const meters=Number(value);
  if(!Number.isFinite(meters)) return '—';
  return meters>=1000?`${fmt(meters/1000,meters%1000?1:0)} km`:`${fmt(meters,0)} m`;
}
function renderScaleField() {
  const grid=$('scaleGrid'), stat=$('scaleCount'), title=$('scaleReadoutTitle'), text=$('scaleReadoutText');
  if(!grid) return;
  const groups=new Map();
  RIPLEY.forEach(row=>{
    const group=String(row.group||'').trim();
    if(!group||group==='controls') return;
    const rows=groups.get(group)||[];
    rows.push(row);
    groups.set(group,rows);
  });
  const entries=[...groups.entries()].map(([group,rows])=>[group,rows.slice().sort((a,b)=>Number(a.radius_m)-Number(b.radius_m))]).sort((a,b)=>a[0].localeCompare(b[0])), active=String($('group')?.value||'');
  if(stat) stat.textContent=entries.reduce((sum,[,rows])=>sum+rows.length,0).toLocaleString();
  if(!entries.length) {
    grid.innerHTML='<span class="footnote">No multi-distance spatial scale is available in this report pack.</span>';
    if(title) title.textContent='Field scale is not reported.';
    if(text) text.textContent='The report pack does not contain a usable Ripley radius summary.';
    return;
  }
  grid.innerHTML=entries.map(([group,rows])=>{
    const label=spatialGroupLabel(group), values=rows.map(row=>Number(row.l_minus_r_m)).filter(Number.isFinite), max=Math.max(1,...values), first=rows[0], last=rows[rows.length-1], peak=rows.slice().sort((a,b)=>Number(b.l_minus_r_m)-Number(a.l_minus_r_m))[0], sample=Number(first.n), start=Number(first.l_minus_r_m), end=Number(last.l_minus_r_m), aria=`${label}: ${scaleRadiusLabel(first.radius_m)} ${fmt(start,1)} metres; ${scaleRadiusLabel(last.radius_m)} ${fmt(end,1)} metres; peak at ${scaleRadiusLabel(peak.radius_m)}`;
    const steps=rows.map(row=>{
      const radius=Number(row.radius_m), value=Number(row.l_minus_r_m), height=Number.isFinite(value)?Math.max(6,Math.min(100,value/max*100)):6;
      return `<span class="scale-step"><i style="height:${height}%"></i><small>${esc(scaleRadiusLabel(radius))}</small><b>${Number.isFinite(value)?`${fmt(value/1000,2)} km`:'—'}</b></span>`;
    }).join('');
    return `<button class="scale-card" type="button" data-scale-group="${esc(group)}" aria-pressed="${group===active}" aria-label="${esc(aria)}"><span class="scale-card-top"><span>${esc(label)}</span><small>${rows.length} radii</small></span><strong>${Number.isFinite(start)?fmt(start/1000,2):'—'} → ${Number.isFinite(end)?fmt(end/1000,2):'—'} km</strong><span class="scale-steps" role="img" aria-label="${esc(aria)}">${steps}</span><small class="scale-card-meta">peak ${esc(scaleRadiusLabel(peak.radius_m))} · ${Number.isFinite(sample)?sample.toLocaleString():'—'} sampled points</small></button>`;
  }).join('');
  const focus=entries.find(([group])=>group===active);
  if(!focus) {
    if(title) title.textContent=`${entries.reduce((sum,[,rows])=>sum+rows.length,0).toLocaleString()} radius observations across ${entries.length.toLocaleString()} cohorts.`;
    if(text) text.textContent='Choose a cohort card to carry its group filter into Explore. Each rail uses its cohort’s own maximum for visual shape; the printed values remain the raw L(r) − r result in metres.';
    return;
  }
  const [group,rows]=focus, first=rows[0], last=rows[rows.length-1], peak=rows.slice().sort((a,b)=>Number(b.l_minus_r_m)-Number(a.l_minus_r_m))[0], firstValue=Number(first.l_minus_r_m), lastValue=Number(last.l_minus_r_m), peakValue=Number(peak.l_minus_r_m), sample=Number(first.n), method=String(first.edge_method||'translation-corrected sampled bounding rectangle');
  if(title) title.textContent=`${spatialGroupLabel(group)} / six-radius field`;
  if(text) text.textContent=`At ${scaleRadiusLabel(first.radius_m)}, the reported L(r) − r value is ${fmt(firstValue,1)} m; at ${scaleRadiusLabel(last.radius_m)} it is ${fmt(lastValue,1)} m. The largest reported value is ${fmt(peakValue,1)} m at ${scaleRadiusLabel(peak.radius_m)} across ${Number.isFinite(sample)?sample.toLocaleString():'—'} sampled points. ${method}; the rail is a scale diagnostic, not a significance envelope.`;
}
function alignmentPercent(value) {
  const number=Number(value);
  return Number.isFinite(number)?`${fmt(number*100,2)}%`:'—';
}
function renderAlignmentField() {
  const grid=$('alignmentGrid'), stat=$('alignmentCount'), title=$('alignmentReadoutTitle'), text=$('alignmentReadoutText');
  if(!grid) return;
  const rows=POINT_PATTERN.filter(row=>String(row.group||'').trim()).slice().sort((a,b)=>String(a.group)==='controls'?1:String(b.group)==='controls'?-1:String(a.group).localeCompare(String(b.group))), active=String($('group')?.value||''), groupSelect=$('group');
  if(stat) stat.textContent=rows.length.toLocaleString();
  if(!rows.length) {
    grid.innerHTML='<span class="footnote">No point-pattern orientation screen is available in this report pack.</span>';
    if(title) title.textContent='Orientation field is not reported.';
    if(text) text.textContent='The report pack does not contain a usable edge-bearing or nearest-neighbour summary.';
    return;
  }
  grid.innerHTML=rows.map(row=>{
    const group=String(row.group), label=spatialGroupLabel(group), bearing=Number(row.bearing_golden_frac), bearingNull=Number(row.bearing_golden_null_mean), bearingP=Number(row.bearing_golden_p), turn=Number(row.turn_golden_frac), turnNull=Number(row.turn_golden_null_mean), nn=Number(row.nn_fib_frac), sham=Number(row.nn_sham_frac), nnDelta=Number(row.nn_fib_vs_sham)*100, peak=Number(row.bearing_peak_angle), peakP=Number(row.bearing_peak_p), edges=Number(row.n_edges), turns=Number(row.n_turns), needle=Number.isFinite(peak)?Math.max(-360,Math.min(360,peak)):0, clickable=group!=='controls'&&groupSelect&&[...groupSelect.options].some(option=>option.value===group), action=clickable?`<button class="alignment-action" type="button" data-alignment-group="${esc(group)}" aria-pressed="${group===active}">${group===active?'Selected cohort':'Explore cohort'} <span>→</span></button>`:'<span class="alignment-reference">Reference field · not a target filter</span>';
    return `<article class="alignment-card"><div><div class="alignment-compass" style="--needle-angle:${needle}deg" aria-hidden="true"></div><span class="alignment-compass-label">peak ${Number.isFinite(peak)?fmt(peak,0):'—'}°</span></div><div class="alignment-card-body"><div class="alignment-card-top"><span>${esc(label)}</span><small>${Number.isFinite(Number(row.n))?Number(row.n).toLocaleString():'—'} rows</small></div><h4>Edges + neighbours</h4><div class="alignment-metrics"><div class="alignment-metric"><span>Golden bearing band</span><strong>${alignmentPercent(bearing)}</strong><small>null ${alignmentPercent(bearingNull)} · p ${fmt(bearingP,3)}</small></div><div class="alignment-metric"><span>Golden turns</span><strong>${alignmentPercent(turn)}</strong><small>null ${alignmentPercent(turnNull)}</small></div><div class="alignment-metric"><span>Fibonacci neighbours</span><strong>${alignmentPercent(nn)}</strong><small>${Number.isFinite(sham)?`sham ${alignmentPercent(sham)} · Δ ${nnDelta>=0?'+':''}${fmt(nnDelta,2)} pp`:'not reported'}</small></div></div><small class="alignment-meta">${Number.isFinite(edges)?edges.toLocaleString():'—'} edges · ${Number.isFinite(turns)?turns.toLocaleString():'—'} turns · peak p ${fmt(peakP,3)}</small>${action}</div></article>`;
  }).join('');
  const target=rows.find(row=>String(row.group)==='worship')||rows.find(row=>String(row.group)!=='controls'), reference=rows.find(row=>String(row.group)==='controls');
  if(!target) {
    if(title) title.textContent='The point-pattern field is incomplete.';
    if(text) text.textContent='A target cohort is needed before the orientation screen can be compared with its reference field.';
    return;
  }
  const targetLabel=spatialGroupLabel(target.group), targetBearing=Number(target.bearing_golden_frac), refBearing=Number(reference?.bearing_golden_frac), targetTurn=Number(target.turn_golden_frac), refTurn=Number(reference?.turn_golden_frac), targetNn=Number(target.nn_fib_frac), targetSham=Number(target.nn_sham_frac), bearingDelta=Number.isFinite(targetBearing)&&Number.isFinite(refBearing)?(targetBearing-refBearing)*100:NaN, turnDelta=Number.isFinite(targetTurn)&&Number.isFinite(refTurn)?(targetTurn-refTurn)*100:NaN, nnDelta=Number.isFinite(targetNn)&&Number.isFinite(targetSham)?(targetNn-targetSham)*100:NaN, focus=rows.find(row=>String(row.group)===active);
  if(title) title.textContent=focus?`${spatialGroupLabel(focus.group)} / orientation screen`:`${targetLabel} / reference field`;
  if(text) text.textContent=`${targetLabel} carries ${alignmentPercent(targetBearing)} of the screened edge bearings in the golden-angle band versus ${alignmentPercent(refBearing)} in controls (${bearingDelta>=0?'+':''}${fmt(bearingDelta,2)} pp). Its peak bearing is ${fmt(Number(target.bearing_peak_angle),0)}° with screen p ${fmt(Number(target.bearing_peak_p),3)}; golden turns are ${alignmentPercent(targetTurn)} versus ${alignmentPercent(refTurn)} in the reference. ${Number.isFinite(targetNn)&&Number.isFinite(targetSham)?`Nearest-neighbour Fibonacci share is ${alignmentPercent(targetNn)} versus sham ${alignmentPercent(targetSham)} (${nnDelta>=0?'+':''}${fmt(nnDelta,2)} pp).`:''} These are exploratory point-pattern diagnostics, not evidence of conscious angle selection or cultural origin.`;
}
function sourceRootLabel(value) {
  const source=String(value||'');
  if(source.includes('combined.json')) return 'Community-mapped geometry snapshot';
  if(source.includes('niah.json')) return 'National heritage inventory snapshot';
  if(source.includes('architects_evidence.csv')) return 'Validated attribution evidence table';
  if(source.includes('historical/references.csv')||source.includes('historical\\references.csv')) return 'Optional curated history register';
  const parts=source.split(/[\\/]/);
  return parts[parts.length-1]||'Source not named';
}
function sourceStatusKind(value) {
  const status=String(value||'').toLowerCase();
  return status==='available'||status==='provided'?'available':status==='fallback'?'fallback':'missing';
}
function sourceStatusLabel(value) {
  const kind=sourceStatusKind(value);
  return kind==='available'?'available':kind==='fallback'?'fallback':'not provided';
}
function renderSourceRoots() {
  const grid=$('sourceGrid'), stat=$('sourceCount'), title=$('sourceReadoutTitle'), text=$('sourceReadoutText');
  if(!grid) return;
  const rows=SOURCE_REGISTER.filter(row=>String(row.source_type||'').trim());
  if(stat) stat.textContent=`${rows.filter(row=>sourceStatusKind(row.status)!=='missing').length}/${rows.length||0}`;
  if(!rows.length) {
    grid.innerHTML='<span class="footnote">No source register is available in this report pack.</span>';
    if(title) title.textContent='Source lineage is not reported.';
    if(text) text.textContent='The report pack does not contain a source register to place beside the measurements.';
    return;
  }
  grid.innerHTML=rows.map((row,index)=>{
    const kind=sourceStatusKind(row.status);
    return `<article class="source-root-card"><div class="source-root-top"><span>${String(index+1).padStart(2,'0')} / lineage</span><span class="source-status ${kind}">${esc(sourceStatusLabel(row.status))}</span></div><h4>${esc(row.source_type)}</h4><p>${esc(sourceRootLabel(row.source))}<br>${esc(row.coverage||'Coverage not reported')}</p><small>${esc(row.notes||'No source note is reported.')}</small></article>`;
  }).join('');
  const present=rows.filter(row=>sourceStatusKind(row.status)!=='missing').length, valid=Number(QUALITY_SUMMARY.valid_geometry_pct), duplicate=Number(QUALITY_SUMMARY.duplicate_centroid_n), review=Number(SUMMARY.review_queue_targets);
  if(title) title.textContent=`${present} of ${rows.length} source families are present in this snapshot.`;
  if(text) text.textContent=`The register names the evidence lineage before interpretation: ${present} source families are available or provided, while ${rows.length-present} remain explicitly absent. Geometry validity is ${fmt(valid,1)}%; ${Number.isFinite(duplicate)?duplicate.toLocaleString():'—'} duplicate centroids are flagged for review; ${Number.isFinite(review)?review.toLocaleString():'—'} targets sit in the expert review queue.`;
}
function trustStatusKind(value) {
  const status=String(value||'').toLowerCase();
  if(status==='pass'||status==='passed') return 'pass';
  if(status==='available'||status==='provided') return 'available';
  if(status.includes('not_provided')||status.includes('missing')) return 'missing';
  return 'check';
}
function trustStatusLabel(value) {
  const kind=trustStatusKind(value);
  return kind==='pass'?'pass':kind==='available'?'available':kind==='missing'?'not provided':'check';
}
function renderTrustField() {
  const grid=$('trustGrid'), stat=$('trustCount'), title=$('trustReadoutTitle'), text=$('trustReadoutText');
  if(!grid) return;
  const validation=SUMMARY.validation||{}, records=validation.records||{}, record=name=>records[name]||{}, schema=record('schema_validation'), reproducibility=record('reproducibility'), verification=record('verification'), holdout=HOLDOUT.find(row=>String(row.target_group)==='worship'&&String(row.signal)==='golden_angle')||HOLDOUT[0]||null, calibration=REVIEW_CALIBRATION[0]||{};
  const core=[schema,reproducibility,verification], passed=core.filter(row=>row.passed===true||trustStatusKind(row.status)==='pass').length;
  if(stat) stat.textContent=`${passed}/${core.length||3}`;
  const holdoutStatus=holdout?.status||'not_provided', holdoutDelta=Number(holdout?.risk_difference_pp), holdoutTarget=Number(holdout?.target_rate), holdoutControl=Number(holdout?.control_rate), holdoutP=holdout?.p_value, holdoutGroup=spatialGroupLabel(holdout?.target_group||'target'), holdoutRows=Number(holdout?.target_n), holdoutControls=Number(holdout?.control_n), reviewN=Number(calibration.labelled_n);
  const cards=[
    {label:'01 / deterministic check',status:holdoutStatus,title:`${holdoutGroup} θ screen`,value:holdout?`${holdoutDelta>=0?'+':''}${fmt(holdoutDelta,2)} pp`:'not reported',note:holdout?`${fmt(holdoutTarget,2)}% vs ${fmt(holdoutControl,2)}% control · p ${heritagePText(holdoutP)}`:'No holdout result is carried in this pack.',meta:holdout?`${holdoutRows.toLocaleString()} target · ${holdoutControls.toLocaleString()} control rows`:'holdout unavailable'},
    {label:'02 / artifact contract',status:schema.status||'not_provided',title:'Schema validation',value:schema.passed===true?'pass':'check',note:schema.passed===true?'Report fields match the declared artifact contract.':'The schema record is incomplete or did not pass.',meta:'independent record'},
    {label:'03 / repeatable ledger',status:reproducibility.status||'not_provided',title:'Reproducibility',value:reproducibility.passed===true?'pass':'check',note:reproducibility.passed===true?'Generated artifacts and hashes remain reproducible for this snapshot.':'The reproducibility record is incomplete or did not pass.',meta:'hash ledger present'},
    {label:'04 / human calibration',status:calibration.status||'not_provided',title:'Expert review labels',value:Number.isFinite(reviewN)?`${reviewN.toLocaleString()} labelled`:'not reported',note:Number.isFinite(reviewN)&&reviewN>0?'Calibration rows are available beside the score heuristic.':'Explicit expert labels are not supplied; calibration remains open.',meta:'score/100 heuristic'}
  ];
  grid.innerHTML=cards.map(card=>{const kind=trustStatusKind(card.status); return `<article class="trust-card ${kind}"><div class="trust-card-top"><span>${esc(card.label)}</span><span class="trust-status ${kind}">${esc(trustStatusLabel(card.status))}</span></div><h4>${esc(card.title)}</h4><strong>${esc(card.value)}</strong><p>${esc(card.note)}</p><small>${esc(card.meta)}</small></article>`;}).join('');
  if(title) title.textContent=`${passed}/${core.length||3} core validation gates pass; ${HOLDOUT.length.toLocaleString()} holdout results remain visible.`;
  if(text) text.textContent=holdout?`The ${holdoutGroup.toLowerCase()} holdout is a pre-registered deterministic hash split: ${fmt(holdoutTarget,2)}% of target rows carry the screened angle band versus ${fmt(holdoutControl,2)}% of controls, a ${holdoutDelta>=0?'+':''}${fmt(holdoutDelta,2)} percentage-point difference with unadjusted p ${heritagePText(holdoutP)}. That makes a precise next question—not a finished cultural explanation. ${Number.isFinite(reviewN)&&reviewN>0?'Expert labels are present for calibration.':'Expert labels are still absent from this snapshot.'}`:'No holdout result is reported in this pack; the core artifact gates are shown, but the signal has no independent holdout readout here.';
}
function renderMakersField() {
  const grid=$('makersGrid'), binary=$('makersBinary'), stat=$('makersCount'), note=$('makersNote');
  if(!grid) return;
  const names=ARCHITECTS.filter(row=>String(row.architect||'').trim()).slice(0,10), query=String($('query')?.value||'').trim().toLowerCase();
  if(stat) stat.textContent=ARCHITECTS.filter(row=>String(row.architect||'').trim()).length.toLocaleString();
  if(binary) binary.innerHTML=ARCHITECTS_BINARY.length
    ? ARCHITECTS_BINARY.map(row=>{
        const named=Number(row.named_rate), anonymous=Number(row.anon_rate), difference=named-anonymous, low=Number(row.named_ci_low), high=Number(row.named_ci_high), interval=Number.isFinite(low)&&Number.isFinite(high)?`95% named interval ${fmt(low,1)}–${fmt(high,1)}%`:'interval not reported';
        return `<article class="makers-binary-card"><span>${esc(String(row.class||'cohort').replaceAll('_',' '))} · named vs unattributed</span><strong>${fmt(named,2)}% vs ${fmt(anonymous,2)}%</strong><p>${difference>=0?'+':''}${fmt(difference,2)} pp · ${esc(interval)} · ${esc(row.method||'two-proportion comparison')}</p></article>`;
      }).join('')
    : '<span class="footnote">No named-versus-unattributed comparison is available in this report pack.</span>';
  if(!names.length) {
    grid.innerHTML='<span class="footnote">No source-linked architect attributions are available in this report pack.</span>';
  } else {
    grid.innerHTML=names.map(row=>{
      const name=String(row.architect), n=Number(row.n), rate=Number(row.golden_angle_rate), active=query===name.toLowerCase();
      return `<button class="maker-chip" type="button" data-maker-name="${esc(name)}" aria-pressed="${active}" aria-label="Search the current field for ${esc(name)}"><strong>${esc(name)}</strong><small>${Number.isFinite(n)?n.toLocaleString():'—'} rows · θ ${fmt(rate,2)}% · ${esc(row.note||'exploratory attribution')}</small></button>`;
    }).join('');
  }
  if(note) note.textContent=`The report pack retains ${ARCHITECTS.length.toLocaleString()} named attribution rows. Names are source-linked evidence; small samples and multiple-name testing make the comparison exploratory, and selecting a maker searches the current field rather than asserting authorship.`;
}
function heritageCenturyForDecade(value) {
  const match=String(value||'').match(/^(\d{4})/), year=match?Number(match[1]):NaN;
  if(!Number.isFinite(year)) return '';
  if(year<1700) return 'pre-18th';
  if(year<1800) return '18th';
  if(year<1900) return '19th';
  if(year<2000) return '20th';
  return '21st';
}
function heritageVerdictLabel(value) {
  const text=String(value||'background').toLowerCase();
  return text==='signal'?'signal':text==='suggestive'?'suggestive':'background';
}
function heritagePText(value) {
  const number=Number(value);
  if(!Number.isFinite(number)) return 'n/a';
  if(number<0.0001) return '<0.0001';
  if(number<0.001) return '<0.001';
  return number.toFixed(3).replace(/0+$/,'').replace(/\.$/,'');
}
function renderHeritageTimeline() {
  const timeline=$('heritageTimeline');
  if(!timeline) return;
  const rows=DECADES.filter(row=>String(row.decade||'').trim()).slice().sort((a,b)=>String(a.decade).localeCompare(String(b.decade),undefined,{numeric:true}));
  if(!rows.length) {
    timeline.innerHTML='<span class="footnote">No NIAH decade screens are available in this report pack.</span>';
    return;
  }
  const maxRate=Math.max(1,...rows.map(row=>Number(row.golden_rate)||0));
  timeline.innerHTML=rows.map(row=>{
    const decade=String(row.decade), rate=Number(row.golden_rate), control=Number(row.era_control_rate), n=Number(row.n_churches), verdict=heritageVerdictLabel(row.verdict), active=decade===heritageEraKey;
    const width=rate>0?Math.max(2,Math.min(100,rate/maxRate*100)):0;
    return `<button class="heritage-era" type="button" role="tab" data-heritage-era="${esc(decade)}" aria-selected="${active}" aria-controls="filters"><span class="heritage-era-top"><span>${esc(decade)}</span><small>${verdict}</small></span><strong>${fmt(rate,2)}%</strong><small>${Number.isFinite(n)?n.toLocaleString():'—'} dated rows · vs ${fmt(control,2)}% controls</small><span class="heritage-era-meter" aria-hidden="true"><i style="width:${width}%"></i></span></button>`;
  }).join('');
  const selected=rows.find(row=>String(row.decade)===heritageEraKey);
  const status=$('heritageTimelineStatus'), title=$('heritageTimelineReadoutTitle'), text=$('heritageTimelineReadoutText');
  if(!selected) {
    if(status) status.textContent=`${rows.length} decades · NIAH date-matched screen`;
    if(title) title.textContent='Choose a decade to read the evidence.';
    if(text) text.textContent='The timeline is a measured comparison surface. Select a decade to carry its century lens into the target table and map.';
    return;
  }
  const decade=String(selected.decade), century=heritageCenturyForDecade(decade), rate=Number(selected.golden_rate), control=Number(selected.era_control_rate), difference=Number(selected.risk_difference), n=Number(selected.n_churches);
  if(status) status.textContent=`${decade} · ${century||'dated context'} · ${heritageVerdictLabel(selected.verdict)}`;
  if(title) title.textContent=`${decade} / ${Number.isFinite(n)?n.toLocaleString():'—'} dated rows`;
  if(text) text.textContent=`Golden-angle screen ${fmt(rate,2)}% vs ${fmt(control,2)}% era-matched controls; risk difference ${fmt(difference,2)} pp; adjusted p ${heritagePText(selected.p_adjusted)}. Select this era to filter the measured field to ${century||'its available century'} context.`;
}
function setHeritageEra(key) {
  const era=DECADES.find(row=>String(row.decade)===String(key));
  if(!era) return;
  heritageEraKey=String(era.decade);
  const century=heritageCenturyForDecade(heritageEraKey), select=$('century');
  if(select && century && [...select.options].some(option=>option.value===century)) select.value=century;
  setAtlasNavActive('filters');
  applyFilters();
  renderHeritageTimeline();
  window.setTimeout(()=>$('filters')?.scrollIntoView({behavior:'smooth',block:'start'}),120);
}
function heritageTypeLabel(value) {
  return String(value||'not classified').replaceAll('_',' ').split('/').map(part=>part.trim().replace(/\b\w/g,letter=>letter.toUpperCase())).join(' / ');
}
function renderHeritageTypology() {
  const grid=$('heritageTypeGrid'), status=$('heritageTypeStatus'), title=$('heritageTypeReadoutTitle'), text=$('heritageTypeReadoutText');
  if(!grid) return;
  const groups=new Map();
  DATA.forEach(row=>{
    const niah=row.niah||{}, type=String(niah.type||'').trim();
    if(!type||!String(niah.reg_no||'').trim()) return;
    const item=groups.get(type)||{type,n:0,angle:0,ratio:0,circularity:0,circularityN:0,groups:new Map(),ratings:new Map()};
    item.n+=1;
    if(rowHasSignal(row,'golden_angle')) item.angle+=1;
    if(rowHasSignal(row,'golden_ratio')) item.ratio+=1;
    const circularity=Number(row.circularity);
    if(Number.isFinite(circularity)){ item.circularity+=circularity; item.circularityN+=1; }
    const group=String(row.group||'unknown'), rating=String(niah.rating||'not rated');
    item.groups.set(group,(item.groups.get(group)||0)+1);
    item.ratings.set(rating,(item.ratings.get(rating)||0)+1);
    groups.set(type,item);
  });
  const all=[...groups.values()].sort((a,b)=>b.n-a.n||a.type.localeCompare(b.type)), rows=all.slice(0,8), active=String($('niahType')?.value||''), scope=SERVER_MODE?'current lazy page':'full embedded snapshot';
  if(status) status.textContent=`${all.length.toLocaleString()} visible types · ${scope}`;
  if(!rows.length){
    grid.innerHTML='<span class="footnote">No NIAH-linked building types are available in this report view.</span>';
    if(title) title.textContent='Heritage typology is not reported.';
    if(text) text.textContent='The current report view has no NIAH type rows from which to draw a building-type comparison.';
    return;
  }
  const max=Math.max(1,...rows.map(row=>row.n));
  grid.innerHTML=rows.map((row,index)=>{
    const angleRate=row.angle/row.n*100, ratioRate=row.ratio/row.n*100, compactness=row.circularityN?row.circularity/row.circularityN:NaN, dominant=[...row.groups.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]||'unknown', rating=[...row.ratings.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]||'not rated', typeLabel=heritageTypeLabel(row.type), selected=row.type===active, width=Math.max(2,Math.min(100,row.n/max*100)), label=`${typeLabel}: ${row.n} NIAH-linked rows; golden angle ${fmt(angleRate,2)} percent; golden ratio ${fmt(ratioRate,2)} percent; mean circularity ${fmt(compactness,3)}`;
    return `<button class="heritage-type-card" type="button" data-heritage-type="${esc(row.type)}" aria-pressed="${selected}" aria-label="${esc(label)}"><span class="heritage-type-card-top"><span>${String(index+1).padStart(2,'0')} / NIAH type</span><small>${row.n.toLocaleString()} rows</small></span><h4>${esc(typeLabel)}</h4><strong>θ ${fmt(angleRate,2)}%</strong><div class="heritage-type-metrics"><span><small>φ screen</small><b>${fmt(ratioRate,2)}%</b></span><span><small>mean C</small><b>${fmt(compactness,3)}</b></span><span><small>main group</small><b>${esc(heritageTypeLabel(dominant))}</b></span></div><span class="heritage-type-track" aria-hidden="true"><i style="width:${width}%"></i></span><em>${esc(rating)} · select to filter Explore</em></button>`;
  }).join('');
  const focus=all.find(row=>row.type===active)||null;
  if(!focus){
    if(title) title.textContent=`Top ${rows.length.toLocaleString()} of ${all.length.toLocaleString()} NIAH types.`;
    if(text) text.textContent=`The cards rank ${scope} by NIAH-linked row count. Each card keeps the source type beside the target-level golden-angle (θ), golden-ratio (φ), circularity (C), and dominant mapped cohort screens.`;
    return;
  }
  const angleRate=focus.angle/focus.n*100, ratioRate=focus.ratio/focus.n*100, compactness=focus.circularityN?focus.circularity/focus.circularityN:NaN, dominant=[...focus.groups.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]||'unknown', rating=[...focus.ratings.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]||'not rated', typeLabel=heritageTypeLabel(focus.type);
  if(title) title.textContent=`${typeLabel} / ${focus.n.toLocaleString()} joined rows`;
  if(text) text.textContent=`Within the ${scope}, ${typeLabel.toLowerCase()} rows carry θ ${fmt(angleRate,2)}% and φ ${fmt(ratioRate,2)}% screens, with mean circularity C ${fmt(compactness,3)}. The dominant mapped cohort is ${heritageTypeLabel(dominant)} and the most common NIAH rating is ${rating}. Select the card again to clear the type filter.`;
}
function setHeritageType(key) {
  const select=$('niahType');
  if(!select||!key||![...select.options].some(option=>option.value===key)) return;
  select.value=select.value===key?'':key;
  heritageEraKey='';
  setAtlasNavActive('filters');
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function renderPlaceBraid() {
  const grid=$('placeBraidGrid'), status=$('placeBraidStatus'), title=$('placeBraidReadoutTitle'), text=$('placeBraidReadoutText');
  if(!grid) return;
  const groups=new Map();
  DATA.forEach(row=>{
    const niah=row.niah||{}, county=rowCounty(row), type=String(niah.type||'').trim();
    if(!county||!type||!String(niah.reg_no||'').trim()) return;
    const item=groups.get(county)||{county,n:0,angle:0,ratio:0,types:new Map()};
    item.n+=1;
    if(rowHasSignal(row,'golden_angle')) item.angle+=1;
    if(rowHasSignal(row,'golden_ratio')) item.ratio+=1;
    const typeStats=item.types.get(type)||{n:0,angle:0,ratio:0};
    typeStats.n+=1;
    if(rowHasSignal(row,'golden_angle')) typeStats.angle+=1;
    if(rowHasSignal(row,'golden_ratio')) typeStats.ratio+=1;
    item.types.set(type,typeStats);
    groups.set(county,item);
  });
  const all=[...groups.values()].sort((a,b)=>b.n-a.n||a.county.localeCompare(b.county)), rows=all.slice(0,6), activeCounty=String($('county')?.value||''), activeType=String($('niahType')?.value||''), scope=SERVER_MODE?'current lazy page':'full embedded snapshot';
  if(status) status.textContent=`${all.length.toLocaleString()} county fields · ${scope}`;
  if(!rows.length){
    grid.innerHTML='<span class="footnote">No county/type braid is available in this report view.</span>';
    if(title) title.textContent='County/type field is not reported.';
    if(text) text.textContent='The current view has no NIAH-linked rows with both county and building-type context.';
    return;
  }
  const max=Math.max(1,...rows.map(row=>row.n));
  grid.innerHTML=rows.map((row,index)=>{
    const angleRate=row.angle/row.n*100, ratioRate=row.ratio/row.n*100, selected=row.county===activeCounty, width=Math.max(3,Math.min(100,row.n/max*100)), typeRows=[...row.types.entries()].sort((a,b)=>b[1].n-a[1].n||a[0].localeCompare(b[0])).slice(0,3), typeSummary=typeRows.map(([type,value])=>`${heritageTypeLabel(type)} (${value.n})`).join(', '), label=`${row.county}: ${row.n} NIAH-linked typed rows; golden angle ${fmt(angleRate,2)} percent; golden ratio ${fmt(ratioRate,2)} percent; leading types ${typeSummary}`;
    const typeButtons=typeRows.map(([type,value])=>{ const typeActive=selected&&type===activeType, typeLabel=heritageTypeLabel(type), typeAngle=value.angle/value.n*100; return `<button class="place-braid-type" type="button" data-braid-county="${esc(row.county)}" data-braid-type="${esc(type)}" aria-pressed="${typeActive}" aria-label="${esc(`${row.county} / ${typeLabel}: ${value.n} rows; golden-angle ${fmt(typeAngle,2)} percent`)}"><span>${esc(typeLabel)}</span><small>${value.n.toLocaleString()} · θ ${fmt(typeAngle,1)}%</small></button>`; }).join('');
    return `<article class="place-braid-card"><button class="place-braid-county" type="button" data-braid-county="${esc(row.county)}" aria-pressed="${selected}" aria-label="${esc(label)}"><span class="place-braid-county-top"><span>${String(index+1).padStart(2,'0')} / county braid</span><small>${selected?'selected':'field'}</small></span><strong>${esc(row.county)}</strong><small>${row.n.toLocaleString()} NIAH-linked typed rows</small></button><div class="place-braid-metrics"><span><small>θ screen</small><b>${fmt(angleRate,2)}%</b></span><span><small>φ screen</small><b>${fmt(ratioRate,2)}%</b></span><span><small>field share</small><b>${fmt(row.n/max*100,1)}%</b></span></div><span class="place-braid-track" aria-hidden="true"><i style="width:${width}%"></i></span><div class="place-braid-types" aria-label="Leading NIAH types in ${esc(row.county)}">${typeButtons||'<span class="footnote">No leading types reported.</span>'}</div></article>`;
  }).join('');
  const focus=all.find(row=>row.county===activeCounty)||null;
  if(!focus){
    if(title) title.textContent=`Top ${rows.length.toLocaleString()} of ${all.length.toLocaleString()} county fields.`;
    if(text) text.textContent=`The braid ranks ${scope} by NIAH-linked typed rows. Each county keeps its φ/θ screen rates beside its leading building types; choose a county or a type chip to return to Explore.`;
    return;
  }
  const focusTypes=[...focus.types.entries()].sort((a,b)=>b[1].n-a[1].n||a[0].localeCompare(b[0])).slice(0,3).map(([type,value])=>`${heritageTypeLabel(type)} ${value.n.toLocaleString()}`).join(' · ');
  if(title) title.textContent=`${focus.county} / ${focus.n.toLocaleString()} typed rows`;
  if(text) text.textContent=`Within the ${scope}, ${focus.county} contributes ${focus.n.toLocaleString()} NIAH-linked typed rows. Its screens are θ ${fmt(focus.angle/focus.n*100,2)}% and φ ${fmt(focus.ratio/focus.n*100,2)}%; leading types are ${focusTypes}. ${activeType?`The current type lens is ${heritageTypeLabel(activeType)}.`:'Choose a type chip to braid county and building type together.'}`;
}
function setPlaceBraidType(county,type) {
  const countySelect=$('county'), typeSelect=$('niahType');
  if(!countySelect||!typeSelect||![...countySelect.options].some(option=>option.value===county)||![...typeSelect.options].some(option=>option.value===type)) return;
  const selected=countySelect.value===county&&typeSelect.value===type;
  countySelect.value=selected?'':county;
  typeSelect.value=selected?'':type;
  heritageEraKey='';
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
const LAND_GROUP_LABELS = {historic:'Historic fabric',worship:'Worship',government:'Government',civic:'Civic',controls:'Controls'};
function landGroupLabel(value) { return LAND_GROUP_LABELS[String(value||'').toLowerCase()] || String(value||'Other'); }
function landStatusLabel(value) { return String(value||'not reported').replaceAll('_',' '); }
function renderLandField() {
  const grid=$('landGroupGrid'), readout=$('landFieldReadout'), densityPanel=$('landDensityPanel'), sourcePanel=$('landSourcePanel');
  if(!grid) return;
  const order=['historic','worship','government','civic','controls'];
  const rows=ROAD_PROXIMITY.filter(row=>row?.group && Number.isFinite(Number(row.mean_distance_m))).slice().sort((a,b)=>{
    const ai=order.indexOf(String(a.group)), bi=order.indexOf(String(b.group));
    return (ai<0?order.length:ai)-(bi<0?order.length:bi);
  });
  if(!rows.length) {
    grid.innerHTML='<span class="footnote">No nearest-road context is available in this report pack.</span>';
    if(readout) readout.innerHTML='<span>Current field / road proximity</span><strong>Land context not reported.</strong><p>The report pack does not contain a usable road-proximity sample.</p>';
  } else {
    const max=Math.max(1,...rows.map(row=>Math.max(Number(row.mean_distance_m)||0,Number(row.p90_distance_m)||0)));
    const active=$('group')?.value||'';
    grid.innerHTML=rows.map(row=>{
      const group=String(row.group), mean=Number(row.mean_distance_m), p90=Number(row.p90_distance_m), within=Number(row.within_25m_pct), sample=Number(row.sample_n), clickable=group!=='controls'&&[...$('group').options].some(option=>option.value===group), width=mean>0?Math.max(2,Math.min(100,mean/max*100)):0;
      return `<button class="land-group" type="button" data-land-group="${esc(group)}" aria-pressed="${group===active}"${clickable?'':' disabled'}><span class="land-group-top"><span>${esc(landGroupLabel(group))}</span><small>${group==='controls'?'reference':'target'}</small></span><strong>${fmt(mean,1)} m</strong><small>mean nearest road · ${fmt(within,1)}% within 25 m</small><span class="land-group-track" aria-hidden="true"><i style="width:${width}%"></i></span><span class="land-group-meta">${Number.isFinite(sample)?sample.toLocaleString():'—'} sampled centroids · p90 ${fmt(p90,1)} m</span></button>`;
    }).join('');
    if(readout) {
      const targetRows=rows.filter(row=>String(row.group)!=='controls'), candidates=targetRows.length?targetRows:rows;
      const closest=candidates.slice().sort((a,b)=>Number(a.mean_distance_m)-Number(b.mean_distance_m))[0], sample=candidates.reduce((sum,row)=>sum+(Number(row.sample_n)||0),0), method=rows.find(row=>row.method)?.method||'nearest mapped drivable-road geometry';
      readout.innerHTML=`<span>Current field / road proximity</span><strong>${esc(landGroupLabel(closest.group))} sits closest in the sampled mean.</strong><p>${Number.isFinite(sample)?sample.toLocaleString():'Several'} target/reference centroids · shortest target mean ${fmt(closest.mean_distance_m,1)} m. ${esc(method)}.</p>`;
    }
  }
  if(densityPanel) {
    const bins=['high','medium','low','sparse','unknown'], counts=Object.fromEntries(bins.map(bin=>[bin,0]));
    DATA.forEach(row=>{ const bin=String(row.spatial?.mapping_density_bin||'unknown').toLowerCase(); counts[Object.prototype.hasOwnProperty.call(counts,bin)?bin:'unknown']++; });
    const maxCount=Math.max(1,...Object.values(counts)), density=SPATIAL_COVARIATES.find(row=>row.covariate==='mapping_density'), scope=SERVER_MODE?'current lazy page':'full embedded snapshot';
    densityPanel.innerHTML=`<span>Mapping density / field texture</span><strong>${DATA.length.toLocaleString()} visible footprints · ${esc(scope)}</strong><p>${esc(landStatusLabel(density?.status||'not_provided'))} source; bins describe mapped coverage, not settlement quality or landscape value.</p><div class="land-density-bars">${bins.map(bin=>`<div class="land-density-row"><span>${esc(bin)}</span><span class="land-density-track" aria-hidden="true"><i style="width:${counts[bin]?Math.max(2,counts[bin]/maxCount*100):0}%"></i></span><b>${counts[bin].toLocaleString()}</b></div>`).join('')}</div>`;
  }
  if(sourcePanel) {
    const statuses=SUMMARY.source_status||{}, method=rows.find(row=>row.method)?.method||'nearest mapped drivable-road geometry';
    sourcePanel.innerHTML=`<span>Evidence boundary</span><p>Road sample: <b>${esc(landStatusLabel(statuses.routing||'not_reported'))}</b> · settlement layer: <b>${esc(landStatusLabel(statuses.settlements||'not_reported'))}</b> · boundaries: <b>${esc(landStatusLabel(statuses.administrative_boundaries||'not_reported'))}</b>.</p><p>${esc(method)}; this is centroid proximity, not route distance, topography, ecology, or proof of how a place was designed.</p>`;
  }
}
function setLandGroup(group) {
  const select=$('group');
  if(!select||String(group)==='controls'||![...select.options].some(option=>option.value===group)) return;
  select.value=select.value===group?'':group;
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function renderPlaceNameField() {
  const chips=$('placeNameChips'), stat=$('placeNameStat'), note=$('placeNameNote'), readout=$('placeNameReadout'), readoutStatus=$('placeNameReadoutStatus'), readoutTitle=$('placeNameReadoutTitle'), readoutText=$('placeNameReadoutText'), rowsValue=$('placeNameRows'), heritageValue=$('placeNameHeritage'), signalsValue=$('placeNameSignals'), formValue=$('placeNameForm'), groupsValue=$('placeNameGroups'), share=$('copyPlaceNameLink'), shareStatus=$('placeNameShareStatus');
  if(!chips) return;
  const set=(element,value)=>{ if(element) element.textContent=value; }, counts=new Map();
  if(share) { share.hidden=true; share.disabled=true; share.dataset.placeNameLink=''; }
  if(shareStatus) shareStatus.textContent='';
  DATA.forEach(row=>{
    if(row.spatial?.settlement_class!=='named_place') return;
    const name=String(row.spatial?.settlement_name||row.address_city||'').trim();
    if(name) counts.set(name,(counts.get(name)||0)+1);
  });
  const entries=[...counts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
  const scope=SERVER_MODE?'current lazy page':'full embedded snapshot', query=String($('query')?.value||'').trim().toLowerCase();
  if(stat) stat.innerHTML=`<strong>${entries.length.toLocaleString()}</strong><small>unique named contexts · ${esc(scope)}</small>`;
  if(!entries.length) {
    chips.innerHTML='<span class="footnote">No named settlement contexts are available in this report view.</span>';
    if(note) note.textContent='The current report view has no named-place rows. Clear filters or use the embedded snapshot to widen the field.';
    set(readoutStatus,'Named-place field unavailable'); set(readoutTitle,'No named context is reported.'); set(readoutText,'The current report view carries no named settlement rows.'); set(rowsValue,'—'); set(heritageValue,'—'); set(signalsValue,'—'); set(formValue,'—'); set(groupsValue,'—');
    if(readout) readout.setAttribute('aria-label','No named settlement context is available in this report view');
    return;
  }
  chips.innerHTML=entries.slice(0,18).map(([name,count])=>`<button class="place-name-chip" type="button" data-place-name="${esc(name)}" aria-pressed="${query===name.toLowerCase()}"><span>${esc(name)}</span><small>${count.toLocaleString()}</small></button>`).join('');
  const selected=entries.find(([name])=>name.toLowerCase()===query), status=SUMMARY.source_status?.settlements||'not_reported';
  if(selected) {
    const [name]=selected, rows=DATA.filter(row=>row.spatial?.settlement_class==='named_place'&&String(row.spatial?.settlement_name||row.address_city||'').trim().toLowerCase()===name.toLowerCase()), niah=rows.filter(row=>Boolean(row.niah?.reg_no)).length, ratio=rows.filter(row=>rowHasSignal(row,'golden_ratio')).length, angle=rows.filter(row=>rowHasSignal(row,'golden_angle')).length, medianRatio=medianValue(rows.map(row=>row.aspect_ratio)), medianCircularity=medianValue(rows.map(row=>row.circularity)), form=Number.isFinite(medianRatio)&&Number.isFinite(medianCircularity)?`r ${fmt(medianRatio,3)} · C ${fmt(medianCircularity,3)}`:'not reported', groupCounts=new Map();
    rows.forEach(row=>{ const label=spatialGroupLabel(row.group); groupCounts.set(label,(groupCounts.get(label)||0)+1); });
    const groups=[...groupCounts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,2).map(([label,count])=>`${label} ${count.toLocaleString()}`).join(' · ')||'not reported';
    if(share) { share.hidden=false; share.disabled=false; share.dataset.placeNameLink=name; }
    set(readoutStatus,`${name} · source context`); set(readoutTitle,`${name} / named-place field`); set(readoutText,`${rows.length.toLocaleString()} named-place rows in the ${scope}; median measured form ${form}; ${niah.toLocaleString()} NIAH joins; ${groups}. These are source-linked screen counts, not an etymological reading.`); set(rowsValue,rows.length.toLocaleString()); set(heritageValue,niah.toLocaleString()); set(signalsValue,`${ratio.toLocaleString()} φ · ${angle.toLocaleString()} θ`); set(formValue,form); set(groupsValue,groups); if(readout) readout.setAttribute('aria-label',`${name} named-place field: ${rows.length.toLocaleString()} rows, median measured form ${form}, ${niah.toLocaleString()} NIAH joins, ${ratio.toLocaleString()} golden-ratio screens, ${angle.toLocaleString()} golden-angle screens, groups ${groups}`);
  } else {
    set(readoutStatus,`${entries.length.toLocaleString()} named contexts`); set(readoutTitle,'Choose a name to read its measured field.'); set(readoutText,`The most represented named contexts are shown from the ${scope}. Select one to keep place coverage, measured form, and geometry screens together.`); set(rowsValue,'—'); set(heritageValue,'—'); set(signalsValue,'—'); set(formValue,'—'); set(groupsValue,'—'); if(readout) readout.setAttribute('aria-label',`${entries.length.toLocaleString()} named settlement contexts are available; choose one to read its measured field`);
  }
  if(note) note.textContent=`Showing the most represented named contexts in the ${scope}. Settlement source status: ${landStatusLabel(status)}. Selecting a name sets the existing text query; the label is a context cue, not an etymological reading.`;
}
function setPlaceName(name) {
  const input=$('query');
  if(!input||!name) return;
  input.value=input.value.trim().toLowerCase()===String(name).toLowerCase()?'':String(name);
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
async function copyPlaceNameLink() {
  const status=$('placeNameShareStatus'), name=String($('copyPlaceNameLink')?.dataset.placeNameLink||$('query')?.value||'').trim();
  if(!name) { if(status) status.textContent='Choose a named place before copying its link.'; return; }
  syncViewState();
  const url=location.href;
  try {
    if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(url);
    if(status) status.textContent=`${name} link copied · query and visible maths are encoded.`;
  } catch(error) {
    if(status) status.textContent='Named-place state saved in the address bar · copy the URL manually.';
  }
}
function spectrumGroupColor(group) {
  const colors={historic:'#bf6d52',worship:'#e1bd66',government:'#8fbe9c',civic:'#7aa5a1',controls:'#a79d8a'};
  return colors[String(group||'').toLowerCase()]||'#c3bca6';
}
function renderGeometrySpectrum() {
  const plot=$('geometrySpectrumPlot'), legend=$('spectrumLegend'), countValue=$('spectrumCount'), medianValueElement=$('spectrumMedian'), signalsValue=$('spectrumSignals'), status=$('spectrumStatus'), title=$('spectrumReadoutTitle'), text=$('spectrumReadoutText'), note=$('spectrumNote');
  if(!plot) return;
  const set=(element,value)=>{ if(element) element.textContent=value; }, scope=SERVER_MODE?'current lazy page':'full embedded snapshot', sourceRows=Array.isArray(filtered)?filtered:DATA, rows=sourceRows.filter(row=>row&&row.osm_id&&Number.isFinite(Number(row.aspect_ratio))&&Number.isFinite(Number(row.circularity)));
  if(!rows.length) {
    plot.innerHTML='<text class="spectrum-axis-label" x="24" y="42">No measured r × C rows are available in this view.</text>';
    plot.setAttribute('aria-label','No measured aspect-ratio and circularity rows are available in this report view');
    if(legend) legend.innerHTML='';
    set(countValue,'0'); set(medianValueElement,'—'); set(signalsValue,'—'); set(status,'Measured field unavailable'); set(title,'The measured constellation is not reported.'); set(text,`The ${scope} carries no rows with both aspect ratio and circularity.`); set(note,`No r × C constellation can be drawn from the ${scope}. Clear filters or widen the report view to restore the field.`);
    return;
  }
  const ratios=rows.map(row=>Number(row.aspect_ratio)), circularities=rows.map(row=>Number(row.circularity)), medianR=medianValue(ratios), medianC=medianValue(circularities), ratioSignals=rows.filter(row=>rowHasSignal(row,'golden_ratio')).length, angleSignals=rows.filter(row=>rowHasSignal(row,'golden_angle')).length, maxPoints=180, stride=Math.max(1,Math.ceil(rows.length/maxPoints)), points=rows.filter((row,index)=>index%stride===0).slice(0,maxPoints), sortedRatios=ratios.slice().sort((a,b)=>a-b), p97=sortedRatios[Math.min(sortedRatios.length-1,Math.floor(sortedRatios.length*.97))], xMin=.5, xMax=Math.max(2.2,Math.min(6,Math.ceil((Number.isFinite(p97)?p97:2.2)*10)/10)), clipped=rows.filter(row=>Number(row.aspect_ratio)<xMin||Number(row.aspect_ratio)>xMax).length;
  const width=760, height=260, left=54, right=24, top=18, bottom=37, plotWidth=width-left-right, plotHeight=height-top-bottom, clamp=value=>Math.max(0,Math.min(1,value)), x=value=>left+clamp((Number(value)-xMin)/(xMax-xMin))*plotWidth, y=value=>top+(1-clamp(Number(value)))*plotHeight, xTicks=[...new Set([xMin,1,1.618,2,xMax].map(value=>Number(value.toFixed(3))))].sort((a,b)=>a-b), yTicks=[0,.5,1], xGrid=xTicks.map(value=>`<line class="spectrum-grid-line" x1="${x(value).toFixed(1)}" y1="${top}" x2="${x(value).toFixed(1)}" y2="${(top+plotHeight).toFixed(1)}"/><text class="spectrum-axis-label" x="${x(value).toFixed(1)}" y="${height-16}" text-anchor="middle">${value.toFixed(value===1.618?3:1)}</text>`).join(''), yGrid=yTicks.map(value=>`<line class="spectrum-grid-line" x1="${left}" y1="${y(value).toFixed(1)}" x2="${(left+plotWidth).toFixed(1)}" y2="${y(value).toFixed(1)}"/><text class="spectrum-axis-label" x="${left-9}" y="${(y(value)+3).toFixed(1)}" text-anchor="end">${value.toFixed(1)}</text>`).join(''), phiX=x(1.618), medianX=x(medianR), medianY=y(medianC), pointMarkup=points.map(row=>{ const id=String(row.osm_id), titleText=contextTitle(row), place=contextPlaceText(row), signal=contextSignalText(row), ratio=Number(row.aspect_ratio), circularity=Number(row.circularity), label=`${titleText} · ${place} · r ${fmt(ratio,3)} · C ${fmt(circularity,3)} · ${signal}`; return `<circle class="spectrum-dot" cx="${x(ratio).toFixed(1)}" cy="${y(circularity).toFixed(1)}" r="3.4" fill="${spectrumGroupColor(row.group)}" data-spectrum-id="${esc(id)}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title></circle>`; }).join('');
  plot.innerHTML=`<rect x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight}" fill="rgba(7,29,32,.22)"/><g aria-hidden="true">${xGrid}${yGrid}<line class="spectrum-axis" x1="${left}" y1="${top+plotHeight}" x2="${left+plotWidth}" y2="${top+plotHeight}"/><line class="spectrum-axis" x1="${left}" y1="${top}" x2="${left}" y2="${top+plotHeight}"/><line class="spectrum-guide" x1="${phiX.toFixed(1)}" y1="${top}" x2="${phiX.toFixed(1)}" y2="${top+plotHeight}"/><text class="spectrum-guide-label" x="${Math.min(left+plotWidth-20,phiX+5).toFixed(1)}" y="${top+11}">φ 1.618</text><line class="spectrum-median-guide" x1="${medianX.toFixed(1)}" y1="${top}" x2="${medianX.toFixed(1)}" y2="${top+plotHeight}"/><line class="spectrum-median-guide" x1="${left}" y1="${medianY.toFixed(1)}" x2="${left+plotWidth}" y2="${medianY.toFixed(1)}"/></g><g>${pointMarkup}</g><text class="spectrum-axis-label" x="${left+plotWidth/2}" y="${height-3}" text-anchor="middle">r / aspect ratio →</text><text class="spectrum-axis-label" transform="translate(12 ${top+plotHeight/2}) rotate(-90)" text-anchor="middle">C / circularity →</text>`;
  plot.setAttribute('aria-label',`Measured footprint constellation for ${points.length.toLocaleString()} sampled rows from ${rows.length.toLocaleString()} visible rows; x aspect ratio r from ${xMin} to ${xMax}, y circularity C from 0 to 1; dashed φ guide at 1.618.`);
  if(countValue) countValue.textContent=`${points.length.toLocaleString()} / ${rows.length.toLocaleString()}`;
  if(medianValueElement) medianValueElement.textContent=`${fmt(medianR,3)} · ${fmt(medianC,3)}`;
  if(signalsValue) signalsValue.textContent=`${ratioSignals.toLocaleString()} φ · ${angleSignals.toLocaleString()} θ`;
  if(status) status.textContent=`Measured field / ${scope}`;
  if(title) title.textContent='Select a point to read its building field.';
  if(text) text.textContent=`${rows.length.toLocaleString()} visible rows; the plot samples ${points.length.toLocaleString()} deterministically for a readable constellation. Dashed φ is a screening reference, not a design claim.`;
  if(note) note.textContent=`Showing ${points.length.toLocaleString()} of ${rows.length.toLocaleString()} rows from the ${scope}; ${clipped.toLocaleString()} aspect values fall outside the displayed ${xMin}–${xMax} window. Click or focus a point to open its mapped dossier. r is length/width; C is 4πA/P².`;
  if(legend) {
    const groups=new Map(); rows.forEach(row=>{ const key=String(row.group||'other'); groups.set(key,(groups.get(key)||0)+1); });
    legend.innerHTML=[...groups.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(([group,count])=>`<span class="spectrum-legend-chip"><i style="background:${spectrumGroupColor(group)}"></i>${esc(spatialGroupLabel(group))} ${count.toLocaleString()}</span>`).join('');
  }
  const readPoint=event=>{ const dot=event.target.closest?.('circle[data-spectrum-id]'); if(!dot) return; const row=targetRowForId(dot.dataset.spectrumId); if(!row) return; const titleText=contextTitle(row), place=contextPlaceText(row), signal=contextSignalText(row); set(status,`Point / ${titleText}`); set(title,`${titleText}${place?` · ${place}`:''}`); set(text,`r ${fmt(row.aspect_ratio,3)} · C ${fmt(row.circularity,3)} · ${signal} · ${spatialGroupLabel(row.group)}. Focus the point to open its source-linked building dossier.`); };
  plot.onpointerover=readPoint;
  plot.onfocusin=readPoint;
}
function setMakerQuery(name) {
  const input=$('query');
  if(!input||!name) return;
  input.value=input.value.trim().toLowerCase()===String(name).toLowerCase()?'':String(name);
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function setRhythmGroup(group) {
  const select=$('group');
  if(!select||!group||![...select.options].some(option=>option.value===group)) return;
  select.value=select.value===group?'':group;
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function renderCultureAtlas() {
  const culture=SUMMARY.culture || {};
  const total=Math.max(1,Number(SUMMARY.targets||0));
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const count=value=>Number(value||0).toLocaleString();
  set('cultureNamedCount',count(culture.named_places));
  set('cultureNamedShare',fmt(Number(culture.named_places||0)/total*100,1)+'%');
  set('cultureHeritageCount',count(culture.heritage_joins));
  set('cultureSharedLifeCount',count(culture.shared_life));
  set('cultureCivicLifeCount',count(culture.civic_life));
  set('cultureCountyCount',count(culture.county_contexts));
  const topEntries=Object.entries(culture.top_counties || {});
  const top=topEntries.map(([name,value])=>name+' '+count(value));
  set('cultureTopCounties',top.length ? 'Most represented heritage-linked contexts in this pack: '+top.join(' · ')+'.' : 'County context is not available in this report pack.');
  const selectedCounty=$('county')?.value||'';
  set('countyFieldStatus',selectedCounty||'All counties');
  const countyChips=$('countyChips');
  if(countyChips) countyChips.innerHTML=topEntries.length
    ? topEntries.map(([name,value])=>`<button class="county-chip" type="button" data-county-focus="${esc(name)}" aria-pressed="${name===selectedCounty}">${esc(name)} · ${count(value)}</button>`).join('')
    : '<span class="footnote">No county contexts are available in this report pack.</span>';
  renderCountyFieldNote();
  renderCountyPulse();
  renderSpatialRhythm();
  renderScaleField();
  renderAlignmentField();
  renderSourceRoots();
  renderTrustField();
  renderMakersField();
  renderHeritageTimeline();
  renderHeritageTypology();
  renderPlaceBraid();
  renderLandField();
  renderPlaceNameField();
  renderGeometrySpectrum();
  document.querySelectorAll('.culture-focus').forEach(button=>{
    const active=button.dataset.cultureFocus===$('cultureLens')?.value;
    button.setAttribute('aria-pressed',String(active));
  });
  renderCultureReadingContext();
}

function renderCultureReadingContext() {
  const panel=$('cultureReadingContext');
  if(!panel) return;
  const id=selectedMarkerId||offlineSelection, row=targetRowForId(id);
  if(!row) { panel.hidden=true; return; }
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const lens=culturalLensForRow(row), niah=row.niah||{}, type=String(niah.type||'').trim();
  const context=[selectionPlaceText(row),lens?CULTURE_LENS_LABELS[lens]:'Áit / place context',type?heritageTypeLabel(type):''].filter(Boolean).join(' · ');
  set('cultureReadingContextTitle',contextTitle(row));
  set('cultureReadingContextText',`${context}. Read the source-linked context beside the mapped footprint; it is not a claim about historic intent.`);
  panel.hidden=false;
}

function renderSummary() {
  const total=SERVER_MODE ? Number(pageStats.total||0) : filtered.length;
  const golden=SERVER_MODE ? Number(pageStats.matching_golden_angle||0) : filtered.filter(row=>row.has_golden_angle).length;
  const niah=SERVER_MODE ? Number(pageStats.matching_niah||0) : filtered.filter(row=>row.niah.reg_no).length;
  $('kTargets').textContent=total.toLocaleString();
  $('kControls').textContent=Number(SUMMARY.controls||0).toLocaleString();
  $('kGolden').textContent=golden.toLocaleString();
  $('kNiah').textContent=niah.toLocaleString();
  $('kPatterns').textContent=PATTERN_CATALOG.filter(item=>Number(item.count||0)>0).length.toLocaleString();
  $('count').textContent=`${total.toLocaleString()} matching targets · showing up to ${PAGE_SIZE} per page`;
  const activeCounty=$('county')?.value;
  $('activeCounty').textContent=activeCounty?`County: ${activeCounty}`:'';
 $('activePattern').textContent=$('pattern').value?`Pattern: ${patternLabel($('pattern').value)}`:'';
  const activeCulture=$('cultureLens')?.value;
  $('activeCulture').textContent=activeCulture?`Culture: ${CULTURE_LENS_LABELS[activeCulture]||activeCulture}`:'';
  renderQuickViews();
  $('reviewCoverage').textContent=`Expert review queue: ${Number(SUMMARY.review_queue_targets||0).toLocaleString()} of ${Number(SUMMARY.targets||0).toLocaleString()} targets (${fmt(SUMMARY.review_queue_coverage_pct,2)}%); unqueued targets are labeled explicitly.`;
}
function renderPatternCatalog() {
  const active=$('pattern').value;
  const total=Number(SUMMARY.targets||0);
  const visibleCount=PATTERN_CATALOG.filter(item=>Number(item.count||0)>0).length;
  $('patternSummary').textContent=active
    ? `${patternLabel(active)} selected · ${Number(PATTERN_BY_KEY.get(active)?.count||0).toLocaleString()} of ${total.toLocaleString()} targets carry this flag.`
    : `${visibleCount.toLocaleString()} pattern types found across ${total.toLocaleString()} targets. Cards are ordered by the catalogue; counts are target-level flags.`;
  const maxCount=Math.max(1,...PATTERN_CATALOG.map(item=>Number(item.count||0)));
  $('patternCatalog').innerHTML=PATTERN_CATALOG.map(item=>{
    const count=Number(item.count||0), activeClass=item.key===active?' active':'';
    const width=count?Math.max(2,Math.round(count/maxCount*100)):0;
    return `<button type="button" class="pattern-card${activeClass}" data-pattern="${esc(item.key)}" aria-pressed="${item.key===active}"><span class="pattern-card-top"><span><span class="pattern-category">${esc(item.category||'Pattern')}</span><br><b class="pattern-card-label">${esc(item.label||patternLabel(item.key))}</b></span><b class="pattern-count">${count.toLocaleString()}</b></span><span class="pattern-meter" aria-hidden="true"><span class="pattern-meter-fill" style="width:${width}%"></span></span><span class="pattern-card-meta">${fmt(item.pct,2)}% of targets</span><span class="pattern-description">${esc(item.description||'Geometry screening flag.')}</span></button>`;
  }).join('') || '<p class="footnote">No geometric pattern flags are available.</p>';
}
function setPatternFilter(key) { $('pattern').value=$('pattern').value===key?'':key; applyFilters(); }
function clearPatternFilter() { $('pattern').value=''; applyFilters(); }
function setCultureFilter(key) {
  $('cultureLens').value=$('cultureLens').value===key?'':key;
  setAtlasNavActive('filters');
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function setCountyFilter(key) {
  $('county').value=$('county').value===key?'':key;
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function quickViewBaseIsNeutral() {
  return !$('query').value.trim() && !$('group').value && !$('century').value && !$('rating').value && !$('niahType').value && !$('county').value && !$('pattern').value && !$('reviewState').value && !$('onlyRatio').checked && !$('onlyCircular').checked && !$('onlyMulti').checked;
}
function currentQuickView() {
  if(!quickViewBaseIsNeutral()) return '';
  const culture=$('cultureLens').value, score=Number($('score').value), angle=$('onlyAngle').checked;
  if(culture && score===0 && !angle && Object.prototype.hasOwnProperty.call(CULTURE_LENS_LABELS,culture)) return culture;
  if(!culture && score===60 && angle) return 'signals';
  if(!culture && score===0 && !angle) return 'all';
  return '';
}
function renderQuickViews() {
  const active=currentQuickView();
  document.querySelectorAll('.quick-view').forEach(button=>{
    const selected=button.dataset.quickView===active;
    button.classList.toggle('active',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
}
function applyQuickView(key) {
  if(key==='all') { clearAllFilters(); $('filters')?.scrollIntoView({behavior:'smooth',block:'start'}); return; }
  heritageEraKey='';
  $('query').value='';
  for(const [id] of VIEW_SELECTS) $(id).value='';
  for(const [id] of VIEW_CHECKS) $(id).checked=false;
  $('score').value='0'; $('onlyAngle').checked=false;
  if(['named','heritage','civic'].includes(key)) $('cultureLens').value=key;
  if(key==='signals') { $('score').value='60'; $('onlyAngle').checked=true; }
  $('scoreValue').textContent=$('score').value; sortKey='score'; sortDesc=true; applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function clearAllFilters() {
  heritageEraKey='';
  $('query').value='';
  for(const [id] of VIEW_SELECTS) $(id).value='';
  for(const [id] of VIEW_CHECKS) $(id).checked=false;
  $('score').value='0'; $('scoreValue').textContent='0'; sortKey='score'; sortDesc=true; applyFilters();
}
document.addEventListener('click',event=>{
  const walkNav=event.target.closest?.('button[data-field-walk-nav]');
  if(walkNav?.dataset.fieldWalkNav) { moveFieldWalk(walkNav.dataset.fieldWalkNav==='next'?1:-1); return; }
  const walk=event.target.closest?.('button[data-field-walk-id]');
  if(walk?.dataset.fieldWalkId) { focusRow(walk.dataset.fieldWalkId,{scroll:true,openPopup:true}); return; }
  const quick=event.target.closest?.('button.quick-view');
  if(quick) { applyQuickView(quick.dataset.quickView); return; }
  const maths=event.target.closest?.('button.maths-card');
  if(maths) { selectMathCard(maths.dataset.mathKey); return; }
  const principle=event.target.closest?.('button[data-field-principle]');
  if(principle?.dataset.fieldPrinciple) { selectFieldPrinciple(principle.dataset.fieldPrinciple); return; }
  const heritageEra=event.target.closest?.('button[data-heritage-era]');
  if(heritageEra?.dataset.heritageEra) { setHeritageEra(heritageEra.dataset.heritageEra); return; }
  const heritageType=event.target.closest?.('button[data-heritage-type]');
  if(heritageType?.dataset.heritageType) { setHeritageType(heritageType.dataset.heritageType); return; }
  const braidType=event.target.closest?.('button[data-braid-type]');
  if(braidType?.dataset.braidCounty&&braidType?.dataset.braidType) { setPlaceBraidType(braidType.dataset.braidCounty,braidType.dataset.braidType); return; }
  const braidCounty=event.target.closest?.('button[data-braid-county]');
  if(braidCounty?.dataset.braidCounty) { setCountyFilter(braidCounty.dataset.braidCounty); return; }
  const card=event.target.closest?.('button.pattern-card');
  if(card) { setPatternFilter(card.dataset.pattern); return; }
  const culture=event.target.closest?.('button.culture-focus');
  if(culture) { setCultureFilter(culture.dataset.cultureFocus); return; }
  const county=event.target.closest?.('button[data-county-focus]');
  if(county?.dataset.countyFocus) { setCountyFilter(county.dataset.countyFocus); return; }
  const rhythm=event.target.closest?.('button[data-rhythm-group]');
  if(rhythm?.dataset.rhythmGroup) { setRhythmGroup(rhythm.dataset.rhythmGroup); return; }
  const scale=event.target.closest?.('button[data-scale-group]');
  if(scale?.dataset.scaleGroup) { setRhythmGroup(scale.dataset.scaleGroup); return; }
  const alignment=event.target.closest?.('button[data-alignment-group]');
  if(alignment?.dataset.alignmentGroup) { setRhythmGroup(alignment.dataset.alignmentGroup); return; }
  const land=event.target.closest?.('button[data-land-group]');
  if(land?.dataset.landGroup && !land.disabled) { setLandGroup(land.dataset.landGroup); return; }
  const maker=event.target.closest?.('button[data-maker-name]');
  if(maker?.dataset.makerName) { setMakerQuery(maker.dataset.makerName); return; }
  const placeName=event.target.closest?.('button[data-place-name]');
  if(placeName?.dataset.placeName) { setPlaceName(placeName.dataset.placeName); return; }
  const spectrum=event.target.closest?.('circle[data-spectrum-id]');
  if(spectrum?.dataset.spectrumId) { focusRow(spectrum.dataset.spectrumId,{scroll:true,openPopup:true}); return; }
  if(event.target.closest?.('#copyPlaceNameLink')) { copyPlaceNameLink(); return; }
  const carry=event.target.closest?.('#carrySelectionToStudio');
  if(carry?.dataset.carryStudio) { carrySelectionToStudio(carry.dataset.carryStudio); return; }
  const carryJourney=event.target.closest?.('#fieldJourneyCarry');
  if(carryJourney?.dataset.carryJourney) { carryJourneyToStudio(); return; }
  const addCompare=event.target.closest?.('#addSelectionCompare');
  if(addCompare?.dataset.compareTarget) { addComparisonTarget(addCompare.dataset.compareTarget); return; }
  const removeCompare=event.target.closest?.('button[data-compare-remove]');
  if(removeCompare?.dataset.compareRemove) { removeComparisonTarget(removeCompare.dataset.compareRemove); return; }
  if(event.target.closest?.('#copyComparisonLink')) { copyComparisonLink(); return; }
  if(event.target.closest?.('#clearComparison')) { clearComparison(); return; }
  const sequence=event.target.closest?.('button[data-sequence-target]');
  if(sequence?.dataset.sequenceTarget) { focusAtlasTarget(sequence.dataset.sequenceTarget,sequence.dataset.sequenceSection||'field'); return; }
  const mathsRead=event.target.closest?.('button[data-maths-read]');
  if(mathsRead?.dataset.mathsRead) { focusAtlasSection(mathsRead.dataset.mathsRead); return; }
  const cultureRead=event.target.closest?.('button[data-culture-read]');
  if(cultureRead?.dataset.cultureRead) { focusAtlasSection(cultureRead.dataset.cultureRead); return; }
  const mathsReturn=event.target.closest?.('#mathsReadingReturn');
  if(mathsReturn) {
    const selection=$('selectionCard');
    if(selection&&!selection.hidden) { setAtlasNavActive('filters'); revealSelectionCard(selection); }
    return;
  }
  const returnToDossier=event.target.closest?.('#cultureReadingReturn');
  if(returnToDossier) {
    const selection=$('selectionCard');
    if(selection&&!selection.hidden) { setAtlasNavActive('filters'); revealSelectionCard(selection); }
    return;
  }
  const selectionCulture=event.target.closest?.('button[data-selection-culture]');
  if(selectionCulture?.dataset.selectionCulture) { setCultureFilter(selectionCulture.dataset.selectionCulture); return; }
  const contextFocus=event.target.closest?.('button[data-context-focus]');
  if(contextFocus?.dataset.contextFocus) { focusRow(contextFocus.dataset.contextFocus,{scroll:true,openPopup:false}); return; }
  if(event.target.closest?.('#downloadSelectionPassport')) { downloadSelectionPassport(); return; }
  if(event.target.closest?.('#focusSelectionMap')) { focusSelectedMap(); return; }
  if(event.target.closest?.('#copySelectionLink')) { copySelectionLink(); return; }
  if(event.target.closest?.('#clearSelection')) { clearSelection(); return; }
  if(event.target.closest?.('#clearPattern')) { clearPatternFilter(); return; }
  if(event.target.closest?.('#clearFilters')) { clearAllFilters(); }
});
document.addEventListener('change',event=>{
  if(event.target?.id==='century') { heritageEraKey=''; renderHeritageTimeline(); }
  if(event.target?.id==='pattern'||event.target?.id==='cultureLens'||event.target?.id==='county'||event.target?.id==='century') queueFilters();
});
document.addEventListener('keydown',event=>{ if(event.key==='Escape'&&!$('selectionCard')?.hidden) clearSelection(); });
function interpretationStatusClass(value) { return String(value||'not_reported').toLowerCase().replace(/[^a-z0-9_-]/g,'_'); }
function renderInterpretation() {
  const findings=INTERPRETATION.findings||[], caveats=INTERPRETATION.caveats||[];
  if(!findings.length && !caveats.length) { $('interpretation').innerHTML='<p class="footnote">No data-derived interpretation is available.</p>'; return; }
  const headline=INTERPRETATION.headline?`<p class="interpretation-headline">${esc(INTERPRETATION.headline)}</p>`:'';
  const cards=findings.map(item=>`<article class="interpretation-card"><header><b>${esc(item.title||'Finding')}</b><span class="interpretation-status ${interpretationStatusClass(item.status)}">${esc(item.status||'not reported')}</span></header><p>${esc(item.text||'')}</p></article>`).join('');
  const caveatBlock=caveats.length?`<ul class="interpretation-caveats">${caveats.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:'';
  $('interpretation').innerHTML=`${headline}<div class="interpretation-grid">${cards}</div>${caveatBlock}`;
}
function flagHtml(row) { return row.flags.slice(0,5).map(flag=>`<span class="flag">${esc(patternLabel(flag))}</span>`).join('') + (row.flags.length>5?` <span class="footnote">+${row.flags.length-5} more</span>`:'') || '<span class="footnote">none</span>'; }
function reviewHref(row) { return `review.html?osm_id=${encodeURIComponent(row.osm_id)}`; }
function reviewFilterState(row) { return row.review?.in_queue ? String(row.review?.label||'not_reviewed') : 'not_queued'; }
function reviewState(row) { return reviewFilterState(row).replaceAll('_',' '); }
function reviewLink(row,label) { const queued=Boolean(row.review?.in_queue); const title=queued?'Open the current expert review queue':'Target is outside the current top-1,000 expert review queue'; return `<a class="review-link" href="${reviewHref(row)}" title="${esc(title)}" target="_blank" rel="noopener">${esc(label)}</a>`; }
function reviewCell(row) { return reviewLink(row,reviewState(row)); }
function culturalLensForRow(row) {
  if(row.spatial?.settlement_class==='named_place') return 'named';
  if(row.niah?.reg_no) return 'heritage';
  if(['government','civic'].includes(row.group)) return 'civic';
  if(row.group==='worship') return 'pobal';
  return '';
}
function selectionPlaceText(row) {
  const settlement=String(row.spatial?.settlement_name||'').trim();
  const county=String(row.spatial?.county||row.niah?.county||'').trim();
  const kind=String(row.spatial?.settlement_class||'').replaceAll('_',' ');
  return [settlement,county].filter(Boolean).join(' · ') || (kind && kind!=='unknown' ? kind : row.address_city || 'Context not reported');
}
function selectionHeritageText(row) {
  const niah=row.niah||{};
  if(!niah.reg_no) return 'No NIAH join in this snapshot';
  return [niah.name||'NIAH-linked record',niah.reg_no,niah.rating,niah.century].filter(Boolean).join(' · ');
}
function selectionGeometryText(row) {
  const flags=row.flags||[];
  if(!flags.length) return 'No screening flags';
  const labels=flags.slice(0,3).map(patternLabel);
  return labels.join(' · ')+(flags.length>3?` · +${flags.length-3} more`:'');
}
function selectionMathText(row) {
  const fourier=[row.fourier_1,row.fourier_2,row.fourier_3,row.fourier_4].map(value=>fmt(value,3)).join(' / ');
  return [`A ${fmt(row.area_m2,1)} m²`,`P ${fmt(row.perimeter_m,1)} m`,`l×w ${fmt(row.length_m,1)} × ${fmt(row.width_m,1)} m`,`r ${fmt(row.aspect_ratio,3)}`,`C ${fmt(row.circularity,3)}`,`R ${fmt(row.rectangularity,3)}`,`σᵣ/μᵣ ${fmt(row.radial_cv,3)}`,`F₁…₄ ${fourier}`].join(' · ');
}
function renderSelectionCulturalTrace(row) {
  const trace=$('selectionCultureTrace'), title=$('selectionCultureTraceTitle'), intro=$('selectionCultureTraceIntro'), lensValue=$('selectionCultureLens'), contextValue=$('selectionCultureContext'), read=$('selectionCultureRead'), filter=$('selectionCulture'), note=$('selectionCultureNote');
  if(!trace) return;
  const lens=culturalLensForRow(row), place=selectionPlaceText(row), hasPlace=place&&place!=='Context not reported', niah=row.niah||{}, type=String(niah.type||'').trim(), record=[type,niah.rating,niah.century].filter(Boolean).join(' · '), hasContext=Boolean(lens||hasPlace||niah.reg_no||row.spatial?.county||row.address_city);
  trace.hidden=!hasContext;
  if(!hasContext) return;
  const lensLabel=lens?CULTURE_LENS_LABELS[lens]:'Áit / place context';
  const sourceLabel=record||(niah.reg_no?`NIAH ${niah.reg_no}`:`${spatialGroupLabel(row.group)} record`);
  if(title) title.textContent=hasPlace?`${lensLabel} / ${place}`:`${lensLabel} / context carried by this row`;
  if(lensValue) lensValue.textContent=lensLabel;
  if(contextValue) contextValue.textContent=sourceLabel;
  if(lens==='named') {
    if(intro) intro.textContent=`The row carries a named settlement context: ${place}. Read that name beside the mapped footprint, then bring local knowledge into the interpretation.`;
  } else if(lens==='heritage') {
    if(intro) intro.textContent=`The row carries a source-linked heritage join${record?` (${record})`:''}. Read the inventory beside the shape without turning a type into a cultural proof.`;
  } else if(lens==='pobal') {
    if(intro) intro.textContent=`The row sits in the worship/shared-life lens${hasPlace?` at ${place}`:''}. Read the mapped room beside its community context, not as a formula for belief or belonging.`;
  } else if(lens==='civic') {
    if(intro) intro.textContent=`The row enters the civic/public-institution lens${hasPlace?` at ${place}`:''}. Carry its measured scale into the shared-space question while keeping the source boundary visible.`;
  } else if(intro) {
    intro.textContent=`The row carries ${hasPlace?`${place} and `:''}${sourceLabel} as context. The atlas keeps that context beside the measurement rather than inventing a cultural reading.`;
  }
  if(read) read.dataset.cultureRead='culture';
  if(filter) {
    filter.hidden=!lens;
    filter.dataset.selectionCulture=lens||'';
    filter.textContent=lens?`Filter ${CULTURE_LENS_LABELS[lens]} →`:'Filter this lens →';
  }
  if(note) note.textContent=`Context comes from the report row’s place, group, and source fields. It is a prompt for a more grounded reading—not evidence that the mapped geometry caused, represents, or proves a cultural tradition.`;
}
function geometryOuterRing(feature) {
  const geometry=feature?.geometry;
  if(!geometry) return [];
  const candidates=geometry.type==='Polygon' ? [geometry.coordinates?.[0]] : geometry.type==='MultiPolygon' ? (geometry.coordinates||[]).map(polygon=>polygon?.[0]) : [];
  return candidates.filter(ring=>Array.isArray(ring)&&ring.length>=3).sort((a,b)=>b.length-a.length)[0]||[];
}
function passportLine(value,max=54) {
  const text=String(value??'').trim()||'Not reported';
  return text.length>max ? `${text.slice(0,Math.max(1,max-1))}…` : text;
}
function passportFileName(value) {
  const slug=String(value??'field').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,56);
  return `${slug||'irish-field'}-field-passport.svg`;
}
function passportPathData(row) {
  const feature=GEOJSON_BY_ID.get(String(row?.osm_id||'')), ring=geometryOuterRing(feature);
  const points=ring.filter(pair=>Array.isArray(pair)&&pair.length>=2&&Number.isFinite(Number(pair[0]))&&Number.isFinite(Number(pair[1]))).map(pair=>[Number(pair[0]),Number(pair[1])]);
  const x=770, y=212, width=340, height=340;
  if(points.length>=3) {
    const xs=points.map(pair=>pair[0]), ys=points.map(pair=>pair[1]), minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys), rangeX=Math.max(1e-12,maxX-minX), rangeY=Math.max(1e-12,maxY-minY), scale=Math.min((width-36)/rangeX,(height-36)/rangeY), usedW=rangeX*scale, usedH=rangeY*scale, left=x+(width-usedW)/2, top=y+(height-usedH)/2;
    return {d:points.map((pair,index)=>`${index?'L':'M'} ${(left+(pair[0]-minX)*scale).toFixed(1)} ${(top+(maxY-pair[1])*scale).toFixed(1)}`).join(' ')+' Z',source:'mapped outline',vertices:Math.max(0,points.length-1)};
  }
  const aspect=Math.max(.3,Math.min(3.6,Number(row?.aspect_ratio)||1)), boxW=aspect>=1?Math.min(width*.72,100+aspect*48):Math.max(72,100*aspect), boxH=aspect>=1?Math.max(74,100/aspect):Math.min(160,100/aspect), left=x+(width-boxW)/2, top=y+(height-boxH)/2;
  return {d:`M ${left.toFixed(1)} ${top.toFixed(1)} H ${(left+boxW).toFixed(1)} V ${(top+boxH).toFixed(1)} H ${left.toFixed(1)} Z`,source:'descriptor guide',vertices:0};
}
function downloadSelectionPassport() {
  const id=selectedMarkerId||offlineSelection, status=$('selectionPassportStatus'), row=targetRowForId(id);
  if(!row) { if(status) status.textContent='Select a place first.'; return; }
  const niah=row.niah||{}, spatial=row.spatial||{}, name=passportLine(contextTitle(row),44), place=passportLine(selectionPlaceText(row),48), heritage=passportLine(niah.reg_no?[niah.name||'NIAH-linked record',niah.reg_no,niah.rating,niah.century].filter(Boolean).join(' · '):'No NIAH join in this snapshot',54), flags=passportLine((row.flags||[]).length?row.flags.slice(0,6).map(patternLabel).join(' · '):'No screening flags reported',62), coords=`${coordinateLabel(row.lat,'N','S')} / ${coordinateLabel(row.lon,'E','W')}`, path=passportPathData(row), source=row.osm_url||`https://www.openstreetmap.org/${encodeURIComponent(row.osm_id||'')}`, geometryRows=[['AREA',`${fmt(row.area_m2,1)} m²`],['PERIMETER',`${fmt(row.perimeter_m,1)} m`],['LENGTH × WIDTH',`${fmt(row.length_m,1)} × ${fmt(row.width_m,1)} m`],['ASPECT',fmt(row.aspect_ratio,3)],['CIRCULARITY',fmt(row.circularity,3)],['RADIAL CV',fmt(row.radial_cv,3)]];
  const metricSvg=geometryRows.map(([label,value],index)=>{ const column=index%3, rowIndex=Math.floor(index/3), x=72+column*218, y=782+rowIndex*93; return `<g><text x="${x}" y="${y}" fill="#9eb4a8" font-size="13" font-weight="700" letter-spacing="2">${esc(label)}</text><text x="${x}" y="${y+31}" fill="#f7f0df" font-size="24" font-weight="700">${esc(value)}</text></g>`; }).join('');
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1500" viewBox="0 0 1200 1500" role="img" aria-labelledby="passportTitle passportDescription">
  <title id="passportTitle">Cruth field passport — ${esc(name)}</title>
  <desc id="passportDescription">A measured visual record of ${esc(name)} in ${esc(place)}. It describes mapped geometry and source context; it does not establish historic intent.</desc>
  <defs><linearGradient id="passportBg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d3030"/><stop offset="1" stop-color="#173e3c"/></linearGradient><pattern id="passportGrid" width="42" height="42" patternUnits="userSpaceOnUse"><path d="M 42 0 L 0 0 0 42" fill="none" stroke="#d8bd72" stroke-opacity=".12" stroke-width="1"/></pattern></defs>
  <rect width="1200" height="1500" fill="url(#passportBg)"/><rect x="34" y="34" width="1132" height="1432" rx="26" fill="none" stroke="#e0bd6e" stroke-opacity=".55" stroke-width="2"/><rect x="58" y="58" width="1084" height="1384" rx="18" fill="url(#passportGrid)" opacity=".62"/>
  <text x="72" y="110" fill="#e0bd6e" font-size="15" font-weight="800" letter-spacing="3">CRUTH / FIELD PASSPORT</text><text x="1128" y="110" text-anchor="end" fill="#9eb4a8" font-size="13" font-weight="700" letter-spacing="2">IRELAND · ${esc(path.source.toUpperCase())}</text>
  <text x="72" y="190" fill="#f7f0df" font-size="58" font-family="Georgia,serif" font-weight="700">${esc(name)}</text><text x="72" y="234" fill="#e0bd6e" font-size="18" font-weight="700" letter-spacing="2">${esc(coords)} · ${esc(row.osm_id||'OSM target')}</text>
  <line x1="72" y1="270" x2="1128" y2="270" stroke="#e0bd6e" stroke-opacity=".42"/>
  <text x="72" y="326" fill="#9eb4a8" font-size="13" font-weight="800" letter-spacing="2">PLACE / AIT</text><text x="72" y="362" fill="#f7f0df" font-size="28" font-family="Georgia,serif">${esc(place)}</text><text x="72" y="400" fill="#b8c8bd" font-size="16">${esc(String(row.group||'other'))} · ${esc(String(spatial.settlement_class||'context not reported').replaceAll('_',' '))}</text>
  <rect x="736" y="176" width="404" height="432" rx="18" fill="#f5efe0" fill-opacity=".92"/><path d="${path.d}" fill="#6d9b8f" fill-opacity=".36" stroke="#315c57" stroke-width="4"/><circle cx="940" cy="382" r="18" fill="#e0bd6e" stroke="#173e3c" stroke-width="3"/><circle cx="940" cy="382" r="58" fill="none" stroke="#bf5b45" stroke-opacity=".7" stroke-dasharray="7 9" stroke-width="2"/><text x="940" y="578" text-anchor="middle" fill="#315c57" font-size="13" font-weight="800" letter-spacing="2">${esc(path.source.toUpperCase())} · ${path.vertices?`${path.vertices} VERTICES`:'ASPECT GUIDE'}</text>
  <text x="72" y="508" fill="#9eb4a8" font-size="13" font-weight="800" letter-spacing="2">OIDHREACHT / HERITAGE</text><text x="72" y="545" fill="#f7f0df" font-size="22" font-family="Georgia,serif">${esc(heritage)}</text><text x="72" y="582" fill="#b8c8bd" font-size="15">Source geometry remains open to inspection at OpenStreetMap.</text>
  <rect x="72" y="640" width="1056" height="1" fill="#e0bd6e" fill-opacity=".42"/><text x="72" y="704" fill="#e0bd6e" font-size="15" font-weight="800" letter-spacing="3">MEASURED GEOMETRY / SNAPSHOT</text>${metricSvg}
  <rect x="72" y="1006" width="1056" height="1" fill="#e0bd6e" fill-opacity=".42"/><text x="72" y="1070" fill="#e0bd6e" font-size="15" font-weight="800" letter-spacing="3">SIGNALS TO INSPECT</text><text x="72" y="1112" fill="#f7f0df" font-size="22" font-family="Georgia,serif">${esc(flags)}</text><text x="72" y="1154" fill="#b8c8bd" font-size="15">Descriptors and screening flags are prompts for place-based review.</text>
  <rect x="72" y="1212" width="1056" height="132" rx="14" fill="#f7f0df" fill-opacity=".1" stroke="#9eb4a8" stroke-opacity=".42"/><text x="98" y="1252" fill="#e0bd6e" font-size="13" font-weight="800" letter-spacing="2">EVIDENCE BOUNDARY</text><text x="98" y="1285" fill="#f7f0df" font-size="16">This card records mapped geometry in a dated research snapshot.</text><text x="98" y="1315" fill="#b8c8bd" font-size="14">It is not proof of historic intention, authorship, cultural origin, planning compliance, or a single Irish architectural tradition.</text>
  <text x="72" y="1400" fill="#9eb4a8" font-size="12">${esc(source)} · Generated by Cruth / Ireland Field Atlas · ${esc(String(SUMMARY.generated_at||'snapshot unavailable'))}</text>
</svg>`;
  download(passportFileName(contextTitle(row)),svg,'image/svg+xml;charset=utf-8');
  if(status) status.textContent='SVG field passport downloaded.';
}
function drawSelectionFingerprint(row) {
  const canvas=$('selectionFingerprint'), label=$('selectionFingerprintLabel'), text=$('selectionFingerprintText'), note=$('selectionFingerprintNote');
  if(!canvas) return;
  const ctx=canvas.getContext?.('2d');
  if(!ctx) {
    if(label) label.textContent='Boundary fingerprint unavailable';
    if(text) text.textContent='This browser cannot render the normalized canvas view; the measured descriptors remain available below.';
    return;
  }
  const width=canvas.width, height=canvas.height, pad=18;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle='#fbf7ee'; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(53,108,105,.12)'; ctx.lineWidth=1;
  for(let x=pad;x<width-pad;x+=Math.max(34,(width-pad*2)/5)){ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,height-pad);ctx.stroke();}
  for(let y=pad;y<height-pad;y+=Math.max(28,(height-pad*2)/4)){ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(width-pad,y);ctx.stroke();}
  ctx.strokeStyle='rgba(191,91,69,.42)'; ctx.setLineDash([5,5]); ctx.beginPath(); ctx.moveTo(width/2,pad); ctx.lineTo(width/2,height-pad); ctx.stroke(); ctx.beginPath(); ctx.moveTo(pad,height/2); ctx.lineTo(width-pad,height/2); ctx.stroke(); ctx.setLineDash([]);
  const feature=GEOJSON_BY_ID.get(String(row.osm_id)), ring=geometryOuterRing(feature), points=ring.filter(pair=>Array.isArray(pair)&&pair.length>=2&&Number.isFinite(Number(pair[0]))&&Number.isFinite(Number(pair[1]))).map(pair=>[Number(pair[0]),Number(pair[1])]);
  if(points.length>=3) {
    const xs=points.map(pair=>pair[0]), ys=points.map(pair=>pair[1]), minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys), rangeX=Math.max(1e-12,maxX-minX), rangeY=Math.max(1e-12,maxY-minY), scale=Math.min((width-pad*2)/rangeX,(height-pad*2)/rangeY), usedW=rangeX*scale, usedH=rangeY*scale, left=(width-usedW)/2, top=(height-usedH)/2, project=pair=>[left+(pair[0]-minX)*scale,top+(maxY-pair[1])*scale], projected=points.map(project);
    ctx.save(); ctx.setLineDash([5,4]); ctx.strokeStyle='#d0a34c'; ctx.lineWidth=1; ctx.strokeRect(left,top,usedW,usedH); ctx.restore();
    ctx.beginPath(); projected.forEach((point,index)=>index?ctx.lineTo(point[0],point[1]):ctx.moveTo(point[0],point[1])); ctx.closePath(); ctx.fillStyle='rgba(53,108,105,.22)'; ctx.fill(); ctx.strokeStyle='#315c57'; ctx.lineWidth=2; ctx.stroke();
    const center=projected.reduce((sum,point)=>[sum[0]+point[0],sum[1]+point[1]],[0,0]).map(value=>value/projected.length);
    ctx.beginPath(); ctx.arc(center[0],center[1],Math.max(4,Math.min(10,Math.min(usedW,usedH)*.06)),0,Math.PI*2); ctx.fillStyle='#e1bd66'; ctx.fill(); ctx.strokeStyle='#103537'; ctx.lineWidth=1.5; ctx.stroke();
    ctx.beginPath(); ctx.arc(center[0],center[1],Math.max(12,Math.min(35,Math.min(usedW,usedH)*.22)),0,Math.PI*2); ctx.strokeStyle='rgba(191,91,69,.62)'; ctx.lineWidth=1; ctx.setLineDash([3,4]); ctx.stroke(); ctx.setLineDash([]);
    if(label) label.textContent=`Mapped outline / ${feature.geometry.type}`;
    if(text) text.textContent=`${Math.max(0,points.length-1).toLocaleString()} boundary vertices · gold frame = measured extent · centre/ring = normalized display guides.`;
    if(note) note.textContent='GeoJSON source outline · normalized for comparison display only; original coordinates remain in the source map/export.';
    canvas.setAttribute('aria-label',`Normalized mapped boundary fingerprint for ${contextTitle(row)}; ${Math.max(0,points.length-1)} boundary vertices.`);
    return;
  }
  const ratio=Math.max(.25,Math.min(4,Number(row.aspect_ratio)||1)), boxW=ratio>=1?Math.min(150,100+ratio*14):Math.max(58,100*ratio), boxH=ratio>=1?Math.max(48,100/ratio):Math.min(120,100/ratio), left=(width-boxW)/2, top=(height-boxH)/2;
  ctx.save(); ctx.setLineDash([6,5]); ctx.strokeStyle='#d0a34c'; ctx.lineWidth=1.5; ctx.strokeRect(left,top,boxW,boxH); ctx.beginPath(); ctx.ellipse(width/2,height/2,boxW*.42,boxH*.42,0,0,Math.PI*2); ctx.strokeStyle='rgba(53,108,105,.62)'; ctx.stroke(); ctx.restore();
  ctx.beginPath(); ctx.arc(width/2,height/2,5,0,Math.PI*2); ctx.fillStyle='#e1bd66'; ctx.fill(); ctx.strokeStyle='#103537'; ctx.stroke();
  if(label) label.textContent='Descriptor-only guide / outline unavailable';
  if(text) text.textContent='The current view has measured proportions but no source ring to draw; the guide does not invent a boundary.';
  if(note) note.textContent='Geometry source unavailable in this view · measured aspect/circularity remain below.';
  canvas.setAttribute('aria-label',`Descriptor-only geometry guide for ${contextTitle(row)}; mapped boundary unavailable in this view.`);
}
function drawSelectionWeave(row) {
  const canvas=$('selectionWeave'), label=$('selectionWeaveLabel'), text=$('selectionWeaveText'), note=$('selectionWeaveNote');
  if(!canvas) return;
  const ctx=canvas.getContext?.('2d');
  if(!ctx) {
    if(label) label.textContent='Derived print unavailable';
    if(text) text.textContent='This browser cannot render the contemporary field print; the measured descriptors remain available above.';
    return;
  }
  const width=canvas.width, height=canvas.height, cx=width/2, cy=height/2, flags=row.flags||[];
  const group=String(row.group||'').toLowerCase();
  const palette=group==='worship'?['#356c69','#d0a34c','#bf5b45']:group==='government'?['#527b85','#d0a34c','#315c57']:group==='civic'?['#bf5b45','#527b85','#d0a34c']:['#315c57','#8f9f7d','#d0a34c'];
  const symmetryFlag=['rot90_symmetry','rot180_symmetry','pentagonal','hexagonal','octagonal'].find(key=>flags.includes(key));
  const symmetrySteps={rot90_symmetry:4,rot180_symmetry:2,pentagonal:5,hexagonal:6,octagonal:8};
  const golden=Boolean(row.has_golden_angle)||flags.includes('golden_angle'), steps=golden?13:(symmetrySteps[symmetryFlag]||8), step=golden?137.5*Math.PI/180:Math.PI*2/steps;
  const aspect=Math.max(.34,Math.min(3.2,Number(row.aspect_ratio)||1)), circularity=Math.max(.1,Math.min(1,Number(row.circularity)||.5)), radial=Math.max(0,Math.min(1.5,Number(row.radial_cv)||.2)), fourier=Math.max(0,Math.min(1,Math.abs(Number(row.fourier_1)||0)));
  ctx.clearRect(0,0,width,height); ctx.fillStyle='#eef2e8'; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(53,108,105,.12)'; ctx.lineWidth=1;
  for(let x=18;x<width-18;x+=Math.max(34,(width-36)/5)){ctx.beginPath();ctx.moveTo(x,14);ctx.lineTo(x,height-14);ctx.stroke();}
  for(let y=14;y<height-14;y+=Math.max(28,(height-28)/4)){ctx.beginPath();ctx.moveTo(14,y);ctx.lineTo(width-14,y);ctx.stroke();}
  ctx.save(); ctx.translate(cx,cy);
  ctx.strokeStyle='rgba(49,92,87,.25)'; ctx.setLineDash([4,5]); ctx.beginPath(); ctx.moveTo(-width*.42,0); ctx.lineTo(width*.42,0); ctx.moveTo(0,-height*.42); ctx.lineTo(0,height*.42); ctx.stroke(); ctx.setLineDash([]);
  for(let ring=1;ring<=4;ring++) { ctx.beginPath(); ctx.arc(0,0,18+ring*19,0,Math.PI*2); ctx.strokeStyle=`rgba(82,123,133,${(.11+ring*.025).toFixed(2)})`; ctx.lineWidth=1; ctx.stroke(); }
  const petalScale=Math.max(.65,Math.min(1.35,.88+fourier*.48)), baseRadius=golden?18:30;
  for(let i=0;i<steps;i++) {
    const angle=step*i-Math.PI/2, radius=baseRadius+(golden?i*4.3:0), wobble=1+Math.sin(i*1.7)*radial*.12;
    ctx.save(); ctx.rotate(angle); ctx.translate(0,-radius); ctx.scale(petalScale,Math.max(.62,Math.min(1.4,1/aspect))*wobble);
    ctx.beginPath(); ctx.ellipse(0,0,10+circularity*8,24,0,0,Math.PI*2); ctx.fillStyle=`${palette[i%palette.length]}${golden?'30':'24'}`; ctx.fill(); ctx.strokeStyle=palette[i%palette.length]; ctx.lineWidth=1.3; ctx.stroke(); ctx.restore();
  }
  ctx.beginPath(); ctx.arc(0,0,Math.max(8,13+circularity*9),0,Math.PI*2); ctx.fillStyle=palette[0]; ctx.fill(); ctx.strokeStyle=palette[1]; ctx.lineWidth=2; ctx.stroke();
  ctx.beginPath(); ctx.arc(0,0,Math.max(3,5+fourier*5),0,Math.PI*2); ctx.fillStyle=palette[2]; ctx.fill();
  ctx.restore();
  const mode=golden?'golden-angle / 13-step field':symmetryFlag?`${patternLabel(symmetryFlag)} / ${steps}-fold guide`:'radial / descriptor-led field';
  if(label) label.textContent=mode;
  if(text) text.textContent=`A repeatable visual translation of aspect ${fmt(aspect,3)}, circularity ${fmt(circularity,3)}, radial variation ${fmt(radial,3)}, and the selected screening flags.`;
  if(note) note.textContent=`${steps} visual steps · ${flags.length?flags.slice(0,2).map(patternLabel).join(' · '):'no screening flags'} · contemporary study only`;
  canvas.setAttribute('aria-label',`Contemporary derived geometry field print for ${contextTitle(row)}; ${mode}; not a historic ornament.`);
}
function comparisonBearingDegrees(a,b) {
  const lat1=Number(a?.lat), lon1=Number(a?.lon), lat2=Number(b?.lat), lon2=Number(b?.lon);
  if(![lat1,lon1,lat2,lon2].every(Number.isFinite)) return NaN;
  const radians=Math.PI/180, dLon=(lon2-lon1)*radians, y=Math.sin(dLon)*Math.cos(lat2*radians), x=Math.cos(lat1*radians)*Math.sin(lat2*radians)-Math.sin(lat1*radians)*Math.cos(lat2*radians)*Math.cos(dLon);
  return (Math.atan2(y,x)*180/Math.PI+360)%360;
}
function comparisonRelationMetric(label,value,note) {
  return `<article class="comparison-relation-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;
}
function drawComparisonRelationPlot(a,b) {
  const canvas=$('comparisonRelationPlot'), note=$('comparisonRelationPlotNote');
  if(!canvas) return;
  const ctx=canvas.getContext?.('2d');
  if(!ctx) { if(note) note.textContent='Relationship plot unavailable in this browser; the relationship ledger remains available.'; return; }
  const width=canvas.width, height=canvas.height, pad=24, centerX=width/2, centerY=height/2;
  ctx.clearRect(0,0,width,height); ctx.fillStyle='#f2f0e8'; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(82,123,133,.14)'; ctx.lineWidth=1;
  for(let x=pad;x<width-pad;x+=Math.max(38,(width-pad*2)/6)){ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,height-pad);ctx.stroke();}
  for(let y=pad;y<height-pad;y+=Math.max(30,(height-pad*2)/4)){ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(width-pad,y);ctx.stroke();}
  const lat1=Number(a?.lat), lon1=Number(a?.lon), lat2=Number(b?.lat), lon2=Number(b?.lon);
  if(![lat1,lon1,lat2,lon2].every(Number.isFinite)) { if(note) note.textContent='One or both selected places lack usable coordinates.'; canvas.setAttribute('aria-label','Relationship plot unavailable because coordinates are missing'); return; }
  const radians=Math.PI/180, midLat=(lat1+lat2)/2*radians, dx=(lon2-lon1)*Math.cos(midLat), dy=lat2-lat1, span=Math.max(Math.abs(dx),Math.abs(dy),.00001), scale=Math.min((width-pad*2)/(2*span),(height-pad*2)/(2*span))*.72, x1=centerX-dx*scale/2, x2=centerX+dx*scale/2, y1=centerY+dy*scale/2, y2=centerY-dy*scale/2;
  ctx.strokeStyle='rgba(49,92,103,.45)'; ctx.lineWidth=2; ctx.setLineDash([6,4]); ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#bf5b45'; ctx.beginPath();ctx.arc(x1,y1,7,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#527b85'; ctx.beginPath();ctx.arc(x2,y2,7,0,Math.PI*2);ctx.fill();
  ctx.strokeStyle='#fffaf0'; ctx.lineWidth=2; ctx.beginPath();ctx.arc(x1,y1,7,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.arc(x2,y2,7,0,Math.PI*2);ctx.stroke();
  ctx.fillStyle='#173f40'; ctx.font='700 10px ui-monospace,SFMono-Regular,Menlo,monospace'; ctx.fillText('A',x1+10,y1+3); ctx.fillText('B',x2+10,y2+3);
  ctx.fillStyle='#68776f'; ctx.font='8px ui-monospace,SFMono-Regular,Menlo,monospace'; ctx.fillText('straight-line centroid chord',centerX-59,height-9);
  const distance=contextDistanceMeters(a,b), bearing=comparisonBearingDegrees(a,b);
  if(note) note.textContent=`${contextDistanceLabel(distance)} span · bearing ${Number.isFinite(bearing)?fmt(bearing,0):'—'}° A → B`;
  canvas.setAttribute('aria-label',`Straight-line centroid relationship between ${contextTitle(a)} and ${contextTitle(b)}; ${contextDistanceLabel(distance)} apart`);
}
function renderComparisonRelation(a,b) {
  const panel=$('comparisonRelation'), metrics=$('comparisonRelationMetrics'), status=$('comparisonRelationStatus'), intro=$('comparisonRelationIntro'), heading=$('comparisonRelationHeading'), note=$('comparisonRelationNote');
  if(!panel||!metrics) return;
  if(!a||!b) { panel.hidden=true; return; }
  panel.hidden=false;
  const distance=contextDistanceMeters(a,b), bearing=comparisonBearingDegrees(a,b), countyA=String(a.spatial?.county||a.niah?.county||'').trim(), countyB=String(b.spatial?.county||b.niah?.county||'').trim(), settlementA=String(a.spatial?.settlement_name||'').trim(), settlementB=String(b.spatial?.settlement_name||'').trim(), typeA=String(a.niah?.type||'').trim(), typeB=String(b.niah?.type||'').trim(), sameSettlement=Boolean(settlementA&&settlementB&&settlementA===settlementB), sameCounty=Boolean(countyA&&countyB&&countyA===countyB), shared=[...(new Set((a.flags||[]).map(patternLabel)))].filter(flag=>new Set((b.flags||[]).map(patternLabel)).has(flag)), deltaAspect=Number(b.aspect_ratio)-Number(a.aspect_ratio), deltaCircularity=Number(b.circularity)-Number(a.circularity), contextValue=sameSettlement?settlementA:sameCounty?countyA:`${countyA||'A context'} ↔ ${countyB||'B context'}`, contextNote=sameSettlement?'same named settlement context':sameCounty?'same county context':'cross-context comparison', heritageValue=typeA&&typeB&&typeA===typeB?heritageTypeLabel(typeA):`${heritageTypeLabel(typeA||'not joined')} ↔ ${heritageTypeLabel(typeB||'not joined')}`, heritageNote=a.niah?.reg_no&&b.niah?.reg_no?'both carry NIAH joins':a.niah?.reg_no||b.niah?.reg_no?'one place carries an NIAH join':'neither place carries an NIAH join', shapeValue=`Δr ${Number.isFinite(deltaAspect)?fmt(deltaAspect,3):'—'} · ΔC ${Number.isFinite(deltaCircularity)?fmt(deltaCircularity,3):'—'}`;
  if(status) status.textContent=`${contextDistanceLabel(distance)} · ${sameCounty?'same county':'place bridge'}`;
  if(heading) heading.textContent=`${contextTitle(a)} ↔ ${contextTitle(b)}`;
  if(intro) intro.textContent=`The line between Field A and Field B carries geographic separation, source context, and measured shape into one readable relationship. A bearing is included as orientation, not as a route.`;
  metrics.innerHTML=[
    comparisonRelationMetric('Centroid span',contextDistanceLabel(distance),`bearing ${Number.isFinite(bearing)?fmt(bearing,0):'—'}° from A to B`),
    comparisonRelationMetric('Place bridge',contextValue,contextNote),
    comparisonRelationMetric('Heritage bridge',heritageValue,heritageNote),
    comparisonRelationMetric('Maths bridge',`${shared.length} shared screen${shared.length===1?'':'s'}`,shared.length?shared.join(' · '):'no shared φ/θ/symmetry screen'),
    comparisonRelationMetric('Shape delta',shapeValue,'B − A from the mapped snapshot'),
    comparisonRelationMetric('Group bridge',a.group===b.group?spatialGroupLabel(a.group):`${spatialGroupLabel(a.group)} ↔ ${spatialGroupLabel(b.group)}`,a.group===b.group?'same mapped cohort':'different mapped cohorts')
  ].join('');
  if(note) note.textContent=`This relationship reads the current ${SERVER_MODE?'lazy page':'embedded snapshot'} only. Straight-line centroid distance is not a road route or walking distance; shared type, place, or mathematical flags do not establish shared authorship, period identity, or historic intent.`;
  drawComparisonRelationPlot(a,b);
}
function comparisonTargetHtml(row,label) {
  const flags=(row.flags||[]).map(patternLabel);
  const heritage=row.niah?.reg_no ? [row.niah.name||'NIAH-linked record',row.niah.reg_no].filter(Boolean).join(' · ') : 'No NIAH join';
  return `<article class="comparison-target"><div class="comparison-target-head"><span>${esc(label)}</span><button type="button" data-compare-remove="${esc(row.osm_id)}">Remove</button></div><h3>${esc(contextTitle(row))}</h3><p>${esc(row.osm_id||'OSM target')} · ${esc(selectionPlaceText(row))} · ${esc(row.group||'other')}</p><div class="comparison-metrics"><div class="comparison-metric"><span>Area</span><strong>${fmt(row.area_m2,0)} m²</strong></div><div class="comparison-metric"><span>Aspect</span><strong>${fmt(row.aspect_ratio,3)}</strong></div><div class="comparison-metric"><span>Circle C</span><strong>${fmt(row.circularity,3)}</strong></div><div class="comparison-metric"><span>Radial CV</span><strong>${fmt(row.radial_cv,3)}</strong></div></div><div class="comparison-signals"><b>Evidence:</b> ${esc(flags.length?flags.join(' · '):'No screening flags')}<br><b>Heritage:</b> ${esc(heritage)}</div></article>`;
}
function renderComparisonTray() {
  const panel=$('comparisonTray'), content=$('comparisonContent'), intro=$('comparisonIntro');
  if(!panel||!content) return;
  const rows=comparisonData.filter(row=>row&&row.osm_id).slice(0,2);
  const copy=$('copyComparisonLink');
  if(copy) copy.disabled=rows.length<2;
  if(!rows.length) { panel.hidden=true; content.innerHTML=''; renderComparisonRelation(null,null); return; }
  panel.hidden=false;
  if(rows.length===1) {
    renderComparisonRelation(null,null);
    if(intro) intro.textContent=`${contextTitle(rows[0])} is held as Field A. Select another map point or table row, then add it to complete the comparison.`;
    content.innerHTML=`<div class="comparison-grid">${comparisonTargetHtml(rows[0],'A / first place')}</div><div class="comparison-awaiting">Waiting for Field B · the comparison will show differences in proportion, compactness and screening signals when a second target is added.</div>`;
    return;
  }
  const [a,b]=rows;
  const delta=(key,digits)=>{ const av=Number(a[key]), bv=Number(b[key]); return Number.isFinite(av)&&Number.isFinite(bv)?fmt(bv-av,digits):'—'; };
  const flagsA=new Set((a.flags||[]).map(patternLabel)), flagsB=new Set((b.flags||[]).map(patternLabel));
  const shared=[...flagsA].filter(flag=>flagsB.has(flag));
  const onlyA=[...flagsA].filter(flag=>!flagsB.has(flag));
  const onlyB=[...flagsB].filter(flag=>!flagsA.has(flag));
  renderComparisonRelation(a,b);
  if(intro) intro.textContent=`Field A and Field B are being read together. Differences are calculated as B − A from the current mapped snapshot.`;
  content.innerHTML=`<div class="comparison-grid">${comparisonTargetHtml(a,'A / first place')}${comparisonTargetHtml(b,'B / second place')}</div><div class="comparison-delta"><span>Difference ledger / B − A</span><strong>Aspect ${delta('aspect_ratio',3)} · Circularity ${delta('circularity',3)} · Area ${delta('area_m2',0)} m² · Radial CV ${delta('radial_cv',3)}</strong><p><b>Shared screens:</b> ${esc(shared.length?shared.join(' · '):'none')}<br><b>Only A:</b> ${esc(onlyA.length?onlyA.join(' · '):'none')}<br><b>Only B:</b> ${esc(onlyB.length?onlyB.join(' · '):'none')}<br>These are measured differences and screening overlaps, not evidence of shared authorship, period identity or historic intent.</p></div>`;
}
function addComparisonTarget(id) {
  const row=targetRowForId(id);
  if(!row) return;
  if(comparisonData.some(item=>item.osm_id===row.osm_id)) return;
  if(comparisonData.length>=2) {
    const status=$('selectionShareStatus');
    if(status) status.textContent='Comparison is full · remove a place before adding another.';
    return;
  }
  comparisonData.push(row);
  syncViewState();
  renderComparisonTray();
  renderMap();
  renderSelectionCard(row.osm_id);
}
function removeComparisonTarget(id) {
  comparisonData=comparisonData.filter(row=>row.osm_id!==id);
  syncViewState();
  renderComparisonTray();
  renderMap();
  const selected=selectedMarkerId||offlineSelection;
  if(selected) renderSelectionCard(selected);
}
function clearComparison() {
  comparisonData=[];
  syncViewState();
  renderComparisonTray();
  renderMap();
  const selected=selectedMarkerId||offlineSelection;
  if(selected) renderSelectionCard(selected);
}
async function copyComparisonLink() {
  const rows=comparisonData.filter(row=>row&&row.osm_id).slice(0,2), status=$('comparisonShareStatus');
  if(rows.length<2) {
    if(status) status.textContent='Add two places before copying a comparison link.';
    return;
  }
  syncViewState();
  const url=location.href;
  try {
    if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(url);
    if(status) status.textContent='Comparison link copied · filters and both fields are encoded.';
  } catch(error) {
    if(status) status.textContent='Comparison state saved in the address bar · copy the URL manually.';
  }
}
function selectionEvidenceStatusKind(value) {
  const status=String(value||'').toLowerCase();
  if(status==='available'||status==='provided'||status==='valid'||status==='present') return 'available';
  if(status==='not_provided'||status==='missing'||status==='absent') return 'missing';
  return 'check';
}
function selectionEvidenceStatusLabel(kind) {
  return kind==='available'?'present':kind==='missing'?'not provided':'review';
}
function evidenceReadableLabel(value) {
  const text=String(value||'').trim().replaceAll('_',' ');
  return text ? text.charAt(0).toUpperCase()+text.slice(1) : '';
}
function renderSelectionEvidence(row) {
  const grid=$('selectionEvidenceGrid'), status=$('selectionEvidenceStatus'), intro=$('selectionEvidenceIntro'), note=$('selectionEvidenceNote');
  if(!grid) return;
  const niah=row.niah||{}, history=row.history||{}, mapping=row.mapping_history||{}, review=row.review||{}, geometryAvailable=Number(row.valid)===1, niahAvailable=Boolean(String(niah.reg_no||'').trim()), referenceCount=Number(history.reference_count), historyHasEvidence=Boolean(String(history.architect||'').trim())||Number.isFinite(referenceCount)&&referenceCount>0, historyKind=historyHasEvidence?'available':String(history.status||'').trim()?'check':'missing', mappingAvailable=String(mapping.status||'').toLowerCase()==='provided', reviewKnown=Boolean(review.in_queue)||String(review.label||'').trim()&&String(review.label)!=='not_reviewed', reviewKind=mappingAvailable?'available':reviewKnown?'check':'missing';
  const geometryDetail=[Number.isFinite(Number(row.n_vertices))?`${Number(row.n_vertices).toLocaleString()} vertices`: 'vertex count not reported',row.multipart?'multipart geometry':'single geometry',row.repaired?'repaired geometry':'not repaired',String(row.geometry_warning||'').trim()||'no geometry warning'].join(' · ');
  const niahDetail=niahAvailable?[niah.type,niah.rating,niah.century,niah.match_mode,Number.isFinite(Number(niah.dist_m))?`${fmt(niah.dist_m,1)} m join distance`: 'join distance not reported'].filter(Boolean).join(' · '):'No NIAH identifier, type, rating, or date is joined to this footprint.';
  const historyDetail=[history.priority?`priority ${history.priority}`:'priority not reported',Number.isFinite(referenceCount)?`${referenceCount.toLocaleString()} references`:'reference count not reported',history.warnings||'no review warning reported'].join(' · ');
  const reviewDetail=[review.in_queue?'inside current review queue':'outside current review queue',review.reviewer?`reviewer ${review.reviewer}`:'reviewer not assigned',mappingAvailable?`${Number(mapping.version_count||0).toLocaleString()} mapping versions`:'mapping history not provided'].join(' · ');
  const cards=[
    {label:'01 / source geometry',kind:geometryAvailable?'available':'check',title:'OpenStreetMap footprint',value:row.osm_id||'OSM id not reported',detail:geometryDetail,links:row.osm_url||row.osm_id?[{href:row.osm_url||osmHref(row.osm_id),label:'Open source geometry'}]:[]},
    {label:'02 / heritage inventory',kind:niahAvailable?'available':'missing',title:'NIAH record',value:niahAvailable?[niah.name||'NIAH-linked record',niah.reg_no].filter(Boolean).join(' · '):'No NIAH join in this snapshot',detail:niahDetail,links:niahAvailable?[{href:'https://www.buildingsofireland.ie/niah-data-download/',label:'NIAH data source'}]:[]},
    {label:'03 / historical evidence',kind:historyKind,title:'History and attribution',value:history.architect||evidenceReadableLabel(history.status)||'Historical evidence not provided',detail:historyDetail,links:review.in_queue?[{href:reviewHref(row),label:'Open review record'}]:[]},
    {label:'04 / review + edits',kind:reviewKind,title:'Review and map history',value:review.in_queue?`${evidenceReadableLabel(review.label)||'Not reviewed'} · queue`:`${evidenceReadableLabel(review.label)||'Not queued'} · review scope`,detail:reviewDetail,links:review.in_queue?[{href:reviewHref(row),label:'Open expert queue'}]:[]}
  ];
  grid.innerHTML=cards.map(card=>{const links=card.links.map(link=>`<a href="${esc(link.href)}" target="_blank" rel="noopener">${esc(link.label)} →</a>`).join(''); return `<article class="selection-evidence-step ${card.kind}"><div class="selection-evidence-top"><span>${esc(card.label)}</span><b>${esc(selectionEvidenceStatusLabel(card.kind))}</b></div><h4>${esc(card.title)}</h4><strong>${esc(card.value)}</strong><p>${esc(card.detail)}</p><div class="selection-evidence-links">${links||'<small>No direct link in this pack</small>'}</div></article>`;}).join('');
  const present=cards.filter(card=>card.kind==='available').length, checks=cards.filter(card=>card.kind==='check').length, gaps=cards.filter(card=>card.kind==='missing').length, summary=[`${present}/${cards.length} source lanes present`,checks?`${checks} need review`:null,gaps?`${gaps} not provided`:null].filter(Boolean).join(' · ');
  if(status) status.textContent=summary;
  if(intro) intro.textContent=`${row.osm_id||'Selected footprint'} · the chain separates mapped geometry, heritage join, historical evidence, and review scope.`;
  if(note) note.textContent=`Geometry status: ${geometryAvailable?'valid source geometry':'geometry needs checking'} · NIAH: ${niahAvailable?'joined':'not joined'} · history: ${history.status||'not provided'} · mapping history: ${mapping.status||'not provided'}. Missing layers remain visible rather than being inferred from mathematical resemblance.`;
}
function contextDistanceMeters(a,b) {
  const lat1=Number(a?.lat), lon1=Number(a?.lon), lat2=Number(b?.lat), lon2=Number(b?.lon);
  if(![lat1,lon1,lat2,lon2].every(Number.isFinite)) return NaN;
  const radians=Math.PI/180, dLat=(lat2-lat1)*radians, dLon=(lon2-lon1)*radians, sinLat=Math.sin(dLat/2), sinLon=Math.sin(dLon/2), h=sinLat*sinLat+Math.cos(lat1*radians)*Math.cos(lat2*radians)*sinLon*sinLon;
  return 6371008.8*2*Math.atan2(Math.sqrt(h),Math.sqrt(Math.max(0,1-h)));
}
function contextDistanceLabel(value) {
  const distance=Number(value);
  if(!Number.isFinite(distance)) return 'distance n/a';
  return distance<1000?`${fmt(distance,0)} m`:`${fmt(distance/1000,2)} km`;
}
function contextPlaceText(row) {
  const spatial=row.spatial||{}, niah=row.niah||{};
  return [spatial.settlement_name,spatial.county||niah.county].map(value=>String(value||'').trim()).filter(Boolean).join(' · ') || String(row.address_city||'').trim() || spatialGroupLabel(row.group);
}
function contextTitle(row) {
  return String(row.name||row.niah?.name||row.osm_id||'Unnamed footprint').trim() || 'Unnamed footprint';
}
function fieldWalkItemForId(id) {
  return FIELD_WALK.find(item=>String(item?.row?.osm_id||'')===String(id||'')) || null;
}
function targetRowForId(id) {
  if(!id) return null;
  return DATA.find(item=>item.osm_id===id) || filtered.find(item=>item.osm_id===id) || fieldWalkItemForId(id)?.row || null;
}
function contextSignalText(row) {
  const symbols={golden_ratio:'φ',golden_angle:'θ',reflective_symmetry:'↔',orthogonal:'□'};
  return Object.entries(symbols).filter(([key])=>rowHasSignal(row,key)).map(([,symbol])=>symbol).join(' · ') || 'no core signal';
}
function drawSelectionContextPlot(row,neighbors) {
  const canvas=$('selectionContextPlot'), note=$('selectionContextPlotNote');
  if(!canvas) return;
  const ctx=canvas.getContext?.('2d');
  if(!ctx) { if(note) note.textContent='Context plot unavailable in this browser; the nearest-place list remains available.'; return; }
  const width=canvas.width, height=canvas.height, pad=24, centerX=width/2, centerY=height/2;
  ctx.clearRect(0,0,width,height); ctx.fillStyle='#f2f0e8'; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(82,123,133,.14)'; ctx.lineWidth=1;
  for(let x=pad;x<width-pad;x+=Math.max(38,(width-pad*2)/6)){ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,height-pad);ctx.stroke();}
  for(let y=pad;y<height-pad;y+=Math.max(30,(height-pad*2)/4)){ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(width-pad,y);ctx.stroke();}
  ctx.strokeStyle='rgba(82,123,133,.24)'; ctx.setLineDash([4,4]); ctx.beginPath();ctx.moveTo(centerX,pad);ctx.lineTo(centerX,height-pad);ctx.stroke();ctx.beginPath();ctx.moveTo(pad,centerY);ctx.lineTo(width-pad,centerY);ctx.stroke();ctx.setLineDash([]);
  const maxDistance=Math.max(40,...neighbors.map(item=>Number(item.contextDistance)||0)), scale=Math.min((width/2-pad)/maxDistance,(height/2-pad)/maxDistance)*.88, radians=Math.PI/180, latitude=Number(row.lat), longitudeScale=111320*Math.cos(latitude*radians), latitudeScale=110540, positions=[];
  [0.25,0.5,0.75,1].forEach(fraction=>{ctx.strokeStyle='rgba(82,123,133,.14)';ctx.beginPath();ctx.arc(centerX,centerY,maxDistance*scale*fraction,0,Math.PI*2);ctx.stroke();});
  neighbors.forEach((item,index)=>{
    const dx=(Number(item.lon)-Number(row.lon))*longitudeScale, dy=(Number(item.lat)-Number(row.lat))*latitudeScale, x=Math.max(pad,Math.min(width-pad,centerX+dx*scale)), y=Math.max(pad,Math.min(height-pad,centerY-dy*scale));
    positions.push({x,y,index});
    ctx.strokeStyle='rgba(49,92,103,.28)'; ctx.lineWidth=1; ctx.beginPath();ctx.moveTo(centerX,centerY);ctx.lineTo(x,y);ctx.stroke();
    ctx.fillStyle='#527b85'; ctx.beginPath();ctx.arc(x,y,4.5,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#315c67'; ctx.font='700 9px ui-monospace,SFMono-Regular,Menlo,monospace'; ctx.fillText(`N${String(index+1).padStart(2,'0')}`,x+7,y-6);
  });
  ctx.fillStyle='#bf5b45'; ctx.beginPath();ctx.arc(centerX,centerY,7,0,Math.PI*2);ctx.fill();
  ctx.strokeStyle='#fffaf0'; ctx.lineWidth=2; ctx.stroke();
  ctx.fillStyle='#173f40'; ctx.font='700 9px ui-monospace,SFMono-Regular,Menlo,monospace'; ctx.fillText('SELECTED',centerX+10,centerY+3);
  if(note) note.textContent=`${neighbors.length} nearest coordinates · outer ring ${contextDistanceLabel(maxDistance)}`;
  canvas.setAttribute('aria-label',`${neighbors.length} nearest mapped buildings around ${contextTitle(row)}; straight-line centroid context plot`);
}
function renderSelectionContext(row) {
  const list=$('selectionContextList'), status=$('selectionContextStatus'), intro=$('selectionContextIntro'), note=$('selectionContextNote');
  if(!list) return;
  const scope=SERVER_MODE?'current lazy page':'full embedded snapshot', hasCoordinates=[row?.lat,row?.lon].every(value=>Number.isFinite(Number(value)));
  if(!hasCoordinates) {
    list.innerHTML='<span class="footnote">No coordinate pair is available for this selected footprint.</span>';
    if(status) status.textContent='Coordinates missing';
    if(intro) intro.textContent='The surrounding field cannot be drawn until the selected footprint carries a usable latitude and longitude.';
    if(note) note.textContent='No spatial context is inferred when the source coordinate is absent.';
    drawSelectionContextPlot(row,[]);
    return;
  }
  const neighbors=DATA.filter(item=>String(item.osm_id)!==String(row.osm_id)).map(item=>({...item,contextDistance:contextDistanceMeters(row,item)})).filter(item=>Number.isFinite(item.contextDistance)).sort((a,b)=>a.contextDistance-b.contextDistance||String(a.osm_id).localeCompare(String(b.osm_id))).slice(0,5);
  if(status) status.textContent=`${neighbors.length} nearest · ${scope}`;
  if(intro) intro.textContent=`${contextTitle(row)} is shown beside the nearest mapped footprints in the ${scope}; the distances keep place measurable without pretending to be routes.`;
  if(!neighbors.length) {
    list.innerHTML='<span class="footnote">No neighbouring coordinates are available in this report view.</span>';
    if(note) note.textContent=`The ${scope} contains no second coordinate pair to place beside the selected footprint.`;
    drawSelectionContextPlot(row,[]);
    return;
  }
  list.innerHTML=neighbors.map((item,index)=>{
    const title=contextTitle(item), place=contextPlaceText(item), signals=contextSignalText(item), aria=`Focus nearby building ${title}, ${contextDistanceLabel(item.contextDistance)} away, ${place}`;
    return `<button class="selection-context-card" type="button" data-context-focus="${esc(item.osm_id)}" aria-label="${esc(aria)}"><span class="selection-context-card-top"><span>N${String(index+1).padStart(2,'0')} / nearby</span><b>${esc(contextDistanceLabel(item.contextDistance))}</b></span><h4>${esc(title)}</h4><p>${esc(`${spatialGroupLabel(item.group)} · ${place}`)}</p><small>A ${fmt(item.area_m2,0)} m² · r ${fmt(item.aspect_ratio,2)} · ${esc(signals)} · focus →</small></button>`;
  }).join('');
  if(note) note.textContent=`Showing ${neighbors.length} nearest mapped footprints from the ${scope}. Distances are straight-line centroid estimates; shared φ/θ/symmetry symbols describe the rows, not a shared historic cause.`;
  drawSelectionContextPlot(row,neighbors);
}
function renderSelectionCard(id) {
  const card=$('selectionCard'), row=targetRowForId(id);
  if(!card||!row) return;
  const set=(element,value)=>{ if(element) element.textContent=value; };
  set($('selectionTitle'),contextTitle(row));
  set($('selectionSubtitle'),`${row.osm_id} · ${row.group||'other'} · score ${fmt(row.score)} · ${fmt(row.area_m2,0)} m²`);
  set($('selectionPlace'),selectionPlaceText(row));
  set($('selectionHeritage'),selectionHeritageText(row));
  set($('selectionGeometry'),selectionGeometryText(row));
  set($('selectionMath'),selectionMathText(row));
  renderSelectionCulturalTrace(row);
  renderCultureReadingContext();
  renderMathsReadingContext();
  renderSelectionEvidence(row);
  renderSelectionContext(row);
  drawSelectionFingerprint(row);
  drawSelectionWeave(row);
  set($('selectionPassportStatus'),'');
  set($('selectionReview'),reviewState(row));
  const source=$('selectionOsm');
  if(source) source.href=row.osm_url||`https://www.openstreetmap.org/${encodeURIComponent(row.osm_id)}`;
  const studioButton=$('carrySelectionToStudio');
  if(studioButton) studioButton.dataset.carryStudio=row.osm_id||'';
  const compareButton=$('addSelectionCompare');
  if(compareButton) {
    const included=comparisonData.some(item=>item.osm_id===row.osm_id), full=comparisonData.length>=2&&!included;
    compareButton.dataset.compareTarget=row.osm_id||'';
    compareButton.disabled=included||full;
    compareButton.textContent=included?'In comparison ✓':full?'Comparison full · clear one':'Add to comparison →';
  }
  const lens=culturalLensForRow(row), lensButton=$('selectionCulture');
  if(lensButton) {
    lensButton.hidden=!lens;
    lensButton.dataset.selectionCulture=lens;
    lensButton.textContent=lens?`Filter ${CULTURE_LENS_LABELS[lens]} →`:'Filter this lens →';
  }
  card.hidden=false;
}
function hideSelectionCard() { const card=$('selectionCard'); if(card) card.hidden=true; }
function focusSelectedMap() {
  const id=selectedMarkerId||offlineSelection, status=$('selectionShareStatus'), row=targetRowForId(id);
  if(!row) { if(status) status.textContent='Select a place before focusing the map.'; return; }
  focusRow(row.osm_id,{scroll:false,openPopup:true});
  if(status) status.textContent=`Map focused on ${contextTitle(row)} · outline follows the source geometry.`;
}
function clearSelection() {
  if(selectedMarkerId) setMarkerSelected(markerById.get(selectedMarkerId),false);
  clearFieldWalkFocusMarker();
  clearSelectionMapOutline();
  selectedMarkerId=null; offlineSelection=null; offlineMapFocus=false;
  syncFocusState('');
  if(map?.closePopup) map.closePopup();
  hideSelectionCard(); renderFieldCoordinate(null); renderCultureReadingContext(); renderMathsReadingContext(); renderFieldWalk(); renderTable();
  if(offlineMap) renderOfflineMap();
}
async function copySelectionLink() {
  const id=selectedMarkerId||offlineSelection, status=$('selectionShareStatus');
  if(!id) { if(status) status.textContent='Select a place first.'; return; }
  syncFocusState(id);
  const link=location.href;
  try {
    await navigator.clipboard.writeText(link);
    if(status) status.textContent='Place link copied.';
  } catch(error) {
    if(status) status.textContent='Place link ready in the address bar.';
  }
}
function qualityValues(value) { return String(value??'').split('|').map(item=>item.trim()).filter(Boolean); }
function osmHref(id) { return `https://www.openstreetmap.org/${encodeURIComponent(id)}`; }
function qualityMemberHtml(value) { return qualityValues(value).map(id=>`<span class="quality-member"><button type="button" data-quality-focus="${esc(id)}">${esc(id)}</button><a href="${esc(osmHref(id))}" target="_blank" rel="noopener">OSM</a></span>`).join('') || '<span class="footnote">none</span>'; }
function qualityGroupHtml(value) { return qualityValues(value).map(group=>`<span class="flag">${esc(group)}</span>`).join('') || '<span class="footnote">none</span>'; }
async function focusQualityId(id) { if(SERVER_MODE) { $('query').value=id; await applyFilters(); focusRow(id); return; } const row=DATA.find(item=>item.osm_id===id); if(!row) { const status=document.querySelector('.quality-status'); if(status) status.textContent=`${id} is not a target row in this dashboard view; use its OSM link for inspection.`; return; } $('query').value=id; applyFilters(); focusRow(id); }
function qualityStatusKind(value) { const status=String(value||''); if(status==='ok') return 'ok'; if(status.startsWith('available')||status.startsWith('provided')) return 'provided'; if(status.startsWith('not_provided')) return 'missing'; return 'check'; }
function qualityStatusHtml(value) { const status=String(value||'not_reported'); return `<span class="quality-badge ${qualityStatusKind(status)}">${esc(status)}</span>`; }
function renderQualityAuditTable() { const scope=$('qualityAuditScope').value; const state=$('qualityAuditStatus').value; const rows=QUALITY_AUDIT.filter(row=>(!scope||row.scope===scope)&&(!state||qualityStatusKind(row.quality_status)===state)); $('qualityAuditCount').textContent=`${rows.length.toLocaleString()} of ${QUALITY_AUDIT.length.toLocaleString()} audit rows`; $('qualityAuditTable').innerHTML=rows.length?`<table><thead><tr><th>Scope</th><th>Group</th><th>Field</th><th>Rows</th><th>Missing</th><th>Invalid</th><th>Status</th><th>Notes</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.scope)}</td><td>${esc(row.group)}</td><td>${esc(row.field)}</td><td>${esc(row.row_n)}</td><td>${esc(row.missing_n)} (${fmt(row.missing_pct,2)}%)</td><td>${esc(row.invalid_n)}</td><td>${qualityStatusHtml(row.quality_status)}</td><td>${esc(row.notes)}</td></tr>`).join('')}</tbody></table>`:'<p class="footnote">No audit rows match this filter.</p>'; }
function renderQualityAudit() { if(!QUALITY_AUDIT.length) { $('qualityAudit').innerHTML='<p class="footnote">No field or source audit rows were reported.</p>'; return; } $('qualityAudit').innerHTML=`<details class="quality-details"><summary>Inspect field and source audit (${QUALITY_AUDIT.length.toLocaleString()} rows)</summary><div class="quality-audit-controls"><label>Scope <select id="qualityAuditScope"><option value="">All</option><option value="analysis">Analysis</option><option value="source">Source</option></select></label><label>Status <select id="qualityAuditStatus"><option value="">All</option><option value="ok">OK</option><option value="provided">Provided</option><option value="missing">Not provided</option><option value="check">Check</option></select></label><button id="downloadQuality" type="button">Download audit CSV</button><span id="qualityAuditCount" class="quality-audit-count footnote"></span></div><div id="qualityAuditTable" class="quality-audit-table"></div></details>`; $('qualityAuditScope').addEventListener('change',renderQualityAuditTable); $('qualityAuditStatus').addEventListener('change',renderQualityAuditTable); $('downloadQuality').addEventListener('click',downloadQualityAudit); renderQualityAuditTable(); }
function renderTable() {
  const visible=SERVER_MODE ? filtered : filtered.slice((page-1)*PAGE_SIZE,(page-1)*PAGE_SIZE+PAGE_SIZE);
  $('tbody').innerHTML=visible.map(row=>{ const title=contextTitle(row); return `<tr data-id="${esc(row.osm_id)}" class="${selectedMarkerId===row.osm_id?'selected':''}" tabindex="0" aria-selected="${selectedMarkerId===row.osm_id}" aria-label="Focus ${esc(title)} ${esc(row.osm_id)}"><td><b>${esc(title)}</b><br><span class="footnote">${esc(row.osm_id)}${row.niah.name?' · '+esc(row.niah.name):''}</span></td><td>${esc(row.group)}${row.niah.century?`<br><span class="footnote">${esc(row.niah.century)}</span>`:''}</td><td>${fmt(row.area_m2,0)} m²</td><td class="score">${fmt(row.score)}</td><td>${flagHtml(row)}</td><td class="review-state ${esc(reviewFilterState(row))}">${reviewCell(row)}</td></tr>`; }).join('');
  $('empty').textContent='No buildings match these filters.';
  $('empty').hidden=visible.length>0;
  const total=SERVER_MODE ? Number(pageStats.total||0) : filtered.length;
  const pages=Math.max(1,Math.ceil(total/PAGE_SIZE)); $('page').textContent=`${Math.min(page,pages)} / ${pages}`; $('prev').disabled=page<=1; $('next').disabled=page>=pages;
  document.querySelectorAll('#tbody tr[data-id]').forEach(tr=>{ tr.addEventListener('click',()=>focusRow(tr.dataset.id)); tr.addEventListener('keydown',event=>{ if((event.key==='Enter'||event.key===' ')&&!event.target.closest('a,button,input,select,textarea')){ event.preventDefault(); focusRow(tr.dataset.id); } }); });
  document.querySelectorAll('#tbody a.review-link').forEach(link=>link.addEventListener('click',event=>event.stopPropagation()));
}
function popup(row) { const reviewLabel=row.review?.in_queue?'Review queue':'Not in review queue'; return `<b>${esc(contextTitle(row))}</b><br>${esc(row.group)} · ${fmt(row.area_m2,0)} m²<br>Score <b>${fmt(row.score)}</b> · aspect ${fmt(row.aspect_ratio,3)}<br>Shape: rectangularity ${fmt(row.rectangularity,3)} · radial CV ${fmt(row.radial_cv,3)}<br>Convexity ${fmt(row.convexity,3)} · ${row.n_vertices} vertices${row.multipart?' · multipart':''}${row.repaired?' · repaired':''}<br>${flagHtml(row)}${row.parts.count?`<br>Mapped parts: ${row.parts.count} · coverage ${fmt(row.parts.coverage_pct,1)}%`:''}${row.height_m?`<br>OSM height: ${fmt(row.height_m,1)} m`:''}${row.niah.name?`<br><span>${esc(row.niah.name)} · ${esc(row.niah.rating)} · ${esc(row.niah.century)}</span>`:''}${row.history.status?`<br>Historical status: ${esc(row.history.status)}${row.history.architect?' · '+esc(row.history.architect):''}`:''}<br><a href="${row.osm_url}" target="_blank" rel="noopener">OpenStreetMap</a> · ${reviewLink(row,reviewLabel)}<br><button class="popup-focus" type="button" data-focus-id="${esc(row.osm_id)}">Focus in list</button>`; }
function comparisonMapRows() {
  const rows=comparisonData.filter(row=>row&&row.osm_id).slice(0,2);
  return rows.length===2&&rows.every(row=>Number.isFinite(Number(row.lat))&&Number.isFinite(Number(row.lon))) ? rows : [];
}
function clearComparisonMapLayer() {
  if(comparisonLine) { comparisonLine.remove(); comparisonLine=null; }
  if(comparisonEndpointLayer) { comparisonEndpointLayer.remove(); comparisonEndpointLayer=null; }
}
function renderComparisonMapLayer() {
  clearComparisonMapLayer();
  if(offlineMap||!map) return;
  const rows=comparisonMapRows();
  if(rows.length<2) return;
  const [a,b]=rows;
  const coordinates=[[Number(a.lat),Number(a.lon)],[Number(b.lat),Number(b.lon)]];
  const distance=contextDistanceMeters(a,b), bearing=comparisonBearingDegrees(a,b);
  const relation=`${contextDistanceLabel(distance)} straight-line centroid span · bearing ${Number.isFinite(bearing)?fmt(bearing,0):'—'}° A → B`;
  comparisonLine=L.polyline(coordinates,{color:'#bf5b45',weight:3,opacity:.92,dashArray:'10 7',lineCap:'round'}).addTo(map);
  comparisonLine.bindTooltip(esc(relation),{sticky:true,opacity:.96});
  const endpoint=(row,label,fillColor)=>L.circleMarker([Number(row.lat),Number(row.lon)],{radius:7,color:'#fffaf0',weight:2,fillColor,fillOpacity:1}).bindTooltip(esc(`${label} / ${contextTitle(row)} · ${selectionPlaceText(row)}`),{direction:'top',opacity:.96});
  const endpointA=endpoint(a,'A','#bf5b45'), endpointB=endpoint(b,'B','#527b85');
  comparisonEndpointLayer=L.layerGroup([endpointA,endpointB]).addTo(map);
  comparisonLine.bringToFront(); endpointA.bringToFront(); endpointB.bringToFront();
}
function offlineBounds() {
  let minLat=Infinity,maxLat=-Infinity,minLon=Infinity,maxLon=-Infinity;
  const comparisonRows=comparisonMapRows();
  const focusedRow=targetRowForId(offlineSelection||selectedMarkerId);
  const nearbyFocusRows=offlineMapFocus&&focusedRow?DATA.filter(row=>String(row.osm_id)!==String(focusedRow.osm_id)).map(row=>({...row,focusDistance:contextDistanceMeters(focusedRow,row)})).filter(row=>Number.isFinite(row.focusDistance)).sort((a,b)=>a.focusDistance-b.focusDistance||String(a.osm_id).localeCompare(String(b.osm_id))).slice(0,8):[];
  const focusRows=offlineMapFocus&&focusedRow?[focusedRow,...nearbyFocusRows]:DATA;
  const routeFocused=Boolean(routeGeometry&&routeGeometry.length>1), points=(routeFocused?routeGeometry.map(([lon,lat])=>({lat,lon})):focusRows).concat(comparisonRows).concat(focusedRow?[focusedRow]:[]);
  points.forEach(row=>{ const lat=Number(row.lat), lon=Number(row.lon); if(Number.isFinite(lat)&&Number.isFinite(lon)){ minLat=Math.min(minLat,lat); maxLat=Math.max(maxLat,lat); minLon=Math.min(minLon,lon); maxLon=Math.max(maxLon,lon); } });
  if(!Number.isFinite(minLat)) return null;
  const focusView=Boolean(offlineMapFocus&&focusedRow&&!routeFocused), latSpan=Math.max(maxLat-minLat,focusView ? 0.004 : routeFocused ? 0.01 : 0.1), lonSpan=Math.max(maxLon-minLon,focusView ? 0.006 : routeFocused ? 0.01 : 0.1);
  const latPad=latSpan*(focusView?.24:.08), lonPad=lonSpan*(focusView?.24:.08);
  return {minLat:minLat-latPad,maxLat:maxLat+latPad,minLon:minLon-lonPad,maxLon:maxLon+lonPad};
}
function offlinePoint(row,bounds) {
  const x=50+(Number(row.lon)-bounds.minLon)/(bounds.maxLon-bounds.minLon)*900;
  const y=650-(Number(row.lat)-bounds.minLat)/(bounds.maxLat-bounds.minLat)*600;
  return {x,y};
}
function setMapLoading(visible,text) {
  const loading=$('mapLoading');
  if(!loading) return;
  if(text) $('mapLoadingText').textContent=text;
  loading.hidden=!visible;
}
function setMarkerSelected(marker,selected) {
  if(!marker || typeof marker.setStyle!=='function') return;
  marker.setStyle(selected
    ? {radius:8,color:'#fff',weight:2.5,fillColor:'#e0bd6e',fillOpacity:1}
    : {radius:5,color:'#17324d',weight:1,fillColor:color(marker.__score),fillOpacity:.86});
}
function clearFieldWalkFocusMarker() {
  if(fieldWalkFocusMarker) { fieldWalkFocusMarker.remove(); fieldWalkFocusMarker=null; }
}
function clearSelectionMapOutline() {
  if(selectionOutlineLayer) { selectionOutlineLayer.remove(); selectionOutlineLayer=null; }
}
function renderSelectionMapOutline(row) {
  clearSelectionMapOutline();
  if(offlineMap||!map||!row||typeof L==='undefined') return;
  const feature=GEOJSON_BY_ID.get(String(row.osm_id||''));
  if(!feature?.geometry) return;
  selectionOutlineLayer=L.geoJSON(feature,{interactive:false,style:{className:'selected-footprint-outline',color:'#fffaf0',weight:3,opacity:1,fillColor:'#e0bd6e',fillOpacity:.2}}).addTo(map);
  selectionOutlineLayer.bindTooltip(esc(`${contextTitle(row)} · ${selectionPlaceText(row)}`),{direction:'top',opacity:.96,sticky:true});
  selectionOutlineLayer.bringToFront();
}
function renderFieldWalkFocusMarker(row) {
  clearFieldWalkFocusMarker();
  if(!map||!row||markerById.has(row.osm_id)) return;
  const lat=Number(row.lat), lon=Number(row.lon);
  if(!Number.isFinite(lat)||!Number.isFinite(lon)||typeof L==='undefined') return;
  fieldWalkFocusMarker=L.circleMarker([lat,lon],{radius:10,color:'#fffaf0',weight:2.5,fillColor:'#bf5b45',fillOpacity:.96}).addTo(map);
  fieldWalkFocusMarker.bindTooltip(esc(`${contextTitle(row)} · ${selectionPlaceText(row)}`),{direction:'top',opacity:.96,sticky:true});
  fieldWalkFocusMarker.openTooltip();
  fieldWalkFocusMarker.bringToFront();
}
function selectMapTarget(id,{scroll=false}={}) {
  if(!id) return;
  if(selectedMarkerId && selectedMarkerId!==id) setMarkerSelected(markerById.get(selectedMarkerId),false);
  selectedMarkerId=id;
  syncFocusState(id);
  renderFieldWalk();
  setMarkerSelected(markerById.get(id),true);
  renderSelectionMapOutline(targetRowForId(id));
  renderFieldCoordinate(targetRowForId(id));
  renderSelectionCard(id);
  setAtlasNavActive('filters');
  document.querySelectorAll('#tbody tr[data-id]').forEach(row=>{
    const active=row.dataset.id===id;
    row.classList.toggle('selected',active);
    row.setAttribute('aria-selected',String(active));
    if(active && scroll) row.scrollIntoView({block:'nearest'});
  });
  const selection=$('selectionCard');
  if(scroll&&selection&&!selection.hidden) {
    selection.classList.remove('selection-card-arrived');
    void selection.offsetWidth;
    selection.classList.add('selection-card-arrived');
    revealSelectionCard(selection);
    setTimeout(()=>setAtlasNavActive('filters'),480);
    setTimeout(()=>selection.classList.remove('selection-card-arrived'),900);
  }
  updateMapHud();
}
function fitMapToResults() {
  if(offlineMap) { offlineMapFocus=false; renderOfflineMap(); return; }
  if(!map || !markerLayer) return;
  const bounds=typeof markerLayer.getBounds==='function'?markerLayer.getBounds():null;
  if(bounds && bounds.isValid && bounds.isValid()) {
    if(routeLine) bounds.extend(routeLine.getBounds());
    if(comparisonLine) bounds.extend(comparisonLine.getBounds());
    map.fitBounds(bounds,{padding:[36,36],maxZoom:17});
  } else {
    resetMapView();
  }
}
function resetMapView() {
  if(map) map.setView(DEFAULT_MAP_CENTER,DEFAULT_MAP_ZOOM);
  if(offlineMap) { offlineSelection=null; offlineMapFocus=false; renderOfflineMap(); }
}
function renderOfflineMap() {
  setMapLoading(false);
  const el=$('map'), bounds=offlineBounds();
  if(!bounds){ el.className='offline-map'; el.innerHTML='<div class="offline-map-note">Offline map fallback · no coordinates available.</div>'; return; }
  const grid=Array.from({length:6},(_,i)=>{ const x=50+i*180, y=50+i*120; return `<line class="offline-grid" x1="${x}" y1="50" x2="${x}" y2="650"/><line class="offline-grid" x1="50" y1="${y}" x2="950" y2="${y}"/>`; }).join('');
  const outlines=OUTLINES.slice(0,200).map(item=>item.rings.map(ring=>`<polyline class="offline-outline" points="${ring.map(([lat,lon])=>{const p=offlinePoint({lat,lon},bounds);return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;}).join(' ')}"/>`).join('')).join('');
  const route=routeGeometry&&routeGeometry.length>1?`<polyline class="offline-route" points="${routeGeometry.map(([lon,lat])=>{const p=offlinePoint({lat,lon},bounds);return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;}).join(' ')}"/>`:'';
  const comparisonRows=comparisonMapRows();
  const comparisonChord=comparisonRows.length===2?(()=>{ const [a,b]=comparisonRows, pa=offlinePoint(a,bounds), pb=offlinePoint(b,bounds), distance=contextDistanceMeters(a,b), bearing=comparisonBearingDegrees(a,b), relation=`${contextDistanceLabel(distance)} straight-line centroid span · bearing ${Number.isFinite(bearing)?fmt(bearing,0):'—'}° A → B`; return `<g class="offline-comparison-chord"><title>${esc(relation)}</title><line x1="${pa.x.toFixed(2)}" y1="${pa.y.toFixed(2)}" x2="${pb.x.toFixed(2)}" y2="${pb.y.toFixed(2)}"/><circle class="offline-comparison-end offline-comparison-end-a" cx="${pa.x.toFixed(2)}" cy="${pa.y.toFixed(2)}" r="7"/><circle class="offline-comparison-end offline-comparison-end-b" cx="${pb.x.toFixed(2)}" cy="${pb.y.toFixed(2)}" r="7"/><text class="offline-comparison-label" x="${(pa.x+10).toFixed(2)}" y="${(pa.y+4).toFixed(2)}">A</text><text class="offline-comparison-label" x="${(pb.x+10).toFixed(2)}" y="${(pb.y+4).toFixed(2)}">B</text></g>`; })():'';
  const focusedManeuver=routeManeuverFocus===null?null:routeManeuverData[routeManeuverFocus];
  const focusedCoordinate=focusedManeuver?.coordinate;
  const focusPoint=Array.isArray(focusedCoordinate)&&focusedCoordinate.length>=2
    ? offlinePoint({lat:Number(focusedCoordinate[1]),lon:Number(focusedCoordinate[0])},bounds)
    : null;
  const routeFocus=focusPoint?`<circle class="offline-route-focus" cx="${focusPoint.x.toFixed(2)}" cy="${focusPoint.y.toFixed(2)}" r="8"><title>${esc(routeManeuverLabel(focusedManeuver))}</title></circle>`:'';
  const mapRows=filtered.slice(0,__MARKER_LIMIT__);
  const selected=targetRowForId(offlineSelection||selectedMarkerId);
  if(selected&&!mapRows.some(row=>row.osm_id===selected.osm_id)) mapRows.push(selected);
  const selectedRing=selected?geometryOuterRing(GEOJSON_BY_ID.get(String(selected.osm_id||''))):[];
  const selectedPoint=selected?offlinePoint(selected,bounds):null;
  const selectedOutline=selected&&selectedRing.length>=3&&selectedPoint?`<g class="offline-selection-layer" aria-hidden="true"><polyline class="offline-selection-outline" points="${selectedRing.map(([lon,lat])=>{const p=offlinePoint({lat,lon},bounds);return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;}).join(' ')}"/><circle class="offline-selection-ring" cx="${selectedPoint.x.toFixed(2)}" cy="${selectedPoint.y.toFixed(2)}" r="12"/><text class="offline-selection-label" text-anchor="end" x="${(selectedPoint.x-16).toFixed(2)}" y="${(selectedPoint.y-12).toFixed(2)}">SELECTED / ${esc(contextTitle(selected))}</text></g>`:'';
  const points=mapRows.map(row=>{ const p=offlinePoint(row,bounds), isSelected=offlineSelection===row.osm_id||selectedMarkerId===row.osm_id; return `<circle class="offline-point${isSelected?' selected':''}" data-id="${esc(row.osm_id)}" cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="${isSelected?6:4}" fill="${color(row.score)}"><title>${esc(contextTitle(row))} · ${esc(row.group)} · score ${fmt(row.score)}</title></circle>`; }).join('');
  const selection=selected?`<div class="offline-selection"><b>${esc(contextTitle(selected))}</b> · ${esc(selected.group)} · score ${fmt(selected.score)}<br><span class="footnote">${esc(selected.osm_id)} · click a point to inspect another target</span></div>`:'';
  const note=routeGeometry&&routeGeometry.length>1?`Offline route view · ${routeGeometry.length.toLocaleString()} path points.`:offlineMapFocus&&selected?`Focused place view · source outline shown · ${selected.osm_id}.`:`Offline map fallback · ${filtered.length.toLocaleString()} matching targets; basemap unavailable.`;
  const focusTransform=offlineMapFocus&&selected&&!(routeGeometry&&routeGeometry.length>1)?' transform="translate(-230 0)"':'';
  el.className='offline-map'; el.innerHTML=`<svg class="offline-map-svg" viewBox="0 0 1000 700" role="img" aria-label="Offline map fallback"><g class="offline-map-field"${focusTransform}>${grid}${outlines}${route}${comparisonChord}${routeFocus}${selectedOutline}${points}</g></svg><div class="offline-map-note">${note}</div>${selection}`;
  el.querySelectorAll('.offline-point').forEach(point=>point.addEventListener('click',()=>focusRow(point.dataset.id,{scroll:false,openPopup:false})));
  updateMapHud();
}
function renderRouteManeuverMarker() {
  if(routeManeuverMarker) { routeManeuverMarker.remove(); routeManeuverMarker=null; }
  if(offlineMap||!map||routeManeuverFocus===null) return;
  const maneuver=routeManeuverData[routeManeuverFocus], coordinate=maneuver?.coordinate;
  if(!Array.isArray(coordinate)||coordinate.length<2) return;
  const lon=Number(coordinate[0]), lat=Number(coordinate[1]);
  if(!Number.isFinite(lat)||!Number.isFinite(lon)) return;
  routeManeuverMarker=L.circleMarker([lat,lon],{radius:8,color:'#fff',weight:2,fillColor:'#f59e0b',fillOpacity:1}).addTo(map);
  routeManeuverMarker.bindTooltip(routeManeuverLabel(maneuver),{direction:'top',opacity:.95});
  routeManeuverMarker.bringToFront();
}
function mapTargetAriaLabel(row) {
  return `Map target ${contextTitle(row)} · ${selectionPlaceText(row)} · ${fmt(row.area_m2,0)} square metres · ${selectionGeometryText(row)}`;
}
function revealPanelTarget(target) {
  const panel=$('panel');
  if(panel&&target&&panel.contains(target)) {
    const stickyOffset=Math.max(54,Math.round($('atlasNav')?.getBoundingClientRect().height||0));
    panel.scrollTo({top:Math.max(0,target.offsetTop-stickyOffset),behavior:'smooth'});
  } else if(target?.scrollIntoView) {
    target.scrollIntoView({behavior:'smooth',block:'start'});
  }
}
function revealSelectionCard(selection) { revealPanelTarget(selection); }
function decorateMapAccessibility() {
  if(!map) return;
  filtered.slice(0,__MARKER_LIMIT__).forEach(row=>{
    const marker=markerById.get(row.osm_id), element=marker?.getElement?.();
    if(!element) return;
    element.setAttribute('role','button'); element.setAttribute('tabindex','0'); element.setAttribute('aria-label',mapTargetAriaLabel(row));
    if(element.dataset.irelandGeometryKeyboard==='1') return;
    element.dataset.irelandGeometryKeyboard='1';
    element.addEventListener('keydown',event=>{ if(event.key!=='Enter'&&event.key!==' ') return; event.preventDefault(); selectMapTarget(row.osm_id,{scroll:true}); marker.openPopup?.(); });
  });
  document.querySelectorAll('#map .marker-cluster').forEach(element=>{
    const count=Number(String(element.textContent||'').replace(/[^0-9]/g,''))||0;
    element.setAttribute('role','button'); element.setAttribute('tabindex','0'); element.setAttribute('aria-label',`Map cluster with ${count.toLocaleString()} analysed building footprints. Activate to zoom in.`);
    if(element.dataset.irelandGeometryKeyboard==='1') return;
    element.dataset.irelandGeometryKeyboard='1';
    element.addEventListener('keydown',event=>{ if(event.key!=='Enter'&&event.key!==' ') return; event.preventDefault(); element.click(); });
  });
}
function renderMap() {
  if(selectedMarkerId && !filtered.some(row=>row.osm_id===selectedMarkerId) && !targetRowForId(selectedMarkerId)) { selectedMarkerId=null; offlineSelection=null; syncFocusState(''); hideSelectionCard(); renderFieldWalk(); }
  if(offlineMap){ clearFieldWalkFocusMarker(); clearSelectionMapOutline(); clearComparisonMapLayer(); renderOfflineMap(); return; }
  if (!map || !markerLayer) return;
  clearFieldWalkFocusMarker();
  if(routeLine){ routeLine.remove(); routeLine=null; }
  markerLayer.clearLayers(); markerById.clear();
  filtered.slice(0,__MARKER_LIMIT__).forEach(row=>{
    const marker=L.circleMarker([row.lat,row.lon],{radius:5,color:'#17324d',weight:1,fillColor:color(row.score),fillOpacity:.86});
    marker.__score=row.score;
    marker.bindPopup(popup(row));
    marker.on('click',()=>selectMapTarget(row.osm_id,{scroll:true}));
    marker.on('popupopen',event=>{
      const button=event.popup.getElement()?.querySelector('[data-focus-id]');
      if(button) button.addEventListener('click',()=>focusRow(row.osm_id,{scroll:true,openPopup:false}),{once:true});
    });
    markerLayer.addLayer(marker); markerById.set(row.osm_id,marker);
    if(selectedMarkerId===row.osm_id) setMarkerSelected(marker,true);
  });
  const selectedWalkRow=selectedMarkerId&&!filtered.some(row=>row.osm_id===selectedMarkerId)?targetRowForId(selectedMarkerId):null;
  if(selectedWalkRow) renderFieldWalkFocusMarker(selectedWalkRow);
  renderSelectionMapOutline(targetRowForId(selectedMarkerId));
  if(routeGeometry&&routeGeometry.length>1){ routeLine=L.polyline(routeGeometry.map(([lon,lat])=>[lat,lon]),{color:'#1d4ed8',weight:5,opacity:.9,lineCap:'round',lineJoin:'round'}).addTo(map); routeLine.bringToFront(); }
  renderComparisonMapLayer();
  renderRouteManeuverMarker();
  decorateMapAccessibility();
  if(typeof requestAnimationFrame==='function') requestAnimationFrame(decorateMapAccessibility);
  updateMapHud();
}
function mapTime(value) {
  const date=value?new Date(value):new Date();
  return date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}
function mapSnapshotText() {
  const generated=String(SUMMARY.generated_at||'').replace('T',' ').replace('Z','');
  return generated ? `snapshot ${generated.slice(0,16)}` : 'snapshot date unavailable';
}
function coordinateLabel(value,positive,negative) {
  const number=Number(value);
  if(!Number.isFinite(number)) return '—';
  return `${Math.abs(number).toFixed(4)}° ${number>=0?positive:negative}`;
}
function updateMapStamp() {
  const center=map?.getCenter?.();
  let lat=Number(center?.lat), lon=Number(center?.lng);
  if(!Number.isFinite(lat)||!Number.isFinite(lon)) {
    const bounds=offlineMap?offlineBounds():null;
    if(bounds) { lat=(bounds.minLat+bounds.maxLat)/2; lon=(bounds.minLon+bounds.maxLon)/2; }
    else { lat=53.2; lon=-7.7; }
  }
  const selectedId=selectedMarkerId||offlineSelection;
  const row=selectedId?targetRowForId(selectedId):null;
  const place=row?.spatial?.settlement_name||row?.address_city||row?.spatial?.county||row?.niah?.county;
  const context=row
    ? [contextTitle(row),place,row.group].filter(Boolean).join(' · ')
    : `${filtered.length.toLocaleString()} matching footprints · ${offlineMap?'offline analytical field':`zoom ${map?.getZoom?.()??'—'}`}`;
  const centerText=$('mapCenterText'), contextText=$('mapContextText');
  if(centerText) centerText.textContent=`${coordinateLabel(lat,'N','S')} / ${coordinateLabel(lon,'E','W')}`;
  if(contextText) contextText.textContent=context;
}
function renderMapConstellation() {
  const rows=Array.isArray(filtered)?filtered:[], total=rows.length;
  const set=(id,value)=>{ const element=$(id); if(element) element.textContent=value; };
  const display=key=>{
    const count=rows.filter(row=>rowHasSignal(row,key)).length;
    return total?`${count.toLocaleString()} · ${fmt(count/total*100,1)}%`:'—';
  };
  const counties=new Set(rows.map(row=>row.spatial?.county||row.niah?.county).filter(Boolean));
  set('mapRatioSignal',display('golden_ratio'));
  set('mapAngleSignal',display('golden_angle'));
  set('mapSymmetrySignal',display('reflective_symmetry'));
  set('mapOrthogonalSignal',display('orthogonal'));
  set('mapConstellationScope',`${SERVER_MODE?'current page':'active field'} · ${counties.size.toLocaleString()} county contexts`);
}
function updateMapHud() {
  updateMapStamp();
  renderMapConstellation();
  const live=$('mapLiveState'), tile=$('mapTileStatus'), count=$('mapVisibleCount');
  if(!live||!tile||!count) return;
  const online=typeof navigator==='undefined'||navigator.onLine!==false;
  const total=SERVER_MODE?Number(pageStats.total||0):filtered.length;
  const plotted=Math.min(filtered.length,__MARKER_LIMIT__);
  count.textContent=SERVER_MODE
    ? `${Number.isFinite(total)?total.toLocaleString():'—'} targets · ${plotted.toLocaleString()} plotted`
    : `${Number.isFinite(total)?total.toLocaleString():'—'} targets`;
  const fit=$('mapFit');
  if(fit) fit.disabled=plotted===0;
  if(offlineMap) {
    live.className='map-live-state offline'; live.textContent='Offline fallback';
    tile.textContent=`Basemap unavailable · ${mapSnapshotText()}`;
  } else if(!online) {
    live.className='map-live-state offline'; live.textContent='Offline';
    tile.textContent=`Waiting for connection · ${mapSnapshotText()}`;
  } else if(mapTileErrorCount>2) {
    live.className='map-live-state error'; live.textContent='Imagery retrying';
    tile.textContent=`Tile errors detected · ${mapSnapshotText()}`;
  } else if(!lastTileLoadedAt) {
    live.className='map-live-state loading'; live.textContent='Loading live tiles';
    tile.textContent=`Requesting ${activeMapLayer} · ${mapSnapshotText()}`;
  } else {
    live.className='map-live-state'; live.textContent=`Live · ${mapTime()}`;
    tile.textContent=`Tiles synced ${mapTime(lastTileLoadedAt)} · ${mapSnapshotText()}`;
  }
}
function syncMapLayerButtons() {
  document.querySelectorAll('[data-map-layer]').forEach(button=>{
    const active=button.dataset.mapLayer===activeMapLayer;
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',String(active));
  });
}
function setMapLayer(name) {
  if(!map||!mapBaseLayers[name]) return;
  Object.values(mapBaseLayers).forEach(layer=>{ if(map.hasLayer(layer)) map.removeLayer(layer); });
  mapBaseLayers[name].addTo(map);
  activeMapLayer=name;
  lastTileLoadedAt=0; mapTileErrorCount=0; setMapLoading(true,`Loading ${name} imagery…`);
  syncMapLayerButtons();
  updateMapHud();
}
function refreshMapImagery() {
  if(!map||offlineMap) { updateMapHud(); return; }
  lastTileLoadedAt=0; mapTileErrorCount=0;
  setMapLoading(true,`Refreshing ${activeMapLayer} imagery…`);
  const layers=activeMapLayer==='hybrid'
    ? [mapBaseLayers.satellite,mapReferenceLayer]
    : [mapBaseLayers[activeMapLayer]];
  layers.filter(Boolean).forEach(layer=>{ if(typeof layer.redraw==='function') layer.redraw(); });
  updateMapHud();
}
function bindMapTileSignals(layer) {
  if(!layer) return;
  layer.on('tileloadstart',()=>{ setMapLoading(true,`Loading ${activeMapLayer} imagery…`); updateMapHud(); });
  layer.on('tileload',()=>{ lastTileLoadedAt=Date.now(); mapTileErrorCount=0; setMapLoading(false); updateMapHud(); });
  layer.on('tileerror',()=>{ mapTileErrorCount+=1; if(mapTileErrorCount>=3) setMapLoading(false); updateMapHud(); });
}
function setAtlasNavActive(key) {
  document.querySelectorAll('[data-nav-section]').forEach(link=>{
    const active=link.dataset.navSection===key;
    link.setAttribute('aria-current',active?'page':'false');
  });
  document.querySelectorAll('[data-sequence-section]').forEach(step=>{
    const active=step.dataset.sequenceSection===key;
    step.classList.toggle('is-active',active);
    step.setAttribute('aria-current',active?'step':'false');
  });
  const status=$('atlasNavStatus');
  if(status) status.textContent=atlasNavStatusText(key);
}
function focusAtlasTarget(targetKey,navKey=targetKey) {
  const section=$(targetKey);
  if(!section) return;
  if(atlasNavFocusTimer!==null) clearTimeout(atlasNavFocusTimer);
  setAtlasNavActive(navKey);
  section.scrollIntoView({behavior:'smooth',block:'start'});
  // IntersectionObserver can report the previous section while a smooth scroll is settling.
  // Re-assert the destination once it is in view so the status text follows the handoff.
  const started=Date.now();
  const reassert=()=>{
    const rect=section.getBoundingClientRect();
    if(rect.top>=-72 && rect.top<=144) { setAtlasNavActive(navKey); atlasNavFocusTimer=null; return; }
    if(Date.now()-started<4200) { atlasNavFocusTimer=setTimeout(reassert,250); return; }
    atlasNavFocusTimer=null;
  };
  atlasNavFocusTimer=setTimeout(reassert,250);
}
function focusAtlasSection(key) { focusAtlasTarget(key,key); }
function initAtlasNav() {
  const panel=$('panel'), links=[...document.querySelectorAll('[data-nav-section]')];
  if(!panel||!links.length) return;
  const selectionCard=$('selectionCard');
  setAtlasNavActive(selectionCard&&!selectionCard.hidden&&(selectedMarkerId||offlineSelection)?'filters':'field');
  links.forEach(link=>link.addEventListener('click',()=>setAtlasNavActive(link.dataset.navSection)));
  const sections=links.map(link=>$(link.dataset.navSection)).filter(Boolean);
  if(typeof IntersectionObserver==='undefined') return;
  const observer=new IntersectionObserver(entries=>{
    const panelRect=panel.getBoundingClientRect(), cardRect=selectionCard?.getBoundingClientRect();
    const selectedDossierVisible=Boolean(selectionCard&&!selectionCard.hidden&&(selectedMarkerId||offlineSelection)&&cardRect&&cardRect.top<=panelRect.top+144&&cardRect.bottom>panelRect.top+54);
    if(selectedDossierVisible) { setAtlasNavActive('filters'); return; }
    const visible=entries.filter(entry=>entry.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];
    if(visible) setAtlasNavActive(visible.target===selectionCard?'filters':visible.target.id);
  },{root:panel,rootMargin:'-54px 0px -58% 0px',threshold:[0.01,0.2,0.5]});
  sections.forEach(section=>observer.observe(section));
  if(selectionCard) observer.observe(selectionCard);
}
function initMapHud() {
  document.querySelectorAll('[data-map-layer]').forEach(button=>button.addEventListener('click',()=>setMapLayer(button.dataset.mapLayer)));
  $('mapRefresh')?.addEventListener('click',refreshMapImagery);
  $('mapFit')?.addEventListener('click',fitMapToResults);
  $('mapReset')?.addEventListener('click',resetMapView);
  window.addEventListener('online',updateMapHud);
  window.addEventListener('offline',updateMapHud);
  if(mapLiveTimer===null) mapLiveTimer=setInterval(updateMapHud,1000);
  updateMapHud();
}
function restoreFocusedTarget() {
  const id=new URLSearchParams(location.search).get('focus');
  if(!id || !targetRowForId(id)) return false;
  focusRow(id,{scroll:true,openPopup:false});
  return true;
}
function mapFocusPanOffset() {
  const width=Number(window.innerWidth)||0;
  return width>=900?[-Math.round(Math.min(240,Math.max(150,width*.17))),0]:[0,0];
}
function focusRow(id,{scroll=true,openPopup=true}={}) {
  const row=targetRowForId(id);
  if(!row) return;
  if(offlineMap) offlineMapFocus=true;
  selectMapTarget(id,{scroll});
  if(map){
    map.setView([row.lat,row.lon],17);
    const offset=mapFocusPanOffset();
    if(offset[0]) map.panBy(offset,{animate:false});
    const marker=markerById.get(id);
    if(marker && openPopup){
      let opened=false;
      const open=()=>{ if(opened) return; opened=true; marker.openPopup(); };
      if(markerLayer?.zoomToShowLayer) markerLayer.zoomToShowLayer(marker,open); else open();
      setTimeout(open,250);
    }
    if(!marker) renderFieldWalkFocusMarker(row);
  } else if(offlineMap){ offlineSelection=id; renderOfflineMap(); }
}
function renderBars() {
  const groups=SIG.filter(row=>row.signal==='golden_angle' && row.group);
  $('groupBars').innerHTML=groups.length?groups.map(row=>`<div class="bar-row"><span>${esc(row.group)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,Number(row.observed_rate)||0)}%"></div></div><span>${fmt(row.observed_rate,2)}% / ${fmt(row.control_rate,2)}%</span></div>`).join(''):'<span class="footnote">No global significance file available.</span>';
  const eras=NIAH_SIG.filter(row=>row.signal==='golden_angle'&&row.group==='worship'&&String(row.reference).includes('era-matched')).sort((a,b)=>String(a.stratum).localeCompare(String(b.stratum),undefined,{numeric:true}));
  $('eraBars').innerHTML=eras.length?eras.map(row=>`<div class="bar-row"><span>${esc(row.stratum)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,Number(row.observed_rate)||0)}%"></div></div><span>${fmt(row.observed_rate,2)}% · H ${pFmt(row.p_adjusted)}</span></div>`).join(''):'<span class="footnote">No NIAH era results available.</span>';
}
function renderStats() {
  const rows=[...SIG.map(row=>({...row,family:'global'})),...NEGATIVE.map(row=>({...row,family:'negative-control'})),...NIAH_SIG.slice(0,30).map(row=>({...row,family:'NIAH'})),...MATCHED.map(row=>({...row,family:'matched'})),...HIER.map(row=>({...row,family:'hierarchical'})),...MORAN.map(row=>({...row,family:'Moran'})),...COUNTY_PERM.map(row=>({...row,family:'county'})),...BOOT.map(row=>({...row,family:'block-bootstrap'}))];
  const observed=row=>row.observed_rate??row.target_rate??row.golden_angle_rate??row.observed_difference_pp;
  const reference=row=>row.control_rate??row.reference_rate??row.matched_control_rate??row.ci_low_pp??'—';
  const result=row=>row.family==='block-bootstrap'?`95% CI ${fmt(row.ci_low_pp,2)} to ${fmt(row.ci_high_pp,2)} pp · P+ ${fmt(row.prob_positive,2)}`:(row.verdict||'diagnostic');
  $('statsTable').innerHTML=rows.length?`<table><thead><tr><th>Test</th><th>Observed</th><th>Reference</th><th>p / Holm</th><th>Result</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.family)} · ${esc(row.signal||row.group||row.stratum||'')}</td><td>${esc(observed(row)??'—')}${row.family==='Moran'?' I':''}</td><td>${esc(reference(row))}${reference(row)!=='—'?'%':''}</td><td>${pFmt(row.p_value)} / ${pFmt(row.p_adjusted)}</td><td class="verdict-${esc(row.verdict)}">${esc(result(row))}</td></tr>`).join('')}</tbody></table>`:'<span class="footnote">No statistical results available.</span>';
}
function renderQuality() {
  const status=SUMMARY.quality_status||'unknown';
  const valid=fmt(SUMMARY.valid_geometry_pct,2);
  const duplicateRows=Number(SUMMARY.duplicate_centroid_n||0);
  $('quality').innerHTML=`<p>Audit status: <b>${esc(status)}</b>; valid geometry: <b>${valid}%</b>.</p><p>${duplicateRows.toLocaleString()} duplicate centroid rows beyond the first are grouped in ${QUALITY_DUPLICATES.length.toLocaleString()} review records. Exact centroid equality is a diagnostic of mapping/representation overlap, not evidence that the footprints are the same building.</p>`;
  if(!QUALITY_DUPLICATES.length) { $('qualityFindings').innerHTML='<p class="footnote">No duplicate-centroid groups were reported.</p>'; renderQualityAudit(); return; }
  $('qualityFindings').innerHTML=`<details class="quality-details"><summary>Inspect duplicate-centroid groups (${QUALITY_DUPLICATES.length.toLocaleString()})</summary><div class="quality-table"><table><thead><tr><th>Centroid</th><th>OSM members</th><th>Groups</th><th>Rows</th><th>Status</th></tr></thead><tbody>${QUALITY_DUPLICATES.map(row=>`<tr><td>${fmt(row.lat,6)}, ${fmt(row.lon,6)}</td><td>${qualityMemberHtml(row.osm_ids)}</td><td>${qualityGroupHtml(row.groups)}</td><td>${esc(row.row_n)} total · ${esc(row.target_n)} target · ${esc(row.control_n)} control</td><td>${esc(row.review_status||'review')}</td></tr>`).join('')}</tbody></table></div><div class="quality-status footnote">Click an OSM ID button to focus a target in the dashboard; use the adjacent OSM link for the source record.</div></details>`;
  document.querySelectorAll('[data-quality-focus]').forEach(button=>button.addEventListener('click',()=>focusQualityId(button.dataset.qualityFocus)));
  renderQualityAudit();
}
function sourceStatusText() {
  const statuses=SUMMARY.source_status||{};
  const entries=Object.entries(statuses);
  return entries.length ? entries.map(([key,value])=>`${esc(key.replaceAll('_',' '))}: <b>${esc(String(value).replaceAll('_',' '))}</b>`).join(' · ') : 'not reported';
}
function sourceFreshnessText() {
  const freshness=SUMMARY.source_freshness||{};
  if(freshness.status!=='reported' || !Number(freshness.source_count)) return freshness.status==='invalid' ? 'invalid freshness record' : 'not reported';
  const oldest=Number(freshness.oldest_age_days);
  const age=Number.isFinite(oldest) ? `; oldest cached source ${fmt(oldest,1)} days old` : '';
  const observed=freshness.observed_at ? `; observed ${esc(freshness.observed_at)}` : '';
  return `${Number(freshness.source_count).toLocaleString()} cached sources under ${esc(freshness.contract||'the freshness contract')}${age}${observed}`;
}
function routePayloadDetails(payload) { return payload.type==='Feature' ? (payload.properties||{}) : payload; }
function routeCoordinates(payload) {
  const geometry=payload.type==='Feature' ? payload.geometry : null;
  const raw=geometry&&geometry.type==='LineString' ? geometry.coordinates : routePayloadDetails(payload).path_coordinates;
  if(!Array.isArray(raw)) return null;
  const coordinates=raw.filter(pair=>Array.isArray(pair)&&pair.length>=2&&Number.isFinite(Number(pair[0]))&&Number.isFinite(Number(pair[1]))).map(pair=>[Number(pair[0]),Number(pair[1])]);
  return coordinates.length>1 ? coordinates : null;
}
function routeDurationText(value) {
  const seconds=Number(value);
  if(!Number.isFinite(seconds)||seconds<0) return '—';
  const whole=Math.round(seconds), hours=Math.floor(whole/3600), minutes=Math.floor((whole%3600)/60), remainder=whole%60;
  if(hours) return `${hours}h${minutes?` ${minutes}m`:''}`;
  if(minutes) return `${minutes}m${remainder?` ${remainder}s`:''}`;
  return `${remainder}s`;
}
function routeWaitText(route) {
  const wait=Number(route.ferry_wait_s), count=Number(route.ferry_wait_n);
  if(!Number.isFinite(wait)||!Number.isFinite(count)) return 'ferry wait unavailable';
  if(wait<=0||count<=0) return 'no ferry wait';
  return `ferry wait ${routeDurationText(wait)} (${count} ${count===1?'wait':'waits'})`;
}
function routeObjectiveText(route) {
  return route.objective==='duration' ? 'fastest duration' : 'shortest distance';
}
function routeFerryText(route) {
  const edges=Number(route.ferry_edge_n), distance=Number(route.ferry_distance_m), crossing=Number(route.ferry_crossing_s), ways=Array.isArray(route.ferry_way_ids)?route.ferry_way_ids.length:0;
  if(!Number.isFinite(edges)||edges<=0) return 'no ferry segment';
  return `ferry ${fmt(distance,0)} m / ${routeDurationText(crossing)} crossing (${ways} ${ways===1?'way':'ways'})`;
}
function routeSegmentText(route) {
  if(!Object.prototype.hasOwnProperty.call(route,'path_segment_source')) return 'path details not requested';
  const segments=Number(route.path_segment_n);
  if(route.path_segment_source==='sqlite_edges'&&Number.isFinite(segments)) {
    const distance=Number(route.path_segment_total_distance_m), duration=Number(route.path_segment_total_duration_s), wait=Number(route.path_segment_total_wait_s);
    const constraints=Array.isArray(route.path_segments) ? route.path_segments.reduce((total, segment)=>total+(Array.isArray(segment.constraints)?segment.constraints.length:0),0) : 0;
    const constraintDetail=constraints>0 ? ` · ${constraints} static ${constraints===1?'edge constraint':'edge constraints'}` : '';
    const conditionalRules=Array.isArray(route.path_segments) ? route.path_segments.reduce((total, segment)=>total+(Array.isArray(segment.conditional_rules)?segment.conditional_rules.length:0),0) : 0;
    const conditionalDetail=conditionalRules>0 ? ` · ${conditionalRules} conditional ${conditionalRules===1?'rule':'rules'}` : '';
    const transitionRules=Array.isArray(route.path_segments) ? route.path_segments.reduce((total, segment)=>total+(Array.isArray(segment.transition_rules)?segment.transition_rules.length:0),0) : 0;
    const transitionDetail=transitionRules>0 ? ` · ${transitionRules} turn ${transitionRules===1?'rule':'rules'}` : '';
    const wayContext=Array.isArray(route.path_segments) ? route.path_segments.reduce((total, segment)=>{
      const context=segment&&segment.road_context;
      return total+(context&&(context.name||context.ref)?1:0);
    },0) : 0;
    const wayContextDetail=wayContext>0 ? ` · ${wayContext} named ${wayContext===1?'way':'ways'}` : '';
    const maneuverCount=Number(route.maneuver_n);
    const maneuverDetail=Number.isFinite(maneuverCount) ? ` · ${maneuverCount} ${maneuverCount===1?'maneuver':'maneuvers'}` : '';
    const detail=Number.isFinite(distance)&&Number.isFinite(duration) ? ` · ${fmt(distance,0)} m / ${routeDurationText(duration)}${Number.isFinite(wait)&&wait>0?` · ${routeDurationText(wait)} wait`:''}${constraintDetail}${conditionalDetail}${transitionDetail}${wayContextDetail}${maneuverDetail}` : `${constraintDetail}${conditionalDetail}${transitionDetail}${wayContextDetail}${maneuverDetail}`;
    return `${segments} mapped road ${segments===1?'segment':'segments'}${detail}`;
  }
  return 'mapped segment IDs unavailable';
}
function routeManeuverRoadText(maneuver) {
  const context=maneuver?.road_context||{};
  const labels=[context.name,context.ref].filter(value=>value);
  if(labels.length) return labels.join(' · ');
  if(context.route==='ferry') return 'Ferry crossing';
  return context.highway ? `Mapped ${context.highway}` : 'Mapped way';
}
function routeManeuverKindText(kind) {
  const labels={start:'Start',arrive:'Arrive',continue:'Continue',change_way:'Continue onto mapped way',slight_left:'Bear left',left:'Turn left',slight_right:'Bear right',right:'Turn right',u_turn:'U-turn',ferry_boarding:'Board ferry',ferry_landing:'Leave ferry'};
  return labels[kind]||String(kind||'Maneuver').replaceAll('_',' ');
}
function routeManeuverLabel(maneuver) {
  if(!maneuver) return 'Route maneuver';
  const kind=String(maneuver.kind||'');
  const road=routeManeuverRoadText(maneuver);
  if(kind==='start') return `Start on ${road}`;
  if(kind==='arrive') return `Arrive from ${road}`;
  if(kind==='ferry_boarding') return `Board ${road}`;
  if(kind==='ferry_landing') return `Leave ${road}`;
  if(kind==='continue') return `Continue on ${road}`;
  if(kind==='change_way') return `Continue onto ${road}`;
  return `${routeManeuverKindText(kind)} onto ${road}`;
}
function routeManeuverMetricText(maneuver) {
  const distance=Number(maneuver?.distance_m), duration=Number(maneuver?.duration_s), wait=Number(maneuver?.wait_s);
  const metrics=[];
  if(Number.isFinite(distance)) metrics.push(`${fmt(distance,0)} m`);
  if(Number.isFinite(duration)) metrics.push(routeDurationText(duration));
  if(Number.isFinite(wait)&&wait>0) metrics.push(`${routeDurationText(wait)} wait`);
  return metrics.join(' · ');
}
function routeSegmentRoadText(segment) {
  const context=segment?.road_context||{};
  const labels=[context.name,context.ref].filter(value=>value);
  if(labels.length) return labels.join(' · ');
  if(segment?.ferry||context.route==='ferry') return 'Ferry crossing';
  if(context.highway) return `Mapped ${context.highway}`;
  return segment?.way_id ? `Way ${segment.way_id}` : 'Mapped way';
}
function routeSegmentChecksText(segment) {
  const counts=[
    [Array.isArray(segment?.constraints)?segment.constraints.length:0,'static'],
    [Array.isArray(segment?.conditional_rules)?segment.conditional_rules.length:0,'conditional'],
    [Array.isArray(segment?.transition_rules)?segment.transition_rules.length:0,'turn'],
  ].filter(([count])=>count>0).map(([count,label])=>`${count} ${label}`);
  return counts.length ? counts.join(' · ') : 'none recorded';
}
function routeSegmentCheckRecords(segment) {
  const records=[];
  const valueText=record=>{
    const value=record?.value;
    if(value===undefined||value===null||String(value)==='') return '';
    return `${value}${record?.unit?` ${record.unit}`:''}`;
  };
  const stateText=record=>record?.evaluated===false?'not evaluated':record?.applied===true?'applied':record?.active===true?'active':record?.active===false?'inactive':'evaluated';
  (Array.isArray(segment?.constraints)?segment.constraints:[]).forEach(record=>{
    records.push(`${record?.key||'static constraint'}${valueText(record)?` = ${valueText(record)}`:''} · ${stateText(record)}`);
  });
  (Array.isArray(segment?.conditional_rules)?segment.conditional_rules:[]).forEach(record=>{
    const condition=record?.condition?` · ${record.condition}`:'';
    const profile=record?.profile?` · ${record.profile}`:'';
    records.push(`${record?.key||'conditional rule'}${valueText(record)?` = ${valueText(record)}`:''}${condition} · ${stateText(record)}${profile}`);
  });
  (Array.isArray(segment?.transition_rules)?segment.transition_rules:[]).forEach(record=>{
    const relation=record?.relation_id?`relation ${record.relation_id}`:'turn restriction';
    const ways=[record?.from_way,record?.to_way].filter(value=>value).join(' → ');
    const via=Array.isArray(record?.via_way_ids)&&record.via_way_ids.length?` via ${record.via_way_ids.join(' → ')}`:'';
    const selected=record?.selected===true?' · selected':record?.selected===false?' · not selected':'';
    records.push(`${relation}${record?.kind?` · ${record.kind}`:''}${ways?` · ${ways}`:''}${via}${selected} · ${stateText(record)}`);
  });
  return records;
}
function routeSegmentChecksHtml(segment) {
  const records=routeSegmentCheckRecords(segment);
  if(!records.length) return 'none recorded';
  return `<details class="route-segment-checks"><summary>${esc(routeSegmentChecksText(segment))}</summary><ul class="route-segment-check-list">${records.map(record=>`<li>${esc(record)}</li>`).join('')}</ul></details>`;
}
function renderRouteSegments(payload) {
  const panel=$('routeSegments'), list=$('routeSegmentList'), summary=$('routeSegmentSummary');
  if(!panel||!list||!summary) return;
  const route=routePayloadDetails(payload), raw=route.path_segments;
  const segments=Array.isArray(raw) ? raw.filter(item=>item&&typeof item==='object') : [];
  if(!segments.length) { panel.hidden=true; list.innerHTML=''; summary.textContent=''; return; }
  const declared=Number(route.path_segment_n);
  const total=Number.isFinite(declared)&&declared>=segments.length ? declared : segments.length;
  const visible=segments.slice(0,ROUTE_SEGMENT_DISPLAY_LIMIT);
  summary.textContent=visible.length<total
    ? `showing ${visible.length} of ${total} mapped segments`
    : `${total} mapped ${total===1?'segment':'segments'} · restriction provenance included`;
  list.innerHTML=visible.map((segment,index)=>{
    const distance=Number(segment.distance_m), duration=Number(segment.duration_s), wait=Number(segment.wait_s);
    const time=[Number.isFinite(duration)?routeDurationText(duration):'—',Number.isFinite(wait)&&wait>0?`${routeDurationText(wait)} wait`:null].filter(Boolean).join(' · ');
    const mode=segment.ferry?'Ferry':'Road';
    const way=segment.way_id||'way ID unavailable';
    return `<tr><th scope="row">${index+1}</th><td><strong>${esc(routeSegmentRoadText(segment))}</strong><small>${esc(way)}</small></td><td>${Number.isFinite(distance)?`${fmt(distance,0)} m`:'—'}</td><td>${esc(time)}</td><td>${mode}</td><td>${routeSegmentChecksHtml(segment)}</td></tr>`;
  }).join('');
  panel.hidden=false;
}
function renderRouteManeuvers(payload) {
  if(routeManeuverMarker) { routeManeuverMarker.remove(); routeManeuverMarker=null; }
  routeManeuverFocus=null;
  const route=routePayloadDetails(payload), raw=route.maneuvers;
  routeManeuverData=Array.isArray(raw) ? raw.filter(item=>item&&typeof item==='object') : [];
  const panel=$('routeManeuvers'), list=$('routeManeuverList'), summary=$('routeManeuverSummary');
  if(!panel||!list||!summary) return;
  if(!routeManeuverData.length) { panel.hidden=true; list.innerHTML=''; summary.textContent=''; return; }
  summary.textContent=`${routeManeuverData.length} mapped steps · click a step to focus the map`;
  list.innerHTML=routeManeuverData.map((maneuver,index)=>{
    const label=routeManeuverLabel(maneuver), metrics=routeManeuverMetricText(maneuver);
    return `<li><button class="route-maneuver" type="button" data-route-maneuver-index="${index}" aria-current="false" aria-label="${esc(`${index+1}. ${label}${metrics?`; ${metrics}`:''}`)}"><span class="route-maneuver-index" aria-hidden="true">${index+1}</span><span class="route-maneuver-copy"><strong>${esc(label)}</strong><small>${esc(metrics||'Map focus available')}</small></span></button></li>`;
  }).join('');
  list.querySelectorAll('[data-route-maneuver-index]').forEach(button=>button.addEventListener('click',()=>focusRouteManeuver(Number(button.dataset.routeManeuverIndex))));
  panel.hidden=false;
}
function focusRouteManeuver(index) {
  if(!Number.isInteger(index)||!routeManeuverData[index]) return;
  routeManeuverFocus=index;
  document.querySelectorAll('[data-route-maneuver-index]').forEach(button=>{
    const active=Number(button.dataset.routeManeuverIndex)===index;
    button.setAttribute('aria-current',String(active));
  });
  const coordinate=routeManeuverData[index].coordinate;
  if(Array.isArray(coordinate)&&coordinate.length>=2) {
    const lon=Number(coordinate[0]), lat=Number(coordinate[1]);
    if(Number.isFinite(lat)&&Number.isFinite(lon)) {
      if(offlineMap) renderOfflineMap();
      else if(map) { map.setView([lat,lon],Math.max(Number(map.getZoom()||0),16),{animate:true}); renderRouteManeuverMarker(); }
    }
  }
}
function clearRouteManeuvers() {
  if(routeManeuverMarker) { routeManeuverMarker.remove(); routeManeuverMarker=null; }
  routeManeuverData=[]; routeManeuverFocus=null;
  const panel=$('routeManeuvers'), list=$('routeManeuverList'), summary=$('routeManeuverSummary');
  if(panel) panel.hidden=true;
  if(list) list.innerHTML='';
  if(summary) summary.textContent='';
}
function clearRouteSegments() {
  const panel=$('routeSegments'), list=$('routeSegmentList'), summary=$('routeSegmentSummary');
  if(panel) panel.hidden=true;
  if(list) list.innerHTML='';
  if(summary) summary.textContent='';
}
function routeWeightText(route) {
  const weight=Number(route.vehicle_weight_t);
  return Number.isFinite(weight)&&weight>0 ? `vehicle-weight profile ${fmt(weight,1)} t` : 'vehicle weight unspecified';
}
function routeRatingText(route) {
  const rating=Number(route.vehicle_rating_t);
  return Number.isFinite(rating)&&rating>0 ? `HGV rating profile ${fmt(rating,1)} t` : 'HGV rating unspecified';
}
function routeHeightText(route) {
  const height=Number(route.vehicle_height_m);
  return Number.isFinite(height)&&height>0 ? `vehicle-height profile ${fmt(height,2)} m` : 'vehicle height unspecified';
}
function routeWidthText(route) {
  const width=Number(route.vehicle_width_m);
  return Number.isFinite(width)&&width>0 ? `vehicle-width profile ${fmt(width,2)} m` : 'vehicle width unspecified';
}
function routeLengthText(route) {
  const length=Number(route.vehicle_length_m);
  return Number.isFinite(length)&&length>0 ? `vehicle-length profile ${fmt(length,2)} m` : 'vehicle length unspecified';
}
function routeAxleloadText(route) {
  const axleload=Number(route.vehicle_axleload_t);
  return Number.isFinite(axleload)&&axleload>0 ? `vehicle-axle-load profile ${fmt(axleload,2)} t` : 'vehicle axle load unspecified';
}
function routeVehicleClassText(route) {
  if(route.vehicle_class==='delivery') return 'delivery profile';
  if(route.vehicle_class==='hgv') return 'HGV profile';
  if(route.vehicle_class==='psv') return 'psv profile';
  if(route.vehicle_class==='taxi') return 'taxi profile';
  return 'general profile';
}
function routeDestinationText(route) { return route.vehicle_class==='hgv' && route.allow_hgv_destination ? 'destination access enabled' : 'destination access default'; }
function routeStatusText(payload) {
  const route=routePayloadDetails(payload);
  if(route.reachable) return `Reachable · ${routeObjectiveText(route)} · ${fmt(route.route_distance_m,0)} m · ${fmt(route.estimated_duration_s,1)} s · ${routeVehicleClassText(route)} · ${routeDestinationText(route)} · ${routeWeightText(route)} · ${routeRatingText(route)} · ${routeHeightText(route)} · ${routeWidthText(route)} · ${routeLengthText(route)} · ${routeAxleloadText(route)} · ${routeSegmentText(route)} · ${routeFerryText(route)} · ${routeWaitText(route)}`;
  return route.error || route.status || 'Route unavailable';
}
function routeComparisonProfileText(row) {
  const labels=[routeVehicleClassText(row)];
  const dimensions=[['vehicle_weight_t','t',1],['vehicle_rating_t','t rating',1],['vehicle_height_m','m high',2],['vehicle_width_m','m wide',2],['vehicle_length_m','m long',2],['vehicle_axleload_t','t axle',2]];
  dimensions.forEach(([key,unit,digits])=>{ const value=Number(row[key]); if(Number.isFinite(value)&&value>0) labels.push(`${fmt(value,digits)} ${unit}`); });
  if(row.vehicle_class==='hgv'&&row.allow_hgv_destination) labels.push('destination access');
  return labels.join(' · ');
}
function routeComparisonDeltaText(row, field, digits, suffix='') {
  const value=Number(row.delta_from_baseline?.[field]);
  if(!Number.isFinite(value)) return '—';
  const sign=value>0?'+':'';
  return `${sign}${fmt(value,digits)}${suffix}`;
}
function routeComparisonStatusText(payload) {
  const total=Number(payload.profile_n), reachable=Number(payload.reachable_n), baseline=payload.baseline_profile||'first profile';
  const objective=payload.objective==='duration'?'fastest duration':'shortest distance';
  return `${Number.isFinite(reachable)?reachable:'?'} of ${Number.isFinite(total)?total:'?'} profiles reachable · baseline ${baseline} · ${objective}`;
}
function clearRouteComparisonResponse() {
  routeComparisonPayloadData=null; routeComparisonSelectedIndex=null;
  const panel=$('routeCompareResults'), list=$('routeCompareList'), summary=$('routeCompareSummary'), result=$('routeCompareResult'), downloadButton=$('routeCompareDownloadJson');
  if(panel) panel.hidden=true;
  if(list) list.innerHTML='';
  if(summary) summary.textContent='';
  if(result) { result.textContent=''; result.hidden=true; }
  if(downloadButton) downloadButton.hidden=true;
}
function renderRouteComparison(payload) {
  const panel=$('routeCompareResults'), list=$('routeCompareList'), summary=$('routeCompareSummary');
  if(!panel||!list||!summary) return;
  const rows=Array.isArray(payload.profiles)?payload.profiles.filter(row=>row&&typeof row==='object'):[];
  summary.textContent=routeComparisonStatusText(payload);
  list.innerHTML=rows.map((row,index)=>{
    const reachable=row.reachable===true;
    const status=reachable?'Reachable':(row.status||'Unavailable');
    const path=row.route&&typeof row.route==='object';
    const baseline=row.name===payload.baseline_profile;
    return `<tr><th scope="row"><strong>${esc(row.name||`Profile ${index+1}`)}${baseline?' · baseline':''}</strong><small>${esc(routeComparisonProfileText(row))}</small></th><td class="${reachable?'':'route-compare-unreachable'}">${esc(status)}</td><td>${reachable?`${fmt(row.route_distance_m,0)} m`:'—'}</td><td>${routeComparisonDeltaText(row,'route_distance_m',0,' m')}</td><td>${reachable?routeDurationText(row.estimated_duration_s):'—'}</td><td>${routeComparisonDeltaText(row,'estimated_duration_s',0,'')}</td><td>${Number.isFinite(Number(row.ferry_wait_s))?routeDurationText(row.ferry_wait_s):'—'}</td><td>${path?`<button class="route-compare-path" type="button" data-route-compare-index="${index}" aria-current="${routeComparisonSelectedIndex===index?'true':'false'}">${routeComparisonSelectedIndex===index?'Showing path':'Show path'}</button>`:'—'}</td></tr>`;
  }).join('');
  list.querySelectorAll('[data-route-compare-index]').forEach(button=>button.addEventListener('click',()=>selectRouteComparisonProfile(Number(button.dataset.routeCompareIndex))));
  panel.hidden=false;
}
function showRoutePathPayload(route,label) {
  routePayloadData=route; setRouteResponseActions(route);
  routeGeometry=routeCoordinates(route); clearRouteManeuvers(); clearRouteSegments();
  renderRouteManeuvers(route); renderRouteSegments(route); renderMap();
  if(map&&routeLine) map.fitBounds(routeLine.getBounds(),{padding:[24,24],maxZoom:16});
  $('routeStatus').textContent=`${label} · ${routeStatusText(route)}${routeGeometry?' · line shown on map':''}`;
}
function selectRouteComparisonProfile(index) {
  const rows=Array.isArray(routeComparisonPayloadData?.profiles)?routeComparisonPayloadData.profiles:[], row=rows[index];
  if(!row) return;
  routeComparisonSelectedIndex=index;
  document.querySelectorAll('[data-route-compare-index]').forEach(button=>{
    const active=Number(button.dataset.routeCompareIndex)===index;
    button.setAttribute('aria-current',String(active)); button.textContent=active?'Showing path':'Show path';
  });
  const route=row.route&&typeof row.route==='object'?row.route:null;
  if(!route) { $('routeStatus').textContent=`${row.name||'Profile'} has no embedded path; rerun with profile paths enabled.`; return; }
  showRoutePathPayload(route,row.name||'Profile');
}
function downloadRouteComparison() {
  if(!routeComparisonPayloadData) return;
  download('ireland-geometry-route-comparison.json',JSON.stringify(routeComparisonPayloadData,null,2),'application/json');
  $('routeCompareStatus').textContent='JSON profile comparison downloaded.';
}
function routeMatrixPointText(point) {
  if(!point||!Number.isFinite(Number(point.lat))||!Number.isFinite(Number(point.lon))) return '—';
  return `${fmt(point.lat,5)}, ${fmt(point.lon,5)}`;
}
function routeMatrixStatusText(payload) {
  const origins=Number(payload.origin_n), destinations=Number(payload.destination_n), pairs=Number(payload.pair_n), reachable=Number(payload.reachable_n);
  const objective=payload.objective==='duration'?'fastest duration':'shortest distance';
  return `${Number.isFinite(origins)?origins:'?'} origins × ${Number.isFinite(destinations)?destinations:'?'} destinations · ${Number.isFinite(pairs)?pairs:'?'} pairs · ${Number.isFinite(reachable)?reachable:'?'} reachable · ${objective}`;
}
function clearRouteMatrixResponse() {
  routeMatrixPayloadData=null; routeMatrixSelectedIndex=null;
  const panel=$('routeMatrixResults'), list=$('routeMatrixList'), summary=$('routeMatrixSummary'), result=$('routeMatrixResult'), downloadButton=$('routeMatrixDownloadJson');
  if(panel) panel.hidden=true;
  if(list) list.innerHTML='';
  if(summary) summary.textContent='';
  if(result) { result.textContent=''; result.hidden=true; }
  if(downloadButton) downloadButton.hidden=true;
}
function renderRouteMatrix(payload) {
  const panel=$('routeMatrixResults'), list=$('routeMatrixList'), summary=$('routeMatrixSummary');
  if(!panel||!list||!summary) return;
  const origins=Array.isArray(payload.origins)?payload.origins:[], destinations=Array.isArray(payload.destinations)?payload.destinations:[], pairs=Array.isArray(payload.pairs)?payload.pairs.filter(pair=>pair&&typeof pair==='object'):[];
  summary.textContent=routeMatrixStatusText(payload);
  list.innerHTML=pairs.map((pair,index)=>{
    const origin=origins[Number(pair.origin_index)], destination=destinations[Number(pair.destination_index)], reachable=pair.reachable===true, path=pair.route&&typeof pair.route==='object';
    const status=reachable?'Reachable':(pair.status||'Unavailable');
    return `<tr><th scope="row"><strong>O${Number(pair.origin_index)+1} → D${Number(pair.destination_index)+1}</strong><small>${esc(routeMatrixPointText(origin))} → ${esc(routeMatrixPointText(destination))}</small></th><td class="${reachable?'':'route-matrix-unreachable'}">${esc(status)}</td><td>${reachable?`${fmt(pair.route_distance_m,0)} m`:'—'}</td><td>${reachable?routeDurationText(pair.estimated_duration_s):'—'}</td><td>${Number.isFinite(Number(pair.ferry_wait_s))?routeDurationText(pair.ferry_wait_s):'—'}</td><td>${pair.arrival?esc(pair.arrival):'—'}</td><td>${path?`<button class="route-matrix-path" type="button" data-route-matrix-index="${index}" aria-current="${routeMatrixSelectedIndex===index?'true':'false'}">${routeMatrixSelectedIndex===index?'Showing path':'Show path'}</button>`:'—'}</td></tr>`;
  }).join('');
  list.querySelectorAll('[data-route-matrix-index]').forEach(button=>button.addEventListener('click',()=>selectRouteMatrixPair(Number(button.dataset.routeMatrixIndex))));
  panel.hidden=false;
}
function selectRouteMatrixPair(index) {
  const pairs=Array.isArray(routeMatrixPayloadData?.pairs)?routeMatrixPayloadData.pairs:[], pair=pairs[index];
  if(!pair) return;
  routeMatrixSelectedIndex=index;
  document.querySelectorAll('[data-route-matrix-index]').forEach(button=>{
    const active=Number(button.dataset.routeMatrixIndex)===index;
    button.setAttribute('aria-current',String(active)); button.textContent=active?'Showing path':'Show path';
  });
  const route=pair.route&&typeof pair.route==='object'?pair.route:null;
  if(!route) { $('routeStatus').textContent='This matrix response does not include pair paths; rerun with pair paths enabled.'; return; }
  showRoutePathPayload(route,`Matrix O${Number(pair.origin_index)+1} → D${Number(pair.destination_index)+1}`);
}
function downloadRouteMatrix() {
  if(!routeMatrixPayloadData) return;
  download('ireland-geometry-route-matrix.json',JSON.stringify(routeMatrixPayloadData,null,2),'application/json');
  $('routeMatrixStatus').textContent='JSON route matrix downloaded.';
}
function routeFieldsFromForm() {
  const fields={
    start_lat:$('routeStartLat').value.trim(), start_lon:$('routeStartLon').value.trim(),
    goal_lat:$('routeGoalLat').value.trim(), goal_lon:$('routeGoalLon').value.trim(),
    speed_kmh:$('routeSpeed').value.trim(), weight_t:$('routeWeight').value.trim(), rating_t:$('routeRating').value.trim(), height_m:$('routeHeight').value.trim(), width_m:$('routeWidth').value.trim(), length_m:$('routeLength').value.trim(), axleload_t:$('routeAxleload').value.trim(), vehicle_class:$('routeVehicleClass').value, allow_hgv_destination:$('routeAllowHgvDestination').checked?'1':'0', objective:$('routeObjective').value
  };
  if($('routeDeparture').value.trim()) fields.departure=$('routeDeparture').value.trim();
  if(!fields.weight_t) delete fields.weight_t;
  if(!fields.rating_t) delete fields.rating_t;
  if(!fields.height_m) delete fields.height_m;
  if(!fields.width_m) delete fields.width_m;
  if(!fields.length_m) delete fields.length_m;
  if(!fields.axleload_t) delete fields.axleload_t;
  if($('routeIncludePath').checked) fields.include_path='1';
  if($('routeIncludeFerries').checked) fields.include_ferries='1';
  if($('routeFormat').value==='geojson') fields.format='geojson';
  return fields;
}
function routeComparisonFieldsFromForm() {
  const fields={
    start_lat:$('routeStartLat').value.trim(), start_lon:$('routeStartLon').value.trim(),
    goal_lat:$('routeGoalLat').value.trim(), goal_lon:$('routeGoalLon').value.trim(),
    speed_kmh:$('routeSpeed').value.trim(), objective:$('routeObjective').value,
    profiles:$('routeCompareProfiles').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),
  };
  if($('routeDeparture').value.trim()) fields.departure=$('routeDeparture').value.trim();
  if($('routeCompareIncludePath').checked) fields.include_path='1';
  if($('routeCompareIncludeFerries').checked) fields.include_ferries='1';
  return fields;
}
function routeMatrixFieldsFromForm() {
  const fields={
    origins:$('routeMatrixOrigins').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),
    destinations:$('routeMatrixDestinations').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),
    speed_kmh:$('routeSpeed').value.trim(), weight_t:$('routeWeight').value.trim(), rating_t:$('routeRating').value.trim(), height_m:$('routeHeight').value.trim(), width_m:$('routeWidth').value.trim(), length_m:$('routeLength').value.trim(), axleload_t:$('routeAxleload').value.trim(), vehicle_class:$('routeVehicleClass').value, allow_hgv_destination:$('routeAllowHgvDestination').checked?'1':'0', objective:$('routeObjective').value,
  };
  if($('routeDeparture').value.trim()) fields.departure=$('routeDeparture').value.trim();
  ['weight_t','rating_t','height_m','width_m','length_m','axleload_t'].forEach(key=>{ if(!fields[key]) delete fields[key]; });
  if($('routeMatrixIncludePath').checked) fields.include_path='1';
  if($('routeMatrixIncludeFerries').checked) fields.include_ferries='1';
  return fields;
}
function routeGeojsonPayload(payload) {
  if(payload?.type==='Feature') return payload;
  const route=routePayloadDetails(payload), coordinates=routeCoordinates(payload);
  const pathOnly=new Set(['path_node_ids','path_coordinates','path_way_ids','path_segments','path_segment_n','path_segment_total_distance_m','path_segment_total_duration_s','path_segment_total_wait_s','path_segment_source','maneuver_n','maneuvers']);
  const properties=Object.fromEntries(Object.entries(route).filter(([key])=>!pathOnly.has(key)));
  const geometry=!coordinates ? null : coordinates.length===1 ? {type:'Point',coordinates:coordinates[0]} : {type:'LineString',coordinates};
  return {type:'Feature',contract:payload?.contract||route.contract||'ireland-geometry.route.v1',properties,geometry};
}
function clearRouteResponseActions() {
  routePayloadData=null;
  const json=$('routeDownloadJson'), geojson=$('routeDownloadGeojson'), status=$('routeShareStatus');
  if(json) json.hidden=true;
  if(geojson) geojson.hidden=true;
  if(status) status.textContent='';
}
function setRouteResponseActions(payload) {
  routePayloadData=payload;
  const json=$('routeDownloadJson'), geojson=$('routeDownloadGeojson');
  if(json) json.hidden=false;
  if(geojson) geojson.hidden=false;
}
function downloadRouteResponse(format) {
  if(!routePayloadData) return;
  const output=format==='geojson' ? routeGeojsonPayload(routePayloadData) : routePayloadData;
  const filename=format==='geojson'?'ireland-geometry-route.geojson':'ireland-geometry-route.json';
  const type=format==='geojson'?'application/geo+json':'application/json';
  download(filename,JSON.stringify(output,null,2),type);
  if($('routeShareStatus')) $('routeShareStatus').textContent=`${format==='geojson'?'GeoJSON':'JSON'} route response downloaded.`;
}
async function copyRouteLink() {
  const fields=routeFieldsFromForm(), missing=['start_lat','start_lon','goal_lat','goal_lon'].filter(key=>!fields[key]);
  const status=$('routeShareStatus');
  if(missing.length) { if(status) status.textContent=`Enter ${missing.join(', ')} before copying a route link.`; return; }
  syncRouteState(fields);
  try {
    if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(location.href);
    if(status) status.textContent='Route link copied; opening it restores the inputs and reruns the query.';
  } catch(error) {
    if(status) status.textContent='Route state saved in the URL; clipboard access is unavailable in this browser.';
  }
}
async function runRoute() {
  const fields=routeFieldsFromForm();
  const missing=['start_lat','start_lon','goal_lat','goal_lon'].filter(key=>!fields[key]);
  if(missing.length) { routeGeometry=null; clearRouteManeuvers(); clearRouteSegments(); renderMap(); $('routeStatus').textContent=`Missing: ${missing.join(', ')}`; return; }
  const numericFields=['speed_kmh','weight_t','rating_t','height_m','width_m','length_m','axleload_t'];
  const invalidNumeric=numericFields.find(key=>fields[key]!==undefined && (!Number.isFinite(Number(fields[key])) || Number(fields[key])<=0));
  if(invalidNumeric) { $('routeStatus').textContent=`${invalidNumeric} must be a finite positive number.`; return; }
  syncRouteState(fields);
  const button=$('routeRun'); button.disabled=true; routeGeometry=null; clearRouteManeuvers(); clearRouteSegments(); clearRouteResponseActions(); renderMap(); $('routeStatus').textContent='Routing…'; $('routeResult').hidden=true;
  const body={
    start:{lat:Number(fields.start_lat),lon:Number(fields.start_lon)},
    goal:{lat:Number(fields.goal_lat),lon:Number(fields.goal_lon)},
    speed_kmh:Number(fields.speed_kmh),
    vehicle_class:fields.vehicle_class,
    allow_hgv_destination:fields.allow_hgv_destination==='1',
    objective:fields.objective,
    include_path:fields.include_path==='1',
    include_ferries:fields.include_ferries==='1',
    format:fields.format||'json'
  };
  ['departure','weight_t','rating_t','height_m','width_m','length_m','axleload_t'].forEach(key=>{ if(fields[key]!==undefined&&fields[key]!=='') body[key]=key==='departure'?fields[key]:Number(fields[key]); });
  try {
    const response=await fetch('/api/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Route request failed (${response.status})`);
    routeGeometry=routeCoordinates(payload); renderRouteManeuvers(payload); renderRouteSegments(payload); renderMap();
    if(map&&routeLine) map.fitBounds(routeLine.getBounds(),{padding:[24,24],maxZoom:16});
    $('routeStatus').textContent=routeStatusText(payload)+(routeGeometry?' · line shown on map':'');
    setRouteResponseActions(payload);
    $('routeResult').textContent=JSON.stringify(payload,null,2); $('routeResult').hidden=false;
  } catch(error) {
    $('routeStatus').textContent=`Route unavailable: ${error.message}`;
  } finally { button.disabled=false; }
}
async function runRouteComparison() {
  const fields=routeComparisonFieldsFromForm(), missing=['start_lat','start_lon','goal_lat','goal_lon'].filter(key=>!fields[key]);
  if(missing.length) { $('routeCompareStatus').textContent=`Missing: ${missing.join(', ')}`; return; }
  if(fields.profiles.length<2||fields.profiles.length>8) { $('routeCompareStatus').textContent='Enter between 2 and 8 non-empty profile lines.'; return; }
  const speed=Number(fields.speed_kmh);
  if(!Number.isFinite(speed)||speed<=0) { $('routeCompareStatus').textContent='speed_kmh must be a finite positive number.'; return; }
  syncRouteComparisonState(fields);
  const button=$('routeCompareRun'); button.disabled=true; clearRouteComparisonResponse(); routeComparisonSelectedIndex=null; $('routeCompareStatus').textContent='Comparing profiles…';
  const body={
    start:{lat:Number(fields.start_lat),lon:Number(fields.start_lon)},
    goal:{lat:Number(fields.goal_lat),lon:Number(fields.goal_lon)},
    profiles:fields.profiles,
    speed_kmh:speed,
    objective:fields.objective,
    include_path:fields.include_path==='1',
    include_ferries:fields.include_ferries==='1'
  };
  if(fields.departure) body.departure=fields.departure;
  try {
    const response=await fetch('/api/route/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}), payload=await response.json();
    if(!response.ok) throw new Error(payload.error||`Profile comparison failed (${response.status})`);
    routeComparisonPayloadData=payload; renderRouteComparison(payload); $('routeCompareStatus').textContent=routeComparisonStatusText(payload);
    const result=$('routeCompareResult'); result.textContent=JSON.stringify(payload,null,2); result.hidden=false; $('routeCompareDownloadJson').hidden=false;
    const first=Array.isArray(payload.profiles)?payload.profiles.findIndex(row=>row&&row.route): -1;
    if(first>=0) selectRouteComparisonProfile(first);
  } catch(error) {
    $('routeCompareStatus').textContent=`Profile comparison unavailable: ${error.message}`;
  } finally { button.disabled=false; }
}
async function runRouteMatrix() {
  const fields=routeMatrixFieldsFromForm(), pairCount=fields.origins.length*fields.destinations.length;
  if(!fields.origins.length||!fields.destinations.length) { $('routeMatrixStatus').textContent='Enter at least one origin and one destination, one lat,lon per line.'; return; }
  if(pairCount>25) { $('routeMatrixStatus').textContent=`This matrix has ${pairCount} pairs; reduce it to the 25-pair limit.`; return; }
  const numericFields=['speed_kmh','weight_t','rating_t','height_m','width_m','length_m','axleload_t'];
  const invalidNumeric=numericFields.find(key=>fields[key]!==undefined && (!Number.isFinite(Number(fields[key])) || Number(fields[key])<=0));
  if(invalidNumeric) { $('routeMatrixStatus').textContent=`${invalidNumeric} must be a finite positive number.`; return; }
  syncRouteMatrixState(fields);
  const button=$('routeMatrixRun'); button.disabled=true; clearRouteMatrixResponse(); routeMatrixSelectedIndex=null; routeGeometry=null; clearRouteManeuvers(); clearRouteSegments(); renderMap(); $('routeMatrixStatus').textContent='Routing matrix…';
  const point=value=>{ const [lat,lon]=value.split(',').map(Number); return {lat,lon}; };
  const body={origins:fields.origins.map(point),destinations:fields.destinations.map(point),speed_kmh:Number(fields.speed_kmh),objective:fields.objective,vehicle_class:fields.vehicle_class,allow_hgv_destination:fields.allow_hgv_destination==='1',include_path:fields.include_path==='1',include_ferries:fields.include_ferries==='1'};
  ['departure','weight_t','rating_t','height_m','width_m','length_m','axleload_t'].forEach(key=>{ if(fields[key]!==undefined&&fields[key]!=='') body[key]=key==='departure'?fields[key]:Number(fields[key]); });
  try {
    const response=await fetch('/api/route/matrix',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}), payload=await response.json();
    if(!response.ok) throw new Error(payload.error||`Route matrix failed (${response.status})`);
    routeMatrixPayloadData=payload; renderRouteMatrix(payload); $('routeMatrixStatus').textContent=routeMatrixStatusText(payload);
    const result=$('routeMatrixResult'); result.textContent=JSON.stringify(payload,null,2); result.hidden=false; $('routeMatrixDownloadJson').hidden=false;
    const first=Array.isArray(payload.pairs)?payload.pairs.findIndex(pair=>pair&&pair.route): -1;
    if(first>=0) selectRouteMatrixPair(first);
  } catch(error) {
    $('routeMatrixStatus').textContent=`Route matrix unavailable: ${error.message}`;
  } finally { button.disabled=false; }
}
function initRoute() {
  $('routeRun').addEventListener('click',runRoute);
  $('routeCopyLink').addEventListener('click',copyRouteLink);
  $('routeDownloadJson').addEventListener('click',()=>downloadRouteResponse('json'));
  $('routeDownloadGeojson').addEventListener('click',()=>downloadRouteResponse('geojson'));
  $('routeCompareRun').addEventListener('click',runRouteComparison);
  $('routeCompareDownloadJson').addEventListener('click',downloadRouteComparison);
  $('routeMatrixRun').addEventListener('click',runRouteMatrix);
  $('routeMatrixDownloadJson').addEventListener('click',downloadRouteMatrix);
  const restored=restoreRouteState(), comparisonRestored=restoreRouteComparisonState(), matrixRestored=restoreRouteMatrixState();
  $('routeStatus').textContent=restored
    ? 'Route inputs restored from this link; rerunning against the local graph…'
    : location.protocol==='file:'
    ? 'Serve this report with ireland-geometry-serve; file mode has no route API.'
    : 'Enter coordinates and run a route against the local graph.';
  if(restored) setTimeout(runRoute,0);
  $('routeCompareStatus').textContent=comparisonRestored
    ? 'Comparison inputs restored from this link; rerunning against the local graph…'
    : location.protocol==='file:'
    ? 'Serve this report with ireland-geometry-serve; file mode has no route comparison API.'
    : 'Compare profiles against the same coordinates and route options.';
  if(comparisonRestored) setTimeout(runRouteComparison,50);
  $('routeMatrixStatus').textContent=matrixRestored
    ? 'Matrix inputs restored from this link; rerunning against the local graph…'
    : location.protocol==='file:'
    ? 'Serve this report with ireland-geometry-serve; file mode has no route matrix API.'
    : 'Run up to 25 ordered origin–destination pairs against the local graph.';
  if(matrixRestored) setTimeout(runRouteMatrix,100);
}
function download(name, content, type) { const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([content],{type})); a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),500); }
async function downloadFiltered(format) {
  const params=currentFilterParameters();
  const endpoint=PACK.endpoints?.export || '/api/report/export';
  const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(reportRequestBody(params,{format}))}); const body=await response.text();
  if(!response.ok) { let message=body; try { message=JSON.parse(body).error || body; } catch(error) {} throw new Error(message || `Export failed (${response.status})`); }
  const runtimeStatus=response.headers.get('X-Ireland-Geometry-Runtime-Status');
  if(applyRuntimeHeaders(response.headers)) { renderRuntimeStatus(); if(runtimeStatus && runtimeStatus!=='pass') renderInterpretation(); }
  if(runtimeStatus && runtimeStatus!=='pass') { const validation=response.headers.get('X-Ireland-Geometry-Validation') || runtimeStatus; const alignment=response.headers.get('X-Ireland-Geometry-Manifest-Alignment') || runtimeStatus; const source=response.headers.get('X-Ireland-Geometry-Source-Alignment') || runtimeStatus; $('count').textContent=`${format.toUpperCase()} export is provisional (runtime ${runtimeStatus}; validation ${validation}; manifest ${alignment}; sources ${source}).`; }
  download(format==='csv'?'ireland-geometry-filtered.csv':'ireland-geometry-filtered.geojson',body,format==='csv'?'text/csv':'application/geo+json');
}
function downloadCsv() { if(SERVER_MODE) { downloadFiltered('csv').catch(error=>{ $('count').textContent=`CSV export unavailable: ${error.message}`; }); return; } const cols=['osm_id','name','group','lat','lon','score','area_m2','aspect_ratio','convexity','circularity','flags','niah_name','niah_rating','niah_century']; const escCsv=v=>`"${String(v??'').replaceAll('"','""')}"`; const lines=[cols.join(',')]; filtered.forEach(row=>lines.push(cols.map(key=>{ if(key==='flags')return escCsv(flagsText(row)); if(key.startsWith('niah_'))return escCsv(row.niah[key.slice(5)]); return escCsv(row[key]); }).join(','))); download('ireland-geometry-filtered.csv',lines.join('\n'),'text/csv'); }
function downloadQualityAudit() { const cols=['scope','group','field','row_n','missing_n','missing_pct','unique_n','invalid_n','quality_status','notes']; const escCsv=v=>`"${String(v??'').replaceAll('"','""')}"`; const lines=[cols.join(','),...QUALITY_AUDIT.map(row=>cols.map(key=>escCsv(row[key])).join(','))]; download('ireland-data-quality-audit.csv',lines.join('\n'),'text/csv'); }
function downloadGeo() { if(SERVER_MODE) { downloadFiltered('geojson').catch(error=>{ $('count').textContent=`GeoJSON export unavailable: ${error.message}`; }); return; } const ids=new Set(filtered.map(row=>row.osm_id)); const copy={...GEOJSON,features:(GEOJSON.features||[]).filter(feature=>ids.has(feature.properties?.osm_id))}; download('ireland-geometry-filtered.geojson',JSON.stringify(copy),'application/geo+json'); }
let mapAssetsAvailable = false;
function loadStyle(href) { const link=document.createElement('link'); link.rel='stylesheet'; link.href=href; document.head.appendChild(link); }
function loadScript(src) { return new Promise((resolve,reject)=>{ const script=document.createElement('script'); script.src=src; script.onload=resolve; script.onerror=()=>reject(new Error(`Could not load map asset ${src}`)); document.head.appendChild(script); }); }
async function loadMapAssets() {
  if(OFFLINE_REQUESTED) { setMapLoading(false); return; }
  setMapLoading(true,'Loading live map controls…');
  loadStyle('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
  loadStyle('https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css');
  loadStyle('https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css');
  try {
    await loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');
    try { await loadScript('https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js'); } catch(error) { console.warn(error); }
    mapAssetsAvailable=typeof L!=='undefined';
  } catch(error) {
    mapAssetsAvailable=false;
    console.warn('Map assets unavailable; using offline fallback.',error);
  }
}
function initMap() {
  if(OFFLINE_REQUESTED || !mapAssetsAvailable || typeof L==='undefined') { offlineMap=true; renderOfflineMap(); restoreFocusedTarget(); return; }
  map=L.map('map',{preferCanvas:true}).setView(DEFAULT_MAP_CENTER,DEFAULT_MAP_ZOOM);
  map.on('moveend zoomend',()=>{ updateMapHud(); decorateMapAccessibility(); });
  setMapLoading(true,'Loading satellite imagery…');
  const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
    attribution:'Esri, Maxar, Earthstar Geographics, and the GIS User Community',maxZoom:19,maxNativeZoom:18,detectRetina:true
  });
  const streets=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
    attribution:'&copy; OpenStreetMap contributors',maxZoom:19,detectRetina:true
  });
  mapReferenceLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',{
    attribution:'Esri reference labels',maxZoom:19,maxNativeZoom:19,opacity:.92,detectRetina:true
  });
  const hybrid=L.layerGroup([satellite,mapReferenceLayer]);
  mapBaseLayers={satellite,hybrid,streets};
  bindMapTileSignals(satellite); bindMapTileSignals(streets); bindMapTileSignals(mapReferenceLayer);
  satellite.addTo(map);
  markerLayer=(L.markerClusterGroup?L.markerClusterGroup({maxClusterRadius:45,disableClusteringAtZoom:14}):L.layerGroup()).addTo(map);
  const outlineLayer=L.layerGroup().addTo(map);
  OUTLINES.forEach(item=>{
    const shapes=item.rings.length===1?item.rings[0]:item.rings;
    L.polygon(shapes,{color:'#f6e5ac',weight:2,fillColor:color(item.score),fillOpacity:.28})
      .bindPopup(`<b>${esc(item.name||'Unnamed')}</b><br>${esc(item.group)} · score ${fmt(item.score)}<br>${flagHtml({flags:item.flags||[]})}`)
      .addTo(outlineLayer);
  });
  map.on('baselayerchange',event=>{
    const layerName=event.name.toLowerCase();
    activeMapLayer=layerName.includes('hybrid')?'hybrid':layerName.includes('street')?'streets':'satellite';
    syncMapLayerButtons(); updateMapHud();
  });
  L.control.layers({'Satellite':satellite,'Hybrid':hybrid,'Streets':streets},{'Top outlines':outlineLayer,'Markers':markerLayer},{collapsed:true}).addTo(map);
  activeMapLayer='satellite'; syncMapLayerButtons(); renderMap(); restoreFocusedTarget();
}
function init() { $('score').addEventListener('input',()=>{$('scoreValue').textContent=$('score').value; $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); queueFilters();}); ['query','group','century','rating','niahType','reviewState','onlyAngle','onlyRatio','onlyCircular','onlyMulti'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',queueFilters)); $('prev').addEventListener('click',()=>{if(page>1){page--; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); $('next').addEventListener('click',()=>{const total=SERVER_MODE?Number(pageStats.total||0):filtered.length; if(page<Math.ceil(total/PAGE_SIZE)){page++; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); document.addEventListener('click',event=>{ const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(header) sortBy(header.dataset.sort); }); document.addEventListener('keydown',event=>{ if(event.key!=='Enter'&&event.key!==' ') return; const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(!header) return; event.preventDefault(); sortBy(header.dataset.sort); }); $('downloadCsv').addEventListener('click',downloadCsv); $('downloadGeo').addEventListener('click',downloadGeo); $('runtimeReload')?.addEventListener('click',()=>location.reload()); restoreViewState(); initAtlasNav(); $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); initMapHud(); initStudio(); initRoute(); $('method').innerHTML=`<p>Target rows: <b>${Number(SUMMARY.targets||0).toLocaleString()}</b>; controls: <b>${Number(SUMMARY.controls||0).toLocaleString()}</b>; NIAH joins: <b>${Number(SUMMARY.niah_matches||0).toLocaleString()}</b> (${Number(SUMMARY.niah_contained||0).toLocaleString()} contained, ${Number(SUMMARY.niah_near||0).toLocaleString()} near).</p><p>Source readiness: ${sourceStatusText()}.</p><p>Input freshness: <b>${sourceFreshnessText()}</b>.</p><p>Analytical readiness: <b>${SUMMARY.analysis_ready?'pass':'incomplete'}</b>; validation records: <b>${esc(SUMMARY.validation?.status||'not reported')}</b>.</p><p>Shape descriptors include rectangularity, angle entropy, radial Fourier coefficients, and radial variability. ${Number(SUMMARY.part_mapped||0).toLocaleString()} target footprints have mapped OSM building parts; LiDAR coverage is ${Number(SUMMARY.lidar_available||0).toLocaleString()} targets. Historical rows are review evidence, not proof of intent.</p><p>Primary rates use building-level two-proportion z-tests, Wilson confidence intervals, risk differences, continuity-corrected odds ratios, matched controls, hierarchical stratified odds ratios, Moran's I, county permutations, and Ripley summaries as sensitivity diagnostics. Construction dates and ratings cover the NIAH dataset, not all of Ireland. Generated ${esc(SUMMARY.generated_at||'unknown')}.</p><p>Sources: OpenStreetMap contributors (ODbL), National Inventory of Architectural Heritage (CC BY 4.0), Esri World Imagery for visual reference, and live basemap tiles from Esri/OSM.</p>`; renderInterpretation(); renderBars();renderQuality();renderStats();applyFilters();initMap();startRuntimeRefresh(); }
async function reportLaunch() { try { await loadMapAssets(); init(); } catch(error) { setMapLoading(false); showReportError(error,'Dashboard initialization unavailable'); } }
document.getElementById('reportRetry')?.addEventListener('click',retryReportRequest);
initFieldAtlas();
renderFieldAtlas();
initSiteIntro();
reportLaunch();
</script>
</body></html>"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=None, help="output directory; defaults to project output/"
    )
    args = parser.parse_args(argv)
    out = project_output_tree_path(args.out_dir, "output")
    out.mkdir(parents=True, exist_ok=True)
    data = build_report_data(out)
    html_text = render_report_data(data)
    report_path = out / "report.html"
    atomic_write_text(report_path, html_text)
    atomic_write_json(out / "report_data.json", data, indent=2)
    atomic_write_json(out / "interpretation.json", interpretation_artifact(data), indent=2)
    atomic_write_text(out / "report_lazy.html", build_lazy_report(out))
    print(f"[report] wrote {report_path} ({len(html_text):,} bytes)")


if __name__ == "__main__":
    main()
