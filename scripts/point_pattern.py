#!/usr/bin/env python3
"""Point-pattern analysis (v4): geometry BETWEEN buildings.

The v2/v3 detectors look *inside* one footprint. This stage asks whether the
PLACEMENT of churches encodes the same golden-ratio geometry:

  1. Bearing alignment   — the orientation of the line segment joining each
     church to its 1..4 nearest neighbours (mod 180, undirected). Is it
     concentrated near the golden angle 137.5 deg?
  2. Spiral turn angles  — for every consecutive triple along the
     nearest-neighbour graph, the turn angle between the two segments.
     A golden spiral turns by 137.5 deg (equivalently 42.5 deg for the
     opposite chirality, 180-137.5) at each step.
  3. Fibonacci distances — are inter-church nearest-neighbour distances
     clustered on Fibonacci meter values (5..2584 m), tested against an
     equally-sized set of SHAM (non-Fibonacci) values so placement
     clustering cannot fake a signal?

Null models (the honest part):
  * orientations/turns: the edge set is FIXED; the null rotates every edge
    uniformly in [0,180). This is isotropic-null, immune to the fact that
    churches cluster in towns.
  * distances: Poisson-uniform point clouds at the same density, plus the
    sham-value comparison.
  * a data-driven peak test: the most concentrated angle anywhere in
    [0,180) is found and tested with the same Monte Carlo (guards against
    cherry-picking 137.5).
  * the same bearing statistic is computed for ORDINARY control buildings —
    if the effect is real it must be absent there.

Reads:  output/analysis_results.csv
Writes: output/point_pattern.csv            (statistics + null + p)
        output/point_pattern_turns.csv      (per-triple turn data)
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import numpy as np
from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
RESULTS = OUT / "analysis_results.csv"

GOLDEN_ANGLE = 137.50776405003785
TOL_DEG = 3.0
FIB_METERS = [5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584]
SHAM_METERS = [
    6,
    11,
    19,
    30,
    47,
    72,
    111,
    171,
    264,
    408,
    630,
    972,
    1500,
    2317,
]  # deliberately NOT Fibonacci
TOL_FRAC = 0.03
K_NEAREST = 4
N_MC = 300
RNG = random.Random(20260812)
NRNG = np.random.default_rng(20260812)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def project(lats, lons):
    """Equirectangular projection around the mean point -> meters."""
    lat0 = math.radians(sum(lats) / len(lats))
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    xs = np.asarray(lons) * kx
    ys = np.asarray(lats) * ky
    return xs, ys


def wrap180(deg):
    """Wrap a signed angle difference to [0, 180)."""
    d = deg % 180.0
    return d


def nearest_neighbours(xs, ys, k, max_r=5000.0):
    """k-nearest neighbours (by index) for every point, via STRtree.
    Neighbour search is capped at max_r meters (local alignment only);
    returns however many neighbours fall inside the cap."""
    pts = [Point(x, y) for x, y in zip(xs, ys)]
    tree = STRtree(pts)
    n = len(xs)
    nbrs = []
    for i in range(n):
        p = pts[i]
        cands = tree.query(p.buffer(max_r))
        idx = [int(j) for j in cands if int(j) != i]
        idx.sort(key=lambda j: math.hypot(xs[j] - xs[i], ys[j] - ys[i]))
        nbrs.append(idx[:k])
    return nbrs


def nearest_neighbour_unbounded(xs, ys):
    """True 1-nearest-neighbour distance for every point (unbounded)."""
    pts = [Point(x, y) for x, y in zip(xs, ys)]
    tree = STRtree(pts)
    n = len(xs)
    dists = []
    for i in range(n):
        r = 100.0
        while True:
            cands = tree.query(pts[i].buffer(r))
            idx = [int(j) for j in cands if int(j) != i]
            if idx or r > 5e5:
                break
            r *= 4.0
        if idx:
            dists.append(min(math.hypot(xs[j] - xs[i], ys[j] - ys[i]) for j in idx))
        else:
            dists.append(float("nan"))
    return dists


def bearings_between(xs, ys, pairs):
    """Undirected line orientation (mod 180) for each pair."""
    out = []
    for i, j in pairs:
        a = math.degrees(math.atan2(ys[j] - ys[i], xs[j] - xs[i]))
        out.append(wrap180(a))
    return np.asarray(out)


def frac_within(angles, target):
    """Fraction of angles within +-TOL_DEG of target (mod 180)."""
    if len(angles) == 0:
        return 0.0
    d = np.abs(((angles - target) + 180.0) % 360.0 - 180.0)
    return float(np.mean(d <= TOL_DEG))


def frac_within_tol(angles, target, tol):
    """Fraction of angles within +-tol of target (mod 180), explicit tol."""
    if len(angles) == 0:
        return 0.0
    d = np.abs(((angles - target) + 180.0) % 360.0 - 180.0)
    return float(np.mean(d <= tol))


def peak_over_grid(angles):
    """Angle (1-deg grid) with the largest +-3 deg concentration, and that
    concentration. Direct evaluation - exact tolerance, no binning."""
    best_a, best_f = 0, -1.0
    for a in range(180):
        f = frac_within(angles, a)
        if f > best_f:
            best_a, best_f = a, f
    return best_a, best_f


def mc_p(observed, null_samples):
    """Empirical p (one-sided upper)."""
    return (1 + sum(1 for v in null_samples if v >= observed)) / (1 + len(null_samples))


def fib_frac(dists):
    """Fraction of distances within 3% of a Fibonacci meter value."""
    if len(dists) == 0:
        return 0.0
    hits = 0
    for d in dists:
        if any(abs(d - f) / f <= TOL_FRAC for f in FIB_METERS):
            hits += 1
    return hits / len(dists)


def sham_frac(dists):
    if len(dists) == 0:
        return 0.0
    hits = sum(1 for d in dists if any(abs(d - s) / s <= TOL_FRAC for s in SHAM_METERS))
    return hits / len(dists)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    import argparse

    global OUT, RESULTS, N_MC, RNG, NRNG
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    ap.add_argument("--seed", type=int, default=20260816)
    ap.add_argument("--mc", type=int, default=300, help="Monte Carlo iterations (default: 300)")
    args = ap.parse_args()
    try:
        from runtime import atomic_write_csv, project_output_tree_path
    except ImportError:
        from scripts.runtime import atomic_write_csv, project_output_tree_path
    OUT = project_output_tree_path(args.out_dir)
    RESULTS = OUT / "analysis_results.csv"
    N_MC = max(50, args.mc)
    RNG = random.Random(args.seed)
    NRNG = np.random.default_rng(args.seed)

    if not RESULTS.exists():
        raise SystemExit(f"Missing {RESULTS}. Run scripts/analyze.py first.")
    rows = list(csv.DictReader(RESULTS.open()))
    worship = [r for r in rows if r["group"] == "worship"]
    controls = [r for r in rows if r["is_control"] == "1"]

    print(f"[pp] {len(worship)} worship footprints, {len(controls)} controls", flush=True)

    def analyse(name, rs, do_distances=False):
        lats = [float(r["lat"]) for r in rs]
        lons = [float(r["lon"]) for r in rs]
        if len(lats) < 20:
            print(f"[pp] {name}: too few points ({len(lats)})", flush=True)
            return None
        xs, ys = project(lats, lons)
        nbrs = nearest_neighbours(xs, ys, K_NEAREST)

        # --- edge set: church to its 1..k nearest (deduped) ---------------
        pairs = set()
        for i, nb in enumerate(nbrs):
            for j in nb:
                pairs.add(tuple(sorted((i, j))))
        pairs = list(pairs)
        bears = bearings_between(xs, ys, pairs)

        # --- spiral turns: consecutive segments through each vertex --------
        nn1 = [nbrs[i][0] if nbrs[i] else None for i in range(len(xs))]
        turns = []
        for a in range(len(xs)):
            b = nn1[a]
            if b is None or b == a:
                continue
            c = nn1[b]
            if c is None or c == b:
                continue
            ang1 = math.degrees(math.atan2(ys[b] - ys[a], xs[b] - xs[a]))
            ang2 = math.degrees(math.atan2(ys[c] - ys[b], xs[c] - xs[b]))
            turns.append(wrap180(abs(ang2 - ang1)))
        turns = np.asarray(turns)
        res_turn = {}
        if len(turns):
            res_turn["turn_median_deg"] = round(float(np.median(turns)), 1)
            res_turn["turn_lt30_pct"] = round(float(np.mean(turns < 30.0)) * 100, 1)
            res_turn["turn_gt90_pct"] = round(float(np.mean(turns > 90.0)) * 100, 1)

        # --- nearest-neighbour distances (1-NN, unbounded) ------------------
        nn_dists = nearest_neighbour_unbounded(xs, ys) if do_distances else []
        nn_dists = [d for d in nn_dists if not math.isnan(d)]

        res = {
            "group": name,
            "n": len(xs),
            "n_edges": len(pairs),
            "n_turns": len(turns),
            **res_turn,
        }

        # 1) a priori golden-bearing fraction, WIDE +-3 band
        obs = frac_within(bears, GOLDEN_ANGLE)
        res["bearing_golden_frac"] = round(obs, 4)
        null = [
            frac_within(NRNG.uniform(0.0, 180.0, len(pairs)), GOLDEN_ANGLE) for _ in range(N_MC)
        ]
        res["bearing_golden_null_mean"] = round(float(np.mean(null)), 4)
        res["bearing_golden_null_sd"] = round(float(np.std(null)), 4)
        res["bearing_golden_p"] = round(mc_p(obs, null), 4)

        # 1b) TIGHT core band +-1 deg (the decisive test: the +-3 window is
        #     wide enough to catch diffuse anisotropy that is not golden)
        core = frac_within_tol(bears, GOLDEN_ANGLE, 1.0)
        core_comp = frac_within_tol(bears, 180.0 - GOLDEN_ANGLE, 1.0)
        res["bearing_golden_core1"] = round(core, 4)
        res["bearing_golden_comp1"] = round(core_comp, 4)
        null_core = [
            frac_within_tol(NRNG.uniform(0.0, 180.0, len(pairs)), GOLDEN_ANGLE, 1.0)
            for _ in range(N_MC)
        ]
        res["bearing_golden_core1_p"] = round(mc_p(core, null_core), 4)

        # 2) data-driven peak alignment angle (cherry-pick guard)
        best_angle, best_frac = peak_over_grid(bears)
        res["bearing_peak_angle"] = best_angle
        res["bearing_peak_frac"] = round(best_frac, 4)
        peak_null = []
        for _ in range(N_MC):
            b = NRNG.uniform(0.0, 180.0, len(pairs))
            _, pf = peak_over_grid(b)
            peak_null.append(pf)
        res["bearing_peak_p"] = round(mc_p(best_frac, peak_null), 4)

        # 3) spiral turns at 137.5 / 42.5 (both chiralities)
        obs_turn = frac_within(turns, GOLDEN_ANGLE) + frac_within(
            turns, 180.0 - GOLDEN_ANGLE
        )  # disjoint bands
        turn_null = []
        for _ in range(N_MC):
            t = NRNG.uniform(0.0, 180.0, len(turns))
            turn_null.append(frac_within(t, GOLDEN_ANGLE) + frac_within(t, 180.0 - GOLDEN_ANGLE))
        res["turn_golden_frac"] = round(obs_turn, 4)
        res["turn_golden_null_mean"] = round(float(np.mean(turn_null)), 4)
        res["turn_golden_p"] = round(mc_p(obs_turn, turn_null), 4)

        # 4) Fibonacci distances vs sham values (worship only)
        if do_distances and nn_dists:
            obs_fib = fib_frac(nn_dists)
            obs_sham = sham_frac(nn_dists)
            res["nn_fib_frac"] = round(obs_fib, 4)
            res["nn_sham_frac"] = round(obs_sham, 4)
            res["nn_fib_vs_sham"] = round(obs_fib - obs_sham, 4)
            res["nn_dist_median_m"] = round(float(np.median(nn_dists)), 1)
            fib_null = []
            for _ in range(min(N_MC, 150)):
                rx = NRNG.uniform(xs.min(), xs.max(), len(xs))
                ry = NRNG.uniform(ys.min(), ys.max(), len(ys))
                d = nearest_neighbour_unbounded(rx, ry)
                d = [x for x in d if not math.isnan(x)]
                fib_null.append(fib_frac(d))
            res["nn_fib_poisson_null_mean"] = round(float(np.mean(fib_null)), 4)
            res["nn_fib_poisson_p"] = round(mc_p(obs_fib, fib_null), 4)
        else:
            res["nn_fib_frac"] = ""
            res["nn_sham_frac"] = ""
            res["nn_fib_vs_sham"] = ""
            res["nn_dist_median_m"] = ""
            res["nn_fib_poisson_null_mean"] = ""
            res["nn_fib_poisson_p"] = ""

        print(f"[pp] {name}: n={len(xs)} edges={len(pairs)} turns={len(turns)}", flush=True)
        print(
            f"     bearing@137.5+-3: {obs * 100:.2f}% "
            f"(null {np.mean(null) * 100:.2f}% "
            f"sd {np.std(null) * 100:.2f}%) p={res['bearing_golden_p']}",
            flush=True,
        )
        print(
            f"     bearing@137.5+-1 core: {core * 100:.2f}% "
            f"(comp 42.5: {core_comp * 100:.2f}%) "
            f"p={res['bearing_golden_core1_p']}",
            flush=True,
        )
        print(
            f"     peak alignment: {best_angle} deg at "
            f"{best_frac * 100:.2f}% (MC p={res['bearing_peak_p']})",
            flush=True,
        )
        print(
            f"     spiral turns@137.5/42.5: {obs_turn * 100:.2f}% "
            f"(null {np.mean(turn_null) * 100:.2f}%) "
            f"p={res['turn_golden_p']}",
            flush=True,
        )
        if do_distances and nn_dists:
            print(
                f"     NN fib: {obs_fib * 100:.2f}% vs sham "
                f"{obs_sham * 100:.2f}% vs poisson-null "
                f"{np.mean(fib_null) * 100:.2f}% (p={res['nn_fib_poisson_p']})",
                flush=True,
            )
        return res, bears, turns, nn_dists

    results = []
    turn_csv_rows = []
    control_sample_n = max(20, min(len(controls), max(1, len(controls) // 10)))
    sampled_controls = RNG.sample(controls, control_sample_n) if controls else []
    for name, rs, do_dist in (("worship", worship, True), ("controls", sampled_controls, False)):
        r = analyse(name, rs, do_dist)
        if r is None:
            continue
        res, _bears, turns, _nn_dists = r
        results.append(res)
        if name == "worship":
            for i, t in enumerate(turns):
                turn_csv_rows.append({"turn_id": i, "turn_deg": round(t, 2)})
        res["seed"] = args.seed
        res["mc_iterations"] = N_MC
        res["control_sample_n"] = control_sample_n

    # ---- write -------------------------------------------------------------
    fields = [
        "group",
        "n",
        "n_edges",
        "n_turns",
        "turn_median_deg",
        "turn_lt30_pct",
        "turn_gt90_pct",
        "bearing_golden_frac",
        "bearing_golden_null_mean",
        "bearing_golden_null_sd",
        "bearing_golden_p",
        "bearing_golden_core1",
        "bearing_golden_comp1",
        "bearing_golden_core1_p",
        "bearing_peak_angle",
        "bearing_peak_frac",
        "bearing_peak_p",
        "turn_golden_frac",
        "turn_golden_null_mean",
        "turn_golden_p",
        "nn_fib_frac",
        "nn_sham_frac",
        "nn_fib_vs_sham",
        "nn_dist_median_m",
        "nn_fib_poisson_null_mean",
        "nn_fib_poisson_p",
        "seed",
        "mc_iterations",
        "control_sample_n",
    ]
    atomic_write_csv(OUT / "point_pattern.csv", fields, results, extrasaction="ignore")
    atomic_write_csv(OUT / "point_pattern_turns.csv", ["turn_id", "turn_deg"], turn_csv_rows)
    print(
        f"[pp] wrote point_pattern.csv ({len(results)} groups) "
        f"and point_pattern_turns.csv ({len(turn_csv_rows)} turns)"
    )


if __name__ == "__main__":
    main()
