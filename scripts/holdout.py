#!/usr/bin/env python3
"""Run a deterministic holdout analysis against a tracked analysis plan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

try:
    from runtime import (
        atomic_write_csv,
        atomic_write_json,
        default_analysis_plan_path,
        project_path,
        sha256_file,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_csv,
        atomic_write_json,
        default_analysis_plan_path,
        project_path,
        sha256_file,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def holdout(row: dict[str, str], seed: int, fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}:{row.get('osm_id', '')}".encode()).hexdigest()
    return int(digest[:12], 16) / 16**12 < fraction


def signal_value(row: dict[str, str], signal: str) -> int:
    if signal == "golden_angle":
        return int(row.get("has_golden_angle") == "1")
    if signal == "golden_ratio":
        return int(number(row.get("golden_ratio_err_pct"), 999) <= 3)
    if signal == "fib_ratio":
        return int(number(row.get("fib_ratio_err_pct"), 999) <= 2)
    raise ValueError(f"unknown signal: {signal}")


def z_p_value(target_rate: float, control_rate: float, target_n: int, control_n: int) -> tuple[float, float]:
    se = math.sqrt(
        max(target_rate * (1 - target_rate) / max(target_n, 1) + control_rate * (1 - control_rate) / max(control_n, 1), 0.0)
    )
    if se == 0:
        return (0.0, 1.0 if target_rate == control_rate else 1e-300)
    z = (target_rate - control_rate) / se
    return z, max(1e-300, math.erfc(abs(z) / math.sqrt(2)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--plan", default=None, help="tracked JSON analysis plan")
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--fraction", type=float, default=None)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    plan_path = (
        project_path(args.plan, "analysis_plan.json")
        if args.plan
        else default_analysis_plan_path()
    )
    if not plan_path.exists():
        raise SystemExit(f"Missing preregistered plan: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    fraction = args.fraction if args.fraction is not None else number(plan.get("holdout_fraction"), 0.3)
    if not 0.0 < fraction < 1.0:
        raise SystemExit("holdout fraction must be between 0 and 1")
    rows = read_csv(out / "analysis_results.csv")
    if not rows:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}")
    assignments = []
    holdout_rows = []
    for row in rows:
        is_holdout = holdout(row, args.seed, fraction)
        if is_holdout:
            holdout_rows.append(row)
        assignments.append(
            {
                "osm_id": row.get("osm_id", ""),
                "is_control": row.get("is_control", ""),
                "group": row.get("group", ""),
                "split": "holdout" if is_holdout else "discovery",
                "seed": args.seed,
                "fraction": fraction,
                "rule": "sha256(seed:osm_id) < holdout_fraction",
            }
        )
    atomic_write_csv(
        out / "holdout_assignments.csv",
        ["osm_id", "is_control", "group", "split", "seed", "fraction", "rule"],
        assignments,
    )
    controls = [row for row in holdout_rows if row.get("is_control") == "1"]
    groups = sorted({row.get("group", "other") for row in holdout_rows if row.get("is_control") == "0"})
    results = []
    for group in groups:
        targets = [row for row in holdout_rows if row.get("is_control") == "0" and row.get("group") == group]
        for signal in plan.get("primary_signals", ["golden_angle"]):
            t_success = sum(signal_value(row, signal) for row in targets)
            c_success = sum(signal_value(row, signal) for row in controls)
            t_rate = t_success / len(targets) if targets else 0.0
            c_rate = c_success / len(controls) if controls else 0.0
            z, p_value = z_p_value(t_rate, c_rate, len(targets), len(controls))
            results.append(
                {
                    "signal": signal,
                    "target_group": group,
                    "split": "holdout",
                    "target_n": len(targets),
                    "control_n": len(controls),
                    "target_successes": t_success,
                    "control_successes": c_success,
                    "target_rate": round(t_rate * 100, 4),
                    "control_rate": round(c_rate * 100, 4),
                    "risk_difference_pp": round((t_rate - c_rate) * 100, 4),
                    "z": round(z, 6),
                    "p_value": p_value,
                    "alpha": number(plan.get("alpha"), 0.05),
                    "pre_registered": 1,
                    "plan_sha256": sha256_file(plan_path),
                    "status": "available" if targets and controls else "insufficient_holdout_rows",
                    "method": "deterministic hash holdout; no model refitting or tuning on holdout",
                }
            )
    atomic_write_csv(
        out / "holdout_results.csv",
        [
            "signal", "target_group", "split", "target_n", "control_n", "target_successes", "control_successes",
            "target_rate", "control_rate", "risk_difference_pp", "z", "p_value", "alpha", "pre_registered",
            "plan_sha256", "status", "method",
        ],
        results or [{"signal": "", "target_group": "", "split": "holdout", "target_n": 0, "control_n": len(controls), "status": "no_target_rows", "pre_registered": 1, "plan_sha256": sha256_file(plan_path), "method": "deterministic hash holdout"}],
    )
    used_plan = {**plan, "plan_path": str(plan_path), "plan_sha256": sha256_file(plan_path), "seed": args.seed, "holdout_fraction": fraction}
    atomic_write_json(out / "analysis_plan_used.json", used_plan, indent=2)
    print(f"[holdout] assigned {len(holdout_rows):,}/{len(rows):,} rows to holdout; wrote {len(results):,} results")


if __name__ == "__main__":
    main()
