#!/usr/bin/env python3
"""Run the stricter validation match with controls used at most once.

This is a deterministic greedy full-matching sensitivity analysis.  Candidate
pairs are generated from the spatial/area matcher, then restricted to the
same observed settlement and mapping-density strata (and the same county when
an administrative boundary layer is genuinely available).  Construction era
is compared when it is observed for both records; missing era is retained as
missing rather than imputed.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path

try:
    from runtime import atomic_write_csv, project_output_tree_path
    from sensitivity import build_matches, matched_significance
except ImportError:
    from scripts.runtime import atomic_write_csv, project_output_tree_path
    from scripts.sensitivity import build_matches, matched_significance


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def era_value(nih_row: dict[str, str]) -> str:
    return (nih_row.get("century") or nih_row.get("date_mid") or "").strip()


def covariate_value(row: dict[str, str], covars: dict[str, str], niah: dict[str, str]) -> dict[str, str]:
    boundary_status = covars.get("boundary_status", "not_provided")
    settlement_status = covars.get("settlement_status", "fallback_osm_address")
    county = covars.get("county", "") if boundary_status == "provided" else ""
    settlement = covars.get("settlement_class", "unknown") if settlement_status else "unknown"
    if not settlement:
        settlement = "unknown"
    density = covars.get("mapping_density_bin", "unknown") or "unknown"
    era = era_value(niah)
    # County and settlement are hard strata only when their source is
    # comparable across records.  NIAH county alone must not force all
    # unmatched controls into an artificial no-match bucket.
    return {
        "county": county or "unknown",
        "settlement_class": settlement,
        "mapping_density_bin": density,
        "era": era or "unknown",
        "era_observed": str(int(bool(era))),
    }


def hard_stratum(values: dict[str, str]) -> str:
    return "|".join(
        f"{key}={values[key]}"
        for key in ("county", "settlement_class", "mapping_density_bin")
    )


def build_strict_matches(
    rows: list[dict[str, str]],
    covariate_rows: list[dict[str, str]],
    niah_rows: list[dict[str, str]],
    *,
    k: int,
    grid_deg: float,
    max_distance_m: float,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    by_id = {row.get("osm_id", ""): row for row in covariate_rows}
    niah_by_id = {row.get("osm_id", ""): row for row in niah_rows}
    values = {
        row["osm_id"]: covariate_value(row, by_id.get(row["osm_id"], {}), niah_by_id.get(row["osm_id"], {}))
        for row in rows
    }
    # More candidate neighbours are required before enforcing global
    # no-replacement assignment; the ordinary stage intentionally stores only k.
    candidate_matches = build_matches(
        rows,
        k=max(12, k * 8),
        grid_deg=grid_deg,
        max_distance_m=max_distance_m,
    )
    by_target: dict[str, list[dict[str, str]]] = defaultdict(list)
    for match in candidate_matches:
        target_values = values.get(match["target_osm_id"], {})
        control_values = values.get(match["control_osm_id"], {})
        if hard_stratum(target_values) != hard_stratum(control_values):
            continue
        era_comparable = target_values.get("era_observed") == "1" and control_values.get("era_observed") == "1"
        era_gap = era_comparable and target_values.get("era") != control_values.get("era")
        cost = number(match.get("cost")) + (0.25 if era_gap else 0.0)
        by_target[match["target_osm_id"]].append(
            {
                **match,
                "strict_cost": f"{cost:.6f}",
                "era_comparable": str(int(era_comparable)),
                "era_gap": str(int(era_gap)),
            }
        )
    target_rows = [row for row in rows if row.get("is_control") == "0"]
    assigned_controls: set[str] = set()
    output = []
    # Matching the most constrained target first avoids letting dense urban
    # targets consume the only available controls for sparse strata.
    ordered_targets = sorted(
        target_rows,
        key=lambda row: (
            len(by_target.get(row["osm_id"], [])),
            -number(row.get("score")),
            row["osm_id"],
        ),
    )
    for target in ordered_targets:
        candidates = sorted(
            by_target.get(target["osm_id"], []),
            key=lambda item: (number(item.get("strict_cost")), item["control_osm_id"]),
        )
        rank = 0
        target_values = values.get(target["osm_id"], {})
        for candidate in candidates:
            control_id = candidate["control_osm_id"]
            if control_id in assigned_controls:
                continue
            rank += 1
            control_values = values.get(control_id, {})
            output.append(
                {
                    **candidate,
                    "match_id": f"{target['osm_id']}::strict::{rank}",
                    "rank": rank,
                    "k": k,
                    "replacement_allowed": 0,
                    "matching_method": "greedy global no-replacement within observed strata",
                    "stratum": hard_stratum(target_values),
                    "county": target_values.get("county", "unknown"),
                    "settlement_class": target_values.get("settlement_class", "unknown"),
                    "mapping_density_bin": target_values.get("mapping_density_bin", "unknown"),
                    "target_era": target_values.get("era", "unknown"),
                    "control_era": control_values.get("era", "unknown"),
                }
            )
            assigned_controls.add(control_id)
            if rank >= k:
                break
    target_n = len(target_rows)
    pair_counts = Counter(row["target_osm_id"] for row in output)
    stats = {
        "target_n": target_n,
        "fully_matched_targets": sum(count >= k for count in pair_counts.values()),
        "partially_matched_targets": sum(0 < count < k for count in pair_counts.values()),
        "unmatched_targets": target_n - len(pair_counts),
        "pair_n": len(output),
        "unique_controls": len(assigned_controls),
    }
    return output, stats


def strict_summary(rows: list[dict[str, str]], matches: list[dict[str, str]], k: int) -> list[dict[str, str]]:
    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for match in matches:
        by_group[match.get("target_group", "other")].append(match)
    target_groups = Counter(row.get("group", "other") for row in rows if row.get("is_control") == "0")
    output = []
    for group in sorted(target_groups):
        group_matches = by_group.get(group, [])
        pairs_by_target = Counter(item["target_osm_id"] for item in group_matches)
        distances = sorted(number(item.get("distance_m")) for item in group_matches)
        ratios = sorted(number(item.get("area_ratio"), 1.0) for item in group_matches)
        target_n = target_groups[group]
        median = distances[len(distances) // 2] if distances else 0.0
        output.append(
            {
                "target_group": group,
                "target_n": target_n,
                "fully_matched_targets": sum(count >= k for count in pairs_by_target.values()),
                "partially_matched_targets": sum(0 < count < k for count in pairs_by_target.values()),
                "unmatched_targets": target_n - len(pairs_by_target),
                "matched_pair_n": len(group_matches),
                "unique_controls": len({item["control_osm_id"] for item in group_matches}),
                "median_distance_m": round(median, 2),
                "median_area_ratio": round(ratios[len(ratios) // 2], 4) if ratios else 0.0,
                "county_observed_pct": round(
                    100.0 * sum(item.get("county") != "unknown" for item in group_matches) / len(group_matches), 2
                ) if group_matches else 0.0,
                "era_comparable_pct": round(
                    100.0 * sum(item.get("era_comparable") == "1" for item in group_matches) / len(group_matches), 2
                ) if group_matches else 0.0,
                "replacement_allowed": 0,
                "k": k,
                "method": "greedy global no-replacement within observed strata",
            }
        )
    return output


def balance_rows(rows: list[dict[str, str]], matches: list[dict[str, str]]) -> list[dict[str, str]]:
    by_id = {row.get("osm_id", ""): row for row in rows}
    output = []
    for field in ("county", "settlement_class", "mapping_density_bin"):
        values = [item.get(field, "unknown") for item in matches]
        output.append(
            {
                "covariate": field,
                "pair_n": len(values),
                "exact_match_pct": 100.0 if values else 0.0,
                "distinct_target_values": len({values[index] for index in range(len(values))}),
                "method": "hard stratum equality",
            }
        )
    gaps = []
    for item in matches:
        target = by_id.get(item["target_osm_id"], {})
        control = by_id.get(item["control_osm_id"], {})
        gaps.append(abs(number(target.get("area_m2")) - number(control.get("area_m2"))))
    output.append(
        {
            "covariate": "area_m2_absolute_gap",
            "pair_n": len(gaps),
            "exact_match_pct": "",
            "distinct_target_values": "",
            "mean_gap": round(sum(gaps) / len(gaps), 4) if gaps else 0.0,
            "method": "reported matching cost diagnostic",
        }
    )
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--grid-deg", type=float, default=0.1)
    parser.add_argument("--max-distance-m", type=float, default=15_000.0)
    args = parser.parse_args(argv)
    out = project_output_tree_path(args.out_dir)
    analysis = read_csv(out / "analysis_results.csv")
    if not analysis:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    covariates = read_csv(out / "spatial_covariates.csv")
    niah = read_csv(out / "niah_join.csv")
    matches, _stats = build_strict_matches(
        analysis,
        covariates,
        niah,
        k=args.k,
        grid_deg=args.grid_deg,
        max_distance_m=args.max_distance_m,
    )
    significance = matched_significance(analysis, matches)
    for row in significance:
        row["method"] = "matched-set mean difference without replacement within strata"
        row["test_family"] = "strict_validation"
    atomic_write_csv(
        out / "matched_controls_strict.csv",
        [
            "match_id", "target_osm_id", "target_group", "control_osm_id", "target_cell",
            "control_cell", "distance_m", "area_ratio", "log_area_gap", "cost", "strict_cost",
            "rank", "k", "replacement_allowed", "matching_method", "stratum", "county",
            "settlement_class", "mapping_density_bin", "target_era", "control_era",
            "era_comparable", "era_gap",
        ],
        matches,
        extrasaction="ignore",
    )
    atomic_write_csv(
        out / "matched_strict_summary.csv",
        [
            "target_group", "target_n", "fully_matched_targets", "partially_matched_targets",
            "unmatched_targets", "matched_pair_n", "unique_controls", "median_distance_m",
            "median_area_ratio", "county_observed_pct", "era_comparable_pct", "replacement_allowed", "k",
            "method",
        ],
        strict_summary(analysis, matches, args.k),
    )
    atomic_write_csv(
        out / "matched_strict_significance.csv",
        [
            "signal", "target_group", "target_set_n", "matched_pair_n", "target_successes",
            "control_successes", "target_rate", "matched_control_rate", "risk_difference_pp",
            "ci_low_pp", "ci_high_pp", "z", "p_value", "p_adjusted", "method", "direction",
            "test_family", "verdict",
        ],
        significance,
        extrasaction="ignore",
    )
    atomic_write_csv(
        out / "matched_strict_balance.csv",
        ["covariate", "pair_n", "exact_match_pct", "distinct_target_values", "mean_gap", "method"],
        balance_rows(analysis, matches),
    )
    print(f"[validation] wrote {len(matches):,} no-replacement pairs")


if __name__ == "__main__":
    main()
