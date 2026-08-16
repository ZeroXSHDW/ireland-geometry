#!/usr/bin/env python3
"""Geometric pattern analysis of Irish building footprints.

Reads  data/combined.json            (built by fetch_geofabrik.py)
Writes output/analysis_results.csv        one row per building
       output/top_patterns.csv            highest-scoring matches
       output/ireland_buildings.geojson   target footprints + metrics
       output/significance.csv            statistical tests vs control

Pattern detectors (per footprint, min-area rotated rectangle basis):
  * golden ratio  (aspect ratio 1.6180339887, 3% tolerance)
  * Fibonacci ratios F(n+1)/F(n)  (1, 2, 1.5, 1.667, 1.6, 1.625, ...)
  * Fibonacci dimension magnitudes (L or W near 5..144 m)
  * golden angle 137.5deg and regular angles (60/90/108/120)
  * reflective symmetry + rotational symmetry 180/90/60/45/72 deg (IoU)
  * circularity 4*pi*A/P^2 (round towers, ringforts)
  * cruciform-candidate signature (concave, many vertices, large)

Every signal is tested for statistical significance:
  * aspect-based signals: vs a CONTROL group of ordinary buildings
    (sampled by the fetcher), two-proportion z-test
  * golden-angle signal: vs the same empirical building-level control group
    (avoiding a target-contaminated angle-histogram null)

Usage:
    python3 scripts/analyze.py
    python3 scripts/analyze.py --min-area 25 --strong-score 55
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, mapping

try:  # direct script execution
    from geometry import (
        convexity_ratio,
        geometry_quality,
        interior_angles,
        iter_polygons,
        polygon_from_element,
        repair_geometry,
        to_local_meters,
    )
    from runtime import atomic_write_csv, atomic_write_text
    from stats import apply_holm, compare_proportions, p_format
except ImportError:  # package/test execution
    from scripts.geometry import (
        convexity_ratio,
        geometry_quality,
        interior_angles,
        iter_polygons,
        polygon_from_element,
        repair_geometry,
        to_local_meters,
    )
    from scripts.runtime import atomic_write_csv, atomic_write_text
    from scripts.stats import apply_holm, compare_proportions, p_format

GOLDEN = (1.0 + math.sqrt(5.0)) / 2.0  # 1.618033988749895
GOLDEN_ANGLE = 137.50776405003785  # degrees
FIB_METERS = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610]
FIB_RATIOS = [1.0] + [FIB_METERS[i + 1] / FIB_METERS[i] for i in range(1, len(FIB_METERS) - 1)]

WORSHIP = {
    "church",
    "cathedral",
    "chapel",
    "basilica",
    "religious",
    "monastery",
    "abbey",
    "priory",
    "convent",
    "shrine",
}
GOV = {"government", "civic", "public", "townhall", "courthouse", "city_hall"}
HIST = {"castle", "manor", "tower", "ruins"}
CIVIC = {"bank", "post_office", "museum", "library"}

FIELDNAMES = [
    "osm_id",
    "osm_type",
    "name",
    "group",
    "subtype",
    "is_control",
    "lat",
    "lon",
    "area_m2",
    "length_m",
    "width_m",
    "aspect_ratio",
    "n_vertices",
    "n_parts",
    "convexity",
    "circularity",
    "iou_reflect",
    "iou_rot180",
    "iou_rot90",
    "iou_rot60",
    "iou_rot45",
    "iou_rot72",
    "right_angle_pct",
    "has_golden_angle",
    "has_108_angle",
    "has_60_angle",
    "has_120_angle",
    "fib_ratio_nearest",
    "fib_ratio_err_pct",
    "golden_ratio_err_pct",
    "fib_len_m",
    "fib_wid_m",
    "score",
    "flags",
    "valid",
    "repaired",
    "degenerate",
    "multipart",
    "hole_count",
    "perimeter_m",
    "geometry_warning",
]

TARGET_GROUPS = ["worship", "government", "historic", "civic", "other"]


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------


def classify(tags: dict) -> str:
    b = tags.get("building", "")
    if tags.get("amenity") == "place_of_worship" or b in WORSHIP or tags.get("religion"):
        return "worship"
    if tags.get("office") == "government" or tags.get("amenity") == "townhall" or b in GOV:
        return "government"
    if b in CIVIC:
        return "civic"
    if tags.get("historic") or b in HIST or tags.get("man_made") == "tower":
        return "historic"
    return "other"


def reflect_iou(poly: Polygon, theta_deg: float) -> float:
    """IoU of polygon vs its mirror across the axis of its min-rotated box."""
    c = poly.centroid
    p = affinity.rotate(poly, -theta_deg, origin=c)
    p2 = affinity.scale(p, xfact=-1.0, yfact=1.0, origin=c)
    p2 = affinity.rotate(p2, theta_deg, origin=c)
    inter = p.intersection(p2).area
    union = p.area + p2.area - inter
    return inter / union if union > 0 else 0.0


def rotate_iou(poly: Polygon, deg: float) -> float:
    p2 = affinity.rotate(poly, deg, origin=poly.centroid)
    inter = poly.intersection(p2).area
    union = poly.area + p2.area - inter
    return inter / union if union > 0 else 0.0


def nearest_fib_ratio(aspect: float) -> tuple[float, float]:
    best, best_err = None, None
    for fr in FIB_RATIOS:
        if fr <= 0:
            continue
        err = abs(aspect - fr) / fr * 100.0
        if best_err is None or err < best_err:
            best, best_err = fr, err
    return best, best_err


def fib_dim(dim: float) -> float:
    for f in FIB_METERS:
        if f >= 5 and dim > 0 and abs(dim - f) / f <= 0.03:
            return f
    return 0.0


# --------------------------------------------------------------------------
# per-building analysis
# --------------------------------------------------------------------------


def analyze_element(el: dict, min_area: float, angle_hist) -> dict | None:
    poly = polygon_from_element(el)
    if poly is None:
        return None
    poly, was_repaired, geometry_warning = repair_geometry(poly)
    quality = geometry_quality(poly, repaired=was_repaired, warning=geometry_warning)
    if not quality["valid"] or quality["degenerate"]:
        return None

    n_parts = sum(1 for _ in iter_polygons(poly))

    centroid = poly.centroid
    lat0, lon0 = centroid.y, centroid.x
    local = to_local_meters(poly, lat0, lon0)
    if local.area < min_area:
        return None

    # --- bounding box (minimum-area rotated rectangle) --------------------
    mrr = local.minimum_rotated_rectangle
    if mrr is None or mrr.area <= 0:
        return None
    box = list(mrr.exterior.coords)[:-1]
    sides = [
        math.hypot(box[i][0] - box[(i + 1) % 4][0], box[i][1] - box[(i + 1) % 4][1])
        for i in range(4)
    ]
    length_m, width_m = max(sides), min(sides)
    aspect = length_m / width_m if width_m > 0 else 0.0
    theta = math.degrees(math.atan2(box[1][1] - box[0][1], box[1][0] - box[0][0]))

    # --- vertices & angles -------------------------------------------------
    angs = interior_angles(local)
    n_verts = len(angs)
    for a in angs:
        b = round(a)
        if 1 <= b <= 179:
            angle_hist[b] += 1
    right_frac = sum(1 for a in angs if abs(a - 90.0) <= 2.0) / n_verts if n_verts else 0.0
    has_golden_angle = any(abs(a - GOLDEN_ANGLE) <= 3.0 for a in angs)
    has_108 = any(abs(a - 108.0) <= 3.0 for a in angs)
    has_60 = any(abs(a - 60.0) <= 3.0 for a in angs)
    has_120 = any(abs(a - 120.0) <= 3.0 for a in angs)

    # --- convexity, symmetry, circularity -----------------------------------
    convexity = convexity_ratio(local)
    perimeter = local.length
    circularity = 4.0 * math.pi * local.area / (perimeter * perimeter) if perimeter > 0 else 0.0
    iou_reflect = reflect_iou(local, theta)
    iou_rot180 = rotate_iou(local, 180.0)
    iou_rot90 = rotate_iou(local, 90.0)
    iou_rot60 = rotate_iou(local, 60.0)
    iou_rot45 = rotate_iou(local, 45.0)
    iou_rot72 = rotate_iou(local, 72.0)

    # --- Fibonacci / golden matches -----------------------------------------
    nearest, fib_err = nearest_fib_ratio(aspect)
    golden_err = abs(aspect - GOLDEN) / GOLDEN * 100.0
    golden_match = golden_err <= 3.0
    fib_ratio_match = nearest is not None and fib_err <= 2.0 and nearest != 1.0 or (aspect == 1.0)
    fib_len = fib_dim(length_m)
    fib_wid = fib_dim(width_m)

    # --- score ---------------------------------------------------------------
    score = 0.0
    if golden_match:
        score += 25
    if fib_ratio_match and nearest not in (1.0,):
        score += 15
    if fib_len or fib_wid:
        score += 12
    score += 12 * right_frac
    score += 20 * max(0.0, iou_reflect - 0.6) / 0.4
    score += 10 * max(0.0, iou_rot180 - 0.6) / 0.4
    if circularity >= 0.85:
        score += 10
    if max(iou_rot60, iou_rot45, iou_rot72) >= 0.85:
        score += 8
    if convexity <= 0.97 and n_verts >= 8 and local.area >= 200:
        score += 6
    if has_golden_angle:
        score += 8
    if has_108:
        score += 3
    if has_120:
        score += 2
    score = min(100.0, round(score, 2))

    flags = []
    if golden_match:
        flags.append("golden_ratio")
    if fib_ratio_match and nearest not in (1.0,):
        flags.append("fib_ratio")
    if fib_len or fib_wid:
        flags.append("fib_dimension")
    if iou_reflect >= 0.85:
        flags.append("reflective_symmetry")
    if iou_rot180 >= 0.85:
        flags.append("rot180_symmetry")
    if iou_rot90 >= 0.85:
        flags.append("rot90_symmetry")
    if iou_rot60 >= 0.85:
        flags.append("hexagonal")
    if iou_rot45 >= 0.85:
        flags.append("octagonal")
    if iou_rot72 >= 0.85:
        flags.append("pentagonal")
    if circularity >= 0.85:
        flags.append("circular")
    if convexity <= 0.97 and n_verts >= 8 and local.area >= 200:
        flags.append("cruciform_candidate")
    if has_golden_angle:
        flags.append("golden_angle")
    if right_frac >= 0.5 and n_verts >= 4:
        flags.append("orthogonal")
    flags_str = ",".join(flags)

    tags = el.get("tags", {})
    is_control = 1 if el.get("control") else 0
    group = "control" if is_control else classify(tags)
    subtype = classify(tags)
    osm_type = el.get("osm_type", "way")

    row = {
        "osm_id": f"{osm_type}/{el['id']}",
        "osm_type": osm_type,
        "name": tags.get("name", ""),
        "group": group,
        "subtype": subtype,
        "is_control": is_control,
        "lat": round(lat0, 6),
        "lon": round(lon0, 6),
        "area_m2": round(local.area, 1),
        "length_m": round(length_m, 2),
        "width_m": round(width_m, 2),
        "aspect_ratio": round(aspect, 4),
        "n_vertices": n_verts,
        "n_parts": n_parts,
        "convexity": round(convexity, 4),
        "circularity": round(circularity, 4),
        "iou_reflect": round(iou_reflect, 4),
        "iou_rot180": round(iou_rot180, 4),
        "iou_rot90": round(iou_rot90, 4),
        "iou_rot60": round(iou_rot60, 4),
        "iou_rot45": round(iou_rot45, 4),
        "iou_rot72": round(iou_rot72, 4),
        "right_angle_pct": round(right_frac * 100.0, 1),
        "has_golden_angle": int(has_golden_angle),
        "has_108_angle": int(has_108),
        "has_60_angle": int(has_60),
        "has_120_angle": int(has_120),
        "fib_ratio_nearest": round(nearest, 6) if nearest else "",
        "fib_ratio_err_pct": round(fib_err, 3) if fib_err is not None else "",
        "golden_ratio_err_pct": round(golden_err, 3),
        "fib_len_m": fib_len,
        "fib_wid_m": fib_wid,
        "score": score,
        "flags": flags_str,
        "valid": quality["valid"],
        "repaired": quality["repaired"],
        "degenerate": quality["degenerate"],
        "multipart": quality["multipart"],
        "hole_count": quality["hole_count"],
        "perimeter_m": round(perimeter, 2),
        "geometry_warning": quality["geometry_warning"],
    }
    return row, poly, tags


# --------------------------------------------------------------------------
# significance tests
# --------------------------------------------------------------------------


def build_significance(rows: list, angle_hist=None) -> list[dict]:
    """Compare every signal with the same empirical control population.

    The previous implementation estimated the golden-angle null from the
    global vertex-angle histogram, including the target observations being
    tested. This version uses building-level target/control comparisons for
    all primary signals and applies Holm correction across the full feature
    family. ``angle_hist`` remains an accepted argument for compatibility.
    """
    control = [r for r in rows if r["is_control"] == 1]
    targets = {g: [r for r in rows if r["group"] == g] for g in TARGET_GROUPS}
    out = []

    def rate_of(rs, pred):
        return sum(1 for r in rs if pred(r)) / len(rs) if rs else 0.0

    has_golden = lambda r: float(r["golden_ratio_err_pct"] or 999) <= 3.0
    has_fib = lambda r: float(r["fib_ratio_err_pct"] or 999) <= 2.0

    for signal, pred in (
        ("golden_ratio", has_golden),
        ("fib_ratio", has_fib),
        ("golden_angle", lambda r: r["has_golden_angle"] == 1),
    ):
        control_successes = sum(1 for r in control if pred(r))
        for g, rs in targets.items():
            n = len(rs)
            if n == 0:
                continue
            successes = sum(1 for r in rs if pred(r))
            result = compare_proportions(successes, n, control_successes, len(control))
            direction = (
                1
                if result["rate1"] > result["rate2"]
                else -1
                if result["rate1"] < result["rate2"]
                else 0
            )
            out.append(
                {
                    "signal": signal,
                    "group": g,
                    "n": n,
                    "successes": successes,
                    "observed_rate": round(result["rate1"] * 100, 2),
                    "control_n": len(control),
                    "control_successes": control_successes,
                    "control_rate": round(result["rate2"] * 100, 2),
                    "ci_low": round(result["rate1_ci_low"] * 100, 2),
                    "ci_high": round(result["rate1_ci_high"] * 100, 2),
                    "control_ci_low": round(result["rate2_ci_low"] * 100, 2),
                    "control_ci_high": round(result["rate2_ci_high"] * 100, 2),
                    "risk_difference": round(result["risk_difference"] * 100, 2),
                    "odds_ratio": round(result["odds_ratio"], 4),
                    "z": round(result["z"], 4),
                    "p_value": result["p_value"],
                    "method": result["method"],
                    "direction": direction,
                    "test_family": "global_building_signal",
                }
            )
    return apply_holm(out)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data", default=None, help="combined JSON path; defaults to project data/combined.json"
    )
    ap.add_argument("--out", default=None, help="output directory; defaults to project output/")
    ap.add_argument("--min-area", type=float, default=25.0)
    ap.add_argument("--strong-score", type=float, default=55.0)
    args = ap.parse_args()

    try:
        from runtime import project_path
    except ImportError:
        from scripts.runtime import project_path
    data_path = project_path(args.data, "data/combined.json")
    if not data_path.exists():
        sys.exit(f"Missing {data_path}. Run scripts/fetch_geofabrik.py first.")
    out_dir = project_path(args.out, "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(data_path.read_text())
    elements = payload["elements"]
    print(f"[analyze] {len(elements)} elements loaded", flush=True)

    angle_hist = np.zeros(181, dtype=np.int64)
    rows, features = [], []
    for i, el in enumerate(elements):
        res = analyze_element(el, args.min_area, angle_hist)
        if res is not None:
            row, poly, tags = res
            rows.append(row)
            if row["is_control"] == 0:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(poly),
                        "properties": {
                            "name": row["name"],
                            "group": row["group"],
                            "score": row["score"],
                            "area_m2": row["area_m2"],
                            "aspect_ratio": row["aspect_ratio"],
                            "flags": row["flags"],
                            "osm_type": row["osm_type"],
                            "osm_id": row["osm_id"],
                            "tags": tags,
                        },
                    }
                )
        if (i + 1) % 10000 == 0:
            print(f"[analyze] ...{i + 1}/{len(elements)}", flush=True)

    print(f"[analyze] {len(rows)} buildings analyzed", flush=True)
    targets = [r for r in rows if r["is_control"] == 0]

    # --- CSVs ----------------------------------------------------------------
    atomic_write_csv(out_dir / "analysis_results.csv", FIELDNAMES, rows)

    strong = sorted(
        [r for r in targets if r["score"] >= args.strong_score],
        key=lambda r: r["score"],
        reverse=True,
    )
    atomic_write_csv(out_dir / "top_patterns.csv", FIELDNAMES, strong)

    fc = {"type": "FeatureCollection", "features": features}
    atomic_write_text(out_dir / "ireland_buildings.geojson", json.dumps(fc))

    # --- significance ----------------------------------------------------------
    sig = build_significance(rows, angle_hist)
    sig_fields = [
        "signal",
        "group",
        "n",
        "successes",
        "observed_rate",
        "control_n",
        "control_successes",
        "control_rate",
        "ci_low",
        "ci_high",
        "control_ci_low",
        "control_ci_high",
        "risk_difference",
        "odds_ratio",
        "z",
        "p_value",
        "p_adjusted",
        "method",
        "direction",
        "test_family",
        "verdict",
    ]
    atomic_write_csv(out_dir / "significance.csv", sig_fields, sig, extrasaction="ignore")

    print(f"[analyze] wrote analysis_results.csv ({len(rows)} rows)")
    print(f"[analyze] wrote top_patterns.csv ({len(strong)} rows)")
    print(f"[analyze] wrote ireland_buildings.geojson ({len(features)} features)")
    print(f"[analyze] wrote significance.csv ({len(sig)} tests)")

    # --- summary ---------------------------------------------------------------
    by_group = Counter(r["group"] for r in rows)
    print("\n[analyze] buildings by group:")
    for g, n in by_group.most_common():
        sub = [r for r in rows if r["group"] == g]
        golden_n = sum(1 for r in sub if r["golden_ratio_err_pct"] <= 3.0)
        circ_n = sum(1 for r in sub if r["circularity"] >= 0.85)
        print(f"  {g:12s} {n:6d}   golden: {golden_n / n * 100:5.1f}%  circular: {circ_n:5d}")

    print("\n[analyze] significance (vs empirical control group):")
    for s in sig:
        print(
            f"  {s['signal']:13s} {s['group']:11s} obs={s['observed_rate']:5.2f}% "
            f"ref={s['control_rate']:5.2f}%  p={p_format(s['p_value'])} "
            f"Holm={p_format(s['p_adjusted'])} {s['verdict']}"
        )

    top = sorted(targets, key=lambda r: r["score"], reverse=True)[:15]
    print("\n[analyze] top 15 by score:")
    for r in top:
        print(
            f"  {r['score']:6.1f}  {r['name'] or '(unnamed)':40s} {r['group']:10s} "
            f"aspect={r['aspect_ratio']:.3f}  [{r['flags']}]"
        )


if __name__ == "__main__":
    main()
