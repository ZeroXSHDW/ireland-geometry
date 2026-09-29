#!/usr/bin/env python3
"""Expanded spatial statistics for footprint signals.

This stage adds three diagnostics that answer different questions:

* Ripley-style K/L summaries ask whether points cluster more than a spatial
  point cloud at several radii. A translation edge correction is used on the
  sampled rectangular observation window.
* Moran's I asks whether the binary golden-angle flag is spatially clustered
  on a k-nearest-neighbour graph; permutation p-values preserve the observed
  locations and flag prevalence.
* County-preserving permutations ask whether a target/control difference
  remains after labels are shuffled within NIAH counties. This is limited to
  buildings with a valid NIAH county and is not a substitute for full county
  boundaries or mapping-age data.

Reads: output/analysis_results.csv, output/niah_join.csv
Writes: output/ripley.csv, output/moran.csv, output/county_permutation.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from collections import defaultdict

import numpy as np
from shapely.geometry import Point
from shapely.strtree import STRtree

try:
    from runtime import atomic_write_csv, project_output_tree_path
    from stats import apply_holm
except ImportError:
    from scripts.runtime import atomic_write_csv, project_output_tree_path
    from scripts.stats import apply_holm


RADII_M = (100.0, 250.0, 500.0, 1000.0, 2000.0, 5000.0)
SAMPLE_LIMIT = 1500
MORAN_LIMIT = 900
MIN_REPORTABLE_P = 1e-300


def project(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    lat0 = math.radians(sum(float(row["lat"]) for row in rows) / len(rows))
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    xs = np.asarray([float(row["lon"]) * kx for row in rows])
    ys = np.asarray([float(row["lat"]) * ky for row in rows])
    return xs, ys


def deterministic_sample(rows: list[dict], limit: int, seed: int) -> list[dict]:
    if len(rows) <= limit:
        return sorted(rows, key=lambda row: row["osm_id"])
    rng = random.Random(seed)
    return sorted(rng.sample(rows, limit), key=lambda row: row["osm_id"])


def ripley(rows: list[dict], group: str, seed: int, limit: int = SAMPLE_LIMIT) -> list[dict]:
    sample = deterministic_sample(rows, limit, seed)
    if len(sample) < 20:
        return []
    xs, ys = project(sample)
    width = max(float(xs.max() - xs.min()), 1.0)
    height = max(float(ys.max() - ys.min()), 1.0)
    window_area = width * height
    points = [Point(float(x), float(y)) for x, y in zip(xs, ys)]
    tree = STRtree(points)
    n = len(points)
    output = []
    for radius in RADII_M:
        weighted_pairs = 0.0
        for i, point in enumerate(points):
            for candidate in tree.query(point.buffer(radius)):
                j = int(candidate)
                if i == j:
                    continue
                dx = abs(float(xs[j] - xs[i]))
                dy = abs(float(ys[j] - ys[i]))
                if dx * dx + dy * dy > radius * radius:
                    continue
                overlap = max(width - dx, 0.0) * max(height - dy, 0.0)
                if overlap > 0:
                    weighted_pairs += window_area / overlap
        k_value = window_area * weighted_pairs / (n * max(n - 1, 1))
        l_value = math.sqrt(max(k_value, 0.0) / math.pi)
        output.append(
            {
                "group": group,
                "radius_m": radius,
                "n": n,
                "k_m2": round(k_value, 4),
                "l_m": round(l_value, 4),
                "l_minus_r_m": round(l_value - radius, 4),
                "edge_method": "translation correction on sampled bounding rectangle",
                "sample_seed": seed,
                "sample_limit": limit,
            }
        )
    return output


def _moran_value(values: np.ndarray, edges: list[tuple[int, int]]) -> float:
    n = len(values)
    mean = float(values.mean())
    centered = values - mean
    denominator = float(np.sum(centered * centered))
    if denominator <= 0 or not edges:
        return 0.0
    numerator = sum(float(centered[i] * centered[j]) for i, j in edges)
    return n / len(edges) * numerator / denominator


def moran(rows: list[dict], group: str, seed: int, iterations: int, limit: int = MORAN_LIMIT) -> dict | None:
    sample = deterministic_sample(rows, limit, seed)
    if len(sample) < 20:
        return None
    xs, ys = project(sample)
    coords = np.column_stack((xs, ys))
    distances = np.sqrt(((coords[:, None, :] - coords[None, :, :]) ** 2).sum(axis=2))
    k = min(8, len(sample) - 1)
    edges_set: set[tuple[int, int]] = set()
    for i in range(len(sample)):
        neighbours = np.argsort(distances[i])[1 : k + 1]
        for j in neighbours:
            a, b = sorted((i, int(j)))
            edges_set.add((a, b))
    edges = sorted(edges_set)
    values = np.asarray([1.0 if row.get("has_golden_angle") == "1" else 0.0 for row in sample])
    observed = _moran_value(values, edges)
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(max(50, iterations)):
        null.append(_moran_value(rng.permutation(values), edges))
    p_value = (1 + sum(value >= observed for value in null)) / (1 + len(null))
    return {
        "group": group,
        "n": len(sample),
        "edges": len(edges),
        "k_neighbours": k,
        "golden_angle_rate": round(float(values.mean()) * 100.0, 4),
        "moran_i": round(observed, 6),
        "null_mean": round(float(np.mean(null)), 6),
        "null_sd": round(float(np.std(null)), 6),
        "p_value": max(MIN_REPORTABLE_P, p_value),
        "permutations": len(null),
        "method": "k-nearest-neighbour Moran's I with prevalence-preserving permutations",
        "direction": 1 if observed > float(np.mean(null)) else -1,
        "test_family": "spatial_moran",
    }


def county_permutations(
    rows: list[dict], joins: list[dict], group: str, signal: str, seed: int, iterations: int
) -> dict | None:
    by_id = {row["osm_id"]: row for row in rows}
    joined = []
    for join in joins:
        row = by_id.get(join.get("osm_id"))
        county = (join.get("county") or "").strip()
        if row is None or not county:
            continue
        if row.get("is_control") == "1" or row.get("group") == group:
            joined.append((county, row, join))
    by_county: dict[str, list[tuple[dict, int]]] = defaultdict(list)
    for county, row, _join in joined:
        value = int(row.get("has_golden_angle") == "1") if signal == "golden_angle" else int(float(row.get("golden_ratio_err_pct") or 999) <= 3.0)
        label = 0 if row.get("is_control") == "1" else 1
        by_county[county].append((row, value if label else -value - 2))
    strata = {}
    for county, values in by_county.items():
        targets = [(row, abs(encoded)) for row, encoded in values if encoded >= 0]
        controls = [(row, encoded) for row, encoded in values if encoded < 0]
        if targets and controls:
            # Store outcome and label separately; the negative encoding above
            # avoids a second wide record structure while keeping 0 outcomes.
            strata[county] = {
                "target": [int(row.get("has_golden_angle") == "1") if signal == "golden_angle" else int(float(row.get("golden_ratio_err_pct") or 999) <= 3.0) for row, _ in targets],
                "control": [int(row.get("has_golden_angle") == "1") if signal == "golden_angle" else int(float(row.get("golden_ratio_err_pct") or 999) <= 3.0) for row, _ in controls],
            }
    if not strata:
        return None
    target_n = sum(len(value["target"]) for value in strata.values())
    control_n = sum(len(value["control"]) for value in strata.values())
    target_successes = sum(sum(value["target"]) for value in strata.values())
    control_successes = sum(sum(value["control"]) for value in strata.values())
    observed = target_successes / target_n - control_successes / control_n
    rng = random.Random(seed)
    null = []
    for _ in range(max(50, iterations)):
        target_total = control_total = target_hits = control_hits = 0
        for value in strata.values():
            labels = [1] * len(value["target"]) + [0] * len(value["control"])
            outcomes = value["target"] + value["control"]
            rng.shuffle(labels)
            target_total += len(value["target"])
            control_total += len(value["control"])
            target_hits += sum(outcome for outcome, label in zip(outcomes, labels) if label)
            control_hits += sum(outcome for outcome, label in zip(outcomes, labels) if not label)
        null.append(target_hits / target_total - control_hits / control_total)
    p_value = (1 + sum(value >= observed for value in null)) / (1 + len(null))
    return {
        "group": group,
        "signal": signal,
        "counties": len(strata),
        "target_n": target_n,
        "control_n": control_n,
        "target_rate": round(target_successes / target_n * 100.0, 4),
        "control_rate": round(control_successes / control_n * 100.0, 4),
        "observed_difference_pp": round(observed * 100.0, 4),
        "null_mean_difference_pp": round(float(np.mean(null)) * 100.0, 4),
        "null_sd_difference_pp": round(float(np.std(null)) * 100.0, 4),
        "p_value": max(MIN_REPORTABLE_P, p_value),
        "permutations": len(null),
        "method": "county-stratified label permutation",
        "direction": 1 if observed > 0 else -1 if observed < 0 else 0,
        "test_family": "county_permutation",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--mc", type=int, default=300)
    args = parser.parse_args(argv)
    out = project_output_tree_path(args.out_dir)
    analysis_path = out / "analysis_results.csv"
    if not analysis_path.exists():
        raise SystemExit(f"Missing {analysis_path}. Run analyze.py first.")
    with analysis_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    joins_path = out / "niah_join.csv"
    joins = []
    if joins_path.exists():
        with joins_path.open(newline="", encoding="utf-8") as fh:
            joins = list(csv.DictReader(fh))
    groups = [(name, [row for row in rows if row.get("group") == name]) for name in ("worship", "government", "historic", "civic", "other")]
    groups.append(("controls", [row for row in rows if row.get("is_control") == "1"]))
    ripley_rows = []
    moran_rows = []
    for index, (name, group_rows) in enumerate(groups):
        ripley_rows.extend(ripley(group_rows, name, args.seed + index))
        result = moran(group_rows, name, args.seed + 100 + index, args.mc)
        if result:
            moran_rows.append(result)
    county_rows = []
    for index, group in enumerate(("worship", "government", "historic", "civic")):
        for signal in ("golden_angle", "golden_ratio"):
            result = county_permutations(rows, joins, group, signal, args.seed + 200 + index, args.mc)
            if result:
                county_rows.append(result)
    moran_rows = apply_holm(moran_rows)
    county_rows = apply_holm(county_rows)
    atomic_write_csv(
        out / "ripley.csv",
        ["group", "radius_m", "n", "k_m2", "l_m", "l_minus_r_m", "edge_method", "sample_seed", "sample_limit"],
        ripley_rows,
    )
    atomic_write_csv(
        out / "moran.csv",
        ["group", "n", "edges", "k_neighbours", "golden_angle_rate", "moran_i", "null_mean", "null_sd", "p_value", "p_adjusted", "permutations", "method", "direction", "test_family", "verdict"],
        moran_rows,
    )
    atomic_write_csv(
        out / "county_permutation.csv",
        ["group", "signal", "counties", "target_n", "control_n", "target_rate", "control_rate", "observed_difference_pp", "null_mean_difference_pp", "null_sd_difference_pp", "p_value", "p_adjusted", "permutations", "method", "direction", "test_family", "verdict"],
        county_rows,
    )
    print(f"[spatial] wrote {len(ripley_rows)} Ripley rows, {len(moran_rows)} Moran rows, {len(county_rows)} county permutations")


if __name__ == "__main__":
    main()
