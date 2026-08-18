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
    from geometry import geometry_from_geojson, iter_polygons
    from runtime import (
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        atomic_write_text,
        project_path,
    )
except ImportError:
    from scripts.geometry import geometry_from_geojson, iter_polygons
    from scripts.runtime import (
        SOURCE_FRESHNESS_CONTRACT,
        atomic_write_json,
        atomic_write_text,
        project_path,
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


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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
    }


def report_matching_targets(
    data: dict,
    *,
    query: str = "",
    group: str = "",
    century: str = "",
    rating: str = "",
    niah_type: str = "",
    review_state: str = "",
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
        "endpoints": {"page": "/api/report/page", "export": "/api/report/export"},
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
<title>Ireland Geometric Pattern Scan</title>
<style>
:root { color-scheme: light; --ink:#17202a; --muted:#667085; --line:#e5e7eb;
        --blue:#2563eb; --red:#c2413b; --green:#18805c; --panel:rgba(255,255,255,.97); }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; color:var(--ink); font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
#map { position:fixed; inset:0; background:#dfe8ee; }
#panel { position:fixed; z-index:1000; top:10px; right:10px; bottom:10px; width:min(560px,calc(100vw - 20px));
         display:flex; flex-direction:column; overflow:hidden; border:1px solid #d9dee5; border-radius:14px;
         background:var(--panel); box-shadow:0 8px 32px rgba(15,23,42,.22); }
header { padding:15px 17px 10px; border-bottom:1px solid var(--line); }
h1 { margin:0; font-size:18px; letter-spacing:-.02em; }
.subtitle { margin-top:4px; color:var(--muted); font-size:12px; }
.header-actions { display:flex; gap:7px; margin-top:9px; }
.header-actions a, .review-link { display:inline-block; color:var(--blue); text-decoration:none; font-size:11px; font-weight:600; }
.header-actions a { padding:5px 8px; border:1px solid #cfd5dd; border-radius:7px; background:#fff; }
.header-actions a:hover, .review-link:hover { text-decoration:underline; }
.review-state { color:#526071; font-size:10px; white-space:nowrap; }
.review-state.not_queued { color:var(--muted); font-style:italic; }
.review-state.supportive { color:var(--green); font-weight:700; }
.review-state.ambiguous { color:#a05a00; font-weight:700; }
.review-state.not_supportive { color:var(--red); font-weight:700; }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; padding:10px 12px; border-bottom:1px solid var(--line); }
.kpi { min-width:0; padding:8px 9px; border:1px solid var(--line); border-radius:9px; background:#fff; }
.kpi b { display:block; font-size:18px; line-height:1.1; }
.kpi span { color:var(--muted); font-size:10px; }
.filters { display:grid; grid-template-columns:1.6fr 1fr 1fr; gap:7px; padding:10px 12px 8px; border-bottom:1px solid var(--line); }
input,select,button { min-height:31px; border:1px solid #cfd5dd; border-radius:7px; background:#fff; color:var(--ink); padding:5px 8px; font:inherit; }
input[type=range] { padding:0; accent-color:var(--blue); }
button { cursor:pointer; font-weight:600; }
button:hover { border-color:var(--blue); color:var(--blue); }
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
.section { padding:10px 12px; border-bottom:1px solid var(--line); }
.section h2 { margin:0 0 7px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:#465467; }
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
@media (max-width:720px) {
  #panel { top:auto; right:0; bottom:0; left:0; width:100%; max-height:72vh; border-radius:14px 14px 0 0; }
  .kpis { grid-template-columns:repeat(4,1fr); }
  .kpi b { font-size:15px; }
  .filters { grid-template-columns:1fr 1fr; }
  .filters input[type=text] { grid-column:1 / -1; }
  .route-grid { grid-template-columns:1fr 1fr; }
}
</style>
</head>
<body>
<div id="map" aria-label="Map of analysed Irish buildings"></div>
<div id="panel">
  <header>
    <h1>Ireland Geometric Pattern Scan</h1>
    <div class="subtitle">Interactive, significance-tested footprint survey. Scores are search heuristics, not proof of design intent.</div>
    <div class="header-actions"><a href="review.html" target="_blank" rel="noopener">Open expert review queue</a></div>
  </header>
  <div class="kpis" aria-live="polite">
    <div class="kpi"><b id="kTargets">—</b><span>visible targets</span></div>
    <div class="kpi"><b id="kControls">—</b><span>controls</span></div>
    <div class="kpi"><b id="kGolden">—</b><span>golden-angle</span></div>
    <div class="kpi"><b id="kNiah">—</b><span>NIAH matched</span></div>
  </div>
  <div class="filters">
    <input id="query" type="text" placeholder="Search name, OSM id, flags, county…" aria-label="Search analyzed targets"/>
    <select id="group" aria-label="Filter by group"><option value="">All groups</option></select>
    <select id="century" aria-label="Filter by century"><option value="">All centuries</option></select>
    <select id="rating" aria-label="Filter by NIAH rating"><option value="">All ratings</option></select>
    <select id="niahType" aria-label="Filter by NIAH class"><option value="">All NIAH classes</option></select>
    <select id="reviewState" aria-label="Filter by review state"><option value="">All review states</option><option value="not_queued">Not in current queue</option><option value="not_reviewed">Not reviewed</option><option value="supportive">Supportive</option><option value="ambiguous">Ambiguous</option><option value="not_supportive">Not supportive</option></select>
    <label class="filter-wide" for="score"><span>Minimum score <b id="scoreValue">0</b></span><input id="score" type="range" min="0" max="100" value="0" aria-label="Minimum score"/></label>
    <div class="checks">
      <label><input id="onlyAngle" type="checkbox"/> golden angle</label>
      <label><input id="onlyRatio" type="checkbox"/> golden ratio</label>
      <label><input id="onlyCircular" type="checkbox"/> circular</label>
      <label><input id="onlyMulti" type="checkbox"/> multipart/repaired</label>
    </div>
  </div>
  <div class="toolbar"><small id="count">Loading…</small><div class="actions"><button id="downloadCsv" type="button">CSV</button><button id="downloadGeo" type="button">GeoJSON</button></div></div>
  <div class="section"><h2>Local route query</h2>
    <div class="route-grid">
      <label>Start latitude<input id="routeStartLat" inputmode="decimal" placeholder="53.3498"/></label>
      <label>Start longitude<input id="routeStartLon" inputmode="decimal" placeholder="-6.2603"/></label>
      <label>Goal latitude<input id="routeGoalLat" inputmode="decimal" placeholder="53.3438"/></label>
      <label>Goal longitude<input id="routeGoalLon" inputmode="decimal" placeholder="-6.2546"/></label>
      <label>Speed km/h<input id="routeSpeed" inputmode="decimal" value="50"/></label>
      <label>Departure (optional)<input id="routeDeparture" placeholder="2026-08-17T08:00:00+00:00"/></label>
      <label>Response<select id="routeFormat"><option value="json">JSON</option><option value="geojson">GeoJSON</option></select></label>
      <div class="route-actions"><button id="routeRun" type="button">Route</button><label class="route-check"><input id="routeIncludePath" type="checkbox" checked/> include path</label><label class="route-check"><input id="routeIncludeFerries" type="checkbox"/> include static ferries</label></div>
    </div>
    <div id="routeStatus" class="footnote route-status" role="status" aria-live="polite">Serve this dashboard with ireland-geometry-serve to enable routing.</div>
    <pre id="routeResult" class="route-result" aria-live="polite" hidden></pre>
  </div>
  <div class="section"><h2>Data-derived interpretation</h2><div id="interpretation"></div></div>
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
const SUMMARY = PACK.summary || {};
const INTERPRETATION = PACK.interpretation || {};
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
const PAGE_SIZE = 50;
let filtered = DATA.slice();
let page = 1;
let pageStats = PACK.page || {total: DATA.length, matching_golden_angle: 0, matching_niah: 0};
let serverPageReady = Boolean(PACK.initial);
let serverRequestId = 0;
let filterTimer = null;
let sortKey = 'score';
let sortDesc = true;
let map = null;
let markerLayer = null;
let offlineMap = false;
let offlineSelection = null;
let routeGeometry = null;
let routeLine = null;
const markerById = new Map();

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits=1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const pFmt = value => { const p=Number(value); if (!Number.isFinite(p)) return 'n/a'; if (p<1e-4) return '&lt;0.0001'; if (p<.001) return '&lt;0.001'; return p.toFixed(4).replace(/0+$/,'').replace(/\.$/,''); };
const flagsText = row => row.flags.join(', ');
const hasFlag = (row, flag) => row.flags.includes(flag);
const unique = key => [...new Set(DATA.map(row => key(row)).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));

function fillSelect(id, values) { for (const value of values) { const option=document.createElement('option'); option.value=value; option.textContent=value; $(id).appendChild(option); } }
const FILTER_OPTIONS = PACK.filter_options || {};
fillSelect('group', FILTER_OPTIONS.group || unique(row=>row.group));
fillSelect('century', FILTER_OPTIONS.century || unique(row=>row.niah.century));
fillSelect('rating', FILTER_OPTIONS.rating || unique(row=>row.niah.rating));
fillSelect('niahType', FILTER_OPTIONS.type || unique(row=>row.niah.type));

const VIEW_SELECTS = [['group','group'],['century','century'],['rating','rating'],['niahType','type'],['reviewState','review']];
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
  const hay=[row.name,row.osm_id,row.group,row.subtype,row.address_city,flagsText(row),row.niah.name,row.niah.county,row.niah.type,row.history.status,row.history.architect].join(' ').toLowerCase();
  return (!q || hay.includes(q)) && (!$('group').value || row.group===$('group').value) &&
    (!$('century').value || row.niah.century===$('century').value) && (!$('rating').value || row.niah.rating===$('rating').value) &&
    (!$('niahType').value || row.niah.type===$('niahType').value) && (!$('reviewState').value || reviewFilterState(row)===$('reviewState').value) && row.score >= Number($('score').value) &&
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
  add('rating',$('rating').value); add('type',$('niahType').value); add('review',$('reviewState').value);
  if(Number($('score').value)>0) add('score',Number($('score').value));
  for(const [id,key] of VIEW_CHECKS) if($(id).checked) params.set(key,'1');
  add('sort',sortKey==='score'?'':sortKey); if(!sortDesc) params.set('desc','0');
  return params;
}
function hasActiveViewState() { const params=currentFilterParameters(); return !sortDesc || [...params.keys()].some(key=>key!=='desc'); }
function sortBy(key) { sortDesc=sortKey===key?!sortDesc:key==='score'; sortKey=key; applyFilters(); }
async function fetchServerPage() {
  const requestId=++serverRequestId;
  const params=currentFilterParameters(); params.set('limit',String(PAGE_SIZE)); params.set('offset',String((page-1)*PAGE_SIZE));
  try {
    const endpoint=PACK.endpoints?.page || '/api/report/page';
    const response=await fetch(`${endpoint}?${params}`);
    const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || `Report page request failed (${response.status})`);
    if(requestId!==serverRequestId) return;
    DATA=payload.targets || []; filtered=DATA.slice(); pageStats=payload.page || {total:0}; renderAll();
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
function renderAll() { renderSummary(); renderTable(); renderMap(); }

function renderSummary() {
  const total=SERVER_MODE ? Number(pageStats.total||0) : filtered.length;
  const golden=SERVER_MODE ? Number(pageStats.matching_golden_angle||0) : filtered.filter(row=>row.has_golden_angle).length;
  const niah=SERVER_MODE ? Number(pageStats.matching_niah||0) : filtered.filter(row=>row.niah.reg_no).length;
  $('kTargets').textContent=total.toLocaleString();
  $('kControls').textContent=Number(SUMMARY.controls||0).toLocaleString();
  $('kGolden').textContent=golden.toLocaleString();
  $('kNiah').textContent=niah.toLocaleString();
  $('count').textContent=`${total.toLocaleString()} matching targets · showing up to ${PAGE_SIZE} per page`;
  $('reviewCoverage').textContent=`Expert review queue: ${Number(SUMMARY.review_queue_targets||0).toLocaleString()} of ${Number(SUMMARY.targets||0).toLocaleString()} targets (${fmt(SUMMARY.review_queue_coverage_pct,2)}%); unqueued targets are labeled explicitly.`;
}
function interpretationStatusClass(value) { return String(value||'not_reported').toLowerCase().replace(/[^a-z0-9_-]/g,'_'); }
function renderInterpretation() {
  const findings=INTERPRETATION.findings||[], caveats=INTERPRETATION.caveats||[];
  if(!findings.length && !caveats.length) { $('interpretation').innerHTML='<p class="footnote">No data-derived interpretation is available.</p>'; return; }
  const headline=INTERPRETATION.headline?`<p class="interpretation-headline">${esc(INTERPRETATION.headline)}</p>`:'';
  const cards=findings.map(item=>`<article class="interpretation-card"><header><b>${esc(item.title||'Finding')}</b><span class="interpretation-status ${interpretationStatusClass(item.status)}">${esc(item.status||'not reported')}</span></header><p>${esc(item.text||'')}</p></article>`).join('');
  const caveatBlock=caveats.length?`<ul class="interpretation-caveats">${caveats.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:'';
  $('interpretation').innerHTML=`${headline}<div class="interpretation-grid">${cards}</div>${caveatBlock}`;
}
function flagHtml(row) { return row.flags.slice(0,5).map(flag=>`<span class="flag">${esc(flag.replaceAll('_',' '))}</span>`).join('') || '<span class="footnote">none</span>'; }
function reviewHref(row) { return `review.html?osm_id=${encodeURIComponent(row.osm_id)}`; }
function reviewFilterState(row) { return row.review?.in_queue ? String(row.review?.label||'not_reviewed') : 'not_queued'; }
function reviewState(row) { return reviewFilterState(row).replaceAll('_',' '); }
function reviewLink(row,label) { const queued=Boolean(row.review?.in_queue); const title=queued?'Open the current expert review queue':'Target is outside the current top-1,000 expert review queue'; return `<a class="review-link" href="${reviewHref(row)}" title="${esc(title)}" target="_blank" rel="noopener">${esc(label)}</a>`; }
function reviewCell(row) { return reviewLink(row,reviewState(row)); }
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
  $('tbody').innerHTML=visible.map(row=>`<tr data-id="${esc(row.osm_id)}" tabindex="0" aria-label="Focus ${esc(row.name||'Unnamed')} ${esc(row.osm_id)}"><td><b>${esc(row.name||'Unnamed')}</b><br><span class="footnote">${esc(row.osm_id)}${row.niah.name?' · '+esc(row.niah.name):''}</span></td><td>${esc(row.group)}${row.niah.century?`<br><span class="footnote">${esc(row.niah.century)}</span>`:''}</td><td>${fmt(row.area_m2,0)} m²</td><td class="score">${fmt(row.score)}</td><td>${flagHtml(row)}</td><td class="review-state ${esc(reviewFilterState(row))}">${reviewCell(row)}</td></tr>`).join('');
  $('empty').hidden=visible.length>0;
  const total=SERVER_MODE ? Number(pageStats.total||0) : filtered.length;
  const pages=Math.max(1,Math.ceil(total/PAGE_SIZE)); $('page').textContent=`${Math.min(page,pages)} / ${pages}`; $('prev').disabled=page<=1; $('next').disabled=page>=pages;
  document.querySelectorAll('#tbody tr[data-id]').forEach(tr=>{ tr.addEventListener('click',()=>focusRow(tr.dataset.id)); tr.addEventListener('keydown',event=>{ if((event.key==='Enter'||event.key===' ')&&!event.target.closest('a,button,input,select,textarea')){ event.preventDefault(); focusRow(tr.dataset.id); } }); });
  document.querySelectorAll('#tbody a.review-link').forEach(link=>link.addEventListener('click',event=>event.stopPropagation()));
}
function popup(row) { const reviewLabel=row.review?.in_queue?'Review queue':'Not in review queue'; return `<b>${esc(row.name||'Unnamed')}</b><br>${esc(row.group)} · ${fmt(row.area_m2,0)} m²<br>Score <b>${fmt(row.score)}</b> · aspect ${fmt(row.aspect_ratio,3)}<br>Shape: rectangularity ${fmt(row.rectangularity,3)} · radial CV ${fmt(row.radial_cv,3)}<br>Convexity ${fmt(row.convexity,3)} · ${row.n_vertices} vertices${row.multipart?' · multipart':''}${row.repaired?' · repaired':''}<br>${flagHtml(row)}${row.parts.count?`<br>Mapped parts: ${row.parts.count} · coverage ${fmt(row.parts.coverage_pct,1)}%`:''}${row.height_m?`<br>OSM height: ${fmt(row.height_m,1)} m`:''}${row.niah.name?`<br><span>${esc(row.niah.name)} · ${esc(row.niah.rating)} · ${esc(row.niah.century)}</span>`:''}${row.history.status?`<br>Historical status: ${esc(row.history.status)}${row.history.architect?' · '+esc(row.history.architect):''}`:''}<br><a href="${row.osm_url}" target="_blank" rel="noopener">OpenStreetMap</a> · ${reviewLink(row,reviewLabel)}`; }
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
function renderOfflineMap() {
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
  el.querySelectorAll('.offline-point').forEach(point=>point.addEventListener('click',()=>{offlineSelection=point.dataset.id;renderOfflineMap();}));
}
function renderMap() {
  if(offlineMap){ renderOfflineMap(); return; }
  if (!map || !markerLayer) return;
  if(routeLine){ routeLine.remove(); routeLine=null; }
  markerLayer.clearLayers(); markerById.clear();
  filtered.slice(0,__MARKER_LIMIT__).forEach(row=>{ const marker=L.circleMarker([row.lat,row.lon],{radius:5,color:'#17324d',weight:1,fillColor:color(row.score),fillOpacity:.86}); marker.bindPopup(popup(row)); markerLayer.addLayer(marker); markerById.set(row.osm_id,marker); });
  if(routeGeometry&&routeGeometry.length>1){ routeLine=L.polyline(routeGeometry.map(([lon,lat])=>[lat,lon]),{color:'#1d4ed8',weight:5,opacity:.9,lineCap:'round',lineJoin:'round'}).addTo(map); routeLine.bringToFront(); }
}
function focusRow(id) { const row=DATA.find(item=>item.osm_id===id); if(!row) return; if(map){ map.setView([row.lat,row.lon],17); const marker=markerById.get(id); if(marker) marker.openPopup(); } else if(offlineMap){ offlineSelection=id; renderOfflineMap(); } }
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
function routeStatusText(payload) {
  const route=routePayloadDetails(payload);
  if(route.reachable) return `Reachable · ${fmt(route.route_distance_m,0)} m · ${fmt(route.estimated_duration_s,1)} s`;
  return route.error || route.status || 'Route unavailable';
}
async function runRoute() {
  const fields={
    start_lat:$('routeStartLat').value.trim(), start_lon:$('routeStartLon').value.trim(),
    goal_lat:$('routeGoalLat').value.trim(), goal_lon:$('routeGoalLon').value.trim(),
    speed_kmh:$('routeSpeed').value.trim()
  };
  if($('routeDeparture').value.trim()) fields.departure=$('routeDeparture').value.trim();
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
  download(format==='csv'?'ireland-geometry-filtered.csv':'ireland-geometry-filtered.geojson',body,format==='csv'?'text/csv':'application/geo+json');
}
function downloadCsv() { if(SERVER_MODE) { downloadFiltered('csv').catch(error=>{ $('count').textContent=`CSV export unavailable: ${error.message}`; }); return; } const cols=['osm_id','name','group','lat','lon','score','area_m2','aspect_ratio','convexity','circularity','flags','niah_name','niah_rating','niah_century']; const escCsv=v=>`"${String(v??'').replaceAll('"','""')}"`; const lines=[cols.join(',')]; filtered.forEach(row=>lines.push(cols.map(key=>{ if(key==='flags')return escCsv(flagsText(row)); if(key.startsWith('niah_'))return escCsv(row.niah[key.slice(5)]); return escCsv(row[key]); }).join(','))); download('ireland-geometry-filtered.csv',lines.join('\n'),'text/csv'); }
function downloadQualityAudit() { const cols=['scope','group','field','row_n','missing_n','missing_pct','unique_n','invalid_n','quality_status','notes']; const escCsv=v=>`"${String(v??'').replaceAll('"','""')}"`; const lines=[cols.join(','),...QUALITY_AUDIT.map(row=>cols.map(key=>escCsv(row[key])).join(','))]; download('ireland-data-quality-audit.csv',lines.join('\n'),'text/csv'); }
function downloadGeo() { if(SERVER_MODE) { downloadFiltered('geojson').catch(error=>{ $('count').textContent=`GeoJSON export unavailable: ${error.message}`; }); return; } const ids=new Set(filtered.map(row=>row.osm_id)); const copy={...GEOJSON,features:(GEOJSON.features||[]).filter(feature=>ids.has(feature.properties?.osm_id))}; download('ireland-geometry-filtered.geojson',JSON.stringify(copy),'application/geo+json'); }
let mapAssetsAvailable = false;
function loadStyle(href) { const link=document.createElement('link'); link.rel='stylesheet'; link.href=href; document.head.appendChild(link); }
function loadScript(src) { return new Promise((resolve,reject)=>{ const script=document.createElement('script'); script.src=src; script.onload=resolve; script.onerror=()=>reject(new Error(`Could not load map asset ${src}`)); document.head.appendChild(script); }); }
async function loadMapAssets() {
  if(OFFLINE_REQUESTED) return;
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
function initMap() { if(OFFLINE_REQUESTED || !mapAssetsAvailable || typeof L==='undefined'){ offlineMap=true; renderOfflineMap(); return; } map=L.map('map').setView([53.35,-8.05],7); const osm=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}).addTo(map); const esri=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri World Imagery',maxZoom:18}); markerLayer=(L.markerClusterGroup?L.markerClusterGroup({maxClusterRadius:45,disableClusteringAtZoom:14}):L.layerGroup()).addTo(map); const outlineLayer=L.layerGroup().addTo(map); OUTLINES.forEach(item=>{ const shapes=item.rings.length===1?item.rings[0]:item.rings; L.polygon(shapes,{color:'#1f2937',weight:2,fillColor:color(item.score),fillOpacity:.2}).bindPopup(`<b>${esc(item.name||'Unnamed')}</b><br>${esc(item.group)} · score ${fmt(item.score)}<br>${flagHtml({flags:item.flags||[]})}`).addTo(outlineLayer); }); L.control.layers({'OSM':osm,'Satellite':esri},{'Top outlines':outlineLayer,'Markers':markerLayer}).addTo(map); renderMap(); }
function init() { $('score').addEventListener('input',()=>{$('scoreValue').textContent=$('score').value; $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); queueFilters();}); ['query','group','century','rating','niahType','reviewState','onlyAngle','onlyRatio','onlyCircular','onlyMulti'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',queueFilters)); $('prev').addEventListener('click',()=>{if(page>1){page--; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); $('next').addEventListener('click',()=>{const total=SERVER_MODE?Number(pageStats.total||0):filtered.length; if(page<Math.ceil(total/PAGE_SIZE)){page++; if(SERVER_MODE) fetchServerPage(); else renderTable();}}); document.addEventListener('click',event=>{ const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(header) sortBy(header.dataset.sort); }); document.addEventListener('keydown',event=>{ if(event.key!=='Enter'&&event.key!==' ') return; const button=event.target.closest?.('button.sort-button'); const header=button?.closest('th[data-sort]'); if(!header) return; event.preventDefault(); sortBy(header.dataset.sort); }); $('downloadCsv').addEventListener('click',downloadCsv); $('downloadGeo').addEventListener('click',downloadGeo); restoreViewState(); $('score').setAttribute('aria-valuetext',`Minimum score ${$('score').value}`); initRoute(); $('method').innerHTML=`<p>Target rows: <b>${Number(SUMMARY.targets||0).toLocaleString()}</b>; controls: <b>${Number(SUMMARY.controls||0).toLocaleString()}</b>; NIAH joins: <b>${Number(SUMMARY.niah_matches||0).toLocaleString()}</b> (${Number(SUMMARY.niah_contained||0).toLocaleString()} contained, ${Number(SUMMARY.niah_near||0).toLocaleString()} near).</p><p>Source readiness: ${sourceStatusText()}.</p><p>Input freshness: <b>${sourceFreshnessText()}</b>.</p><p>Analytical readiness: <b>${SUMMARY.analysis_ready?'pass':'incomplete'}</b>; validation records: <b>${esc(SUMMARY.validation?.status||'not reported')}</b>.</p><p>Shape descriptors include rectangularity, angle entropy, radial Fourier coefficients, and radial variability. ${Number(SUMMARY.part_mapped||0).toLocaleString()} target footprints have mapped OSM building parts; LiDAR coverage is ${Number(SUMMARY.lidar_available||0).toLocaleString()} targets. Historical rows are review evidence, not proof of intent.</p><p>Primary rates use building-level two-proportion z-tests, Wilson confidence intervals, risk differences, continuity-corrected odds ratios, matched controls, hierarchical stratified odds ratios, Moran's I, county permutations, and Ripley summaries as sensitivity diagnostics. Construction dates and ratings cover the NIAH dataset, not all of Ireland. Generated ${esc(SUMMARY.generated_at||'unknown')}.</p><p>Sources: OpenStreetMap contributors (ODbL), National Inventory of Architectural Heritage (CC BY 4.0), and Esri World Imagery for visual reference.</p>`; renderInterpretation(); renderBars();renderQuality();renderStats();applyFilters();initMap(); }
async function reportLaunch() { try { await loadMapAssets(); init(); } catch(error) { document.body.innerHTML=`<pre style="padding:20px">${error}</pre>`; } }
reportLaunch();
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=None, help="output directory; defaults to project output/"
    )
    args = parser.parse_args()
    out = project_path(args.out_dir, "output")
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
