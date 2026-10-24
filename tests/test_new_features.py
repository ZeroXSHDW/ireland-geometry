import csv
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts import runtime as runtime_module
from scripts.building_parts import parse_las_lidar, parse_lidar, parse_raster_lidar
from scripts.columnar import iter_csv_batches, iter_csv_rows, write_jsonl
from scripts.columnar import main as columnar_main
from scripts.data_quality import build_audit, build_duplicate_groups
from scripts.holdout import holdout
from scripts.holdout import main as holdout_main
from scripts.negative_controls import build_negative_controls
from scripts.osm_history import build_history
from scripts.review import calibration
from scripts.road_routing import (
    PortableRoadGraph,
    SQLiteRoadGraph,
    build_node_index,
    conditional_access_profile_rules,
    graph_from_rows,
    is_access_restricted,
    load_graph,
    nearest_node,
    parse_conditional_access,
    parse_conditional_access_profile_rule,
    parse_conditional_access_profile_rules_value,
    parse_conditional_access_rule,
    parse_duration_seconds,
    parse_hgv_destination_profiles,
    parse_maxaxleload_profile,
    parse_maxheight_profile,
    parse_maxlength_profile,
    parse_maxspeed_conditional_profile,
    parse_maxspeed_kmh,
    parse_maxspeed_profile,
    parse_maxweight_profile,
    parse_maxweight_t,
    parse_maxweightrating_hgv_profile,
    parse_maxweightrating_hgv_profiles,
    parse_maxweightrating_hgv_t,
    parse_maxwidth_profile,
    parse_oneway_conditional_profile,
    parse_vehicle_weight_condition,
    parse_weekly_schedule,
    shortest_path,
    shortest_path_metrics,
    write_graph,
    write_sqlite_graph,
)
from scripts.road_routing import main as road_routing_main
from scripts.route_query import main as route_query_main
from scripts.route_query import query_route, query_route_matrix
from scripts.runtime import sha256_file
from scripts.spatial_bootstrap import bootstrap_rows
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


def test_holdout_uses_packaged_plan_when_external_project_has_no_override(tmp_path, monkeypatch):
    project = tmp_path / "project"
    output = project / "output"
    output.mkdir(parents=True)
    rows = [analysis_row("way/target", 53.0, -8.0), analysis_row("way/control", 53.1, -8.1, True)]
    with (output / "analysis_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    monkeypatch.setattr(runtime_module, "ROOT", project)
    holdout_main(["--out-dir", str(output)])

    packaged = Path(__file__).resolve().parents[1] / "schemas" / "analysis_plan.json"
    used = json.loads((output / "analysis_plan_used.json").read_text(encoding="utf-8"))
    assert Path(used["plan_path"]).resolve() == packaged.resolve()
    assert used["plan_sha256"] == sha256_file(packaged)


def test_holdout_rejects_symlinked_plan_override(tmp_path):
    target = tmp_path / "target-plan.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "plan.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="analysis plan must not be a symlink"):
        holdout_main(["--out-dir", str(tmp_path / "output"), "--plan", str(link)])


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


def test_routing_graph_export_round_trips_and_indexes_nearest_nodes(tmp_path):
    nodes, adjacency = graph_from_rows(
        [
            {"node_id": "a", "lat": "53", "lon": "-8"},
            {"node_id": "b", "lat": "53", "lon": "-8.001"},
            {"node_id": "c", "lat": "53.001", "lon": "-8"},
        ],
        [{"u": "a", "v": "b", "length_m": "10"}, {"u": "b", "v": "c", "length_m": "10"}],
    )
    graph_path = tmp_path / "graph"
    write_graph(graph_path, nodes, adjacency, source="sample.pbf")
    loaded_nodes, loaded_adjacency = load_graph(graph_path)
    assert shortest_path("a", "c", loaded_adjacency) == 20
    assert nearest_node(
        {"lat": "53", "lon": "-8.0009"}, loaded_nodes, build_node_index(loaded_nodes)
    ) == "b"
    assert (graph_path / "road_graph_metadata.json").exists()


def test_portable_graph_preserves_directed_way_context_and_path_segments(tmp_path):
    graph = graph_from_rows(
        [
            {"node_id": "a", "lat": "53", "lon": "-8"},
            {"node_id": "b", "lat": "53", "lon": "-8.001"},
            {"node_id": "c", "lat": "53", "lon": "-8.002"},
        ],
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "10",
                "oneway": "yes",
                "way_id": "way/a-b",
                "name": "Main Road",
                "highway": "primary",
            },
            {
                "u": "b",
                "v": "c",
                "length_m": "20",
                "oneway": "yes",
                "way_id": "way/b-c",
                "ref": "R1",
            },
        ],
    )
    assert isinstance(graph, PortableRoadGraph)
    result = query_route(53.0, -8.0, 53.0, -8.002, graph, include_path=True)
    assert result["path_segment_source"] == "portable_edges"
    assert result["path_way_ids"] == ["way/a-b", "way/b-c"]
    assert result["path_segment_total_distance_m"] == 30.0
    assert result["path_segments"][0]["road_context"] == {
        "name": "Main Road",
        "ref": None,
        "highway": "primary",
        "route": None,
        "oneway": "yes",
    }
    assert result["graph"]["way_context"] is True
    assert result["graph"]["way_context_n"] == 2

    graph_path = tmp_path / "portable-graph"
    write_graph(graph_path, graph, source="fixture.pbf")
    loaded = load_graph(graph_path)
    loaded_result = query_route(53.0, -8.0, 53.0, -8.002, loaded, include_path=True)
    assert loaded_result["path_segment_source"] == "portable_edges"
    assert loaded_result["path_way_ids"] == ["way/a-b", "way/b-c"]
    metadata = json.loads((graph_path / "road_graph_metadata.json").read_text(encoding="utf-8"))
    assert metadata["path_segment_source"] == "portable_edges"


def test_portable_ferry_geometry_is_opt_in_and_round_trips(tmp_path):
    graph = graph_from_rows(
        [
            {"node_id": "a", "lat": "53", "lon": "-8"},
            {"node_id": "f1", "lat": "53", "lon": "-8.001"},
            {"node_id": "f2", "lat": "53", "lon": "-8.002"},
            {"node_id": "b", "lat": "53", "lon": "-8.003"},
        ],
        [
            {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road/a"},
            {
                "u": "f1",
                "v": "f2",
                "length_m": "2",
                "oneway": "yes",
                "way_id": "ferry/1",
                "route": "ferry",
            },
            {"u": "f2", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road/b"},
        ],
    )

    assert graph.ferry_edge_count == 1
    assert graph.neighbours("f1")[0].ferry is True
    assert shortest_path("a", "b", graph) is None
    assert shortest_path("a", "b", graph, include_ferries=True) == 4

    excluded = query_route(53.0, -8.0, 53.0, -8.003, graph, include_path=True)
    assert excluded["reachable"] is False
    assert excluded["graph"]["ferry_edge_n"] == 1
    assert "ferry geometry available but excluded" in excluded["method"]

    included = query_route(
        53.0,
        -8.0,
        53.0,
        -8.003,
        graph,
        include_ferries=True,
        include_path=True,
    )
    assert included["reachable"] is True
    assert included["ferry_way_ids"] == ["ferry/1"]
    assert included["ferry_distance_m"] == 2.0
    assert included["ferry_crossing_s"] == 0.0
    assert included["ferry_edge_n"] == 1
    assert included["path_segments"][1]["ferry"] is True
    assert included["path_segments"][1]["road_context"]["route"] == "ferry"
    assert "schedules, waits, and crossing durations unavailable" in included["method"]

    compact = query_route(
        53.0,
        -8.0,
        53.0,
        -8.003,
        graph,
        include_ferries=True,
    )
    assert compact["reachable"] is True
    assert compact["ferry_way_ids"] == ["ferry/1"]
    assert compact["ferry_distance_m"] == 2.0
    assert compact["ferry_crossing_s"] == 0.0
    assert compact["ferry_edge_n"] == 1
    assert "path_segments" not in compact

    matrix = query_route_matrix(
        [(53.0, -8.0)],
        [(53.0, -8.003)],
        graph,
        include_ferries=True,
    )
    assert matrix["pairs"][0]["ferry_distance_m"] == 2.0
    assert matrix["pairs"][0]["ferry_edge_n"] == 1

    graph_path = tmp_path / "portable-ferry-graph"
    write_graph(graph_path, graph, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, PortableRoadGraph)
    assert loaded.ferry_edge_count == 1
    assert shortest_path("a", "b", loaded) is None
    assert shortest_path("a", "b", loaded, include_ferries=True) == 4
    metadata = json.loads((graph_path / "road_graph_metadata.json").read_text(encoding="utf-8"))
    assert metadata["ferry_edge_n"] == 1


def test_sqlite_graph_round_trips_and_routes_without_loading_adjacency(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "b": (53.0, -8.001),
        "c": (53.001, -8.0),
    }
    edges = [
        {"u": "a", "v": "b", "length_m": "10"},
        {"u": "b", "v": "c", "length_m": "10"},
        {"u": "a", "v": "c", "length_m": "30", "oneway": "yes"},
    ]
    graph_path = tmp_path / "sqlite-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf", complete=True, max_ways=0)
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert shortest_path("a", "c", loaded) == 20
    assert shortest_path("c", "a", loaded) == 20
    assert nearest_node({"lat": "53", "lon": "-8.0009"}, loaded) == "b"
    loaded.close()
    metadata = json.loads((graph_path / "road_graph_metadata.json").read_text(encoding="utf-8"))
    assert metadata["format"] == "ireland-geometry-road-sqlite-v21"
    assert metadata["complete"] is True


def test_route_matrix_reuses_route_contract_and_bounds_pair_count(tmp_path):
    graph_path = tmp_path / "matrix-graph"
    write_sqlite_graph(
        graph_path,
        {
            "a": (53.0, -8.0),
            "b": (53.0, -8.001),
            "c": (53.0, -8.002),
        },
        [
            {"u": "a", "v": "b", "length_m": "10", "way_id": "way/a-b"},
            {"u": "b", "v": "c", "length_m": "10", "way_id": "way/b-c"},
        ],
        source="fixture.pbf",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    try:
        matrix = query_route_matrix(
            [(53.0, -8.0), (53.0, -8.001)],
            [(53.0, -8.001), (53.0, -8.002)],
            loaded,
            speed_kmh=36,
        )
        assert matrix["contract"] == "ireland-geometry.route-matrix.v1"
        assert matrix["pair_n"] == 4
        assert matrix["reachable_n"] == 4
        assert matrix["pairs"][0]["route_distance_m"] == 10.0
        assert matrix["pairs"][0]["route"] is None
        with pytest.raises(ValueError, match="must not exceed 25"):
            query_route_matrix(
                [(53.0, -8.0)] * 6,
                [(53.0, -8.001)] * 5,
                loaded,
            )
    finally:
        loaded.close()


def test_maxweight_parser_supports_numeric_units_and_fails_closed_values():
    assert parse_maxweight_profile("3.5 t") == ("supported", 3.5)
    assert parse_maxweight_profile("3 lt") == ("supported", 3.0)
    assert parse_maxweight_t("unrestricted") is None
    assert parse_maxweight_profile("below_default") == ("unsupported", None)


def test_hgv_permitted_rating_parser_is_distinct_and_fails_closed_values():
    assert parse_maxweightrating_hgv_profile("7.5 t") == ("supported", 7.5)
    assert parse_maxweightrating_hgv_t("7.5 t") == 7.5
    assert parse_maxweightrating_hgv_profile("unrestricted") == ("unlimited", None)
    assert parse_maxweightrating_hgv_profile("no") == ("unsupported", None)
    assert parse_maxweightrating_hgv_t("no") is None
    assert parse_maxweightrating_hgv_profiles("", "3") == ("supported", 3.0)
    assert parse_maxweightrating_hgv_profiles("7.5", "3") == ("supported", 3.0)
    assert parse_maxweightrating_hgv_profiles("no", "3") == ("unsupported", None)


def test_hgv_destination_profile_parser_supports_static_and_conditional_forms():
    entries = parse_hgv_destination_profiles(
        {
            "hgv": "destination",
            "maxweight:hgv:conditional": "none @ (destination)",
            "maxweightrating:hgv:conditional": "none @ destination",
        }
    )
    assert [entry["kind"] for entry in entries] == [
        "access",
        "maxweight_hgv_exception",
        "maxweightrating_hgv_exception",
    ]
    assert all(entry["status"] == "supported" for entry in entries)
    unsupported = parse_hgv_destination_profiles(
        {"maxweight:hgv:conditional": "5 @ destination"}
    )
    assert unsupported[0]["kind"] == "maxweight_hgv_exception_unsupported"
    assert unsupported[0]["status"] == "unsupported"


def test_sqlite_graph_enforces_hgv_destination_access_and_limit_exceptions(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "b": (53.0, -8.001),
        "c": (53.001, -8.0),
    }
    graph_path = tmp_path / "hgv-destination-graph"
    write_sqlite_graph(
        graph_path,
        coordinates,
        [
            {"u": "a", "v": "b", "length_m": "2", "hgv": "destination"},
            {"u": "a", "v": "c", "length_m": "5"},
            {"u": "c", "v": "b", "length_m": "5"},
        ],
        source="fixture.pbf",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.hgv_destination_way_n == 1
    assert shortest_path("a", "b", loaded, vehicle_class="general") == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv") == 10
    assert shortest_path(
        "a", "b", loaded, vehicle_class="hgv", allow_hgv_destination=True
    ) == 2
    loaded.close()

    exception_path = tmp_path / "hgv-destination-exception-graph"
    write_sqlite_graph(
        exception_path,
        coordinates,
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "2",
                "maxweight:hgv": "3",
                "maxweight:hgv:conditional": "none @ destination",
            },
            {"u": "a", "v": "c", "length_m": "5"},
            {"u": "c", "v": "b", "length_m": "5"},
        ],
        source="fixture.pbf",
    )
    loaded = load_graph(exception_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_weight_t=2) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_weight_t=4) == 10
    assert shortest_path(
        "a",
        "b",
        loaded,
        vehicle_class="hgv",
        vehicle_weight_t=4,
        allow_hgv_destination=True,
    ) == 2
    loaded.close()


