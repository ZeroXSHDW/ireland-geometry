from scripts.verify import check_probabilities


def test_probability_contract_rejects_literal_zero():
    errors = []
    check_probabilities(
        [{"p_value": "0", "p_adjusted": "0.01", "method": "test", "verdict": "SIGNAL"}],
        "sample.csv",
        errors,
    )
    assert any("invalid p_value" in error for error in errors)


def test_probability_contract_accepts_decade_p_column():
    errors = []
    check_probabilities(
        [{"p": "0.02", "p_adjusted": "0.04", "method": "test", "verdict": "suggestive"}],
        "decades.csv",
        errors,
    )
    assert errors == []
