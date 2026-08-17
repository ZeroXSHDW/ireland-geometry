#!/usr/bin/env python3
"""Spatially matched controls and hierarchical sensitivity analysis.

The original analysis compares targets with one large empirical control pool.
That is useful as a first screen, but it can confound geography, mapping
density, and footprint scale.  This stage adds two pre-specified sensitivity
analyses:

* deterministic nearest-neighbour controls within a local latitude/longitude
  grid, prioritising geographic distance and log-area similarity;
* a random-effects, stratified log-odds model across the same local grid and
  area strata.  The latter is a small dependency-free meta-analytic analogue
  of a random-intercept logistic model, with an explicit between-stratum
  variance estimate (DerSimonian--Laird).

The stage deliberately does not call these results causal.  Controls are
matched on observable footprint and location proxies only; OSM mapping age,
settlement type, construction era, and survey completeness still need better
independent data.

Reads:  output/analysis_results.csv
Writes: output/matched_controls.csv
        output/matched_control_summary.csv
        output/matched_significance.csv
        output/hierarchical_model.csv
"""

from __future__ import annotations

import argparse
import csv
import heapq
import math
from collections import Counter, defaultdict
from pathlib import Path

try:
    from runtime import atomic_write_csv, project_path
    from stats import apply_holm, p_format
except ImportError:
    from scripts.runtime import atomic_write_csv, project_path
    from scripts.stats import apply_holm, p_format


GRID_DEG = 0.1
DEFAULT_K = 3
DEFAULT_MAX_DISTANCE_M = 15_000.0
MIN_REPORTABLE_P = 1e-300
Z95 = 1.959963984540054

SIGNALS = {
    "golden_angle": lambda row: row.get("has_golden_angle") == "1",
    "golden_ratio": lambda row: _number(row.get("golden_ratio_err_pct"), 999.0) <= 3.0,
    "fib_ratio": lambda row: _number(row.get("fib_ratio_err_pct"), 999.0) <= 2.0,
}


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cell(lat: float, lon: float, grid_deg: float = GRID_DEG) -> tuple[int, int]:
    return math.floor(lat / grid_deg), math.floor(lon / grid_deg)


def _distance_m(a: dict, b: dict) -> float:
    lat0 = math.radians((float(a["lat"]) + float(b["lat"])) / 2.0)
    dx = (float(a["lon"]) - float(b["lon"])) * 111320.0 * math.cos(lat0)
    dy = (float(a["lat"]) - float(b["lat"])) * 110540.0
    return math.hypot(dx, dy)


def _candidate_offsets(max_distance_m: float, grid_deg: float) -> int:
    cell_m = min(111320.0 * grid_deg, 110540.0 * grid_deg)
    return max(1, math.ceil(max_distance_m / cell_m) + 1)


class _KDNode:
    __slots__ = ("axis", "index", "left", "right")

    def __init__(self, index: int, axis: int, left=None, right=None):
        self.index = index
        self.axis = axis
        self.left = left
        self.right = right


def _build_kdtree(indices: list[int], coords: list[tuple[float, float]], depth: int = 0):
    if not indices:
        return None
    axis = depth % 2
    indices.sort(key=lambda index: coords[index][axis])
    middle = len(indices) // 2
    return _KDNode(
        indices[middle],
        axis,
        _build_kdtree(indices[:middle], coords, depth + 1),
        _build_kdtree(indices[middle + 1 :], coords, depth + 1),
    )


def _nearest_k(
    node: _KDNode | None,
    point: tuple[float, float],
    coords: list[tuple[float, float]],
    heap: list[tuple[float, int]],
    k: int,
) -> None:
    if node is None:
        return
    index = node.index
    dx = coords[index][0] - point[0]
    dy = coords[index][1] - point[1]
    distance_sq = dx * dx + dy * dy
    item = (-distance_sq, index)
    if len(heap) < k:
        heapq.heappush(heap, item)
    elif distance_sq < -heap[0][0]:
        heapq.heapreplace(heap, item)
    axis_gap = point[node.axis] - coords[index][node.axis]
    near, far = (node.left, node.right) if axis_gap <= 0 else (node.right, node.left)
    _nearest_k(near, point, coords, heap, k)
    if len(heap) < k or axis_gap * axis_gap < -heap[0][0]:
        _nearest_k(far, point, coords, heap, k)