def test_maxheight_parser_and_sqlite_filter_enforce_legal_physical_and_ambiguous_values(tmp_path):
    assert parse_maxheight_profile("3.5 m") == ("supported", 3.5)
    status, imperial_limit = parse_maxheight_profile("9'6\"")
    assert status == "supported"
    assert imperial_limit == pytest.approx(2.8956)
    status, centimetre_limit = parse_maxheight_profile("190 cm")
    assert status == "supported"
    assert centimetre_limit == pytest.approx(1.9)
    assert parse_maxheight_profile("none") == ("unlimited", None)
    assert parse_maxheight_profile("default") == ("unsupported", None)
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
        "d": (53.001, -8.0015),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {
            "u": "v",
            "v": "b",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "2",
            "maxheight": "9'6\"",
            "maxheight_physical": "3.0",
        },
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {
            "u": "v",
            "v": "d",
            "length_m": "0.5",
            "oneway": "yes",
            "way_id": "5",
            "maxheight": "default",
        },
        {"u": "d", "v": "b", "length_m": "0.5", "oneway": "yes", "way_id": "6"},
    ]
    graph_path = tmp_path / "maxheight-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_maxheight_profiles is True
    assert loaded.maxheight_way_n == 2
    assert loaded.maxheight_supported_way_n == 1
    assert loaded.maxheight_unsupported_way_n == 1
    assert loaded.maxheight_physical_way_n == 1
    assert loaded.maxheight_physical_supported_way_n == 1
    assert shortest_path("a", "b", loaded) == 2
    assert shortest_path("a", "b", loaded, vehicle_height_m=2.5) == 2
    assert shortest_path("a", "b", loaded, vehicle_height_m=3.2) == 11
    assert shortest_path("a", "b", loaded, vehicle_height_m=4) == 11
    loaded.close()
    output = tmp_path / "maxheight-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--height-m",
            "3.2",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_height_m"] == 3.2
    assert result["graph"]["maxheight_profiles"] is True
    assert result["graph"]["maxheight_supported_way_n"] == 1
    assert "maxheight limits enforced at 3.2 m" in result["method"]


def test_vehicle_dimension_profiles_parse_and_enforce_width_length_axleload(tmp_path):
    assert parse_maxwidth_profile("8'6\"") == ("supported", 2.5908)
    assert parse_maxwidth_profile("8 ft") == ("supported", 2.4384)
    assert parse_maxlength_profile("30'") == ("supported", 9.144)
    assert parse_maxlength_profile("default") == ("unsupported", None)
    assert parse_maxaxleload_profile("3 t") == ("supported", 3.0)
    assert parse_maxaxleload_profile("unrestricted") == ("unlimited", None)
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
        "d": (53.001, -8.0015),
        "e": (53.002, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {
            "u": "v",
            "v": "b",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "2",
            "maxwidth": "2.5",
            "maxlength": "8'6\"",
            "maxaxleload": "3",
        },
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {
            "u": "v",
            "v": "d",
            "length_m": "0.5",
            "oneway": "yes",
            "way_id": "5",
            "maxwidth": "default",
            "maxlength": "default",
            "maxaxleload": "default",
        },
        {"u": "d", "v": "b", "length_m": "0.5", "oneway": "yes", "way_id": "6"},
        {
            "u": "v",
            "v": "e",
            "length_m": "0.25",
            "oneway": "yes",
            "way_id": "7",
            "maxwidth": "none",
            "maxlength": "default",
            "maxaxleload": "default",
        },
        {"u": "e", "v": "b", "length_m": "0.25", "oneway": "yes", "way_id": "8"},
    ]
    graph_path = tmp_path / "vehicle-dimension-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_vehicle_dimension_profiles is True
    assert loaded.maxwidth_way_n == 3
    assert loaded.maxwidth_supported_way_n == 1
    assert loaded.maxwidth_unlimited_way_n == 1
    assert loaded.maxlength_way_n == 3
    assert loaded.maxaxleload_way_n == 3
    assert shortest_path("a", "b", loaded) == 1.5
    assert shortest_path(
        "a",
        "b",
        loaded,
        vehicle_width_m=2.5,
        vehicle_length_m=2.5,
        vehicle_axleload_t=3,
    ) == 2
    assert shortest_path("a", "b", loaded, vehicle_width_m=2.6) == 1.5
    assert shortest_path("a", "b", loaded, vehicle_width_m=100) == 1.5
    assert shortest_path("a", "b", loaded, vehicle_length_m=2.6) == 11
    assert shortest_path("a", "b", loaded, vehicle_axleload_t=3.1) == 11
    loaded.close()
    output = tmp_path / "vehicle-dimension-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--width-m",
            "2.5",
            "--length-m",
            "2.5",
            "--axleload-t",
            "3",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_width_m"] == 2.5
    assert result["vehicle_length_m"] == 2.5
    assert result["vehicle_axleload_t"] == 3.0
    assert result["graph"]["maxwidth_profiles"] is True
    assert "maxwidth limits enforced at 2.5 m" in result["method"]
    assert "maxlength limits enforced at 2.5 m" in result["method"]
    assert "maxaxleload limits enforced at 3 t" in result["method"]


def test_maxspeed_parser_and_duration_routing_apply_per_way_ceilings(tmp_path):
    assert parse_maxspeed_profile("50") == ("supported", 50.0)
    assert parse_maxspeed_profile("50 mph") == ("supported", 80.4672)
    assert parse_maxspeed_kmh("10 knots") == 18.52
    assert parse_maxspeed_profile("none") == ("unlimited", None)
    assert parse_maxspeed_profile("RO:urban") == ("unsupported", None)

    graph_path = tmp_path / "maxspeed-graph"
    write_sqlite_graph(
        graph_path,
        {
            "a": (53.0, -8.0),
            "b": (53.0, -8.001),
            "c": (53.0, -8.002),
        },
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "yes",
                "way_id": "slow",
                "maxspeed": "30",
            },
            {
                "u": "b",
                "v": "c",
                "length_m": "1000",
                "oneway": "yes",
                "way_id": "fast",
                "maxspeed": "100 mph",
            },
            {
                "u": "a",
                "v": "c",
                "length_m": "4000",
                "oneway": "yes",
                "way_id": "ambiguous",
                "maxspeed": "RO:urban",
            },
        ],
        source="fixture.pbf",
        complete=True,
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_maxspeed_profiles is True
    assert loaded.maxspeed_way_n == 3
    assert loaded.maxspeed_supported_way_n == 2
    assert loaded.maxspeed_unsupported_way_n == 1
    distance, duration = shortest_path_metrics(
        "a",
        "c",
        loaded,
        speed_kmh=60,
        objective="duration",
    )
    assert distance == 2000.0
    assert duration == pytest.approx(180.0)
    loaded.close()


