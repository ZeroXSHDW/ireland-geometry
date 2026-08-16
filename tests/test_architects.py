from scripts.architects import clean_name, extract_architect_detail


def test_false_positive_institution_names_are_rejected():
    assert clean_name("Second Ecumenical Council") is None
    assert clean_name("Roman Catholic Church") is None
    assert clean_name("James Fraher The") is None


def test_architect_evidence_is_retained():
    detail = extract_architect_detail(
        {
            "composition": "Built to a design by William Henry Byrne (1844-1917).",
            "appraisal": "",
        }
    )
    assert detail is not None
    assert detail["name"] == "William Henry Byrne"
    assert "design" in detail["evidence"].lower()
    assert detail["confidence"] == "high"