def build_matches(
    rows: list[dict],
    *,
    k: int = DEFAULT_K,
    grid_deg: float = GRID_DEG,
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
) -> list[dict]:
    """Return deterministic target-to-control nearest-neighbour matches.

    Matching is with replacement.  This is intentional: a control can be the
    best local comparator for more than one target, and reuse is reported in
    the summary rather than silently pretending observations are independent.
    """
    if k < 1:
        raise ValueError("k must be positive")
    controls = [row for row in rows if row.get("is_control") == "1"]
    targets = [row for row in rows if row.get("is_control") == "0"]
    # A small KD-tree is built per coarse log-area band.  It avoids scanning
    # every control in a dense urban grid while still returning exact spatial
    # nearest neighbours for each area band.
    lat_scale = 111320.0 * math.cos(math.radians(53.3))
    coords = [
        (float(row["lon"]) * lat_scale, float(row["lat"]) * 110540.0)
        for row in controls
    ]
    by_area_bin: dict[int, list[int]] = defaultdict(list)
    for index, control in enumerate(controls):
        area = max(_number(control.get("area_m2"), 1.0), 1e-6)
        by_area_bin[math.floor(math.log10(area) * 2.0)].append(index)
    trees = {
        area_bin: _build_kdtree(indices.copy(), coords)
        for area_bin, indices in by_area_bin.items()
    }
    matches: list[dict] = []
    for target in sorted(targets, key=lambda row: row["osm_id"]):
        lat = float(target["lat"])
        lon = float(target["lon"])
        target_cell = _cell(lat, lon, grid_deg)
        candidates: list[tuple[float, dict, float, float]] = []
        target_area = max(_number(target.get("area_m2"), 1.0), 1e-6)
        target_log_area = math.log(target_area)
        target_bin = math.floor(math.log10(target_area) * 2.0)
        target_xy = (lon * lat_scale, lat * 110540.0)
        candidate_indices: set[int] = set()
        for area_bin in range(target_bin - 2, target_bin + 3):
            tree = trees.get(area_bin)
            if tree is None:
                continue
            heap: list[tuple[float, int]] = []
            _nearest_k(tree, target_xy, coords, heap, 24)
            candidate_indices.update(index for _distance_sq, index in heap)
        for index in candidate_indices:
            control = controls[index]
            distance = _distance_m(target, control)
            if distance > max_distance_m:
                continue
            control_area = max(_number(control.get("area_m2"), 1.0), 1e-6)
            log_gap = abs(target_log_area - math.log(control_area))
            # A 10 km spatial difference and a 10x area difference each
            # contribute roughly one unit of matching cost.
            cost = distance / max_distance_m + log_gap / math.log(10.0)
            candidates.append((cost, control, distance, log_gap))
        candidates.sort(key=lambda item: (item[0], item[1]["osm_id"]))
        chosen = candidates[:k]
        for rank, (cost, control, distance, log_gap) in enumerate(chosen, 1):
            control_area = max(_number(control.get("area_m2"), 1.0), 1e-6)
            matches.append(
                {
                    "match_id": f"{target['osm_id']}::{rank}",
                    "target_osm_id": target["osm_id"],
                    "target_group": target.get("group", "other"),
                    "control_osm_id": control["osm_id"],
                    "target_cell": f"{target_cell[0]}:{target_cell[1]}",
                    "control_cell": (
                        f"{_cell(float(control['lat']), float(control['lon']), grid_deg)[0]}:"
                        f"{_cell(float(control['lat']), float(control['lon']), grid_deg)[1]}"
                    ),
                    "distance_m": round(distance, 2),
                    "area_ratio": round(max(target_area, control_area) / min(target_area, control_area), 4),
                    "log_area_gap": round(log_gap, 6),
                    "cost": round(cost, 6),
                    "rank": rank,
                    "k": k,
                    "replacement_allowed": 1,
                }
            )
    return matches