def test_conditional_maxspeed_profiles_are_time_aware_and_fail_closed(tmp_path):
    supported = parse_maxspeed_conditional_profile("30 @ (Mo-Fr 08:00-18:00)")
    assert supported[0]["status"] == "supported"
    assert supported[0]["speed_kmh"] == 30.0
    assert parse_maxspeed_conditional_profile("20 mph @ flashing")[0]["status"] == "unsupported"
    assert parse_maxspeed_conditional_profile("30")[0]["condition"] == "24/7"

    graph_path = tmp_path / "conditional-maxspeed-graph"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "yes",
                "way_id": "school",
                "maxspeed": "50",
                "maxspeed:conditional": "30 @ (Mo-Fr 08:00-18:00)",
            },
        ],
        source="fixture.pbf",
        complete=True,
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_maxspeed_conditional_profiles is True
    assert loaded.maxspeed_conditional_way_n == 1
    assert loaded.maxspeed_conditional_supported_way_n == 1
    active = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        speed_kmh=60,
        objective="duration",
    )
    inactive = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 7, 0, tzinfo=timezone.utc),
        speed_kmh=60,
        objective="duration",
    )
    assert active is not None and active[1] == pytest.approx(120.0)
    assert inactive is not None and inactive[1] == pytest.approx(72.0)
    loaded.close()


def test_conditional_oneway_profiles_are_time_aware_and_fail_closed(tmp_path):
    schedule_only = parse_oneway_conditional_profile("Mo-Fr 08:00-10:00")
    assert schedule_only[0]["mode"] == "yes"
    assert schedule_only[0]["status"] == "supported"
    seasonal = parse_oneway_conditional_profile("yes @ May-Sep")
    assert seasonal[0]["status"] == "supported"
    unsupported = parse_oneway_conditional_profile("no @ permit")
    assert unsupported[0]["status"] == "unsupported"

    graph_path = tmp_path / "conditional-oneway-graph"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "",
                "way_id": "school",
                "oneway:conditional": "Mo-Fr 08:00-10:00",
            },
        ],
        source="fixture.pbf",
        complete=True,
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_oneway_conditional_profiles is True
    assert loaded.oneway_conditional_way_n == 1
    assert loaded.oneway_conditional_supported_way_n == 1
    active_departure = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    inactive_departure = datetime(2026, 8, 17, 7, 0, tzinfo=timezone.utc)
    assert shortest_path("b", "a", loaded, departure=active_departure) is None
    assert shortest_path("b", "a", loaded, departure=inactive_departure) == 1000
    assert shortest_path_metrics(
        "b",
        "a",
        loaded,
        departure=active_departure,
        objective="duration",
    ) is None
    loaded.close()

    reverse_graph_path = tmp_path / "conditional-oneway-reverse-graph"
    write_sqlite_graph(
        reverse_graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "yes",
                "way_id": "school",
                "oneway:conditional": "no @ (Mo-Fr 08:00-10:00)",
            },
        ],
        source="fixture.pbf",
        complete=True,
    )
    loaded = load_graph(reverse_graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert shortest_path("b", "a", loaded, departure=active_departure) == 1000
    assert shortest_path("b", "a", loaded, departure=inactive_departure) is None
    loaded.close()

    unsupported_graph_path = tmp_path / "unsupported-conditional-oneway-graph"
    write_sqlite_graph(
        unsupported_graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "yes",
                "way_id": "permit-road",
                "oneway:conditional": "no @ permit",
            },
        ],
        source="fixture.pbf",
        complete=True,
    )
    loaded = load_graph(unsupported_graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.oneway_conditional_supported_way_n == 0
    assert loaded.oneway_conditional_unsupported_way_n == 1
    assert shortest_path("b", "a", loaded, departure=active_departure) is None
    loaded.close()


def test_sqlite_graph_enforces_generic_maxweight_only_with_weight_profile(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
        "d": (53.001, -8.0015),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2", "maxweight": "3.5"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {"u": "v", "v": "d", "length_m": "0.5", "oneway": "yes", "way_id": "5", "maxweight": "below_default"},
        {"u": "d", "v": "b", "length_m": "0.5", "oneway": "yes", "way_id": "6"},
    ]
    graph_path = tmp_path / "maxweight-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_maxweight_profiles is True
    assert loaded.maxweight_way_n == 2
    assert loaded.maxweight_supported_way_n == 1
    assert loaded.maxweight_unsupported_way_n == 1
    assert shortest_path("a", "b", loaded) == 2
    assert shortest_path("a", "b", loaded, vehicle_weight_t=3.5) == 2
    assert shortest_path("a", "b", loaded, vehicle_weight_t=4) == 11
    assert shortest_path("a", "b", loaded, vehicle_weight_t=2) == 2
    loaded.close()
    output = tmp_path / "maxweight-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--weight-t",
            "4",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["graph"]["maxweight_profiles"] is True
    assert result["graph"]["maxweight_supported_way_n"] == 1
    assert "generic maxweight limits enforced at 4 t" in result["method"]


def test_sqlite_graph_enforces_hgv_specific_maxweight_for_hgv_profiles(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {
            "u": "v",
            "v": "b",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "2",
            "maxweight": "20",
            "maxweight_hgv": "3.5 t",
        },
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "hgv-maxweight-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_hgv_maxweight_profiles is True
    assert loaded.maxweight_hgv_way_n == 1
    assert loaded.maxweight_hgv_supported_way_n == 1
    assert shortest_path("a", "b", loaded, vehicle_weight_t=4) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="delivery", vehicle_weight_t=4) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_weight_t=3.5) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_weight_t=4) == 11
    loaded.close()
    assert parse_conditional_access_profile_rule("hgv @ (Mo-Fr 07:00-19:00)") == (
        "allow",
        "hgv",
        "(Mo-Fr 07:00-19:00)",
    )


def test_sqlite_graph_enforces_hgv_permitted_rating_separately_from_actual_weight(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {
            "u": "v",
            "v": "b",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "2",
            "maxweight": "20",
            "maxweightrating:hgv": "7.5 t",
        },
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "hgv-rating-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.has_hgv_maxweightrating_profiles is True
    assert loaded.maxweightrating_hgv_way_n == 1
    assert loaded.maxweightrating_hgv_supported_way_n == 1
    assert shortest_path("a", "b", loaded) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_rating_t=7.5) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_rating_t=8) == 11
    assert shortest_path(
        "a", "b", loaded, vehicle_class="delivery", vehicle_rating_t=8
    ) == 2
    assert shortest_path(
        "a", "b", loaded, vehicle_class="hgv", vehicle_weight_t=19, vehicle_rating_t=8
    ) == 11
    loaded.close()
    output = tmp_path / "hgv-rating-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--vehicle-class",
            "hgv",
            "--rating-t",
            "8",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_rating_t"] == 8.0
    assert result["graph"]["maxweightrating_hgv_profiles"] is True
    assert result["route_distance_m"] == 11.0
    assert "HGV maxweightrating limits enforced at 8 t" in result["method"]


def test_sqlite_graph_fails_closed_for_ambiguous_hgv_permitted_rating(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {
            "u": "a",
            "v": "v",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "ambiguous",
            "maxweightrating:hgv": "no",
        },
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "a", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "hgv-rating-ambiguous-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.maxweightrating_hgv_unsupported_way_n == 1
    assert shortest_path("a", "b", loaded) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_rating_t=1) == 10
    loaded.close()


def test_sqlite_graph_enforces_irish_goods_permitted_rating_alias(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {
            "u": "v",
            "v": "b",
            "length_m": "1",
            "oneway": "yes",
            "way_id": "irish-goods-limit",
            "maxweightrating:goods": "3",
        },
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "hgv-goods-rating-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.maxweightrating_hgv_way_n == 1
    assert loaded.maxweightrating_hgv_supported_way_n == 1
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_rating_t=3) == 2
    assert shortest_path("a", "b", loaded, vehicle_class="hgv", vehicle_rating_t=3.1) == 11
    assert shortest_path("a", "b", loaded, vehicle_class="general", vehicle_rating_t=3.1) == 2
    loaded.close()


