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
async function loadPack() {
  const params = new URLSearchParams(location.search);
  const offline = params.get('offline') === '1';
  const url = offline
    ? 'report_data.json'
    : `/api/report/page?${new URLSearchParams({initial:'1',limit:'50',offset:'0'})}`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(offline
      ? `Could not load report_data.json (${response.status})`
      : `Could not load the paginated report API (${response.status}); serve this file with ireland-geometry-serve or use ?offline=1`);
  }
  boot(await response.json());
}
function boot(PACK) {
""" + body + "\nreportLaunch();\n}\nloadPack().catch(error => { document.body.innerHTML = `<pre style=\"padding:20px\">${error}</pre>`; });\n</script>"
    return template[: start - len(marker)] + script + template[end + len("\n</script>") :]


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Cruth — Ireland Civic Geometry Atlas</title>
<style>
:root { color-scheme: light; --ink:#183233; --muted:#66736f; --line:#ded8ca;
        --blue:#356c69; --red:#bf5b45; --green:#4c765f; --gold:#d5a84b;
        --deep:#103537; --deep-2:#1f514f; --paper:#f7f3ea; --panel:rgba(247,243,234,.97); }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; color:var(--ink); background:var(--deep); font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
#map { position:fixed; inset:0; background:linear-gradient(135deg,#173e40 0%,#0d2d31 47%,#1a4745 100%); }
#map::after { content:""; position:absolute; inset:0; pointer-events:none; opacity:.22; background-image:linear-gradient(rgba(232,218,184,.16) 1px,transparent 1px),linear-gradient(90deg,rgba(232,218,184,.16) 1px,transparent 1px); background-size:64px 64px; mask-image:linear-gradient(90deg,rgba(0,0,0,.95),transparent 68%); }
#panel { position:fixed; z-index:1000; top:18px; right:18px; bottom:18px; width:min(780px,calc(100vw - 36px));
         display:flex; flex-direction:column; overflow-y:auto; overflow-x:hidden; border:1px solid rgba(228,218,193,.78); border-radius:26px;
         background:var(--panel); box-shadow:0 22px 80px rgba(4,20,23,.38); }
#panel > header { flex:0 0 auto; padding:24px 26px 22px; border-bottom:1px solid rgba(231,219,193,.2); color:#f7f2e6; background:linear-gradient(135deg,var(--deep) 0%,#154241 54%,#2b5c53 100%); position:relative; overflow:hidden; }
#panel > header::after { content:"᚛ ᚜"; position:absolute; right:22px; bottom:-26px; color:rgba(231,201,135,.16); font:130px/1 Georgia,serif; letter-spacing:-.18em; transform:rotate(-10deg); }
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
.route-actions { display:flex; align-items:center; gap:7px; grid-column:1 / -1; }
.route-actions button { min-width:80px; }
.route-status { margin-top:7px; }
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
.clear-button { color:#526071; font-size:11px; }
.selection-card { margin:0 18px 10px; padding:13px 14px; border:1px solid #cfc09c; border-radius:12px; background:linear-gradient(135deg,#f7f0df 0%,#edf3eb 100%); box-shadow:0 7px 18px rgba(31,63,59,.07); }
.selection-card[hidden] { display:none; }
.selection-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.selection-kicker { display:block; color:#897c67; font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.selection-card h2 { margin:4px 0 0; color:var(--deep); font:700 21px/1.05 Georgia,serif; letter-spacing:-.04em; }
.selection-card p { margin:5px 0 0; color:#617069; font-size:10px; }
.selection-close { flex:0 0 auto; min-height:27px; padding:4px 8px; color:#65726a; background:rgba(255,253,248,.72); font-size:10px; }
.selection-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:11px; }
.selection-fact { min-width:0; padding:8px; border:1px solid rgba(207,192,156,.75); border-radius:8px; background:rgba(255,253,248,.7); }
.selection-fact span { display:block; color:#897c67; font-size:9px; font-weight:750; letter-spacing:.08em; text-transform:uppercase; }
.selection-fact strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:var(--deep); font-size:11px; line-height:1.3; }
.selection-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-top:10px; }
.selection-actions a, .selection-actions button { min-height:27px; padding:4px 8px; border:1px solid #c9b995; border-radius:7px; color:#315c57; background:rgba(255,253,248,.78); font-size:10px; font-weight:750; text-decoration:none; }
.selection-actions a:hover, .selection-actions button:hover { border-color:var(--deep-2); color:#f7f2e6; background:var(--deep); }
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
.offline-route { fill:none; stroke:#1d4ed8; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; opacity:.9; pointer-events:none; }
.offline-point { stroke:#17324d; stroke-width:1; cursor:pointer; opacity:.82; }
.offline-point:hover, .offline-point.selected { stroke:#111827; stroke-width:2.5; opacity:1; }
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
.culture-mosaic { display:grid; grid-template-columns:1.15fr .85fr; gap:1px; margin-top:10px; border:1px solid #d6cdbd; background:#d6cdbd; }
.culture-mosaic > div { min-height:118px; padding:15px; background:rgba(255,252,244,.78); }
.culture-mosaic h3 { margin:8px 0 0; color:var(--deep); font:700 19px/1.08 Georgia,serif; letter-spacing:-.035em; }
.culture-mosaic h3 b { color:var(--red); }
.culture-mosaic p { margin:8px 0 0; color:#69766e; font-size:10px; line-height:1.45; }
.culture-mosaic-label { color:#897c67; font-size:9px; font-weight:800; letter-spacing:.11em; text-transform:uppercase; }
.culture-word-links { display:flex; flex-wrap:wrap; gap:6px; margin-top:15px; }
.culture-word-links a { display:flex; flex-direction:column; min-width:74px; padding:7px 8px; border:1px solid #d8cdb8; border-radius:7px; color:var(--deep); background:#f7f0e3; font:700 12px/1 Georgia,serif; text-decoration:none; }
.culture-word-links a:hover { border-color:var(--blue); color:var(--blue); }
.culture-word-links small { margin-top:4px; color:#8a7c68; font:600 9px/1.1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
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
  .equation-grid, .typology-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .culture-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .lab-toolbar { align-items:stretch; }
  .lab-toolbar select { width:100%; }
  .lab-toolbar label { min-width:calc(50% - 10px); }
  .scenario-control-grid, .performance-control-grid, .design-spec, .design-schedule { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .evidence-grid { grid-template-columns:1fr; }
  .lab-grid { grid-template-columns:1fr; }
  .lab-copy { border-top:1px solid #ddd1bc; border-left:0; }
  .selection-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width:720px) {
  #mapHud { top:70px; left:12px; width:min(300px,calc(100vw - 24px)); }
  #panel { top:auto; right:0; bottom:0; left:0; width:100%; max-height:72vh; border-radius:14px 14px 0 0; }
  .atlas-nav { padding:6px 12px; }
  .atlas-nav-status { display:none; }
  .kpis { grid-template-columns:repeat(3,1fr); }
  .kpi b { font-size:15px; }
  .filters { grid-template-columns:1fr 1fr; }
  .filters input[type=text] { grid-column:1 / -1; }
  .route-grid { grid-template-columns:1fr 1fr; }
  .pattern-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .brief-head { flex-direction:column; }
  .brief-actions { justify-content:flex-start; }
  .culture-section { padding:18px; }
  .culture-head { display:block; }
  .culture-mark { display:none; }
  .culture-grid { grid-template-columns:1fr; }
  .culture-card { min-height:0; }
  .culture-mosaic { grid-template-columns:1fr; }
  .toolbar { align-items:flex-start; flex-direction:column; }
  .toolbar-actions { width:100%; justify-content:flex-start; }
  .selection-card { margin:0 12px 10px; }
}
</style>
</head>
<body>
<div id="map" aria-label="Map of analysed Irish buildings"></div>
<div id="mapLoading" role="status" aria-live="polite"><span class="map-loading-dot" aria-hidden="true"></span><span id="mapLoadingText">Loading live basemap…</span></div>
<div id="mapLabel" aria-hidden="true"><span class="map-label-kicker">Live satellite / measured Ireland</span><strong>Shape makes place.</strong><small>Explore the map as a field of building footprints, then translate the patterns into civic rooms, thresholds and shared space.</small></div>
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
    <div class="hero-topline"><span>IRELAND / 01—ATLAS</span><span class="hero-tag">Civic design lab</span></div>
    <h1><em>Cruth</em>: the shape<br/>of public life</h1>
    <div class="subtitle">A data-backed Irish building survey expanded into a contemporary architecture studio: equations become bays, courtyards, paths, canopies and places to gather.</div>
    <p class="hero-note">The scan finds geometric signals. The studio tests how those signals might responsibly inform new civic architecture; it does not claim historic intent.</p>
    <div class="header-actions"><a href="#studio">Enter the design studio</a><a href="#culture">Read the cultural lens</a><a href="#patterns">Browse measured patterns</a><a href="review.html" target="_blank" rel="noopener">Open expert review queue</a></div>
  </header>
  <nav id="atlasNav" class="atlas-nav" aria-label="Atlas sections">
    <div class="atlas-nav-links"><a href="#studio" data-nav-section="studio" data-nav-label="Design studio" aria-current="page">Studio</a><a href="#culture" data-nav-section="culture" data-nav-label="Cultural lens">Culture</a><a href="#filters" data-nav-section="filters" data-nav-label="Explore targets">Explore</a><a href="#evidence" data-nav-section="evidence" data-nav-label="Evidence and findings">Evidence</a></div>
    <span id="atlasNavStatus" class="atlas-nav-status" aria-live="polite">Design studio</span>
  </nav>
  <section id="studio" class="studio-section atlas-section">
    <div class="studio-head">
      <div class="studio-kicker">Irish civic geometry / design hypothesis</div>
      <h2>Four equations. <em>Four ways</em> to make a public room.</h2>
      <p>Irish places are interesting when landscape, weather, craft and social ritual meet. This design grammar treats mathematics as a legible tool for making space—not as a shortcut to explain culture. Select an equation, then test it against a civic typology.</p>
      <span class="studio-disclaimer">Contemporary translation inspired by Irish/Celtic visual language · not a historical reconstruction</span>
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
    <div class="culture-mosaic">
      <div>
        <span class="culture-mosaic-label">Contae / county mosaic</span>
        <h3><b id="cultureCountyCount">—</b> county contexts in the heritage-linked rows.</h3>
        <p id="cultureTopCounties">County context will appear when the report pack loads.</p>
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
  <div class="toolbar"><div class="toolbar-left"><small id="count">Loading…</small><span id="activeCulture" class="active-filter" aria-live="polite"></span><span id="activePattern" class="active-filter" aria-live="polite"></span><span id="runtimeStatus" class="runtime-status" role="status" aria-live="polite"></span></div><div class="toolbar-actions"><button id="clearFilters" class="clear-button" type="button">Reset filters</button><div class="actions"><button id="downloadCsv" type="button">CSV</button><button id="downloadGeo" type="button">GeoJSON</button></div></div></div>
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
    <div class="selection-actions"><a id="selectionOsm" href="#" target="_blank" rel="noopener">Open source geometry →</a><button id="selectionCulture" type="button" data-selection-culture="" hidden>Explore this cultural lens →</button></div>
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
      <div class="route-actions"><button id="routeRun" type="button">Route</button><label class="route-check"><input id="routeIncludePath" type="checkbox" checked/> include path</label><label class="route-check"><input id="routeIncludeFerries" type="checkbox"/> include static ferries</label><label class="route-check"><input id="routeAllowHgvDestination" type="checkbox"/> allow HGV destination access</label></div>
    </div>
    <div id="routeStatus" class="footnote route-status" role="status" aria-live="polite">Serve this dashboard with ireland-geometry-serve to enable routing.</div>
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
let INTERPRETATION = {...BASE_INTERPRETATION};
let REPORT_RUNTIME = PACK.runtime || null;
const SIG = PACK.significance || [];
const NEGATIVE = PACK.negative_controls || [];
const NIAH_SIG = PACK.niah_significance || [];
const DECADES = PACK.decades || [];
const MATCHED = PACK.matched_significance || [];
const HIER = PACK.hierarchical_model || [];
const MORAN = PACK.moran || [];
const COUNTY_PERM = PACK.county_permutation || [];
const BOOT = PACK.spatial_bootstrap || [];
const QUALITY_DUPLICATES = PACK.data_quality_duplicates || [];
const QUALITY_AUDIT = PACK.data_quality || [];
const GEOJSON = PACK.geojson || {type:'FeatureCollection',features:[]};
const PATTERN_CATALOG = PACK.pattern_catalog || [];
const PATTERN_BY_KEY = new Map(PATTERN_CATALOG.map(item=>[item.key,item]));
const PAGE_SIZE = 50;
let filtered = DATA.slice();
let page = 1;
let pageStats = PACK.page || {total: DATA.length, matching_golden_angle: 0, matching_niah: 0};
let serverPageReady = Boolean(PACK.initial);
let serverRequestId = 0;
let runtimePollTimer = null;
let runtimePollInFlight = false;
let runtimeRefreshError = '';
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
let routeGeometry = null;
let routeLine = null;
const markerById = new Map();
const DEFAULT_MAP_CENTER = [53.35,-8.05];
const DEFAULT_MAP_ZOOM = 7;
let selectedMarkerId = null;

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
    accessWidth: scenarioNumber('accessWidth',1.8),
    phases: Math.round(scenarioNumber('futurePhases',2)),
    publicMix: scenarioNumber('publicMix',70),
    levels: Math.round(scenarioNumber('buildingLevels',2)),
    season: $('season')?.value || 'midsummer',
    material: $('material')?.value || 'stone',
    grammar: $('grammar')?.value || 'radial'
  };
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
  const worship=SIG.find(row=>row.signal==='golden_angle'&&row.group==='worship') || {};
  const caveat=(Array.isArray(INTERPRETATION.caveats)&&INTERPRETATION.caveats[0]) || 'Geometry flags are screening evidence, not evidence of design intent.';
  const validation=String(SUMMARY.validation?.status || (SUMMARY.analysis_ready?'pass':'incomplete'));
  return [
    'CRUTH / IRISH CIVIC GEOMETRY ATLAS',
    'CONCEPT DESIGN BRIEF',
    'Generated from the current interactive test-fit · contemporary design hypothesis · not a historical reconstruction',
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
    `Accessible route setting: ${fmt(values.accessWidth,1)} m clear route — ${typology.access}`,
    `Future delivery: ${fmt(values.phases,0)} ${values.phases===1?'phase':'phases'} with ${fmt(values.bays,0)} bays available for adaptation`,
    `Material note: ${material.note}`,
    '',
    '5 / EVIDENCE POSITION',
    `Validation status: ${validation}`,
    `Measured signal: ${fmt(worship.observed_rate,2)}% worship targets vs ${fmt(worship.control_rate,2)}% controls for the golden-angle flag.`,
    'Use the measured pack, NIAH records and OSM geometry to form questions about place; do not infer historic intent from a geometric match.',
    `Caveat: ${caveat}`,
    '',
    '6 / SOURCE TRAIL',
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
  renderDesignBrief();
  renderEvidenceBridge();
  renderDesignDiagram();
}
function initStudio() {
  document.querySelectorAll('.equation-card').forEach(card=>card.addEventListener('click',()=>{ selectedEquation=card.dataset.equation || 'phi'; renderStudio(); }));
  $('typology')?.addEventListener('change',renderStudio);
  $('season')?.addEventListener('change',renderStudio);
  $('material')?.addEventListener('change',renderStudio);
  $('grammar')?.addEventListener('change',renderStudio);
  ['moduleScale','courtyardScale','bayCount','pathAngle','publicDensity','windShelter','rainCapture','accessWidth','futurePhases','publicMix','buildingLevels'].forEach(id=>$(id)?.addEventListener('input',renderStudio));
  $('copyBrief')?.addEventListener('click',copyDesignBrief);
  $('downloadBrief')?.addEventListener('click',downloadDesignBrief);
  renderStudio();
}

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits=1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const pFmt = value => { const p=Number(value); if (!Number.isFinite(p)) return 'n/a'; if (p<1e-4) return '&lt;0.0001'; if (p<.001) return '&lt;0.001'; return p.toFixed(4).replace(/0+$/,'').replace(/\.$/,''); };
const flagsText = row => row.flags.join(', ');
const hasFlag = (row, flag) => row.flags.includes(flag);
const patternLabel = key => PATTERN_BY_KEY.get(key)?.label || String(key||'').replaceAll('_',' ');
const patternNamesText = row => row.flags.map(patternLabel).join(', ');
const CULTURE_LENS_LABELS = {named:'Ainm / named places',heritage:'Oidhreacht / heritage joins',pobal:'Pobal / shared life',civic:'Civic / public institutions'};
const ATLAS_NAV_LABELS = {studio:'Design studio',culture:'Cultural lens',filters:'Explore targets',evidence:'Evidence and findings'};
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
fillPatternSelect();

const VIEW_SELECTS = [['group','group'],['century','century'],['rating','rating'],['niahType','type'],['pattern','pattern'],['reviewState','review'],['cultureLens','culture']];
const VIEW_CHECKS = [['onlyAngle','angle'],['onlyRatio','ratio'],['onlyCircular','circular'],['onlyMulti','multi']];
const SORT_KEYS = new Set(['name','group','area_m2','score','flags']);
function restoreViewState() {
  const params=new URLSearchParams(location.search);
  if(params.has('q')) $('query').value=params.get('q');
  for(const [id,key] of VIEW_SELECTS) { const value=params.get(key); if(value!==null && [...$(id).options].some(option=>option.value===value)) $(id).value=value; }
  if(params.has('score')) { const value=Number(params.get('score')); if(Number.isFinite(value)) $('score').value=String(Math.max(0,Math.min(100,Math.round(value)))); }
  for(const [id,key] of VIEW_CHECKS) $(id).checked=params.get(key)==='1';
  const requestedSort=params.get('sort'); if(requestedSort && SORT_KEYS.has(requestedSort)) sortKey=requestedSort;
  sortDesc=params.get('desc') !== '0';
  $('scoreValue').textContent=$('score').value;
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
  const query=params.toString(); history.replaceState(null,'',`${location.pathname}${query?`?${query}`:''}${location.hash}`);
}

function color(score) { return score >= 60 ? '#b42318' : score >= 35 ? '#d97706' : score >= 15 ? '#2563eb' : '#3f8f65'; }
function matches(row) {
  const q=$('query').value.trim().toLowerCase();
  const hay=[row.name,row.osm_id,row.group,row.subtype,row.address_city,flagsText(row),patternNamesText(row),row.niah.name,row.niah.county,row.niah.type,row.history.status,row.history.architect].join(' ').toLowerCase();
  return (!q || hay.includes(q)) && (!$('group').value || row.group===$('group').value) &&
    (!$('century').value || row.niah.century===$('century').value) && (!$('rating').value || row.niah.rating===$('rating').value) &&
    (!$('niahType').value || row.niah.type===$('niahType').value) && (!$('pattern').value || hasFlag(row,$('pattern').value)) && (!$('reviewState').value || reviewFilterState(row)===$('reviewState').value) &&
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
  add('rating',$('rating').value); add('type',$('niahType').value); add('review',$('reviewState').value); add('culture',$('cultureLens').value);
  if(Number($('score').value)>0) add('score',Number($('score').value));
  for(const [id,key] of VIEW_CHECKS) if($(id).checked) params.set(key,'1');
  add('sort',sortKey==='score'?'':sortKey); if(!sortDesc) params.set('desc','0');
  return params;
}
function hasActiveViewState() { const params=currentFilterParameters(); return !sortDesc || [...params.keys()].some(key=>key!=='desc'); }
function sortBy(key) { sortDesc=sortKey===key?!sortDesc:key==='score'; sortKey=key; applyFilters(); }
function applyRuntime(runtime) {
  if(!runtime || typeof runtime!=='object') return false;
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
function renderRuntimeStatus() {
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
async function fetchServerPage() {
  const requestId=++serverRequestId;
  const params=currentFilterParameters(); params.set('limit',String(PAGE_SIZE)); params.set('offset',String((page-1)*PAGE_SIZE));
  try {
    const endpoint=PACK.endpoints?.page || '/api/report/page';
    const response=await fetch(`${endpoint}?${params}`);
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Report page request failed (${response.status})`);
    if(requestId!==serverRequestId) return;
    const runtimeChanged=applyRuntime(payload.runtime);
    DATA=payload.targets || []; filtered=DATA.slice(); pageStats=payload.page || {total:0}; renderAll();
    if(runtimeChanged) { renderInterpretation(); renderStudio(); }
  } catch(error) {
    if(requestId!==serverRequestId) return;
    $('count').textContent=`Report API unavailable: ${error.message}`;
    $('tbody').innerHTML=''; $('empty').hidden=false;
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
function renderAll() { renderCultureAtlas(); renderSummary(); renderMethod(); renderPatternCatalog(); renderTable(); renderMap(); renderRuntimeStatus(); }

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
  const top=Object.entries(culture.top_counties || {}).map(([name,value])=>name+' '+count(value));
  set('cultureTopCounties',top.length ? 'Most represented heritage-linked contexts in this pack: '+top.join(' · ')+'.' : 'County context is not available in this report pack.');
  document.querySelectorAll('.culture-focus').forEach(button=>{
    const active=button.dataset.cultureFocus===$('cultureLens')?.value;
    button.setAttribute('aria-pressed',String(active));
  });
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
  applyFilters();
  $('filters')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function quickViewBaseIsNeutral() {
  return !$('query').value.trim() && !$('group').value && !$('century').value && !$('rating').value && !$('niahType').value && !$('pattern').value && !$('reviewState').value && !$('onlyRatio').checked && !$('onlyCircular').checked && !$('onlyMulti').checked;
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
  $('query').value='';
  for(const [id] of VIEW_SELECTS) $(id).value='';
  for(const [id] of VIEW_CHECKS) $(id).checked=false;
  $('score').value='0'; $('scoreValue').textContent='0'; sortKey='score'; sortDesc=true; applyFilters();
}
document.addEventListener('click',event=>{
  const quick=event.target.closest?.('button.quick-view');
  if(quick) { applyQuickView(quick.dataset.quickView); return; }
  const card=event.target.closest?.('button.pattern-card');
  if(card) { setPatternFilter(card.dataset.pattern); return; }
  const culture=event.target.closest?.('button.culture-focus');
  if(culture) { setCultureFilter(culture.dataset.cultureFocus); return; }
  const selectionCulture=event.target.closest?.('button[data-selection-culture]');
  if(selectionCulture?.dataset.selectionCulture) { setCultureFilter(selectionCulture.dataset.selectionCulture); return; }
  if(event.target.closest?.('#clearSelection')) { clearSelection(); return; }
  if(event.target.closest?.('#clearPattern')) { clearPatternFilter(); return; }
  if(event.target.closest?.('#clearFilters')) { clearAllFilters(); }
});
document.addEventListener('change',event=>{ if(event.target?.id==='pattern'||event.target?.id==='cultureLens') queueFilters(); });
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
function renderSelectionCard(id) {
  const card=$('selectionCard'), row=DATA.find(item=>item.osm_id===id)||filtered.find(item=>item.osm_id===id);
  if(!card||!row) return;
  const set=(element,value)=>{ if(element) element.textContent=value; };
  set($('selectionTitle'),row.name||'Unnamed target');
  set($('selectionSubtitle'),`${row.osm_id} · ${row.group||'other'} · score ${fmt(row.score)} · ${fmt(row.area_m2,0)} m²`);
  set($('selectionPlace'),selectionPlaceText(row));
  set($('selectionHeritage'),selectionHeritageText(row));
  set($('selectionGeometry'),selectionGeometryText(row));
  set($('selectionReview'),reviewState(row));
  const source=$('selectionOsm');
  if(source) source.href=row.osm_url||`https://www.openstreetmap.org/${encodeURIComponent(row.osm_id)}`;
  const lens=culturalLensForRow(row), lensButton=$('selectionCulture');
  if(lensButton) {
    lensButton.hidden=!lens;
    lensButton.dataset.selectionCulture=lens;
    lensButton.textContent=lens?`Explore ${CULTURE_LENS_LABELS[lens]} →`:'Explore this cultural lens →';
  }
  card.hidden=false;
}
function hideSelectionCard() { const card=$('selectionCard'); if(card) card.hidden=true; }
function clearSelection() {
  if(selectedMarkerId) setMarkerSelected(markerById.get(selectedMarkerId),false);
  selectedMarkerId=null; offlineSelection=null;
  if(map?.closePopup) map.closePopup();
  hideSelectionCard(); renderTable();
  if(offlineMap) renderOfflineMap();
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
  $('tbody').innerHTML=visible.map(row=>`<tr data-id="${esc(row.osm_id)}" class="${selectedMarkerId===row.osm_id?'selected':''}" tabindex="0" aria-selected="${selectedMarkerId===row.osm_id}" aria-label="Focus ${esc(row.name||'Unnamed')} ${esc(row.osm_id)}"><td><b>${esc(row.name||'Unnamed')}</b><br><span class="footnote">${esc(row.osm_id)}${row.niah.name?' · '+esc(row.niah.name):''}</span></td><td>${esc(row.group)}${row.niah.century?`<br><span class="footnote">${esc(row.niah.century)}</span>`:''}</td><td>${fmt(row.area_m2,0)} m²</td><td class="score">${fmt(row.score)}</td><td>${flagHtml(row)}</td><td class="review-state ${esc(reviewFilterState(row))}">${reviewCell(row)}</td></tr>`).join('');
  $('empty').hidden=visible.length>0;
  const total=SERVER_MODE ? Number(pageStats.total||0) : filtered.length;
  const pages=Math.max(1,Math.ceil(total/PAGE_SIZE)); $('page').textContent=`${Math.min(page,pages)} / ${pages}`; $('prev').disabled=page<=1; $('next').disabled=page>=pages;
  document.querySelectorAll('#tbody tr[data-id]').forEach(tr=>{ tr.addEventListener('click',()=>focusRow(tr.dataset.id)); tr.addEventListener('keydown',event=>{ if((event.key==='Enter'||event.key===' ')&&!event.target.closest('a,button,input,select,textarea')){ event.preventDefault(); focusRow(tr.dataset.id); } }); });
  document.querySelectorAll('#tbody a.review-link').forEach(link=>link.addEventListener('click',event=>event.stopPropagation()));
}
function popup(row) { const reviewLabel=row.review?.in_queue?'Review queue':'Not in review queue'; return `<b>${esc(row.name||'Unnamed')}</b><br>${esc(row.group)} · ${fmt(row.area_m2,0)} m²<br>Score <b>${fmt(row.score)}</b> · aspect ${fmt(row.aspect_ratio,3)}<br>Shape: rectangularity ${fmt(row.rectangularity,3)} · radial CV ${fmt(row.radial_cv,3)}<br>Convexity ${fmt(row.convexity,3)} · ${row.n_vertices} vertices${row.multipart?' · multipart':''}${row.repaired?' · repaired':''}<br>${flagHtml(row)}${row.parts.count?`<br>Mapped parts: ${row.parts.count} · coverage ${fmt(row.parts.coverage_pct,1)}%`:''}${row.height_m?`<br>OSM height: ${fmt(row.height_m,1)} m`:''}${row.niah.name?`<br><span>${esc(row.niah.name)} · ${esc(row.niah.rating)} · ${esc(row.niah.century)}</span>`:''}${row.history.status?`<br>Historical status: ${esc(row.history.status)}${row.history.architect?' · '+esc(row.history.architect):''}`:''}<br><a href="${row.osm_url}" target="_blank" rel="noopener">OpenStreetMap</a> · ${reviewLink(row,reviewLabel)}<br><button class="popup-focus" type="button" data-focus-id="${esc(row.osm_id)}">Focus in list</button>`; }
function offlineBounds() {
  let minLat=Infinity,maxLat=-Infinity,minLon=Infinity,maxLon=-Infinity;
  const points=routeGeometry&&routeGeometry.length>1?routeGeometry.map(([lon,lat])=>({lat,lon})):DATA;
  points.forEach(row=>{ const lat=Number(row.lat), lon=Number(row.lon); if(Number.isFinite(lat)&&Number.isFinite(lon)){ minLat=Math.min(minLat,lat); maxLat=Math.max(maxLat,lat); minLon=Math.min(minLon,lon); maxLon=Math.max(maxLon,lon); } });
  if(!Number.isFinite(minLat)) return null;
  const routeFocused=Boolean(routeGeometry&&routeGeometry.length>1);
  const latSpan=Math.max(maxLat-minLat,routeFocused ? 0.01 : 0.1), lonSpan=Math.max(maxLon-minLon,routeFocused ? 0.01 : 0.1);
  const latPad=latSpan*.08, lonPad=lonSpan*.08;
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
function selectMapTarget(id,{scroll=false}={}) {
  if(!id) return;
  if(selectedMarkerId && selectedMarkerId!==id) setMarkerSelected(markerById.get(selectedMarkerId),false);
  selectedMarkerId=id;
  setMarkerSelected(markerById.get(id),true);
  renderSelectionCard(id);
  document.querySelectorAll('#tbody tr[data-id]').forEach(row=>{
    const active=row.dataset.id===id;
    row.classList.toggle('selected',active);
    row.setAttribute('aria-selected',String(active));
    if(active && scroll) row.scrollIntoView({block:'nearest'});
  });
}
function fitMapToResults() {
  if(offlineMap) { renderOfflineMap(); return; }
  if(!map || !markerLayer) return;
  const bounds=typeof markerLayer.getBounds==='function'?markerLayer.getBounds():null;
  if(bounds && bounds.isValid && bounds.isValid()) {
    if(routeLine) bounds.extend(routeLine.getBounds());
    map.fitBounds(bounds,{padding:[36,36],maxZoom:17});
  } else {
    resetMapView();
  }
}
function resetMapView() {
  if(map) map.setView(DEFAULT_MAP_CENTER,DEFAULT_MAP_ZOOM);
  if(offlineMap) { offlineSelection=null; renderOfflineMap(); }
}
function renderOfflineMap() {
  setMapLoading(false);
  const el=$('map'), bounds=offlineBounds();
  if(!bounds){ el.className='offline-map'; el.innerHTML='<div class="offline-map-note">Offline map fallback · no coordinates available.</div>'; return; }
  const grid=Array.from({length:6},(_,i)=>{ const x=50+i*180, y=50+i*120; return `<line class="offline-grid" x1="${x}" y1="50" x2="${x}" y2="650"/><line class="offline-grid" x1="50" y1="${y}" x2="950" y2="${y}"/>`; }).join('');
  const outlines=OUTLINES.slice(0,200).map(item=>item.rings.map(ring=>`<polyline class="offline-outline" points="${ring.map(([lat,lon])=>{const p=offlinePoint({lat,lon},bounds);return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;}).join(' ')}"/>`).join('')).join('');
  const route=routeGeometry&&routeGeometry.length>1?`<polyline class="offline-route" points="${routeGeometry.map(([lon,lat])=>{const p=offlinePoint({lat,lon},bounds);return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;}).join(' ')}"/>`:'';
  const points=filtered.slice(0,__MARKER_LIMIT__).map(row=>{ const p=offlinePoint(row,bounds), selected=offlineSelection===row.osm_id; return `<circle class="offline-point${selected?' selected':''}" data-id="${esc(row.osm_id)}" cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="${selected?6:4}" fill="${color(row.score)}"><title>${esc(row.name||row.osm_id)} · ${esc(row.group)} · score ${fmt(row.score)}</title></circle>`; }).join('');
  const selected=offlineSelection&&DATA.find(row=>row.osm_id===offlineSelection);
  const selection=selected?`<div class="offline-selection"><b>${esc(selected.name||'Unnamed')}</b> · ${esc(selected.group)} · score ${fmt(selected.score)}<br><span class="footnote">${esc(selected.osm_id)} · click a point to inspect another target</span></div>`:'';
  const note=routeGeometry&&routeGeometry.length>1?`Offline route view · ${routeGeometry.length.toLocaleString()} path points.`:`Offline map fallback · ${filtered.length.toLocaleString()} matching targets; basemap unavailable.`;
  el.className='offline-map'; el.innerHTML=`<svg class="offline-map-svg" viewBox="0 0 1000 700" role="img" aria-label="Offline map fallback">${grid}${outlines}${route}${points}</svg><div class="offline-map-note">${note}</div>${selection}`;
  el.querySelectorAll('.offline-point').forEach(point=>point.addEventListener('click',()=>focusRow(point.dataset.id,{scroll:false,openPopup:false})));
  updateMapHud();
}
function renderMap() {
  if(selectedMarkerId && !filtered.some(row=>row.osm_id===selectedMarkerId)) { selectedMarkerId=null; hideSelectionCard(); }
  if(offlineMap){ renderOfflineMap(); return; }
  if (!map || !markerLayer) return;
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
  if(routeGeometry&&routeGeometry.length>1){ routeLine=L.polyline(routeGeometry.map(([lon,lat])=>[lat,lon]),{color:'#1d4ed8',weight:5,opacity:.9,lineCap:'round',lineJoin:'round'}).addTo(map); routeLine.bringToFront(); }
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
function updateMapHud() {
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
  const status=$('atlasNavStatus');
  if(status) status.textContent=ATLAS_NAV_LABELS[key]||'Atlas';
}
function initAtlasNav() {
  const panel=$('panel'), links=[...document.querySelectorAll('[data-nav-section]')];
  if(!panel||!links.length) return;
  setAtlasNavActive('studio');
  links.forEach(link=>link.addEventListener('click',()=>setAtlasNavActive(link.dataset.navSection)));
  const sections=links.map(link=>$(link.dataset.navSection)).filter(Boolean);
  if(typeof IntersectionObserver==='undefined') return;
  const observer=new IntersectionObserver(entries=>{
    const visible=entries.filter(entry=>entry.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];
    if(visible) setAtlasNavActive(visible.target.id);
  },{root:panel,rootMargin:'-54px 0px -58% 0px',threshold:[0.01,0.2,0.5]});
  sections.forEach(section=>observer.observe(section));
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
function focusRow(id,{scroll=true,openPopup=true}={}) {
  const row=DATA.find(item=>item.osm_id===id);
  if(!row) return;
  selectMapTarget(id,{scroll});
  if(map){
    map.setView([row.lat,row.lon],17);
    const marker=markerById.get(id);
    if(marker && openPopup){
      let opened=false;
      const open=()=>{ if(opened) return; opened=true; marker.openPopup(); };
      if(markerLayer?.zoomToShowLayer) markerLayer.zoomToShowLayer(marker,open); else open();
      setTimeout(open,250);
    }
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
async function runRoute() {
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
  const missing=['start_lat','start_lon','goal_lat','goal_lon'].filter(key=>!fields[key]);
  if(missing.length) { routeGeometry=null; renderMap(); $('routeStatus').textContent=`Missing: ${missing.join(', ')}`; return; }
  const button=$('routeRun'); button.disabled=true; routeGeometry=null; renderMap(); $('routeStatus').textContent='Routing…'; $('routeResult').hidden=true;
  try {
    const response=await fetch(`/api/route?${new URLSearchParams(fields)}`);
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Route request failed (${response.status})`);
    routeGeometry=routeCoordinates(payload); renderMap();
    if(map&&routeLine) map.fitBounds(routeLine.getBounds(),{padding:[24,24],maxZoom:16});
    $('routeStatus').textContent=routeStatusText(payload)+(routeGeometry?' · line shown on map':'');
    $('routeResult').textContent=JSON.stringify(payload,null,2); $('routeResult').hidden=false;
  } catch(error) {
    $('routeStatus').textContent=`Route unavailable: ${error.message}`;
  } finally { button.disabled=false; }
}
function initRoute() {
  $('routeRun').addEventListener('click',runRoute);
  $('routeStatus').textContent=location.protocol==='file:'
    ? 'Serve this report with ireland-geometry-serve; file mode has no route API.'
    : 'Enter coordinates and run a route against the local graph.';
}
function download(name, content, type) { const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([content],{type})); a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),500); }
async function downloadFiltered(format) {
  const params=currentFilterParameters(); params.set('format',format);
  const endpoint=PACK.endpoints?.export || '/api/report/export';
  const response=await fetch(`${endpoint}?${params}`); const body=await response.text();
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
  if(OFFLINE_REQUESTED || !mapAssetsAvailable || typeof L==='undefined') { offlineMap=true; renderOfflineMap(); return; }
  map=L.map('map',{preferCanvas:true}).setView(DEFAULT_MAP_CENTER,DEFAULT_MAP_ZOOM);
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
  activeMapLayer='satellite'; syncMapLayerButtons(); renderMap();
}
function init() { $('score').addEventListener('input',()=>{$('scoreValue').textContent=$('score').value; $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); queueFilters();}); ['query','group','century','rating','niahType','reviewState','onlyAngle','onlyRatio','onlyCircular','onlyMulti'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',queueFilters)); $('prev').addEventListener('click',()=>{if(page>1){page--; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); $('next').addEventListener('click',()=>{const total=SERVER_MODE?Number(pageStats.total||0):filtered.length; if(page<Math.ceil(total/PAGE_SIZE)){page++; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); document.addEventListener('click',event=>{ const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(header) sortBy(header.dataset.sort); }); document.addEventListener('keydown',event=>{ if(event.key!=='Enter'&&event.key!==' ') return; const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(!header) return; event.preventDefault(); sortBy(header.dataset.sort); }); $('downloadCsv').addEventListener('click',downloadCsv); $('downloadGeo').addEventListener('click',downloadGeo); restoreViewState(); initAtlasNav(); $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); initMapHud(); initStudio(); initRoute(); $('method').innerHTML=`<p>Target rows: <b>${Number(SUMMARY.targets||0).toLocaleString()}</b>; controls: <b>${Number(SUMMARY.controls||0).toLocaleString()}</b>; NIAH joins: <b>${Number(SUMMARY.niah_matches||0).toLocaleString()}</b> (${Number(SUMMARY.niah_contained||0).toLocaleString()} contained, ${Number(SUMMARY.niah_near||0).toLocaleString()} near).</p><p>Source readiness: ${sourceStatusText()}.</p><p>Input freshness: <b>${sourceFreshnessText()}</b>.</p><p>Analytical readiness: <b>${SUMMARY.analysis_ready?'pass':'incomplete'}</b>; validation records: <b>${esc(SUMMARY.validation?.status||'not reported')}</b>.</p><p>Shape descriptors include rectangularity, angle entropy, radial Fourier coefficients, and radial variability. ${Number(SUMMARY.part_mapped||0).toLocaleString()} target footprints have mapped OSM building parts; LiDAR coverage is ${Number(SUMMARY.lidar_available||0).toLocaleString()} targets. Historical rows are review evidence, not proof of intent.</p><p>Primary rates use building-level two-proportion z-tests, Wilson confidence intervals, risk differences, continuity-corrected odds ratios, matched controls, hierarchical stratified odds ratios, Moran's I, county permutations, and Ripley summaries as sensitivity diagnostics. Construction dates and ratings cover the NIAH dataset, not all of Ireland. Generated ${esc(SUMMARY.generated_at||'unknown')}.</p><p>Sources: OpenStreetMap contributors (ODbL), National Inventory of Architectural Heritage (CC BY 4.0), Esri World Imagery for visual reference, and live basemap tiles from Esri/OSM.</p>`; renderInterpretation(); renderBars();renderQuality();renderStats();applyFilters();initMap();startRuntimeRefresh(); }
async function reportLaunch() { try { await loadMapAssets(); init(); } catch(error) { setMapLoading(false); document.body.innerHTML=`<pre style="padding:20px">${error}</pre>`; } }
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