def match_summary(matches: list[dict]) -> list[dict]:
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in matches:
        by_group[row["target_group"]].append(row)
    out = []
    for group in sorted(by_group):
        values = by_group[group]
        targets = {row["target_osm_id"] for row in values}
        reuse = Counter(row["control_osm_id"] for row in values)
        distances = sorted(float(row["distance_m"]) for row in values)
        ratios = sorted(float(row["area_ratio"]) for row in values)
        median = lambda xs: xs[len(xs) // 2] if xs else 0.0
        out.append(
            {
                "target_group": group,
                "target_n": len(targets),
                "matched_pair_n": len(values),
                "median_distance_m": round(median(distances), 2),
                "p90_distance_m": round(distances[min(len(distances) - 1, int(len(distances) * 0.9))], 2),
                "median_area_ratio": round(median(ratios), 4),
                "same_cell_pct": round(
                    100.0
                    * sum(row["target_cell"] == row["control_cell"] for row in values)
                    / len(values),
                    2,
                ),
                "unique_controls": len(reuse),
                "reused_controls": sum(count > 1 for count in reuse.values()),
                "max_control_reuse": max(reuse.values(), default=0),
            }
        )
    return out


def matched_significance(rows: list[dict], matches: list[dict]) -> list[dict]:
    by_id = {row["osm_id"]: row for row in rows}
    sets: dict[str, list[dict]] = defaultdict(list)
    for match in matches:
        target = by_id.get(match["target_osm_id"])
        control = by_id.get(match["control_osm_id"])
        if target is not None and control is not None:
            sets[match["target_osm_id"]].append({"target": target, "control": control})
    out = []
    for group in sorted({row.get("target_group", "other") for row in matches}):
        group_sets = {
            target_id: pairs
            for target_id, pairs in sets.items()
            if pairs and by_id[target_id].get("group") == group
        }
        for signal, predicate in SIGNALS.items():
            diffs = []
            target_successes = 0
            control_successes = 0
            pair_n = 0
            for target_id in sorted(group_sets):
                pairs = group_sets[target_id]
                t = int(predicate(by_id[target_id]))
                c_rate = sum(int(predicate(pair["control"])) for pair in pairs) / len(pairs)
                diffs.append(t - c_rate)
                target_successes += t
                control_successes += sum(int(predicate(pair["control"])) for pair in pairs)
                pair_n += len(pairs)
            if not diffs:
                continue
            mean = sum(diffs) / len(diffs)
            if len(diffs) > 1:
                variance = sum((value - mean) ** 2 for value in diffs) / (len(diffs) - 1)
                se = math.sqrt(variance / len(diffs))
            else:
                se = 0.0
            if se > 0:
                z = mean / se
                p_value = max(MIN_REPORTABLE_P, math.erfc(abs(z) / math.sqrt(2.0)))
            else:
                z = 0.0 if mean == 0 else math.copysign(float("inf"), mean)
                p_value = 1.0 if mean == 0 else MIN_REPORTABLE_P
            out.append(
                {
                    "signal": signal,
                    "target_group": group,
                    "target_set_n": len(diffs),
                    "matched_pair_n": pair_n,
                    "target_successes": target_successes,
                    "control_successes": control_successes,
                    "target_rate": round(target_successes / len(diffs) * 100.0, 4),
                    "matched_control_rate": round(control_successes / pair_n * 100.0, 4),
                    "risk_difference_pp": round(mean * 100.0, 4),
                    "ci_low_pp": round((mean - Z95 * se) * 100.0, 4),
                    "ci_high_pp": round((mean + Z95 * se) * 100.0, 4),
                    "z": round(z, 6) if math.isfinite(z) else z,
                    "p_value": p_value,
                    "method": "matched-set mean difference with replacement",
                    "direction": 1 if mean > 0 else -1 if mean < 0 else 0,
                    "test_family": "matched_sensitivity",
                }
            )
    return apply_holm(out)


def _stratum(row: dict, grid_deg: float) -> str:
    area = max(_number(row.get("area_m2"), 1.0), 1e-6)
    area_bin = math.floor(math.log10(area) * 2.0)
    cell = _cell(float(row["lat"]), float(row["lon"]), grid_deg)
    return f"{cell[0]}:{cell[1]}:{area_bin}"


def _random_effects(items: list[tuple[int, int, int, int]]) -> dict:
    """Estimate a random-effects log odds ratio from 2x2 stratum tables."""
    estimates = []
    for a, b, c, d in items:
        aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
        estimate = math.log((aa * dd) / (bb * cc))
        variance = 1 / aa + 1 / bb + 1 / cc + 1 / dd
        estimates.append((estimate, variance))
    weights = [1.0 / variance for _, variance in estimates]
    weight_sum = sum(weights)
    fixed = sum(weight * estimate for weight, (estimate, _) in zip(weights, estimates)) / weight_sum
    q = sum(weight * (estimate - fixed) ** 2 for weight, (estimate, _) in zip(weights, estimates))
    k = len(estimates)
    sum_w_sq = sum(weight * weight for weight in weights)
    denominator = weight_sum - sum_w_sq / weight_sum if weight_sum else 0.0
    tau2 = max(0.0, (q - max(0, k - 1)) / denominator) if denominator > 0 else 0.0
    random_weights = [1.0 / (variance + tau2) for _, variance in estimates]
    random_sum = sum(random_weights)
    pooled = sum(weight * estimate for weight, (estimate, _) in zip(random_weights, estimates)) / random_sum
    se = math.sqrt(1.0 / random_sum)
    z = pooled / se if se > 0 else 0.0
    p_value = max(MIN_REPORTABLE_P, math.erfc(abs(z) / math.sqrt(2.0)))
    return {
        "fixed_log_or": fixed,
        "tau2": tau2,
        "random_log_or": pooled,
        "random_or": math.exp(pooled),
        "random_or_ci_low": math.exp(pooled - Z95 * se),
        "random_or_ci_high": math.exp(pooled + Z95 * se),
        "z": z,
        "p_value": p_value,
    }


def hierarchical_model(rows: list[dict], *, grid_deg: float = GRID_DEG) -> list[dict]:
    """Fit a random-effects stratified log-odds model for each signal/group."""
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("is_control") == "1":
            continue
        by_group[row.get("group", "other")].append(row)
    out = []
    for group in sorted(by_group):
        strata: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"target": [], "control": []})
        for row in rows:
            key = _stratum(row, grid_deg)
            side = "control" if row.get("is_control") == "1" else "target" if row.get("group") == group else "other"
            if side != "other":
                strata[key][side].append(row)
        for signal, predicate in SIGNALS.items():
            tables = []
            target_n = control_n = target_successes = control_successes = 0
            for key in sorted(strata):
                target_rows = strata[key]["target"]
                control_rows = strata[key]["control"]
                if len(target_rows) < 2 or len(control_rows) < 2:
                    continue
                a = sum(int(predicate(row)) for row in target_rows)
                b = len(target_rows) - a
                c = sum(int(predicate(row)) for row in control_rows)
                d = len(control_rows) - c
                tables.append((a, b, c, d))
                target_n += len(target_rows)
                control_n += len(control_rows)
                target_successes += a
                control_successes += c
            if not tables:
                continue
            result = _random_effects(tables)
            out.append(
                {
                    "signal": signal,
                    "target_group": group,
                    "strata_n": len(tables),
                    "target_n": target_n,
                    "control_n": control_n,
                    "target_successes": target_successes,
                    "control_successes": control_successes,
                    "target_rate": round(target_successes / target_n * 100.0, 4),
                    "control_rate": round(control_successes / control_n * 100.0, 4),
                    "fixed_log_or": round(result["fixed_log_or"], 6),
                    "tau2": round(result["tau2"], 6),
                    "random_log_or": round(result["random_log_or"], 6),
                    "random_or": round(result["random_or"], 6),
                    "or_ci_low": round(result["random_or_ci_low"], 6),
                    "or_ci_high": round(result["random_or_ci_high"], 6),
                    "z": round(result["z"], 6),
                    "p_value": result["p_value"],
                    "method": "random-effects stratified log-odds meta-analysis",
                    "direction": 1 if result["random_log_or"] > 0 else -1 if result["random_log_or"] < 0 else 0,
                    "test_family": "hierarchical_sensitivity",
                }
            )
    return apply_holm(out)