def test_sqlite_graph_can_opt_into_persisted_ferry_geometry(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "f1": (53.0, -8.001),
        "f2": (53.0, -8.003),
        "b": (53.0, -8.004),
    }
    edges = [
        {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road-a"},
        {"u": "f2", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road-b"},
    ]
    graph_path = tmp_path / "ferry-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            ("f1", "f2", 10.0, "yes", "ferry/1"),
        )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.ferry_edge_count == 1
    assert shortest_path("a", "b", loaded) is None
    assert shortest_path("a", "b", loaded, include_ferries=True) == 12
    loaded.close()
    output = tmp_path / "ferry-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.004",
            "--include-ferries",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reachable"] is True
    assert result["route_distance_m"] == 12.0
    assert result["graph"]["ferry_edge_n"] == 1
    assert "ferry geometry included" in result["method"]


def test_ferry_schedule_contract_filters_service_and_applies_crossing_duration(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "f1": (53.0, -8.001),
        "f2": (53.0, -8.003),
        "b": (53.0, -8.004),
    }
    edges = [
        {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road-a"},
        {"u": "f2", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road-b"},
    ]
    graph_path = tmp_path / "scheduled-ferry-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            ("f1", "f2", 10.0, "yes", "ferry/1"),
        )
    (graph_path / "ferry_schedules.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [
                    {
                        "way_id": "ferry/1",
                        "opening_hours": "Mo-Fr 07:00-10:00",
                        "duration_s": 1800,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.ferry_schedule_n == 1
    assert loaded.ferry_duration_n == 1
    departure = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    assert shortest_path("a", "b", loaded, departure=departure, include_ferries=True) == 12
    assert shortest_path("a", "b", loaded, departure=datetime(2026, 8, 17, 12, tzinfo=timezone.utc), include_ferries=True) is None
    metrics = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=departure,
        speed_kmh=36,
        return_path=True,
        include_ferries=True,
    )
    assert metrics is not None
    assert metrics[0] == 12.0
    assert metrics[1] == pytest.approx(1800.2)
    assert metrics[2] == ["a", "f1", "f2", "b"]
    detailed = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=departure,
        speed_kmh=36,
        return_path=True,
        return_path_details=True,
        include_ferries=True,
    )
    assert detailed is not None
    assert detailed[3] == ["road-a", "ferry/1", "road-b"]
    assert detailed[4] == [
        {
            "from_node": "a",
            "to_node": "f1",
            "way_id": "road-a",
            "distance_m": 1.0,
            "duration_s": pytest.approx(0.1),
            "wait_s": 0.0,
            "ferry": False,
        },
        {
            "from_node": "f1",
            "to_node": "f2",
            "way_id": "ferry/1",
            "distance_m": 10.0,
            "duration_s": pytest.approx(1800.0),
            "wait_s": 0.0,
            "ferry": True,
        },
        {
            "from_node": "f2",
            "to_node": "b",
            "way_id": "road-b",
            "distance_m": 1.0,
            "duration_s": pytest.approx(0.1),
            "wait_s": 0.0,
            "ferry": False,
        },
    ]
    waiting = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
        speed_kmh=36,
        return_path=True,
        include_ferries=True,
    )
    assert waiting is not None
    assert waiting[0] == 12.0
    assert waiting[1] == pytest.approx(19 * 3600 + 1800.1)
    loaded.close()
    assert parse_duration_seconds("30 min") == 1800.0
    assert parse_duration_seconds("00:30") == 1800.0
    assert parse_duration_seconds(1800) == 1800.0
    listed_days = parse_weekly_schedule("Mo,We,Fr 07:00-10:00")
    assert listed_days is not None
    assert listed_days.active_at(departure) is True
    assert listed_days.active_at(datetime(2026, 8, 18, 8, tzinfo=timezone.utc)) is False

    output = tmp_path / "scheduled-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.004",
            "--departure",
            "2026-08-17T08:00:00+00:00",
            "--speed-kmh",
            "36",
            "--include-ferries",
            "--include-path",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reachable"] is True
    assert result["estimated_duration_s"] == 1800.2
    assert result["arrival"] == "2026-08-17T08:30:00.200000+00:00"
    assert result["ferry_wait_s"] == 0.0
    assert result["ferry_wait_n"] == 0
    assert result["ferry_way_ids"] == ["ferry/1"]
    assert result["ferry_distance_m"] == 10.0
    assert result["ferry_crossing_s"] == 1800.0
    assert result["ferry_edge_n"] == 1
    assert result["path_segment_total_wait_s"] == 0.0
    assert result["path_segments"][1]["ferry"] is True
    assert result["path_segments"][1]["duration_s"] == 1800.0
    assert [maneuver["kind"] for maneuver in result["maneuvers"]] == [
        "start",
        "ferry_boarding",
        "ferry_landing",
        "arrive",
    ]
    assert result["maneuvers"][1]["road_context"]["route"] == "ferry"
    assert "ferry service windows and waiting evaluated" in result["method"]
    assert "ferry crossing durations applied" in result["method"]

    waiting_output = tmp_path / "waiting-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.004",
            "--departure",
            "2026-08-17T12:00:00+00:00",
            "--speed-kmh",
            "36",
            "--include-ferries",
            "--include-path",
            "--out",
            str(waiting_output),
        ]
    )
    waiting_result = json.loads(waiting_output.read_text(encoding="utf-8"))
    assert waiting_result["reachable"] is True
    assert waiting_result["ferry_wait_s"] == pytest.approx(68399.9)
    assert waiting_result["ferry_wait_n"] == 1
    assert waiting_result["ferry_way_ids"] == ["ferry/1"]
    assert waiting_result["ferry_distance_m"] == 10.0
    assert waiting_result["ferry_crossing_s"] == 1800.0
    assert waiting_result["ferry_edge_n"] == 1
    assert waiting_result["path_segment_total_wait_s"] == 68399.9
    assert waiting_result["path_segments"][1]["wait_s"] == 68399.9


