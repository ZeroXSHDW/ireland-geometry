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


def test_generic_by_phrases_do_not_create_architect_attributions():
    assert (
        extract_architect_detail(
            {
                "composition": "Farmleigh was purchased from the Guinness family by the Irish Government.",
                "appraisal": "",
            }
        )
        is None
    )
    assert (
        extract_architect_detail(
            {
                "composition": "The foundation stone was laid by the Most Revd. Dr. Farren.",
                "appraisal": "",
            }
        )
        is None
    )
    assert (
        extract_architect_detail(
            {
                "composition": "The stained glass windows were designed by F.X. Zettler Studio.",
                "appraisal": "",
            }
        )
        is None
    )
    assert (
        extract_architect_detail(
            {
                "composition": "The Henry Brett-designed Swinford Courthouse is nearby.",
                "appraisal": "",
            }
        )
        is None
    )


def test_explicit_architect_roles_and_firms_are_retained():
    detail = extract_architect_detail(
        {
            "composition": (
                "The church was designed by the Arts & Crafts influenced architect "
                "William Scott."
            ),
            "appraisal": "",
        }
    )
    assert detail is not None
    assert detail["name"] == "William Scott"

    firm = extract_architect_detail(
        {
            "composition": "The building was constructed to designs by Blackwood and Jury.",
            "appraisal": "",
        }
    )
    assert firm is not None
    assert firm["name"] == "Blackwood and Jury"


def test_explicit_single_name_attribution_is_preserved():
    detail = extract_architect_detail(
        {
            "composition": "A church was erected to a design attributed to a local architect named only as Canning.",
            "appraisal": "",
        }
    )
    assert detail is not None
    assert detail["name"] == "Canning"


def test_initialed_architect_names_are_preserved():
    assert clean_name("N. A. Mills") == "N. A. Mills"


def test_institutional_architect_role_keeps_the_person_name():
    detail = extract_architect_detail(
        {
            "composition": (
                "It was designed by the Poor Law Commissioners's architect, "
                "George Wilkinson."
            ),
            "appraisal": "",
        }
    )
    assert detail is not None
    assert detail["name"] == "George Wilkinson"


def test_extended_design_context_is_retained_as_high_confidence():
    detail = extract_architect_detail(
        {
            "composition": "The designs for this complex were prepared by George Wilkinson.",
            "appraisal": "",
        }
    )
    assert detail is not None
    assert detail["name"] == "George Wilkinson"
    assert detail["confidence"] == "high"
    assert "designs for this complex" in detail["evidence"].lower()

    dated = extract_architect_detail(
        {
            "composition": "The church was erected to a design (1926) by Rudolph Maximilian Butler.",
            "appraisal": "",
        }
    )
    assert dated is not None
    assert dated["name"] == "Rudolph Maximilian Butler"
