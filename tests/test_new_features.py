import csv
import json
import sqlite3
from datetime import datetime, timezone
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
    SQLiteRoadGraph,
    build_node_index,
    graph_from_rows,
    is_access_restricted,
    load_graph,
    nearest_node,
    parse_weekly_schedule,
    shortest_path,
    write_graph,
    write_sqlite_graph,
)
from scripts.road_routing import main as road_routing_main
from scripts.route_query import main as route_query_main
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
    assert metadata["format"] == "ireland-geometry-road-sqlite-v6"
    assert metadata["complete"] is True


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
    assert result["start"]["snap_distance_m"] == 0.0
    assert result["goal"]["snap_distance_m"] == 0.0
    assert result["path_node_ids"] == ["a", "v", "c", "b"]
    assert result["path_node_n"] == 4
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


def test_access_restriction_uses_specific_motor_vehicle_override():
    assert is_access_restricted({"access": "private"}) is True
    assert is_access_restricted({"access": "private", "motor_vehicle": "yes"}) is False
    assert is_access_restricted({"access": "yes", "vehicle": "no"}) is True
    assert is_access_restricted({"access": "destination"}) is False


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