def test_route_duration_objective_can_prefer_a_longer_road_over_a_slow_ferry(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "f1": (53.0, -8.001),
        "f2": (53.0, -8.002),
        "c": (53.001, -8.001),
        "b": (53.0, -8.003),
    }
    edges = [
        {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road-a"},
        {"u": "f2", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road-b"},
        {"u": "a", "v": "c", "length_m": "50", "oneway": "yes", "way_id": "road-long-a"},
        {"u": "c", "v": "b", "length_m": "50", "oneway": "yes", "way_id": "road-long-b"},
    ]
    graph_path = tmp_path / "objective-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            ("f1", "f2", 5.0, "yes", "ferry/slow"),
        )
    (graph_path / "ferry_schedules.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [{"way_id": "ferry/slow", "opening_hours": "Mo-Fr 07:00-10:00", "duration_s": 1800}],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    departure = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    shortest = shortest_path_metrics(
        "a", "b", loaded, departure=departure, speed_kmh=36, return_path=True, include_ferries=True
    )
    fastest = shortest_path_metrics(
        "a", "b", loaded, departure=departure, speed_kmh=36, return_path=True, include_ferries=True, objective="duration"
    )
    assert shortest is not None and fastest is not None
    assert shortest[0] == pytest.approx(7.0)
    assert shortest[2] == ["a", "f1", "f2", "b"]
    assert fastest[0] == pytest.approx(100.0)
    assert fastest[1] == pytest.approx(10.0)
    assert fastest[2] == ["a", "c", "b"]
    loaded.close()


def test_ferry_schedule_parser_supports_observed_seasonal_and_daily_syntax():
    seasonal_text = (
        "Jun-Aug: Mo-Sa 07:00-21:50; "
        "Jun-Aug: Su 09:00-21:50; Dec 25"
    )
    assert parse_weekly_schedule(seasonal_text) is None
    seasonal = parse_weekly_schedule(seasonal_text, allow_calendar=True)
    assert seasonal is not None
    assert seasonal.active_at(datetime(2026, 7, 6, 10, tzinfo=timezone.utc)) is True
    assert seasonal.active_at(datetime(2026, 9, 6, 10, tzinfo=timezone.utc)) is False
    assert seasonal.active_at(datetime(2026, 12, 25, 10, tzinfo=timezone.utc)) is False

    daily = parse_weekly_schedule(
        "Mo-Fr 07:30-22:45, Sa 08:00-23:15, Su 09:30-22:45",
        allow_calendar=True,
    )
    assert daily is not None
    assert daily.active_at(datetime(2026, 8, 17, 8, tzinfo=timezone.utc)) is True
    assert daily.active_at(datetime(2026, 8, 16, 8, tzinfo=timezone.utc)) is False
    assert parse_weekly_schedule("Jul-Sep Mo-Su 09:00-20:30", allow_calendar=True) is not None
    holiday_schedule_text = (
        "Apr-Jun Mo-Sa,PH 07:45-21:30; Apr-Jun Su 09:00-21:30"
    )
    assert parse_weekly_schedule(holiday_schedule_text) is None
    holiday_schedule = parse_weekly_schedule(holiday_schedule_text, allow_calendar=True)
    assert holiday_schedule is not None
    assert holiday_schedule.requires_public_holiday_calendar is True
    sunday_morning = datetime(2026, 6, 7, 8, tzinfo=timezone.utc)
    assert holiday_schedule.active_at(sunday_morning) is False
    assert holiday_schedule.active_at(sunday_morning, frozenset({date(2026, 6, 7)})) is True


def test_ferry_wait_is_applied_once_for_a_multi_segment_way(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "f1": (53.0, -8.001),
        "f2": (53.0, -8.002),
        "f3": (53.0, -8.003),
        "b": (53.0, -8.004),
    }
    edges = [
        {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road-a"},
        {"u": "f3", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road-b"},
    ]
    graph_path = tmp_path / "multi-segment-ferry-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            [
                ("f1", "f2", 5.0, "yes", "ferry/1"),
                ("f2", "f3", 5.0, "yes", "ferry/1"),
            ],
        )
    (graph_path / "ferry_schedules.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [
                    {
                        "way_id": "ferry/1",
                        "opening_hours": "Mo-Fr 07:00-10:00",
                        "duration_s": 1800,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    result = shortest_path_metrics(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
        speed_kmh=36,
        return_path=True,
        include_ferries=True,
    )
    assert result is not None
    assert result[0] == 12.0
    assert result[1] == pytest.approx(19 * 3600 + 1800.1)
    assert result[2] == ["a", "f1", "f2", "f3", "b"]
    loaded.close()


def test_ferry_schedule_contract_applies_explicit_public_holiday_calendar(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "f1": (53.0, -8.001),
        "f2": (53.0, -8.003),
        "b": (53.0, -8.004),
    }
    edges = [
        {"u": "a", "v": "f1", "length_m": "1", "oneway": "yes", "way_id": "road-a"},
        {"u": "f2", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "road-b"},
    ]
    graph_path = tmp_path / "public-holiday-ferry-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
            ("f1", "f2", 10.0, "yes", "ferry/holiday"),
        )
    (graph_path / "ferry_schedules.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [
                    {
                        "way_id": "ferry/holiday",
                        "opening_hours": "Mo-Sa,PH 07:00-10:00",
                        "duration_s": 1800,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sunday = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.ferry_schedule_n == 1
    assert loaded.ferry_public_holiday_schedule_n == 1
    assert loaded.public_holiday_contract_status == "not_provided"
    assert shortest_path("a", "b", loaded, departure=sunday, include_ferries=True) is None
    loaded.close()

    (graph_path / "public_holidays.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.public-holidays.v1",
                "source": "fixture-calendar",
                "dates": ["2026-08-16"],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.public_holiday_contract_status == "available"
    assert loaded.public_holiday_n == 1
    assert loaded.public_holiday_min_date == "2026-08-16"
    assert loaded.public_holiday_max_date == "2026-08-16"
    assert shortest_path("a", "b", loaded, departure=sunday, include_ferries=True) == 12
    loaded.close()

    output = tmp_path / "public-holiday-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.004",
            "--departure",
            "2026-08-16T08:00:00+00:00",
            "--include-ferries",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["graph"]["public_holiday_contract"] == "available"
    assert result["graph"]["public_holiday_n"] == 1
    assert result["graph"]["public_holiday_min_date"] == "2026-08-16"
    assert result["graph"]["public_holiday_max_date"] == "2026-08-16"
    assert "public-holiday calendar applied" in result["method"]


def test_sqlite_graph_metadata_tracks_public_holiday_companion(tmp_path):
    graph_path = tmp_path / "calendar-metadata-graph"
    (graph_path).mkdir()
    (graph_path / "public_holidays.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.public-holidays.v1",
                "source": "fixture-calendar",
                "dates": ["2026-01-01", "2026-12-26"],
            }
        ),
        encoding="utf-8",
    )
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [{"u": "a", "v": "b", "length_m": "1", "oneway": "yes"}],
        source="fixture.pbf",
    )
    metadata = json.loads(
        (graph_path / "road_graph_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["public_holiday_contract"] == "available"
    assert metadata["public_holiday_n"] == 2
    assert metadata["public_holiday_min_date"] == "2026-01-01"
    assert metadata["public_holiday_max_date"] == "2026-12-26"


def test_cached_public_holiday_calendar_contains_verified_2026_dates():
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "data/roads/public_holidays.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["contract"] == "ireland-geometry.public-holidays.v1"
    assert {
        value for value in payload["dates"] if value.startswith("2026-")
    } == {
        "2026-01-01",
        "2026-02-02",
        "2026-03-17",
        "2026-04-06",
        "2026-05-04",
        "2026-06-01",
        "2026-08-03",
        "2026-10-26",
        "2026-12-25",
        "2026-12-26",
    }


def test_ferry_schedule_contract_rejects_unknown_contract(tmp_path):
    graph_path = tmp_path / "invalid-ferry-contract"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [{"u": "a", "v": "b", "length_m": "1", "oneway": "yes"}],
        source="fixture.pbf",
    )
    (graph_path / "ferry_schedules.json").write_text(
        json.dumps({"contract": "wrong", "schedules": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ferry schedule contract"):
        load_graph(graph_path)


def test_public_holiday_contract_rejects_invalid_dates(tmp_path):
    graph_path = tmp_path / "invalid-public-holiday-contract"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [{"u": "a", "v": "b", "length_m": "1", "oneway": "yes"}],
        source="fixture.pbf",
    )
    (graph_path / "public_holidays.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.public-holidays.v1",
                "dates": ["2026-02-30"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ISO-8601"):
        load_graph(graph_path)


def test_ferry_schedule_contract_rejects_symlinked_companion(tmp_path):
    graph_path = tmp_path / "symlinked-ferry-contract"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [{"u": "a", "v": "b", "length_m": "1", "oneway": "yes"}],
        source="fixture.pbf",
    )
    target = tmp_path / "ferry-schedules.json"
    target.write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        (graph_path / "ferry_schedules.json").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    with pytest.raises(ValueError, match="must not be a symlink"):
        load_graph(graph_path)


def test_sqlite_graph_applies_no_and_only_turn_restrictions(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
        "d": (52.999, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {"u": "v", "v": "d", "length_m": "2", "oneway": "yes", "way_id": "5"},
        {"u": "d", "v": "b", "length_m": "2", "oneway": "yes", "way_id": "6"},
    ]
    graph_path = tmp_path / "turn-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO turn_restrictions(relation_id, via_node, from_way, to_way, kind) VALUES (?, ?, ?, ?, ?)",
            [
                ("relation/no", "v", "1", "2", "no"),
                ("relation/only", "v", "1", "3", "only"),
            ],
        )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.turn_restriction_n == 2
    assert loaded.turn_allowed("v", "1", "2") is False
    assert loaded.turn_allowed("v", "1", "3") is True
    assert loaded.turn_allowed("v", "1", "5") is False
    assert shortest_path("a", "b", loaded) == 11
    loaded.close()


def test_sqlite_graph_applies_via_way_no_and_only_restrictions(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "x": (53.0, -8.001),
        "y": (53.0, -8.002),
        "b": (53.0, -8.003),
        "c": (53.001, -8.002),
        "d": (52.999, -8.002),
    }
    edges = [
        {"u": "a", "v": "x", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "x", "v": "y", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "y", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "3"},
        {"u": "y", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "5"},
        {"u": "y", "v": "d", "length_m": "2", "oneway": "yes", "way_id": "6"},
        {"u": "d", "v": "b", "length_m": "2", "oneway": "yes", "way_id": "7"},
    ]
    graph_path = tmp_path / "via-way-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("relation/no-via", "y", "1", "3", "no", '["2"]'),
                ("relation/only-via", "y", "1", "4", "only", '["2"]'),
            ],
        )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    active = (("1", "2"),)
    assert loaded.turn_allowed("y", "2", "3", active) is False
    assert loaded.turn_allowed("y", "2", "4", active) is True
    assert loaded.turn_allowed("y", "2", "6", active) is False
    assert shortest_path("a", "b", loaded) == 12
    loaded.close()


def test_route_query_emits_via_way_transition_provenance(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "x": (53.0, -8.001),
        "y": (53.0, -8.002),
        "b": (53.0, -8.003),
        "c": (53.001, -8.002),
    }
    edges = [
        {"u": "a", "v": "x", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "x", "v": "y", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "y", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "3"},
        {"u": "y", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "4"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "5"},
    ]
    graph_path = tmp_path / "via-way-query-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("relation/no-via", "y", "1", "3", "no", '["2"]'),
                ("relation/only-via", "y", "1", "4", "only", '["2"]'),
            ],
        )
    output = tmp_path / "via-way-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.003",
            "--include-path",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["path_way_ids"] == ["1", "2", "4", "5"]
    transition_rules = result["path_segments"][2]["transition_rules"]
    assert transition_rules == [
        {
            "key": "turn_restriction",
            "relation_id": "relation/no-via",
            "via_node": "y",
            "from_way": "1",
            "to_way": "3",
            "via_way_ids": ["2"],
            "kind": "no",
            "condition": "",
            "status": "supported",
            "evaluated": True,
            "active": True,
            "applied": True,
            "selected": False,
            "profile": "unconditional",
        },
        {
            "key": "turn_restriction",
            "relation_id": "relation/only-via",
            "via_node": "y",
            "from_way": "1",
            "to_way": "4",
            "via_way_ids": ["2"],
            "kind": "only",
            "condition": "",
            "status": "supported",
            "evaluated": True,
            "active": True,
            "applied": True,
            "selected": True,
            "profile": "unconditional",
        },
    ]
    assert result["path_segments"][0]["transition_rules"] == []
    assert result["path_segments"][1]["transition_rules"] == []
    assert result["path_segments"][3]["transition_rules"] == []


def test_route_query_emits_human_readable_way_context(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "b": (53.0, -8.001),
        "c": (53.0, -8.002),
    }
    edges = [
        {
            "u": "a",
            "v": "b",
            "length_m": "10",
            "oneway": "yes",
            "way_id": "way/main",
            "highway": "primary",
            "name": "Main Street",
            "ref": "R100",
        },
        {
            "u": "b",
            "v": "c",
            "length_m": "10",
            "oneway": "no",
            "way_id": "way/branch",
            "highway": "residential",
            "name": "Harbour Road",
        },
    ]
    graph_path = tmp_path / "way-context-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    output = tmp_path / "way-context-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--include-path",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["graph"]["way_context"] is True
    assert result["graph"]["way_context_n"] == 2
    assert result["path_segments"][0]["road_context"] == {
        "name": "Main Street",
        "ref": "R100",
        "highway": "primary",
        "route": None,
        "oneway": "yes",
    }
    assert result["path_segments"][1]["road_context"] == {
        "name": "Harbour Road",
        "ref": None,
        "highway": "residential",
        "route": None,
        "oneway": "no",
    }


def test_route_query_emits_geometry_derived_maneuvers(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "b": (53.0, -8.001),
        "c": (53.001, -8.001),
    }
    edges = [
        {
            "u": "a",
            "v": "b",
            "length_m": "10",
            "oneway": "yes",
            "way_id": "way/main",
            "highway": "primary",
            "name": "Main Street",
            "ref": "R100",
        },
        {
            "u": "b",
            "v": "c",
            "length_m": "10",
            "oneway": "yes",
            "way_id": "way/branch",
            "highway": "residential",
            "name": "Harbour Road",
        },
    ]
    graph_path = tmp_path / "maneuver-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    output = tmp_path / "maneuver-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.001",
            "--goal-lon",
            "-8.001",
            "--include-path",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["maneuver_n"] == 3
    assert [maneuver["kind"] for maneuver in result["maneuvers"]] == [
        "start",
        "right",
        "arrive",
    ]
    assert result["maneuvers"][0]["coordinate"] == [-8.0, 53.0]
    assert result["maneuvers"][0]["to_way_id"] == "way/main"
    assert result["maneuvers"][0]["distance_m"] == 10.0
    assert result["maneuvers"][1]["node_id"] == "b"
    assert result["maneuvers"][1]["from_way_id"] == "way/main"
    assert result["maneuvers"][1]["to_way_id"] == "way/branch"
    assert result["maneuvers"][1]["bearing_before_deg"] == 270.0
    assert result["maneuvers"][1]["bearing_after_deg"] == 0.0
    assert result["maneuvers"][1]["turn_angle_deg"] == 90.0
    assert result["maneuvers"][1]["road_context"]["name"] == "Harbour Road"
    assert result["maneuvers"][2]["to_way_id"] is None
    assert result["maneuvers"][2]["distance_m"] == 0.0


def test_sqlite_graph_evaluates_conditional_turn_windows_with_departure_profile(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "conditional-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json, condition) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("relation/conditional", "v", "1", "2", "no", "[]", "Mo-Fr 07:00-10:00"),
        )
    schedule = parse_weekly_schedule("(Mo-Fr 07:00-10:00)")
    assert schedule is not None
    assert schedule.active_at(datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)) is True
    assert schedule.active_at(datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)) is False
    assert parse_weekly_schedule("(weight>7.5)") is None
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_restriction_n == 1
    assert shortest_path("a", "b", loaded, departure=datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)) == 11
    assert shortest_path("a", "b", loaded, departure=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)) == 2
    loaded.close()


def test_sqlite_graph_evaluates_vehicle_weight_conditional_turns(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "weight-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json, condition) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("relation/weight", "v", "1", "2", "no", "[]", "(weight>7.5)"),
        )
    assert parse_vehicle_weight_condition("(weight>7.5)") == (">", 7.5)
    assert parse_vehicle_weight_condition("(Mo-Fr 07:00-10:00)") is None
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_weight_restriction_n == 1
    assert shortest_path("a", "b", loaded) == 2
    assert shortest_path("a", "b", loaded, vehicle_weight_t=7.5) == 2
    assert shortest_path("a", "b", loaded, vehicle_weight_t=10) == 11
    loaded.close()
    output = tmp_path / "weight-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--weight-t",
            "10",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_weight_t"] == 10.0
    assert result["route_distance_m"] == 11.0
    assert "vehicle-weight conditional restrictions evaluated at 10 t" in result["method"]


def test_sqlite_graph_evaluates_conditional_road_access_windows(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "conditional-access-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, condition) VALUES (?, ?)",
            ("2", "(19:00-07:00)"),
        )
    assert parse_conditional_access("yes @ (19:00-07:00)") == "(19:00-07:00)"
    overnight = parse_weekly_schedule("19:00-07:00")
    assert overnight is not None
    assert overnight.active_at(datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)) is True
    assert overnight.active_at(datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)) is True
    assert overnight.active_at(datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc)) is False
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert shortest_path("a", "b", loaded) == 11
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc),
    ) == 2
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    ) == 11
    loaded.close()
    output = tmp_path / "conditional-access-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T20:00:00+00:00",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["route_distance_m"] == 2.0
    assert result["graph"]["conditional_access_n"] == 1
    assert "conditional road access windows evaluated" in result["method"]


