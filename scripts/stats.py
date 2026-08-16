"""Small dependency-free statistical helpers shared by pipeline stages."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

MIN_REPORTABLE_P = 1e-300


def ztest_two_prop(n1: int, p1: float, n2: int, p2: float) -> tuple[float, float]:
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    if not 0 < pooled < 1:
        return 0.0, 1.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se <= 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return z, max(MIN_REPORTABLE_P, p)


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def odds_ratio(success1: int, total1: int, success2: int, total2: int) -> float:
    a = success1 + 0.5
    b = max(0, total1 - success1) + 0.5
    c = success2 + 0.5
    d = max(0, total2 - success2) + 0.5
    return (a * d) / (b * c)


def compare_proportions(success1: int, total1: int, success2: int, total2: int) -> dict:
    p1 = success1 / total1 if total1 else 0.0
    p2 = success2 / total2 if total2 else 0.0
    z, p = ztest_two_prop(total1, p1, total2, p2)
    lo1, hi1 = wilson_interval(success1, total1)
    lo2, hi2 = wilson_interval(success2, total2)
    return {
        "success1": success1,
        "n1": total1,
        "rate1": p1,
        "success2": success2,
        "n2": total2,
        "rate2": p2,
        "z": z,
        "p_value": p,
        "method": "two-proportion-z",
        "rate1_ci_low": lo1,
        "rate1_ci_high": hi1,
        "rate2_ci_low": lo2,
        "rate2_ci_high": hi2,
        "risk_difference": p1 - p2,
        "odds_ratio": odds_ratio(success1, total1, success2, total2),
    }


def holm_adjust(p_values: Iterable[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values in original order."""
    values = [min(1.0, max(MIN_REPORTABLE_P, float(p))) for p in p_values]
    order = sorted(range(len(values)), key=values.__getitem__)
    adjusted = [1.0] * len(values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (len(values) - rank) * values[index])
        adjusted[index] = min(1.0, running)
    return adjusted


def p_format(value: float | str | None) -> str:
    if value is None or value == "":
        return "n/a"
    p = float(value)
    if p < 1e-4:
        return "<0.0001"
    if p < 0.001:
        return "<0.001"
    return f"{p:.4f}".rstrip("0").rstrip(".")


def assign_verdict(
    p_adjusted: float, direction: int, *, signal_alpha: float = 0.01, suggestive_alpha: float = 0.05
) -> str:
    if direction > 0 and p_adjusted < signal_alpha:
        return "SIGNAL"
    if direction > 0 and p_adjusted < suggestive_alpha:
        return "suggestive"
    return "background"


def apply_holm(rows: list[dict], family_field: str = "test_family") -> list[dict]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[str(row.get(family_field, "default"))].append(index)
    for indices in groups.values():
        corrected = holm_adjust([float(rows[i].get("p_value", 1.0)) for i in indices])
        for index, adjusted in zip(indices, corrected):
            row = rows[index]
            row["p_adjusted"] = adjusted
            direction = int(row.get("direction", 0))
            row["verdict"] = assign_verdict(adjusted, direction)
    return rows
