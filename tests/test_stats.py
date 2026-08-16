import pytest

from scripts.stats import apply_holm, compare_proportions, holm_adjust, p_format


def test_holm_preserves_order_and_monotonicity():
    result = holm_adjust([0.04, 0.001, 0.02])
    assert len(result) == 3
    assert result[1] <= result[2] <= result[0]
    assert all(0 <= p <= 1 for p in result)


def test_proportion_comparison_has_effect_and_intervals():
    result = compare_proportions(20, 100, 5, 100)
    assert result["risk_difference"] == pytest.approx(0.15)
    assert result["rate1_ci_low"] < 0.2 < result["rate1_ci_high"]
    assert result["odds_ratio"] > 1
    assert 0 <= result["p_value"] <= 1
    assert result["p_value"] > 0


def test_apply_holm_drives_verdicts():
    rows = [
        {"p_value": 0.001, "direction": 1, "test_family": "a"},
        {"p_value": 0.4, "direction": 1, "test_family": "a"},
    ]
    apply_holm(rows)
    assert rows[0]["p_adjusted"] == 0.002
    assert rows[0]["verdict"] == "SIGNAL"
    assert rows[1]["verdict"] == "background"


def test_p_format_never_emits_zero():
    assert p_format(0.0) == "<0.0001"
    assert p_format(0.0008) == "<0.001"