def test_sqlite_graph_evaluates_conditional_road_access_deny_windows(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "conditional-access-deny-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, condition, mode) VALUES (?, ?, ?)",
            ("2", "(Mo-Sa 07:00-19:00)", "deny"),
        )
    parsed = parse_conditional_access_rule(
        "no @ (Mo-Th 11:00-17:00; Fr-Su 10:30-18:00)"
    )
    assert parsed is not None
    assert parsed[0] == "deny"
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
    ) == 11
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc),
    ) == 2
    loaded.close()


def test_sqlite_graph_evaluates_conditional_road_access_date_ranges(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "conditional-access-date-range-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, condition, mode) VALUES (?, ?, ?)",
            ("2", "(2026 Jul 04 - 2026 Oct 05)", "allow"),
        )
    parsed = parse_conditional_access_rule("yes @ (2026 Jul 04 - 2026 Oct 05)")
    assert parsed == ("allow", "(2026 Jul 04 - 2026 Oct 05)")
    schedule = parse_weekly_schedule("2026 Jul 04 - 2026 Oct 05", allow_calendar=True)
    assert schedule is not None
    assert schedule.active_at(datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)) is True
    assert schedule.active_at(datetime(2026, 10, 5, 23, 59, tzinfo=timezone.utc)) is True
    assert schedule.active_at(datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)) is False
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    ) == 2
    assert shortest_path(
        "a",
        "b",
        loaded,
        departure=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
    ) == 11
    loaded.close()


def test_sqlite_graph_evaluates_delivery_vehicle_class_access(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "delivery-class-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, condition, mode, vehicle_class) VALUES (?, ?, ?, ?)",
            ("2", "(19:00-07:00)", "allow", "delivery"),
        )
    assert parse_conditional_access_profile_rule("delivery") == ("allow", "delivery", "24/7")
    assert parse_conditional_access_profile_rule("delivery @ (19:00-07:00)") == (
        "allow",
        "delivery",
        "(19:00-07:00)",
    )
    assert parse_conditional_access_rule("delivery @ (19:00-07:00)") is None
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert loaded.conditional_access_vehicle_class_n == {
        "general": 0,
        "delivery": 1,
        "hgv": 0,
        "psv": 0,
        "taxi": 0,
    }
    evening = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
    midday = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    assert shortest_path("a", "b", loaded, departure=evening) == 11
    assert shortest_path("a", "b", loaded, departure=evening, vehicle_class="delivery") == 2
    assert shortest_path("a", "b", loaded, departure=midday, vehicle_class="delivery") == 11
    loaded.close()
    output = tmp_path / "delivery-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T20:00:00+00:00",
            "--vehicle-class",
            "delivery",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_class"] == "delivery"
    assert result["route_distance_m"] == 2.0
    assert "vehicle-class profile=delivery" in result["method"]


def test_sqlite_graph_evaluates_multiple_conditional_access_clauses(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    clause_value = (
        "delivery @ (Mo-Sa 07:00-11:00); "
        "yes @ (Mo-Sa 18:30-24:00; Su 00:00-24:00)"
    )
    assert parse_conditional_access_profile_rules_value(clause_value) == (
        ("allow", "delivery", "(Mo-Sa 07:00-11:00)"),
        ("allow", "general", "(Mo-Sa 18:30-24:00; Su 00:00-24:00)"),
    )
    assert conditional_access_profile_rules(
        {"motor_vehicle:conditional": clause_value}
    ) == [
        ("allow", "delivery", "(Mo-Sa 07:00-11:00)", "both"),
        ("allow", "general", "(Mo-Sa 18:30-24:00; Su 00:00-24:00)", "both"),
    ]
    graph_path = tmp_path / "multi-clause-access-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO conditional_access(way_id, direction, condition, mode, vehicle_class, rule_n) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2", "both", "(Mo-Sa 07:00-11:00)", "allow", "delivery", 0),
                (
                    "2",
                    "both",
                    "(Mo-Sa 18:30-24:00; Su 00:00-24:00)",
                    "allow",
                    "general",
                    1,
                ),
            ],
        )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 2
    assert loaded.conditional_access_multiclause_n == 1
    morning = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    midday = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
    assert shortest_path("a", "b", loaded, departure=morning) == 11
    assert shortest_path("a", "b", loaded, departure=morning, vehicle_class="delivery") == 2
    assert shortest_path("a", "b", loaded, departure=midday) == 11
    assert shortest_path("a", "b", loaded, departure=midday, vehicle_class="delivery") == 11
    assert shortest_path("a", "b", loaded, departure=evening) == 2
    assert shortest_path("a", "b", loaded, departure=evening, vehicle_class="delivery") == 2
    loaded.close()


def test_sqlite_graph_evaluates_psv_conditional_exception_access(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "psv-class-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, direction, condition, mode, vehicle_class) VALUES (?, ?, ?, ?, ?)",
            ("2", "both", "(Mo-Fr 07:00-19:00)", "allow", "psv"),
        )
    assert parse_conditional_access_profile_rule("psv") == ("allow", "psv", "24/7")
    assert parse_conditional_access_profile_rule("psv @ (Mo-Fr 07:00-19:00)") == (
        "allow",
        "psv",
        "(Mo-Fr 07:00-19:00)",
    )
    assert parse_conditional_access_rule("psv @ (Mo-Fr 07:00-19:00)") is None
    assert conditional_access_profile_rules(
        {"psv:backward:conditional": "yes @ (Mo-Sa 07:00-10:00,16:00-19:00)"}
    ) == [
        ("allow", "psv", "(Mo-Sa 07:00-10:00,16:00-19:00)", "backward")
    ]
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert loaded.conditional_access_vehicle_class_n == {
        "general": 0,
        "delivery": 0,
        "hgv": 0,
        "psv": 1,
        "taxi": 0,
    }
    midday = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
    assert shortest_path("a", "b", loaded, departure=midday) == 11
    assert shortest_path("a", "b", loaded, departure=evening) == 2
    assert shortest_path("a", "b", loaded, departure=midday, vehicle_class="psv") == 2
    assert shortest_path("a", "b", loaded, departure=evening, vehicle_class="psv") == 11
    loaded.close()
    output = tmp_path / "psv-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T12:00:00+00:00",
            "--vehicle-class",
            "psv",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_class"] == "psv"
    assert result["route_distance_m"] == 2.0
    assert "vehicle-class profile=psv" in result["method"]


def test_sqlite_graph_evaluates_taxi_conditional_access(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "taxi-class-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, direction, condition, mode, vehicle_class) VALUES (?, ?, ?, ?, ?)",
            ("2", "both", "(Mo-Su 00:00-06:00)", "allow", "taxi"),
        )
    assert parse_conditional_access_profile_rule("taxi") == ("allow", "taxi", "24/7")
    assert parse_conditional_access_profile_rule("yes @ (Mo-Su 00:00-06:00)") == (
        "allow",
        "general",
        "(Mo-Su 00:00-06:00)",
    )
    assert conditional_access_profile_rules(
        {"taxi:backward:conditional": "yes @ (Mo-Su 00:00-06:00)"}
    ) == [("allow", "taxi", "(Mo-Su 00:00-06:00)", "backward")]
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert loaded.conditional_access_vehicle_class_n == {
        "general": 0,
        "delivery": 0,
        "hgv": 0,
        "psv": 0,
        "taxi": 1,
    }
    early = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
    daytime = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    assert shortest_path("a", "b", loaded, departure=early) == 11
    assert shortest_path("a", "b", loaded, departure=daytime) == 11
    assert shortest_path("a", "b", loaded, departure=early, vehicle_class="taxi") == 2
    assert shortest_path("a", "b", loaded, departure=daytime, vehicle_class="taxi") == 11
    loaded.close()
    output = tmp_path / "taxi-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T05:00:00+00:00",
            "--vehicle-class",
            "taxi",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["vehicle_class"] == "taxi"
    assert result["route_distance_m"] == 2.0
    assert "vehicle-class profile=taxi" in result["method"]


def test_sqlite_graph_applies_directional_conditional_road_access(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "", "way_id": "4"},
    ]
    graph_path = tmp_path / "directional-access-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, direction, condition, mode) VALUES (?, ?, ?, ?)",
            ("2", "forward", "(Mo-Su 00:00-24:00)", "deny"),
        )
    parsed = conditional_access_profile_rules(
        {"motor_vehicle:forward:conditional": "no @ (Mo-Su 00:00-24:00)"}
    )
    assert parsed == [("deny", "general", "(Mo-Su 00:00-24:00)", "forward")]
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert loaded.conditional_access_direction_n == {
        "both": 0,
        "forward": 1,
        "backward": 0,
    }
    departure = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    assert shortest_path("v", "b", loaded, departure=departure) == 10
    assert shortest_path("b", "v", loaded, departure=departure) == 1
    loaded.close()
    output = tmp_path / "directional-access-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.001",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T12:00:00+00:00",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["route_distance_m"] == 10.0
    assert "directional conditional road access" in result["method"]


def test_sqlite_graph_evaluates_weight_qualified_delivery_access(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "weight-access-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_access(way_id, condition, mode, vehicle_class) VALUES (?, ?, ?, ?)",
            ("2", "(weight>5T)", "allow", "delivery"),
        )
    assert parse_conditional_access_profile_rule("delivery @ (weight>5T)") == (
        "allow",
        "delivery",
        "(weight>5T)",
    )
    loaded = load_graph(graph_path)
    assert isinstance(loaded, SQLiteRoadGraph)
    assert loaded.conditional_access_n == 1
    assert loaded.conditional_access_weight_n == 1
    assert shortest_path("a", "b", loaded, vehicle_class="delivery") == 11
    assert shortest_path(
        "a", "b", loaded, vehicle_class="delivery", vehicle_weight_t=3
    ) == 11
    assert shortest_path(
        "a", "b", loaded, vehicle_class="delivery", vehicle_weight_t=10
    ) == 2
    loaded.close()
    output = tmp_path / "weight-access-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--vehicle-class",
            "delivery",
            "--weight-t",
            "10",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["route_distance_m"] == 2.0
    assert "vehicle-weight conditional road access evaluated at 10 t" in result["method"]


