#!/usr/bin/env python3
"""Estimate spatial block-bootstrap uncertainty for primary signal rates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
from collections import defaultdict
from pathlib import Path

try:
    from runtime import atomic_write_csv, project_output_tree_path
except ImportError:
    from scripts.runtime import atomic_write_csv, project_output_tree_path


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


def cell(row: dict[str, str], grid_deg: float) -> tuple[int, int]:
    return (math.floor(number(row.get("lat")) / grid_deg), math.floor(number(row.get("lon")) / grid_deg))


def signal_value(row: dict[str, str], signal: str) -> int:
    if signal == "golden_angle":
        return int(row.get("has_golden_angle") == "1")
    if signal == "golden_ratio":
        return int(number(row.get("golden_ratio_err_pct"), 999.0) <= 3.0)
    if signal == "fib_ratio":
        return int(number(row.get("fib_ratio_err_pct"), 999.0) <= 2.0)
    raise ValueError(f"unknown signal: {signal}")


def quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(probability * (len(ordered) - 1))))]


def stable_seed(seed: int, group: str, signal: str) -> int:
    digest = hashlib.sha256(f"{seed}:{group}:{signal}".encode()).hexdigest()
    return seed + int(digest[:10], 16)


def bootstrap_rows(
    rows: list[dict[str, str]],
    *,
    seed: int,
    iterations: int,
    grid_deg: float,
) -> list[dict[str, object]]:
    signals = ("golden_angle", "golden_ratio", "fib_ratio")
    groups = sorted({row.get("group", "other") for row in rows if row.get("is_control") == "0"})
    output = []
    for group in groups:
        targets = [row for row in rows if row.get("is_control") == "0" and row.get("group") == group]
        controls = [row for row in rows if row.get("is_control") == "1"]
        for signal in signals:
            blocks: dict[tuple[int, int], dict[str, list[int]]] = defaultdict(lambda: {"target": [], "control": []})
            for row in targets:
                blocks[cell(row, grid_deg)]["target"].append(signal_value(row, signal))
            for row in controls:
                blocks[cell(row, grid_deg)]["control"].append(signal_value(row, signal))
            eligible = [block for block in blocks.values() if block["target"] and block["control"]]
            target_n = sum(len(block["target"]) for block in eligible)
            control_n = sum(len(block["control"]) for block in eligible)
            target_successes = sum(sum(block["target"]) for block in eligible)
            control_successes = sum(sum(block["control"]) for block in eligible)
            observed = target_successes / target_n - control_successes / control_n if target_n and control_n else 0.0
            if not eligible:
                output.append(
                    {
                        "signal": signal,
                        "target_group": group,
                        "block_n": 0,
                        "target_n": 0,
                        "control_n": 0,
                        "observed_difference_pp": "",
                        "bootstrap_n": 0,
                        "ci_low_pp": "",
                        "ci_high_pp": "",
                        "prob_positive": "",
                        "prob_negative": "",
                        "status": "insufficient_overlap",
                        "seed": stable_seed(seed, group, signal),
                        "method": "spatial block bootstrap over 0.1-degree cells",
                    }
                )
                continue
            rng = random.Random(stable_seed(seed, group, signal))
            differences = []
            for _ in range(max(1, iterations)):
                sample = [eligible[rng.randrange(len(eligible))] for _ in eligible]
                sample_target_n = sum(len(block["target"]) for block in sample)
                sample_control_n = sum(len(block["control"]) for block in sample)
                sample_target_rate = sum(sum(block["target"]) for block in sample) / sample_target_n
                sample_control_rate = sum(sum(block["control"]) for block in sample) / sample_control_n
                differences.append(sample_target_rate - sample_control_rate)
            output.append(
                {
                    "signal": signal,
                    "target_group": group,
                    "block_n": len(eligible),
                    "target_n": target_n,
                    "control_n": control_n,
                    "observed_difference_pp": round(observed * 100.0, 6),
                    "bootstrap_n": len(differences),
                    "ci_low_pp": round(quantile(differences, 0.025) * 100.0, 6),
                    "ci_high_pp": round(quantile(differences, 0.975) * 100.0, 6),
                    "prob_positive": round(sum(value > 0 for value in differences) / len(differences), 6),
                    "prob_negative": round(sum(value < 0 for value in differences) / len(differences), 6),
                    "status": "available",
                    "seed": stable_seed(seed, group, signal),
                    "method": "spatial block bootstrap over 0.1-degree cells",
                }
            )
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--grid-deg", type=float, default=0.1)
    args = parser.parse_args(argv)
    out = project_output_tree_path(args.out_dir)
    rows = read_csv(out / "analysis_results.csv")
    if not rows:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    results = bootstrap_rows(rows, seed=args.seed, iterations=args.iterations, grid_deg=args.grid_deg)
    atomic_write_csv(
        out / "spatial_bootstrap.csv",
        [
            "signal", "target_group", "block_n", "target_n", "control_n", "observed_difference_pp",
            "bootstrap_n", "ci_low_pp", "ci_high_pp", "prob_positive", "prob_negative", "status", "seed", "method",
        ],
        results,
    )
    print(f"[bootstrap] wrote {len(results)} spatial uncertainty rows with {args.iterations} iterations")


if __name__ == "__main__":
    main()
