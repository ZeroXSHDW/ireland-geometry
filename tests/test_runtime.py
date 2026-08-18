import json
import os

from scripts import runtime
from scripts.runtime import (
    build_manifest,
    default_analysis_plan_path,
    output_counts,
    package_version,
    packaged_analysis_plan_path,
    runtime_signature,
    sha256_path,
)


def test_default_project_root_uses_caller_directory_for_wheel_like_install(tmp_path, monkeypatch):
    package_root = tmp_path / "site-packages"
    project_root = tmp_path / "project"
    package_root.mkdir()
    project_root.mkdir()
    monkeypatch.setattr(runtime, "PACKAGE_ROOT", package_root)
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("IRELAND_GEOMETRY_PROJECT_ROOT", raising=False)
    assert runtime.default_project_root() == project_root


def test_configured_project_root_overrides_wheel_default(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    monkeypatch.setenv("IRELAND_GEOMETRY_PROJECT_ROOT", str(configured))
    assert runtime.default_project_root() == configured.resolve()


def test_default_analysis_plan_prefers_project_override_and_packages_fallback(tmp_path):
    root = tmp_path / "package"
    (root / "schemas").mkdir(parents=True)
    packaged = root / "schemas" / "analysis_plan.json"
    packaged.write_text('{"plan_id":"packaged","version":1}', encoding="utf-8")
    assert default_analysis_plan_path(tmp_path, package_root=root) == packaged

    project = tmp_path / "analysis_plan.json"
    project.write_text('{"plan_id":"override","version":2}', encoding="utf-8")
    assert default_analysis_plan_path(tmp_path, package_root=root) == project
    assert packaged_analysis_plan_path(root) == packaged


def test_package_version_reads_declared_source_metadata(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "PACKAGE_ROOT", tmp_path)
    assert runtime.package_version() == "9.9.9"


def test_manifest_counts_csv_records_not_physical_lines(tmp_path):
    (tmp_path / "architects_evidence.csv").write_text(
        'id,evidence\n1,"first line\nsecond line"\n2,"single line"\n',
        encoding="utf-8",
    )
    counts = output_counts(tmp_path)
    assert counts["architects_evidence.csv"] == 2


def test_manifest_counts_scale_exports_and_geojson_features(tmp_path):
    (tmp_path / "analysis_results.csv").write_text("osm_id\nway/1\nway/2\n", encoding="utf-8")
    (tmp_path / "analysis_results.jsonl").write_text('{"osm_id":"way/1"}\n', encoding="utf-8")
    (tmp_path / "analysis_results.parquet").write_bytes(b"parquet")
    (tmp_path / "analysis.duckdb").write_bytes(b"duckdb")
    (tmp_path / "ireland_buildings.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature"}]}),
        encoding="utf-8",
    )
    counts = output_counts(tmp_path)
    assert counts["analysis_results.jsonl"] == 2
    assert counts["analysis_results.parquet"] == 2
    assert counts["analysis.duckdb"] == 2
    assert counts["ireland_buildings.geojson"] == 1


def test_manifest_hashes_the_complete_road_graph_directory(tmp_path):
    graph = tmp_path / "roads"
    graph.mkdir()
    (graph / "road_nodes.csv").write_text("node_id,lat,lon\na,53,-8\n", encoding="utf-8")
    (graph / "road_edges.csv").write_text("u,v,length_m\na,a,0\n", encoding="utf-8")
    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        parameters={},
        sources=[{"kind": "optional_road_graph", "path": graph}],
    )
    source = manifest["sources"][0]
    assert source["kind"] == "directory"
    assert source["source_kind"] == "optional_road_graph"
    assert source["sha256"] == sha256_path(graph)
    assert source["bytes"] is None


def test_manifest_records_source_freshness_contract(tmp_path):
    source_path = tmp_path / "data" / "combined.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}", encoding="utf-8")
    os.utime(source_path, (1_600_000_000, 1_600_000_000))

    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        parameters={},
        project_root=tmp_path,
        sources=[{"kind": "combined_osm_json", "path": source_path}],
    )

    assert manifest["source_freshness"]["contract"] == "ireland-geometry.freshness.v1"
    freshness = manifest["source_freshness"]["sources"][0]
    assert freshness["modified_at"].startswith("2020-09-13T12:26:40")
    assert freshness["age_seconds"] > 0
    assert manifest["sources"][0]["modified_at"] == freshness["modified_at"]


def test_manifest_uses_explicit_project_root_for_identity(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = build_manifest(
        data_root=project_root / "data",
        out_dir=project_root / "output",
        parameters={},
        project_root=project_root,
    )
    assert manifest["project_root"] == str(project_root.resolve())


def test_manifest_excludes_timestamped_doctor_diagnostic(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "doctor.json").write_text('{"generated_at":"now"}', encoding="utf-8")
    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=output,
        parameters={},
    )
    assert manifest["package_version"] == package_version()
    assert manifest["runtime"] == runtime_signature()
    assert not any(item["path"].endswith("/doctor.json") for item in manifest["artifacts"])


def test_manifest_records_relative_references_for_relocation(tmp_path):
    project = tmp_path / "project"
    data = project / "data"
    output = project / "output"
    data.mkdir(parents=True)
    output.mkdir()
    (data / "combined.json").write_text("{}", encoding="utf-8")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    external = tmp_path / "external.csv"
    external.write_text("value\n1\n", encoding="utf-8")

    manifest = build_manifest(
        data_root=data,
        out_dir=output,
        parameters={},
        project_root=project,
        sources=[
            {"kind": "combined_osm_json", "path": data / "combined.json"},
            {"kind": "external", "path": external},
        ],
    )

    assert manifest["path_contract"] == {
        "version": 1,
        "relative_path_field": "relative_path",
        "source_base": "project_root",
        "artifact_base": "output_dir",
        "absolute_path_field": "path",
    }
    assert manifest["data_root_relative"] == "data"
    assert manifest["output_dir_relative"] == "output"
    sources = {row["source_kind"]: row for row in manifest["sources"]}
    assert sources["combined_osm_json"]["relative_path"] == "data/combined.json"
    assert sources["combined_osm_json"]["path_base"] == "project_root"
    assert "relative_path" not in sources["external"]
    assert sources["external"]["path_base"] == "external"
    artifact = next(row for row in manifest["artifacts"] if row["path"].endswith("analysis_results.csv"))
    assert artifact["relative_path"] == "analysis_results.csv"
    assert artifact["path_base"] == "output_dir"