def test_route_query_emits_snap_duration_and_arrival_metadata(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "v": (53.0, -8.001),
        "b": (53.0, -8.002),
        "c": (53.001, -8.001),
    }
    edges = [
        {"u": "a", "v": "v", "length_m": "1", "oneway": "yes", "way_id": "1"},
        {"u": "v", "v": "b", "length_m": "1", "oneway": "yes", "way_id": "2"},
        {"u": "v", "v": "c", "length_m": "5", "oneway": "yes", "way_id": "3"},
        {"u": "c", "v": "b", "length_m": "5", "oneway": "yes", "way_id": "4"},
    ]
    graph_path = tmp_path / "query-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.execute(
            "INSERT INTO conditional_turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json, condition) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("relation/conditional", "v", "1", "2", "no", "[]", "Mo-Fr 07:00-10:00"),
        )
    output = tmp_path / "route.json"
    geojson = tmp_path / "route.geojson"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T08:00:00+00:00",
            "--speed-kmh",
            "36",
            "--out",
            str(output),
            "--include-path",
            "--geojson-out",
            str(geojson),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reachable"] is True
    assert result["route_distance_m"] == 11.0
    assert result["estimated_duration_s"] == 1.1
    assert result["arrival"] == "2026-08-17T08:00:01.100000+00:00"
    assert result["ferry_wait_s"] == 0.0
    assert result["ferry_wait_n"] == 0
    assert result["start"]["snap_distance_m"] == 0.0
    assert result["goal"]["snap_distance_m"] == 0.0
    assert result["path_node_ids"] == ["a", "v", "c", "b"]
    assert result["path_node_n"] == 4
    assert result["path_way_ids"] == ["1", "3", "4"]
    assert result["path_segment_n"] == 3
    assert result["path_segment_source"] == "sqlite_edges"
    assert result["path_segment_total_distance_m"] == 11.0
    assert result["path_segment_total_duration_s"] == 1.1
    assert result["path_segment_total_wait_s"] == 0.0
    assert result["path_segments"] == [
        {
            "from_node": "a",
            "to_node": "v",
            "way_id": "1",
            "distance_m": 1.0,
            "duration_s": 0.1,
            "wait_s": 0.0,
            "ferry": False,
            "road_context": {
                "name": None,
                "ref": None,
                "highway": None,
                "route": None,
                "oneway": "yes",
            },
            "constraints": [],
            "conditional_rules": [],
            "transition_rules": [],
        },
        {
            "from_node": "v",
            "to_node": "c",
            "way_id": "3",
            "distance_m": 5.0,
            "duration_s": 0.5,
            "wait_s": 0.0,
            "ferry": False,
            "road_context": {
                "name": None,
                "ref": None,
                "highway": None,
                "route": None,
                "oneway": "yes",
            },
            "constraints": [],
            "conditional_rules": [],
            "transition_rules": [
                {
                    "key": "conditional_turn_restriction",
                    "relation_id": "relation/conditional",
                    "via_node": "v",
                    "from_way": "1",
                    "to_way": "2",
                    "via_way_ids": [],
                    "kind": "no",
                    "condition": "Mo-Fr 07:00-10:00",
                    "status": "supported",
                    "evaluated": True,
                    "active": True,
                    "applied": True,
                    "selected": False,
                    "profile": "departure",
                }
            ],
        },
        {
            "from_node": "c",
            "to_node": "b",
            "way_id": "4",
            "distance_m": 5.0,
            "duration_s": 0.5,
            "wait_s": 0.0,
            "ferry": False,
            "road_context": {
                "name": None,
                "ref": None,
                "highway": None,
                "route": None,
                "oneway": "yes",
            },
            "constraints": [],
            "conditional_rules": [],
            "transition_rules": [],
        },
    ]

    inactive_output = tmp_path / "inactive-turn-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.002",
            "--departure",
            "2026-08-17T12:00:00+00:00",
            "--speed-kmh",
            "36",
            "--include-path",
            "--out",
            str(inactive_output),
        ]
    )
    inactive_result = json.loads(inactive_output.read_text(encoding="utf-8"))
    assert inactive_result["path_way_ids"] == ["1", "2"]
    inactive_transition = inactive_result["path_segments"][1]["transition_rules"][0]
    assert inactive_transition["evaluated"] is True
    assert inactive_transition["active"] is False
    assert inactive_transition["applied"] is False
    assert inactive_transition["selected"] is True

    assert result["path_coordinates"] == [
        [-8.0, 53.0],
        [-8.001, 53.0],
        [-8.001, 53.001],
        [-8.002, 53.0],
    ]
    assert "conditional windows evaluated" in result["method"]
    assert result["graph"]["backend"] == "sqlite"
    feature = json.loads(geojson.read_text(encoding="utf-8"))
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "LineString"
    assert feature["geometry"]["coordinates"] == result["path_coordinates"]
    assert "path_node_ids" not in feature["properties"]
    assert "path_way_ids" not in feature["properties"]
    assert "path_segments" not in feature["properties"]
    assert "path_segment_total_distance_m" not in feature["properties"]
    assert "path_segment_total_duration_s" not in feature["properties"]
    assert "path_segment_total_wait_s" not in feature["properties"]
    assert "path_segment_source" not in feature["properties"]


def test_route_query_emits_static_edge_constraint_provenance(tmp_path):
    coordinates = {
        "a": (53.0, -8.0),
        "b": (53.0, -8.001),
    }
    edges = [
        {
            "u": "a",
            "v": "b",
            "length_m": "10",
            "oneway": "yes",
            "way_id": "constraint-way",
            "hgv": "destination",
            "maxweight": "3.5 t",
            "maxweight:hgv": "4 t",
            "maxweightrating:hgv": "7.5 t",
            "maxheight": "3.5 m",
            "maxheight_physical": "3.2 m",
            "maxwidth": "2.4 m",
            "maxlength": "12 m",
            "maxaxleload": "4 t",
            "maxspeed": "30",
        }
    ]
    graph_path = tmp_path / "constraint-provenance-graph"
    write_sqlite_graph(graph_path, coordinates, edges, source="fixture.pbf")
    output = tmp_path / "constraint-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.001",
            "--speed-kmh",
            "50",
            "--vehicle-class",
            "hgv",
            "--allow-hgv-destination",
            "--weight-t",
            "3",
            "--rating-t",
            "7",
            "--height-m",
            "3",
            "--width-m",
            "2",
            "--length-m",
            "10",
            "--axleload-t",
            "3",
            "--include-path",
            "--out",
            str(output),
        ]
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reachable"] is True
    assert result["path_segment_source"] == "sqlite_edges"
    assert result["path_segments"][0]["constraints"] == [
        {
            "key": "maxweight",
            "value": 3.5,
            "unit": "t",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_weight_t",
        },
        {
            "key": "maxweight:hgv",
            "value": 4.0,
            "unit": "t",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_class=hgv + vehicle_weight_t",
        },
        {
            "key": "maxweightrating:hgv",
            "value": 7.5,
            "unit": "t",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_class=hgv + vehicle_rating_t",
        },
        {
            "key": "maxheight",
            "value": 3.5,
            "unit": "m",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_height_m",
        },
        {
            "key": "maxheight:physical",
            "value": 3.2,
            "unit": "m",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_height_m",
        },
        {
            "key": "maxwidth",
            "value": 2.4,
            "unit": "m",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_width_m",
        },
        {
            "key": "maxlength",
            "value": 12.0,
            "unit": "m",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_length_m",
        },
        {
            "key": "maxaxleload",
            "value": 4.0,
            "unit": "t",
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_axleload_t",
        },
        {
            "key": "maxspeed",
            "value": 30.0,
            "unit": "km/h",
            "status": "supported",
            "evaluated": True,
            "profile": "speed_kmh",
        },
        {
            "key": "hgv",
            "value": "destination",
            "unit": None,
            "status": "supported",
            "evaluated": True,
            "profile": "vehicle_class=hgv + allow_hgv_destination",
        },
    ]

    unprofiled_output = tmp_path / "unprofiled-constraint-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.001",
            "--include-path",
            "--out",
            str(unprofiled_output),
        ]
    )
    unprofiled = json.loads(unprofiled_output.read_text(encoding="utf-8"))
    evaluated = {
        record["key"]: record["evaluated"]
        for record in unprofiled["path_segments"][0]["constraints"]
    }
    assert evaluated["maxspeed"] is True
    assert evaluated["hgv"] is False
    assert all(
        evaluated[key] is False
        for key in (
            "maxweight",
            "maxweight:hgv",
            "maxweightrating:hgv",
            "maxheight",
            "maxheight:physical",
            "maxwidth",
            "maxlength",
            "maxaxleload",
        )
    )


def test_access_restriction_uses_specific_motor_vehicle_override():
    assert is_access_restricted({"access": "private"}) is True
    assert is_access_restricted({"access": "private", "motor_vehicle": "yes"}) is False
    assert is_access_restricted({"access": "yes", "vehicle": "no"}) is True
    assert is_access_restricted({"access": "destination"}) is False


def test_route_query_emits_conditional_rule_provenance_at_segment_entry(tmp_path):
    always = parse_weekly_schedule("24/7")
    assert always is not None
    assert always.always_active is True
    assert always.active_at(datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)) is True
    graph_path = tmp_path / "conditional-provenance-graph"
    write_sqlite_graph(
        graph_path,
        {"a": (53.0, -8.0), "b": (53.0, -8.001)},
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "1000",
                "oneway": "",
                "way_id": "conditional-way",
                "maxspeed": "50",
                "maxspeed:conditional": "30 @ (Mo-Fr 08:00-18:00)",
                "oneway:conditional": "Mo-Fr 08:00-18:00",
            }
        ],
        source="fixture.pbf",
    )
    with sqlite3.connect(graph_path / "road_graph.sqlite") as connection:
        connection.executemany(
            "INSERT INTO conditional_access(way_id, direction, condition, mode, vehicle_class, rule_n) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "conditional-way",
                    "both",
                    "(Mo-Fr 08:00-18:00)",
                    "allow",
                    "delivery",
                    0,
                ),
                (
                    "conditional-way",
                    "both",
                    "24/7",
                    "allow",
                    "general",
                    1,
                ),
            ],
        )

    active_output = tmp_path / "active-conditional-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.001",
            "--vehicle-class",
            "delivery",
            "--departure",
            "2026-08-17T09:00:00+00:00",
            "--speed-kmh",
            "60",
            "--include-path",
            "--out",
            str(active_output),
        ]
    )
    active_result = json.loads(active_output.read_text(encoding="utf-8"))
    assert active_result["reachable"] is True
    assert active_result["estimated_duration_s"] == 120.0
    active_rules = active_result["path_segments"][0]["conditional_rules"]
    active_by_key = {
        (rule["key"], rule["mode"], rule["vehicle_class"]): rule
        for rule in active_rules
    }
    assert active_by_key[("maxspeed:conditional", "speed", "general")] == {
        "key": "maxspeed:conditional",
        "value": 30.0,
        "unit": "km/h",
        "condition": "(Mo-Fr 08:00-18:00)",
        "mode": "speed",
        "direction": "both",
        "vehicle_class": "general",
        "status": "supported",
        "evaluated": True,
        "active": True,
        "applied": True,
        "profile": "departure",
    }
    assert active_by_key[("oneway:conditional", "yes", "general")]["active"] is True
    assert active_by_key[("oneway:conditional", "yes", "general")]["applied"] is True
    assert active_by_key[("conditional_access", "allow", "delivery")] == {
        "key": "conditional_access",
        "value": "allow",
        "unit": None,
        "condition": "(Mo-Fr 08:00-18:00)",
        "mode": "allow",
        "direction": "both",
        "vehicle_class": "delivery",
        "status": "supported",
        "evaluated": True,
        "active": True,
        "applied": True,
        "profile": "departure",
    }

    inactive_output = tmp_path / "inactive-conditional-route.json"
    route_query_main(
        [
            "--road-graph",
            str(graph_path),
            "--start-lat",
            "53.0",
            "--start-lon",
            "-8.0",
            "--goal-lat",
            "53.0",
            "--goal-lon",
            "-8.001",
            "--vehicle-class",
            "delivery",
            "--departure",
            "2026-08-17T07:00:00+00:00",
            "--speed-kmh",
            "60",
            "--include-path",
            "--out",
            str(inactive_output),
        ]
    )
    inactive_result = json.loads(inactive_output.read_text(encoding="utf-8"))
    assert inactive_result["reachable"] is True
    assert inactive_result["estimated_duration_s"] == 72.0
    inactive_rules = inactive_result["path_segments"][0]["conditional_rules"]
    inactive_by_key = {
        (rule["key"], rule["mode"], rule["vehicle_class"]): rule
        for rule in inactive_rules
    }
    for key in (
        ("maxspeed:conditional", "speed", "general"),
        ("oneway:conditional", "yes", "general"),
        ("conditional_access", "allow", "delivery"),
    ):
        assert inactive_by_key[key]["evaluated"] is True
        assert inactive_by_key[key]["active"] is False
        assert inactive_by_key[key]["applied"] is False
    assert inactive_by_key[("conditional_access", "allow", "general")]["active"] is True
    assert inactive_by_key[("conditional_access", "allow", "general")]["applied"] is True


