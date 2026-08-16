#!/usr/bin/env python3
"""NIAH correlation stage (v3): join construction dates & heritage ratings
to the OSM footprints, then re-test every pattern signal stratified by
century, rating and building class.

Method
------
1. Spatial join — every footprint in data/combined.json is matched to the
   NIAH record whose survey centroid falls *inside* the polygon (preferred,
   exact), else to the nearest NIAH point within 50 m.
2. Merge the join onto analysis_results.csv (one row per analyzed building)
   so every pattern signal carries NIAH date/rating/class metadata.
3. Stratified significance tests (two-proportion z-test):
     a. worship golden-angle rate by construction century, vs an
        ERA-MATCHED baseline: ordinary control buildings joined to NIAH
        and dated to the SAME century (this holds era constant, which the
        v2 global control could not).
     b. same, vs the global control rate (continuity with v2).
     c. high rating (National/International) vs Regional, within worship.
     d. every NIAH original_type with n >= 50, golden-angle rate vs control.

Reads:  data/niah/niah.json, data/combined.json, output/analysis_results.csv
Writes: output/niah_join.csv         (osm_id -> NIAH record)
        output/niah_significance.csv (stratified z-tests)
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import Point
from shapely.strtree import STRtree

try:
    from analyze import GOLDEN_ANGLE
except ImportError:
    from scripts.analyze import GOLDEN_ANGLE
try:
    from geometry import geometry_from_element, interior_angles, repair_geometry, to_local_meters
    from runtime import atomic_write_csv, project_path
    from stats import apply_holm, compare_proportions
except ImportError:
    from scripts.geometry import (
        geometry_from_element,
        interior_angles,
        repair_geometry,
        to_local_meters,
    )
    from scripts.runtime import atomic_write_csv, project_path
    from scripts.stats import apply_holm, compare_proportions

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

NIAH_JSON = DATA / "niah" / "niah.json"
COMBINED = DATA / "combined.json"
RESULTS = OUT / "analysis_results.csv"

NEAR_M = 50.0  # max distance (m) for a fallback "near" match

CENTURY_ORDER = ["pre-18th", "18th", "19th", "20th", "21st"]


# --------------------------------------------------------------------------
# spatial join
# --------------------------------------------------------------------------


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_join(elements: list, niah: list) -> dict:
    """Return {osm_id: {niah record fields..., mode, dist_m}} for matched
    elements. OSM ids are '{osm_type}/{id}'."""
    pts = np.empty((len(niah), 2))
    for i, rec in enumerate(niah):
        pts[i, 0], pts[i, 1] = rec["lon"], rec["lat"]
    tree = STRtree([Point(lon, lat) for lon, lat in pts])

    joined: dict[str, dict] = {}
    n_contained = n_near = n_none = 0
    for k, el in enumerate(elements):
        poly = geometry_from_element(el)
        poly, _, _ = repair_geometry(poly)
        if poly is None or poly.is_empty or not poly.is_valid:
            continue
        osm_id = f"{el.get('osm_type', 'way')}/{el['id']}"

        c = poly.centroid
        # buffer the bbox by ~250 m so near matches are always candidates
        buf = 0.0025 + 0.0025 / max(0.4, math.cos(math.radians(c.y)))
        minx, miny, maxx, maxy = poly.bounds
        hits = tree.query(
            Point((minx + maxx) / 2, (miny + maxy) / 2).buffer(
                math.hypot(maxx - minx, maxy - miny) / 2 + buf
            )
        )

        contained = []
        for idx in hits:
            rec = niah[int(idx)]
            p = Point(rec["lon"], rec["lat"])
            if poly.contains(p) or poly.boundary.distance(p) < 1e-9:
                contained.append((rec, haversine_m(c.y, c.x, rec["lat"], rec["lon"])))
        if contained:
            rec, dist = min(contained, key=lambda t: t[1])
            mode = "contained"
            n_contained += 1
        else:
            best, best_d = None, None
            for idx in hits:
                rec = niah[int(idx)]
                d = haversine_m(c.y, c.x, rec["lat"], rec["lon"])
                if d <= NEAR_M and (best_d is None or d < best_d):
                    best, best_d = rec, d
            if best is not None:
                rec, dist, mode = best, best_d, "near"
                n_near += 1
            else:
                n_none += 1
                continue

        joined[osm_id] = {
            "reg_no": rec["reg_no"],
            "niah_name": rec["name"],
            "source_region": rec.get("source_region", ""),
            "county": rec["county"],
            "rating": rec["rating"],
            "rating_high": rec["rating_high"],
            "niah_type": rec["original_type"],
            "date_from": rec["date_from"],
            "date_to": rec["date_to"],
            "date_mid": rec["date_mid"],
            "century": rec["century"],
            "century50": rec["century50"],
            "match_mode": mode,
            "dist_m": round(dist, 1) if dist is not None else None,
        }
        if (k + 1) % 20000 == 0:
            print(
                f"[niah] join ...{k + 1}/{len(elements)} "
                f"(contained={n_contained} near={n_near} none={n_none})",
                flush=True,
            )

    print(f"[niah] join done: contained={n_contained} near={n_near} unmatched={n_none}")
    return joined


# --------------------------------------------------------------------------
# significance helpers
# --------------------------------------------------------------------------


def golden_band(angle: float) -> str:
    """Classify a matched angle inside the 137.5+-3 deg window.

    The window (134.5-140.5 deg) overlaps the octagon interior angle (135).
    Buckets isolate that confound:
      135-band  = 134.5-136.5  (octagonal / segmental bays)
      core      = 136.5-138.5  (true golden-angle core)
      high      = 138.5-140.5  (approaching regular nonagon 140)
    """
    if angle < 136.5:
        return "octagon-ish"
    if angle <= 138.5:
        return "golden-core"
    return "high-side"


def closest_angle_in_band(poly, band_lo: float, band_hi: float):
    """Nearest interior vertex angle within the band (or None)."""
    c = poly.centroid
    local = to_local_meters(poly, c.y, c.x)
    best = None
    for a in interior_angles(local):
        if band_lo <= a <= band_hi and (
            best is None or abs(a - GOLDEN_ANGLE) < abs(best - GOLDEN_ANGLE)
        ):
            best = a
    return best


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    import argparse

    global DATA, OUT, NIAH_JSON, COMBINED, RESULTS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    ap.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    args = ap.parse_args()
    DATA = project_path(args.data_root, "data")
    OUT = project_path(args.out_dir, "output")
    NIAH_JSON = DATA / "niah" / "niah.json"
    COMBINED = DATA / "combined.json"
    RESULTS = OUT / "analysis_results.csv"

    if not NIAH_JSON.exists():
        sys.exit(f"Missing {NIAH_JSON}. Run scripts/fetch_niah.py first.")
    if not COMBINED.exists() or not RESULTS.exists():
        sys.exit(
            "Missing data/combined.json or output/analysis_results.csv. Run the v2 pipeline first."
        )

    OUT.mkdir(parents=True, exist_ok=True)

    # ---- load -------------------------------------------------------------
    niah = json.loads(NIAH_JSON.read_text())
    payload = json.loads(COMBINED.read_text())
    elements = payload["elements"]
    print(f"[niah] {len(niah)} NIAH records, {len(elements)} OSM elements", flush=True)

    joined = build_join(elements, niah)

    join_csv = OUT / "niah_join.csv"
    fields = [
        "osm_id",
        "reg_no",
        "niah_name",
        "source_region",
        "county",
        "rating",
        "rating_high",
        "niah_type",
        "date_from",
        "date_to",
        "date_mid",
        "century",
        "century50",
        "match_mode",
        "dist_m",
    ]
    atomic_write_csv(
        join_csv,
        fields,
        ({"osm_id": osm_id, **rec} for osm_id, rec in joined.items()),
        extrasaction="ignore",
    )
    print(f"[niah] wrote {join_csv} ({len(joined)} matches)", flush=True)

    # ---- merge with analysis results --------------------------------------
    rows = list(csv.DictReader(RESULTS.open()))
    for r in rows:
        j = joined.get(r["osm_id"])
        if j:
            r["niah"] = j
    n_matched = sum(1 for r in rows if r.get("niah"))
    print(
        f"[niah] {n_matched} analyzed buildings matched to NIAH "
        f"({n_matched / len(rows) * 100:.1f}%)",
        flush=True,
    )

    # empirical global control rates
    control = [r for r in rows if r["is_control"] == "1"]

    def rate(rs, pred):
        return sum(1 for r in rs if pred(r)) / len(rs) if rs else 0.0

    ctrl_ga = rate(control, lambda r: r["has_golden_angle"] == "1")
    ctrl_ga_n = len(control)
    print(
        f"[niah] global control golden-angle rate: {ctrl_ga * 100:.2f}% (n={ctrl_ga_n})", flush=True
    )

    worship = [r for r in rows if r["group"] == "worship" and r.get("niah")]
    worship_cent = {c: [r for r in worship if r["niah"]["century"] == c] for c in CENTURY_ORDER}

    # control buildings with NIAH dates (era-matched baseline)
    ctrl_dated = [r for r in control if r.get("niah")]
    ctrl_cent = {c: [r for r in ctrl_dated if r["niah"]["century"] == c] for c in CENTURY_ORDER}

    tests = []

    def add(signal, stratum, n, obs, ref, ref_label, group_label=""):
        successes = round(obs * n)
        ref_successes = round(ref["rate"] * ref["n"])
        result = compare_proportions(successes, n, ref_successes, ref["n"])
        direction = (
            1
            if result["rate1"] > result["rate2"]
            else -1
            if result["rate1"] < result["rate2"]
            else 0
        )
        if "era-matched" in ref_label and stratum.endswith("th"):
            family = "niah_era_matched_century"
        elif "Regional" in ref_label:
            family = "niah_rating"
        elif "global" in ref_label and group_label == "":
            family = "niah_original_type"
        elif "global" in ref_label:
            family = "niah_global_century"
        else:
            family = f"niah_{signal}"
        tests.append(
            {
                "signal": signal,
                "stratum": stratum,
                "group": group_label,
                "n": n,
                "successes": successes,
                "observed_rate": round(result["rate1"] * 100, 2),
                "reference_n": ref["n"],
                "reference_successes": ref_successes,
                "reference_rate": round(result["rate2"] * 100, 2),
                "reference": ref_label,
                "ci_low": round(result["rate1_ci_low"] * 100, 2),
                "ci_high": round(result["rate1_ci_high"] * 100, 2),
                "reference_ci_low": round(result["rate2_ci_low"] * 100, 2),
                "reference_ci_high": round(result["rate2_ci_high"] * 100, 2),
                "risk_difference": round(result["risk_difference"] * 100, 2),
                "odds_ratio": round(result["odds_ratio"], 4),
                "z": round(result["z"], 4),
                "p_value": result["p_value"],
                "method": result["method"],
                "direction": direction,
                "test_family": family,
            }
        )

    # ---- (a) worship golden-angle by century, era-matched baseline --------
    for c in CENTURY_ORDER:
        ws, cs = worship_cent[c], ctrl_cent[c]
        if len(ws) < 10 or len(cs) < 10:
            continue
        obs = rate(ws, lambda r: r["has_golden_angle"] == "1")
        ref = rate(cs, lambda r: r["has_golden_angle"] == "1")
        add(
            "golden_angle",
            c,
            len(ws),
            obs,
            {"n": len(cs), "rate": ref},
            f"controls dated {c} (era-matched)",
            group_label="worship",
        )

    # ---- (b) worship golden-angle by century vs global control ------------
    for c in CENTURY_ORDER:
        ws = worship_cent[c]
        if len(ws) < 10:
            continue
        obs = rate(ws, lambda r: r["has_golden_angle"] == "1")
        add(
            "golden_angle",
            c,
            len(ws),
            obs,
            {"n": ctrl_ga_n, "rate": ctrl_ga},
            "all controls (global)",
            group_label="worship",
        )

    # ---- (b2) worship golden-angle by DECADE, era-matched baseline --------
    # Finer than centuries: pinpoints which decade drives the 20th-c spike
    # (v5 improvement). Writes output/niah_decades.csv separately so the
    # century table in the report stays unchanged.
    by_decade: dict[int, list] = {}
    ctrl_decade: dict[int, list] = {}
    for r in worship:
        d = r["niah"].get("date_mid")
        if d:
            by_decade.setdefault(int(d) // 10 * 10, []).append(r)
    for r in ctrl_dated:
        d = r["niah"].get("date_mid")
        if d:
            ctrl_decade.setdefault(int(d) // 10 * 10, []).append(r)
    decade_rows = []
    for d in sorted(set(by_decade) & set(ctrl_decade)):
        ws, cs = by_decade[d], ctrl_decade[d]
        if len(ws) < 10 or len(cs) < 10:
            continue
        obs = rate(ws, lambda r: r["has_golden_angle"] == "1")
        ref = rate(cs, lambda r: r["has_golden_angle"] == "1")
        result = compare_proportions(round(obs * len(ws)), len(ws), round(ref * len(cs)), len(cs))
        decade_rows.append(
            {
                "decade": f"{d}s",
                "n_churches": len(ws),
                "golden_rate": round(obs * 100, 2),
                "era_control_rate": round(ref * 100, 2),
                "golden_ci_low": round(result["rate1_ci_low"] * 100, 2),
                "golden_ci_high": round(result["rate1_ci_high"] * 100, 2),
                "control_ci_low": round(result["rate2_ci_low"] * 100, 2),
                "control_ci_high": round(result["rate2_ci_high"] * 100, 2),
                "risk_difference": round(result["risk_difference"] * 100, 2),
                "odds_ratio": round(result["odds_ratio"], 4),
                "p": result["p_value"],
                "method": result["method"],
                "direction": 1 if obs > ref else -1,
                "test_family": "niah_decade",
            }
        )
    if decade_rows:
        decade_tests = apply_holm(
            [
                {
                    "p_value": row["p"],
                    "direction": row["direction"],
                    "test_family": row["test_family"],
                }
                for row in decade_rows
            ]
        )
        for row, result in zip(decade_rows, decade_tests):
            row["p_adjusted"] = result["p_adjusted"]
            row["verdict"] = result["verdict"]
        atomic_write_csv(OUT / "niah_decades.csv", list(decade_rows[0]), decade_rows)
        sig = [r for r in decade_rows if r["verdict"] == "SIGNAL"]
        print(
            f"[niah] wrote niah_decades.csv ({len(decade_rows)} decades, {len(sig)} SIGNAL)",
            flush=True,
        )

    # ---- (c) rating: high vs Regional, within worship ---------------------
    wh = [r for r in worship if r["niah"]["rating_high"]]
    wr = [r for r in worship if not r["niah"]["rating_high"]]
    if len(wh) >= 10 and len(wr) >= 10:
        obs = rate(wh, lambda r: r["has_golden_angle"] == "1")
        ref = rate(wr, lambda r: r["has_golden_angle"] == "1")
        add(
            "golden_angle",
            "National/International",
            len(wh),
            obs,
            {"n": len(wr), "rate": ref},
            "Regional-rated worship",
            group_label="worship",
        )
        for sig, pred in (
            ("golden_ratio", lambda r: float(r["golden_ratio_err_pct"] or 999) <= 3.0),
            ("fib_ratio", lambda r: float(r["fib_ratio_err_pct"] or 999) <= 2.0),
        ):
            obs = rate(wh, pred)
            ref = rate(wr, pred)
            add(
                sig,
                "National/International",
                len(wh),
                obs,
                {"n": len(wr), "rate": ref},
                "Regional-rated worship",
                group_label="worship",
            )

    # ---- (e) golden_ratio / fib_ratio by century (era-matched) ------------
    for sig, pred in (
        ("golden_ratio", lambda r: float(r["golden_ratio_err_pct"] or 999) <= 3.0),
        ("fib_ratio", lambda r: float(r["fib_ratio_err_pct"] or 999) <= 2.0),
    ):
        for c in CENTURY_ORDER:
            ws, cs = worship_cent[c], ctrl_cent[c]
            if len(ws) < 10 or len(cs) < 10:
                continue
            obs = rate(ws, pred)
            ref = rate(cs, pred)
            add(
                sig,
                c,
                len(ws),
                obs,
                {"n": len(cs), "rate": ref},
                f"controls dated {c} (era-matched)",
                group_label="worship",
            )

    # ---- (d) golden-angle by NIAH original_type (n >= 50) -----------------
    by_type: dict[str, list] = {}
    for r in rows:
        if not r.get("niah"):
            continue
        by_type.setdefault(r["niah"]["niah_type"], []).append(r)
    for t, rs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 50:
            continue
        obs = rate(rs, lambda r: r["has_golden_angle"] == "1")
        add(
            "golden_angle",
            t,
            len(rs),
            obs,
            {"n": ctrl_ga_n, "rate": ctrl_ga},
            "all controls (global)",
        )

    # ---- (f) angle-band breakdown: octagon confound vs golden core --------
    # The 137.5+-3 window overlaps the octagon interior angle (135). Recompute
    # the matched vertex angle for every dated worship building and bucket it
    # so the claim is honest about the octagonal-plan confound.
    el_by_id = {f"{el.get('osm_type', 'way')}/{el['id']}": el for el in elements}
    band_rows = []
    bands = Counter()
    bands_by_century: dict[str, Counter] = {}
    for r in worship:
        if r["has_golden_angle"] != "1":
            continue
        el = el_by_id.get(r["osm_id"])
        if el is None:
            continue
        poly = geometry_from_element(el)
        if poly is None:
            continue
        poly, _, _ = repair_geometry(poly)
        if poly is None:
            continue
        a = closest_angle_in_band(poly, GOLDEN_ANGLE - 3, GOLDEN_ANGLE + 3)
        if a is None:
            continue
        b = golden_band(a)
        bands[b] += 1
        bands_by_century.setdefault(r["niah"]["century"], Counter())[b] += 1
        band_rows.append(
            {
                "osm_id": r["osm_id"],
                "name": r["name"],
                "century": r["niah"]["century"],
                "angle_deg": round(a, 2),
                "band": b,
            }
        )
    atomic_write_csv(
        OUT / "niah_golden_angles.csv",
        ["osm_id", "name", "century", "angle_deg", "band"],
        band_rows,
    )
    print(f"[niah] wrote niah_golden_angles.csv ({len(band_rows)} matches)")
    if bands:
        tot = sum(bands.values())
        print(f"\n[niah] golden-angle band breakdown (dated worship, n={tot}):")
        for b in ("octagon-ish", "golden-core", "high-side"):
            print(f"  {b:12s} {bands[b]:5d} ({bands[b] / tot * 100:4.1f}%)")
        for c in CENTURY_ORDER:
            bc = bands_by_century.get(c)
            if bc:
                ct = sum(bc.values())
                print(
                    f"  {c:9s}: "
                    + ", ".join(
                        f"{b}={bc[b]}" for b in ("octagon-ish", "golden-core", "high-side") if bc[b]
                    )
                    + f"  (n={ct})"
                )

    # ---- write -------------------------------------------------------------
    tests = apply_holm(tests)
    sig_fields = [
        "signal",
        "stratum",
        "group",
        "n",
        "successes",
        "observed_rate",
        "reference_n",
        "reference_successes",
        "reference_rate",
        "reference",
        "ci_low",
        "ci_high",
        "reference_ci_low",
        "reference_ci_high",
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
    atomic_write_csv(OUT / "niah_significance.csv", sig_fields, tests)
    print(f"[niah] wrote niah_significance.csv ({len(tests)} tests)")

    # ---- summary ------------------------------------------------------------
    print("\n[niah] worship golden-angle by century (era-matched control):")
    for c in CENTURY_ORDER:
        ws, cs = worship_cent[c], ctrl_cent[c]
        if not ws and not cs:
            continue
        obs = rate(ws, lambda r: r["has_golden_angle"] == "1")
        ref = rate(cs, lambda r: r["has_golden_angle"] == "1")
        print(
            f"  {c:9s} worship n={len(ws):4d} {obs * 100:5.2f}%  |  "
            f"controls n={len(cs):4d} {ref * 100:5.2f}%"
        )

    print("\n[niah] worship matched to NIAH by century:")
    for c in CENTURY_ORDER:
        ws = worship_cent[c]
        if ws:
            print(f"  {c:9s} n={len(ws)}")

    print(f"\n[niah] NIAH rating within worship: high={len(wh)} regional={len(wr)}")
    print(
        f"[niah] church median build year: "
        f"{np.median([r['niah']['date_mid'] for r in worship]):.0f}"
    )


if __name__ == "__main__":
    main()