def _write_outputs(out: Path, matches: list[dict], matched: list[dict], hierarchical: list[dict]) -> None:
    atomic_write_csv(
        out / "matched_controls.csv",
        [
            "match_id",
            "target_osm_id",
            "target_group",
            "control_osm_id",
            "target_cell",
            "control_cell",
            "distance_m",
            "area_ratio",
            "log_area_gap",
            "cost",
            "rank",
            "k",
            "replacement_allowed",
        ],
        matches,
    )
    summaries = match_summary(matches)
    atomic_write_csv(
        out / "matched_control_summary.csv",
        [
            "target_group",
            "target_n",
            "matched_pair_n",
            "median_distance_m",
            "p90_distance_m",
            "median_area_ratio",
            "same_cell_pct",
            "unique_controls",
            "reused_controls",
            "max_control_reuse",
        ],
        summaries,
    )
    atomic_write_csv(
        out / "matched_significance.csv",
        [
            "signal",
            "target_group",
            "target_set_n",
            "matched_pair_n",
            "target_successes",
            "control_successes",
            "target_rate",
            "matched_control_rate",
            "risk_difference_pp",
            "ci_low_pp",
            "ci_high_pp",
            "z",
            "p_value",
            "p_adjusted",
            "method",
            "direction",
            "test_family",
            "verdict",
        ],
        matched,
        extrasaction="ignore",
    )
    atomic_write_csv(
        out / "hierarchical_model.csv",
        [
            "signal",
            "target_group",
            "strata_n",
            "target_n",
            "control_n",
            "target_successes",
            "control_successes",
            "target_rate",
            "control_rate",
            "fixed_log_or",
            "tau2",
            "random_log_or",
            "random_or",
            "or_ci_low",
            "or_ci_high",
            "z",
            "p_value",
            "p_adjusted",
            "method",
            "direction",
            "test_family",
            "verdict",
        ],
        hierarchical,
        extrasaction="ignore",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="controls per target (default: 3)")
    parser.add_argument("--grid-deg", type=float, default=GRID_DEG)
    parser.add_argument("--max-distance-m", type=float, default=DEFAULT_MAX_DISTANCE_M)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    results_path = out / "analysis_results.csv"
    if not results_path.exists():
        raise SystemExit(f"Missing {results_path}. Run analyze.py first.")
    rows = list(csv.DictReader(results_path.open(newline="", encoding="utf-8")))
    matches = build_matches(
        rows,
        k=args.k,
        grid_deg=args.grid_deg,
        max_distance_m=args.max_distance_m,
    )
    matched = matched_significance(rows, matches)
    hierarchical = hierarchical_model(rows, grid_deg=args.grid_deg)
    _write_outputs(out, matches, matched, hierarchical)
    print(f"[sensitivity] matched {len(matches):,} target-control pairs", flush=True)
    print(f"[sensitivity] wrote {len(matched)} matched tests and {len(hierarchical)} hierarchical tests")
    for row in matched:
        print(
            f"  matched {row['signal']:13s} {row['target_group']:12s} "
            f"diff={row['risk_difference_pp']:7.3f} pp p={p_format(row['p_value'])} "
            f"Holm={p_format(row['p_adjusted'])} {row['verdict']}"
        )


if __name__ == "__main__":
    main()
