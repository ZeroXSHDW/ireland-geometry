from scripts.holdout import holdout
from scripts.osm_history import build_history
from scripts.review import calibration
from scripts.road_routing import graph_from_rows, shortest_path
from scripts.validation import build_strict_matches


def analysis_row(osm_id, lat, lon, control=False, area=100, group="worship"):
    return {
        "osm_id": osm_id,
        "lat": str(lat),
        "lon": str(lon),
        "area_m2": str(area),
        "is_control": "1" if control else "0",
        "group": "control" if control else group,
        "score": "70" if not control else "10",
        "has_golden_angle": "1" if not control else "0",
        "golden_ratio_err_pct": "2" if not control else "8",
        "fib_ratio_err_pct": "1" if not control else "5",
    }


def test_strict_matching_never_reuses_a_control_and_preserves_strata():
    rows = [
        analysis_row("way/t1", 53.0, -8.0),
        analysis_row("way/t2", 53.0002, -8.0002),
        analysis_row("way/c1", 53.0001, -8.0001, True),
        analysis_row("way/c2", 53.0003, -8.0003, True),
    ]
    covariates = [
        {"osm_id": row["osm_id"], "settlement_class": "town", "mapping_density_bin": "medium", "boundary_status": "not_provided", "settlement_status": "provided"}
        for row in rows
    ]
    matches, stats = build_strict_matches(rows, covariates, [], k=1, grid_deg=0.1, max_distance_m=1000)
    assert len(matches) == 2
    assert len({row["control_osm_id"] for row in matches}) == 2
    assert all(row["replacement_allowed"] == 0 for row in matches)
    assert stats["fully_matched_targets"] == 2
    assert all("settlement_class=town" in row["stratum"] for row in matches)


def test_osm_history_aggregates_versions_and_is_explicit_when_available():
    rows = [analysis_row("way/1", 53, -8), analysis_row("way/2", 53, -8, True)]
    history = [
        {"osm_id": "way/1", "version": "1", "timestamp": "2020-01-01T00:00:00Z", "uid": "a", "geometry_changed": "1"},
        {"osm_id": "way/1", "version": "2", "timestamp": "2021-01-01T00:00:00Z", "uid": "b", "tags_changed": "1"},
    ]
    output = build_history(rows, history, "history.csv")
    assert output[0]["status"] == "provided"
    assert output[0]["version_count"] == 2
    assert output[0]["editor_count"] == 2
    assert output[1]["status"] == "not_provided"


def test_holdout_assignment_is_deterministic():
    row = {"osm_id": "way/123"}
    assert holdout(row, 42, 0.3) == holdout(row, 42, 0.3)


def test_road_graph_routes_shortest_path_and_respects_oneway():
    nodes, adjacency = graph_from_rows(
        [
            {"node_id": "a", "lat": "53", "lon": "-8"},
            {"node_id": "b", "lat": "53", "lon": "-8.001"},
            {"node_id": "c", "lat": "53.001", "lon": "-8"},
        ],
        [
            {"u": "a", "v": "b", "length_m": "10"},
            {"u": "b", "v": "c", "length_m": "10"},
            {"u": "a", "v": "c", "length_m": "30"},
        ],
    )
    assert set(nodes) == {"a", "b", "c"}
    assert shortest_path("a", "c", adjacency) == 20


def test_review_calibration_keeps_ambiguous_labels_out_of_binary_metrics():
    rows = [
        {"score": "90", "label": "supportive"},
        {"score": "20", "label": "not_supportive"},
        {"score": "90", "label": "ambiguous"},
    ]
    result = calibration(rows)
    assert result[0]["labelled_n"] == 2
    assert result[0]["ambiguous_n"] == 1
    assert result[0]["status"] == "provided"
