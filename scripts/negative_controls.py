#!/usr/bin/env python3
"""Run pre-specified conventional-angle negative-control diagnostics.

These tests are deliberately reported outside the primary golden-angle family.
They ask whether target/control differences also appear for common 60° and
120° footprint angles, which helps expose generic angular or mapping effects.
They do not prove that either angle is a valid causal negative control.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

try:
    from runtime import atomic_write_csv, project_path
    from stats import apply_holm, compare_proportions
except ImportError:
    from scripts.runtime import atomic_write_csv, project_path
    from scripts.stats import apply_holm, compare_proportions


SIGNALS = ("has_60_angle", "has_120_angle")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_negative_controls(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    controls = [row for row in rows if row.get("is_control") == "1"]
    groups = sorted({row.get("group", "other") for row in rows if row.get("is_control") == "0"})
    output: list[dict[str, object]] = []
    for signal in SIGNALS:
        control_successes = sum(row.get(signal) == "1" for row in controls)
        for group in groups:
            targets = [
                row for row in rows if row.get("is_control") == "0" and row.get("group") == group
            ]
            if not targets:
                continue
            target_successes = sum(row.get(signal) == "1" for row in targets)
            result = compare_proportions(
                target_successes,
                len(targets),
                control_successes,
                len(controls),
            )
            direction = (result["rate1"] > result["rate2"]) - (result["rate1"] < result["rate2"])
            output.append(
                {
                    "signal": signal,
                    "group": group,
                    "n": len(targets),
                    "successes": target_successes,
                    "observed_rate": round(result["rate1"] * 100, 4),
                    "control_n": len(controls),
                    "control_successes": control_successes,
                    "control_rate": round(result["rate2"] * 100, 4),
                    "ci_low": round(result["rate1_ci_low"] * 100, 4),
                    "ci_high": round(result["rate1_ci_high"] * 100, 4),
                    "control_ci_low": round(result["rate2_ci_low"] * 100, 4),
                    "control_ci_high": round(result["rate2_ci_high"] * 100, 4),
                    "risk_difference": round(result["risk_difference"] * 100, 4),
                    "odds_ratio": round(result["odds_ratio"], 6),
                    "z": round(result["z"], 6),
                    "p_value": result["p_value"],
                    "method": "building-level target/control negative-control diagnostic",
                    "direction": direction,
                    "test_family": "negative_control_angle",
                }
            )
    return apply_holm(output)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    rows = read_csv(out / "analysis_results.csv")
    if not rows:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    results = build_negative_controls(rows)
    fields = [
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
    atomic_write_csv(out / "negative_controls.csv", fields, results, extrasaction="ignore")
    print(f"[negative-controls] wrote {len(results)} conventional-angle diagnostics")


if __name__ == "__main__":
    main()
