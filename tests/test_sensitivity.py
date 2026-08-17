from scripts.sensitivity import (
    build_matches,
    hierarchical_model,
    match_summary,
    matched_significance,
)


def row(osm_id, lat, lon, control, area, golden):
    return {
        "osm_id": osm_id,
        "lat": str(lat),
        "lon": str(lon),
        "area_m2": str(area),
        "is_control": "1" if control else "0",
        "group": "control" if control else "worship",
        "has_golden_angle": "1" if golden else "0",
        "golden_ratio_err_pct": "2" if golden else "8",
        "fib_ratio_err_pct": "1" if golden else "5",
    }


def test_matching_is_local_size_aware_and_deterministic():
    rows = [row("target/1", 53.0, -8.0, False, 100, True), row("target/2", 53.0005, -8.0005, False, 110, False)]
    rows += [
        row("control/1", 53.0001, -8.0001, True, 102, False),
        row("control/2", 53.001, -8.001, True, 98, True),
        row("control/3", 53.05, -8.05, True, 105, False),
    ]
    matches = build_matches(rows, k=2, max_distance_m=20_000)
    assert len(matches) == 4
    assert matches == build_matches(rows, k=2, max_distance_m=20_000)
    assert all(float(match["area_ratio"]) >= 1 for match in matches)
    assert match_summary(matches)[0]["target_n"] == 2


def test_matched_and_hierarchical_outputs_have_adjusted_p_values():
    rows = []
    for index in range(4):
        rows.append(row(f"target/{index}", 53.0 + index * 0.0001, -8.0, False, 100, index < 2))
    for index in range(12):
        rows.append(row(f"control/{index}", 53.0 + index * 0.00005, -8.0, True, 100, index < 1))
    matches = build_matches(rows, k=2, max_distance_m=20_000)
    matched = matched_significance(rows, matches)
    hierarchical = hierarchical_model(rows)
    assert matched and all(0 < float(item["p_adjusted"]) <= 1 for item in matched)
    assert hierarchical and all("verdict" in item for item in hierarchical)