def test_complete_pbf_scan_requires_disk_backed_graph_output():
    with pytest.raises(SystemExit):
        road_routing_main(["--from-pbf", "--max-ways", "0"])


def test_grid_nearest_node_index_matches_brute_force_across_cells():
    nodes = {
        "a": (53.0000, -8.0000),
        "b": (53.0040, -8.0040),
        "c": (53.0210, -8.0210),
        "d": (53.0410, -8.0010),
    }
    index = build_node_index(nodes)
    queries = (
        {"lat": "53.0035", "lon": "-8.0038"},
        {"lat": "53.0200", "lon": "-8.0200"},
        {"lat": "53.0400", "lon": "-8.0020"},
    )
    assert all(nearest_node(query, nodes, index) == nearest_node(query, nodes) for query in queries)


def test_review_calibration_keeps_ambiguous_labels_out_of_binary_metrics():
    rows = [
        {"score": "90", "label": "supportive"},
        {"score": "20", "label": "not_supportive"},
        {"score": "90", "label": "ambiguous"},
    ]
    result = calibration(rows)
    assert result[0]["labelled_n"] == 2
    assert result[0]["ambiguous_n"] == 1


def test_columnar_stage_cleans_stale_optional_exports(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "analysis_results.csv").write_text("osm_id,area_m2\nway/1,12\n", encoding="utf-8")
    parquet = out / "analysis_results.parquet"
    duckdb = out / "analysis.duckdb"
    parquet.write_text("stale", encoding="utf-8")
    duckdb.write_text("stale", encoding="utf-8")
    columnar_main(["--out-dir", str(out)])
    status = json.loads((out / "columnar_status.json").read_text(encoding="utf-8"))
    assert status["jsonl"]["status"] == "available"
    for name, path in (("parquet", parquet), ("duckdb", duckdb)):
        assert path.exists() is (status[name]["status"] == "available")


def test_columnar_stream_helpers_preserve_rows_and_bound_batches(tmp_path):
    source = tmp_path / "analysis_results.csv"
    source.write_text(
        "osm_id,name\nway/1,\"first\\nline\"\nway/2,second\nway/3,third\n",
        encoding="utf-8",
    )
    batches = list(iter_csv_batches(source, 2))
    assert [len(batch) for batch in batches] == [2, 1]
    jsonl = tmp_path / "analysis_results.jsonl"
    assert write_jsonl(jsonl, iter_csv_rows(source)) == 3
    assert [json.loads(line)["osm_id"] for line in jsonl.read_text(encoding="utf-8").splitlines()] == [
        "way/1", "way/2", "way/3"
    ]


def test_normalized_lidar_and_geotiff_sampling_contract(tmp_path):
    normalized = tmp_path / "heights.csv"
    normalized.write_text(
        "osm_id,roof_height_m,elevation_m,coverage_m2\nway/1,8.5,101,42\n",
        encoding="utf-8",
    )
    records = parse_lidar(normalized)
    assert records["way/1"]["roof_height_m"] == 8.5

    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_origin

    raster_path = tmp_path / "surface.tif"
    values = np.arange(25, dtype="float32").reshape((5, 5))
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=5,
        width=5,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-8.005, 53.005, 0.001, 0.001),
    ) as destination:
        destination.write(values, 1)
    raster_records, info = parse_raster_lidar(
        raster_path,
        [{"osm_id": "way/1", "lat": "53.002", "lon": "-8.002"}],
    )
    assert info["status"] == "provided"
    assert raster_records["way/1"]["source_type"] == "GeoTIFF"
    assert raster_records["way/1"]["point_n"] > 0


def test_geographic_las_lidar_aggregation_contract(tmp_path):
    laspy = pytest.importorskip("laspy")
    import numpy as np

    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([1e-7, 1e-7, 0.01])
    header.offsets = np.array([0.0, 0.0, 0.0])
    cloud = laspy.LasData(header)
    cloud.x = np.array([-8.003, -8.0032, -8.0028])
    cloud.y = np.array([53.001, 53.0012, 53.0008])
    cloud.z = np.array([12.0, 12.5, 11.5])
    las_path = tmp_path / "surface.las"
    cloud.write(las_path)

    elements = [
        {
            "osm_type": "way",
            "id": 1,
            "geometry": [
                {"lat": 53.0, "lon": -8.004},
                {"lat": 53.0, "lon": -8.002},
                {"lat": 53.002, "lon": -8.002},
                {"lat": 53.002, "lon": -8.004},
            ],
        }
    ]
    records, info = parse_las_lidar(las_path, [{"osm_id": "way/1"}], elements)

    assert info["status"] == "provided"
    assert records["way/1"]["source_type"] == "LAS/LAZ"
    assert records["way/1"]["point_n"] == 3
    assert records["way/1"]["roof_height_m"] == 12.5


def test_normalized_lidar_json_variants_and_malformed_inputs_are_safe(tmp_path):
    array_path = tmp_path / "heights.json"
    array_path.write_text(
        json.dumps([{"osm_id": "way/2", "roof_height_m": 7.25}]), encoding="utf-8"
    )
    assert parse_lidar(array_path)["way/2"]["roof_height_m"] == 7.25

    rows_path = tmp_path / "heights-rows.json"
    rows_path.write_text(
        json.dumps({"rows": [{"osm_id": "way/3", "roof_height_m": 6.5}]}),
        encoding="utf-8",
    )
    assert parse_lidar(rows_path)["way/3"]["roof_height_m"] == 6.5

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    assert parse_lidar(malformed) == {}


def test_quality_audit_reports_geometry_and_source_missingness(tmp_path):
    rows = [analysis_row("way/1", 53, -8), analysis_row("way/2", 53, -8, True)]
    rows[0]["valid"] = "1"
    rows[0]["repaired"] = "0"
    rows[0]["multipart"] = "0"
    rows[1]["valid"] = "1"
    rows[1]["repaired"] = "0"
    rows[1]["multipart"] = "0"
    fields = sorted({key for row in rows for key in row})
    with (tmp_path / "analysis_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    audit, summary = build_audit(tmp_path)
    assert audit
    assert summary["analysis_rows"] == 2
    assert summary["duplicate_osm_id_n"] == 0
    assert summary["valid_geometry_pct"] == 100.0


def test_quality_audit_counts_quality_only_source_status(tmp_path):
    rows = [analysis_row("way/1", 53, -8)]
    rows[0]["valid"] = "1"
    rows[0]["repaired"] = "0"
    rows[0]["multipart"] = "0"
    fields = sorted(rows[0])
    with (tmp_path / "analysis_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (tmp_path / "lidar_coverage.csv").write_text(
        "osm_id,quality\nway/1,provided_but_unreadable\n", encoding="utf-8"
    )
    _audit, summary = build_audit(tmp_path)
    lidar_row = next(row for row in _audit if row["field"] == "lidar_coverage")
    assert lidar_row["missing_n"] == 1
    assert lidar_row["missing_pct"] == 100.0
    assert summary["source_status"]["lidar_coverage"] == "provided_but_unreadable:1"


def test_duplicate_centroids_are_exported_as_reviewable_groups():
    rows = [
        analysis_row("way/target", 53, -8),
        analysis_row("way/control", 53, -8, True),
        analysis_row("way/other", 53.1, -8.1),
    ]
    groups = build_duplicate_groups(rows)
    assert len(groups) == 1
    assert groups[0]["duplicate_n"] == 1
    assert groups[0]["target_n"] == 1
    assert groups[0]["control_n"] == 1
    assert groups[0]["review_status"] == "review"


def test_spatial_bootstrap_is_deterministic_and_bounded():
    rows = [
        analysis_row("way/t1", 53.0, -8.0),
        analysis_row("way/t2", 53.1, -8.1),
        analysis_row("way/c1", 53.0, -8.0, True),
        analysis_row("way/c2", 53.1, -8.1, True),
    ]
    result = bootstrap_rows(rows, seed=7, iterations=20, grid_deg=0.1)
    assert len(result) == 3
    assert result == bootstrap_rows(rows, seed=7, iterations=20, grid_deg=0.1)
    assert all(0 <= float(row["prob_positive"]) <= 1 for row in result)


def test_negative_controls_stay_in_their_separate_family():
    rows = [
        {**analysis_row("way/t", 53, -8), "has_60_angle": "1", "has_120_angle": "0"},
        {**analysis_row("way/c", 53, -8, True), "has_60_angle": "0", "has_120_angle": "1"},
    ]
    result = build_negative_controls(rows)
    assert {row["signal"] for row in result} == {"has_60_angle", "has_120_angle"}
    assert all(row["test_family"] == "negative_control_angle" for row in result)
    assert all("p_adjusted" in row for row in result)
