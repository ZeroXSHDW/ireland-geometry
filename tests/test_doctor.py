import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.doctor import (
    DOCTOR_VERSION,
    analysis_plan_status,
    bundle_capability_status,
    console_commands_status,
    dependency_status,
    format_report,
    geo3d_capability_status,
    inspect_project,
    manifest_path_status,
    query_data_status,
    report_capability_status,
    report_server_status,
    review_workflow_status,
    route_query_status,
    routing_graph_status,
    schema_registry_status,
    verification_status,
)
from scripts.doctor import main as doctor_main
from scripts.runtime import package_version, runtime_signature


def _write_current_manifest(output):
    artifacts = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        artifacts.append(
            {
                "relative_path": path.relative_to(output).as_posix(),
                "path_base": "output_dir",
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (output / "manifest.json").write_text(
        json.dumps({"artifacts": artifacts}), encoding="utf-8"
    )


def test_doctor_reports_the_published_osmium_distribution():
    assert dependency_status("osmium", required=True)["module"] == "osmium"


def test_doctor_reports_the_packaged_schema_registry():
    root = Path(__file__).resolve().parents[1]
    capability = schema_registry_status(root)
    assert capability["status"] == "available"
    assert capability["schema_version"] == 1
    assert capability["artifact_count"] == 12


def test_doctor_reports_project_override_and_packaged_analysis_plan(tmp_path):
    root = Path(__file__).resolve().parents[1]
    packaged = analysis_plan_status(tmp_path / "no-plan-project", root)
    assert packaged["status"] == "available"
    assert packaged["source"] == "packaged"
    assert packaged["plan_id"] == "ireland-geometry-v1"

    project = root / "analysis_plan.json"
    override = analysis_plan_status(root, root)
    assert override["status"] == "available"
    assert override["source"] == "project"
    assert override["path"] == str(project)


def test_doctor_reports_manifest_relocation_contract(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "path_contract": {"version": 1},
                "artifacts": [
                    {"path_base": "output_dir", "relative_path": "analysis_results.csv"}
                ],
                "sources": [
                    {"path_base": "project_root", "relative_path": "data/combined.json"},
                    {"path_base": "external", "path": "/mnt/source.csv"},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = manifest_path_status(output)
    assert result["status"] == "available"
    assert result["contract_version"] == 1
    assert result["portable_artifact_count"] == 1
    assert result["portable_source_count"] == 1
    assert result["external_source_count"] == 1


def test_project_declares_the_installable_osmium_distribution():
    root = Path(__file__).resolve().parents[1]
    assert '"osmium>=3.6"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyosmium>=3.6" not in (root / "requirements.txt").read_text(encoding="utf-8")


def test_doctor_report_is_json_serializable_and_surfaces_missing_cache(tmp_path):
    result = inspect_project(tmp_path)
    json.dumps(result)
    assert result["doctor_version"] == DOCTOR_VERSION
    assert result["package_version"] == package_version()
    assert result["runtime"] == runtime_signature()
    assert result["summary"]["missing_required_inputs"] == 3
    assert result["summary"]["status"] in {"warning", "fail"}
    assert "Inputs:" in format_report(result)
    assert "command wrappers installed" in format_report(result)
    assert "Active tree boundaries:" in format_report(result)
    assert result["verification"]["status"] == "not_provided"
    assert "Verification: not_provided" in format_report(result)
    blocker_codes = {item["code"] for item in result["summary"]["strict_blockers"]}
    assert {"missing_required_inputs", "missing_required_outputs"} <= blocker_codes
    assert "git_unavailable" in blocker_codes


def test_doctor_reports_source_age_and_enforces_configured_limit(tmp_path):
    data = tmp_path / "data"
    for path in (
        data / "raw" / "ireland-latest.osm.pbf",
        data / "combined.json",
        data / "niah" / "niah.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_600_000_000, 1_600_000_000))

    result = inspect_project(
        tmp_path,
        data_root=data,
        max_input_age_days=1,
    )
    required = result["inputs"]["required"]
    assert all(item["freshness_status"] == "stale" for item in required)
    assert result["source_freshness"]["contract"] == "ireland-geometry.freshness.v1"
    assert result["summary"]["stale_required_inputs"] == 3
    assert result["summary"]["strict_ready"] is False
    assert any("age limit" in warning for warning in result["summary"]["warnings"])


def test_doctor_separates_project_data_from_packaged_capabilities(tmp_path):
    package_root = Path(__file__).resolve().parents[1]
    result = inspect_project(tmp_path, package_root=package_root)
    assert result["project_root"] == str(tmp_path.resolve())
    assert result["package_root"] == str(package_root.resolve())
    assert result["capabilities"]["pipeline"]["status"] == "available"
    assert result["capabilities"]["pipeline"]["cache_version"] == 5
    assert result["capabilities"]["pipeline"]["dependency_aware_cache"] is True
    assert result["capabilities"]["pipeline"]["cache_explanations"] is True
    assert result["capabilities"]["pipeline"]["supports_dry_run_cache_explanations"] is True
    assert result["capabilities"]["pipeline"]["post_validation_report_refresh"] is True
    assert result["capabilities"]["pipeline"]["post_validation_bundle"] is True
    assert result["capabilities"]["pipeline"]["rejects_output_symlink"] is True
    assert result["capabilities"]["pipeline"]["rejects_output_non_directory"] is True
    assert result["capabilities"]["pipeline"]["rejects_nested_symlinks"] is True
    assert result["capabilities"]["pipeline"]["diagnostic_json_outputs"] is True
    assert result["capabilities"]["pipeline"]["reproducibility_rejects_symlinks"] is True
    output_safety = result["capabilities"]["output_safety"]
    assert output_safety["contract"] == "ireland-geometry.output-safety.v1"
    assert output_safety["status"] == "available"
    assert output_safety["allows_canonical_macos_tmp_alias"] is True
    assert output_safety["rejects_non_directory"] is True
    assert all(output_safety["guards"].values())
    assert all(output_safety["non_directory_guards"].values())
    assert output_safety["nested_guards"] == {
        "verify": True,
        "report_server": True,
        "query": True,
        "schema_audit": True,
        "report": True,
        "columnar": True,
    }
    assert all(output_safety["stage_output_guards"].values())
    assert all(output_safety["stage_output_tree_guards"].values())
    assert output_safety["file_output_guards"] == {"doctor": True, "route-query": True}
    data_safety = result["capabilities"]["data_safety"]
    assert data_safety["contract"] == "ireland-geometry.data-safety.v1"
    assert data_safety["status"] == "available"
    assert data_safety["rejects_non_directory"] is True
    assert all(data_safety["guards"].values())
    assert data_safety["nested_guards"] == {"fetch-osm": True, "fetch-niah": True}
    assert data_safety["extraction_tree_guards"] == {"fetch-niah": True}
    assert data_safety["satellite_tree_guards"] == {"fetch-satellite": True}
    assert data_safety["file_guards"] == {
        "fetch": True,
        "fetch-niah": True,
        "fetch-osm": True,
    }
    assert data_safety["tree_guards"] == {
        "pipeline": True,
        "niah": True,
        "architects": True,
        "building-parts": True,
        "historical": True,
        "osm-history": True,
        "review": True,
        "road-proximity": True,
        "road-routing": True,
        "roads": True,
        "spatial-covariates": True,
        "route-query": True,
        "verify": True,
        "report-server": True,
    }
    assert data_safety["graph_output_tree_guards"] == {"road-routing": True}
    assert data_safety["input_guards"] == {
        "analyze": True,
        "building-parts": True,
        "historical": True,
        "osm-history": True,
        "review": True,
        "road-proximity": True,
        "road-routing": True,
        "roads": True,
        "spatial-covariates": True,
        "route-query": True,
        "verify": True,
        "report-server": True,
        "holdout": True,
        "schema-audit": True,
    }
    assert data_safety["observed_root"]["status"] in {"pass", "not_provided"}
    assert result["capabilities"]["output_safety"]["observed_root"]["status"] in {
        "pass",
        "not_provided",
    }
    file_write_safety = result["capabilities"]["file_write_safety"]
    assert file_write_safety["contract"] == "ireland-geometry.file-write-safety.v1"
    assert file_write_safety["status"] == "available"
    assert all(file_write_safety["guards"].values())
    assert result["capabilities"]["release_check"]["handles_non_directory_roots"] is True
    assert result["capabilities"]["schema_registry"]["status"] == "available"
    assert result["capabilities"]["analysis_plan"]["status"] == "available"
    assert result["capabilities"]["pages"]["status"] == "not_provided"
    assert result["capabilities"]["pages"]["audit_available"] is True
    assert result["capabilities"]["pages"]["publisher_available"] is True
    assert result["capabilities"]["pages"]["publisher_checks_manifest_artifacts"] is True
    assert result["capabilities"]["pages"]["publisher_rejects_site_inside_output"] is True
    assert result["capabilities"]["pages"]["publisher_rejects_symlink_roots"] is True
    assert result["capabilities"]["pages"]["publisher_handles_non_directory_roots"] is True
    assert result["capabilities"]["pages"]["audit_rejects_symlink_root"] is True
    assert result["capabilities"]["pages"]["audit_handles_non_directory_root"] is True
    assert result["capabilities"]["release_check"]["status"] == "available"
    assert result["capabilities"]["release_check"]["read_only"] is True
    assert result["capabilities"]["release_check"]["checks_output"] is True
    assert result["capabilities"]["release_check"]["checks_output_inventory"] is True
    assert result["capabilities"]["release_check"]["checks_output_symlinks"] is True
    assert result["capabilities"]["release_check"]["rejects_symlink_roots"] is True
    assert result["capabilities"]["release_check"]["checks_validation_records"] is True
    assert result["capabilities"]["release_check"]["checks_bundle"] is True
    assert result["capabilities"]["bundle"]["rejects_symlinks"] is True
    assert result["capabilities"]["bundle"]["rejects_archive_input_symlink"] is True
    assert result["capabilities"]["bundle"]["rejects_extraction_parent_symlink"] is True
    assert result["capabilities"]["bundle"]["checks_manifest_artifacts"] is True
    assert result["capabilities"]["report_server"]["checks_manifest_alignment"] is True
    assert result["capabilities"]["report_server"]["report_page_runtime"] is True
    assert result["capabilities"]["report_server"]["report_export_runtime_headers"] is True
    assert result["capabilities"]["report_server"]["report_runtime_api"] is True
    assert result["capabilities"]["report_server"]["report_runtime_snapshot"] is True
    assert result["capabilities"]["report_server"]["report_runtime_source_alignment"] is True
    assert result["capabilities"]["report_server"]["report_source_alignment_surfaces"] is True
    assert result["capabilities"]["validation"]["source_alignment"]["status"] == "not_provided"
    assert result["capabilities"]["commands"]["status"] == "available"
    assert result["capabilities"]["commands"]["command_count"] == 9
    assert set(result["capabilities"]["commands"]["commands"]) == {
        "ireland-geometry",
        "ireland-geometry-doctor",
        "ireland-geometry-serve",
        "ireland-geometry-route",
        "ireland-geometry-query",
        "ireland-geometry-bundle",
        "ireland-geometry-schema-audit",
        "ireland-geometry-repro",
        "ireland-geometry-release-check",
    }
    assert result["inputs"]["required"][0]["path"].startswith(str(tmp_path.resolve()))


def test_doctor_strict_readiness_requires_output_safety_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.doctor.output_safety_status",
        lambda _root: {
            "status": "incomplete",
            "guards": {"report": False},
        },
    )

    result = inspect_project(tmp_path)

    assert result["summary"]["strict_ready"] is False
    assert any(
        blocker["code"] == "output_safety_incomplete"
        for blocker in result["summary"]["strict_blockers"]
    )
    assert any("Output-root safety guards are incomplete" in warning for warning in result["summary"]["warnings"])


def test_doctor_strict_readiness_requires_data_safety_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.doctor.data_safety_status",
        lambda _root: {
            "status": "incomplete",
            "guards": {"fetch": False},
        },
    )

    result = inspect_project(tmp_path)

    assert result["summary"]["strict_ready"] is False
    assert any(
        blocker["code"] == "data_safety_incomplete"
        for blocker in result["summary"]["strict_blockers"]
    )
    assert any("Data-root safety guards are incomplete" in warning for warning in result["summary"]["warnings"])


@pytest.mark.parametrize("root_kind", ("file", "symlink"))
def test_doctor_reports_invalid_active_data_root(tmp_path, root_kind):
    data = tmp_path / "data"
    if root_kind == "file":
        data.write_text("not a directory", encoding="utf-8")
    else:
        target = tmp_path / "real-data"
        target.mkdir()
        try:
            data.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable in this test environment")

    result = inspect_project(tmp_path)

    observed = result["capabilities"]["data_safety"]["observed_root"]
    assert observed["status"] == "fail"
    assert result["summary"]["strict_ready"] is False
    assert any(
        blocker["code"] == "data_root_invalid"
        for blocker in result["summary"]["strict_blockers"]
    )


def test_doctor_reports_nested_active_data_and_output_symlinks(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "output"
    data.mkdir()
    output.mkdir()
    target = tmp_path / "external.txt"
    target.write_text("external", encoding="utf-8")
    try:
        (data / "nested-input.txt").symlink_to(target)
        (output / "nested-output.txt").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    result = inspect_project(tmp_path)

    observed_data = result["capabilities"]["data_safety"]["observed_root"]
    observed_output = result["capabilities"]["output_safety"]["observed_root"]
    assert observed_data["status"] == "fail"
    assert observed_data["reason"] == "nested_symlinks"
    assert observed_data["symlink_count"] == 1
    assert observed_data["symlink_paths"] == ["nested-input.txt"]
    assert observed_output["status"] == "fail"
    assert observed_output["reason"] == "nested_symlinks"
    assert observed_output["symlink_count"] == 1
    assert observed_output["symlink_paths"] == ["nested-output.txt"]
    assert result["summary"]["strict_ready"] is False
    blocker_codes = {item["code"] for item in result["summary"]["strict_blockers"]}
    assert {"data_root_invalid", "output_root_invalid"} <= blocker_codes


def test_doctor_strict_readiness_requires_file_write_safety_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.doctor.file_write_safety_status",
        lambda _root: {
            "status": "incomplete",
            "guards": {"fetch-osm": False},
        },
    )

    result = inspect_project(tmp_path)

    assert result["summary"]["strict_ready"] is False
    assert any(
        blocker["code"] == "file_write_safety_incomplete"
        for blocker in result["summary"]["strict_blockers"]
    )


def test_console_command_status_rejects_missing_entrypoint(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project.scripts]\nexample = 'missing.module:main'\n", encoding="utf-8"
    )

    result = console_commands_status(tmp_path)

    assert result["status"] == "incomplete"
    assert result["command_count"] == 1
    assert result["commands"]["example"]["exists"] is False


def test_console_command_status_reports_installed_wrapper_separately(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        "[project.scripts]\nexample = 'example:main'\n", encoding="utf-8"
    )
    (tmp_path / "example.py").write_text("def main(): pass\n", encoding="utf-8")
    executable = tmp_path / "example"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        "scripts.doctor.shutil.which",
        lambda command: str(executable) if command == "example" else None,
    )

    result = console_commands_status(tmp_path)

    assert result["status"] == "available"
    assert result["installation_status"] == "available"
    assert result["installed_count"] == 1
    assert result["commands"]["example"]["installed"] is True
    assert result["commands"]["example"]["executable_path"] == str(executable)


def test_doctor_recognizes_cached_core_inputs_and_outputs(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "output"
    for path in (
        data / "raw" / "ireland-latest.osm.pbf",
        data / "combined.json",
        data / "niah" / "niah.json",
        out / "analysis_results.csv",
        out / "report.html",
        out / "verification.json",
        out / "manifest.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    result = inspect_project(tmp_path, data_root=data, out_dir=out)
    assert result["summary"]["missing_required_inputs"] == 0
    assert result["summary"]["generated_outputs_available"] == 4
    core_labels = {"analysis results", "standalone report", "verification record", "provenance manifest"}
    assert all(item["exists"] for item in result["outputs"] if item["label"] in core_labels)
    assert not any(item["exists"] for item in result["outputs"] if item["label"] == "Parquet analysis export")


def test_doctor_verification_status_controls_strict_readiness(tmp_path, monkeypatch):
    data = tmp_path / "data"
    out = tmp_path / "output"
    schema = tmp_path / "schemas" / "artifacts.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    source_schema = Path(__file__).resolve().parents[1] / "schemas" / "artifacts.json"
    schema.write_text(source_schema.read_text(encoding="utf-8"), encoding="utf-8")
    for path in (
        data / "raw" / "ireland-latest.osm.pbf",
        data / "combined.json",
        data / "niah" / "niah.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    monkeypatch.setattr("scripts.doctor.git_dirty", lambda _root: False)
    monkeypatch.setattr("scripts.doctor.git_revision", lambda _root: "test")
    monkeypatch.setattr("scripts.doctor.sys.version_info", (3, 11, 0))

    unverified = inspect_project(tmp_path, data_root=data, out_dir=out)
    assert unverified["summary"]["strict_ready"] is False
    assert unverified["capabilities"]["validation"]["status"] == "incomplete"
    assert any("complete validation gate" in warning for warning in unverified["summary"]["warnings"])
    assert verification_status(out)["status"] == "not_provided"

    out.mkdir(parents=True, exist_ok=True)
    (out / "verification.json").write_text(
        json.dumps({"passed": True, "errors": []}), encoding="utf-8"
    )
    for name in ("analysis_results.csv", "report.html", "interpretation.json"):
        (out / name).write_text("ready", encoding="utf-8")
    (out / "schema_validation.json").write_text(
        json.dumps({"passed": True, "artifacts": {}}), encoding="utf-8"
    )
    (out / "reproducibility.json").write_text(
        json.dumps({"passed": True, "artifact_count": 0}), encoding="utf-8"
    )
    _write_current_manifest(out)
    verified = inspect_project(tmp_path, data_root=data, out_dir=out)
    assert verified["verification"]["status"] == "pass"
    assert verified["capabilities"]["validation"]["status"] == "pass"
    assert verified["capabilities"]["validation"]["output_alignment"]["status"] == "pass"
    assert verified["summary"]["strict_ready"] is True
    assert next(
        item for item in verified["outputs"] if item["label"] == "interpretation sidecar"
    )["exists"] is True

    (out / "report.html").write_text("tampered", encoding="utf-8")
    tampered = inspect_project(tmp_path, data_root=data, out_dir=out)
    assert tampered["capabilities"]["validation"]["output_alignment"]["status"] == "fail"
    assert tampered["summary"]["strict_ready"] is False
    assert any(
        "do not match the manifest" in warning for warning in tampered["summary"]["warnings"]
    )
    (out / "report.html").write_text("ready", encoding="utf-8")

    (out / "verification.json").write_text(
        json.dumps({"passed": False, "errors": ["broken"]}), encoding="utf-8"
    )
    failed = inspect_project(tmp_path, data_root=data, out_dir=out)
    assert failed["verification"]["status"] == "fail"
    assert failed["summary"]["strict_ready"] is False

    (out / "verification.json").write_text("[]", encoding="utf-8")
    assert verification_status(out)["status"] == "invalid"


def test_doctor_verification_status_reports_manifest_cache_alignment(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "verification.json").write_text(
        json.dumps(
            {
                "passed": True,
                "errors": [],
                "manifest_cache_alignment": {
                    "contract": "ireland-geometry.manifest-cache.v1",
                    "status": "pass",
                    "check_count": 28,
                    "mismatches": [],
                    "unavailable_stages": [],
                },
            }
        ),
        encoding="utf-8",
    )

    result = verification_status(out)

    assert result["manifest_cache_alignment"] == {
        "contract": "ireland-geometry.manifest-cache.v1",
        "status": "pass",
        "check_count": 28,
        "mismatch_count": 0,
        "unavailable_stages": [],
    }


def test_doctor_strict_readiness_requires_complete_command_installation(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.doctor.git_dirty", lambda _root: False)
    monkeypatch.setattr("scripts.doctor.git_revision", lambda _root: "test")
    monkeypatch.setattr("scripts.doctor.sys.version_info", (3, 11, 0))
    monkeypatch.setattr(
        "scripts.doctor.console_commands_status",
        lambda _root: {
            "label": "console commands",
            "status": "available",
            "installation_status": "partial",
            "installed_count": 6,
            "command_count": 9,
            "commands": {},
        },
    )
    data = tmp_path / "data"
    out = tmp_path / "output"
    for path in (
        data / "raw" / "ireland-latest.osm.pbf",
        data / "combined.json",
        data / "niah" / "niah.json",
        out / "analysis_results.csv",
        out / "report.html",
        out / "verification.json",
        out / "manifest.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"passed": True, "errors": []})
            if path.name == "verification.json"
            else "ready",
            encoding="utf-8",
        )

    result = inspect_project(tmp_path, data_root=data, out_dir=out)

    assert result["summary"]["strict_ready"] is False
    assert any("wrappers are incomplete" in warning for warning in result["summary"]["warnings"])


def test_doctor_requires_both_routing_graph_components(tmp_path):
    data = tmp_path / "data"
    graph = data / "roads"
    graph.mkdir(parents=True)
    (graph / "road_nodes.csv").write_text("node_id,lat,lon\na,53,-8\n", encoding="utf-8")
    incomplete = routing_graph_status(data)
    assert incomplete["exists"] is False
    assert incomplete["status"] == "incomplete"
    (graph / "road_edges.csv").write_text("u,v,length_m\na,a,0\n", encoding="utf-8")
    complete = routing_graph_status(data)
    assert complete["exists"] is True
    assert complete["status"] == "available"


def test_doctor_detects_complete_sqlite_routing_graph(tmp_path):
    graph = tmp_path / "data" / "roads"
    graph.mkdir(parents=True)
    (graph / "road_graph.sqlite").write_bytes(b"sqlite")
    (graph / "road_graph_metadata.json").write_text(
        json.dumps(
            {
                "complete": True,
                "node_n": 10,
                "edge_n": 20,
                "way_n": 3,
                "maxweightrating_hgv_way_n": 2,
                "maxweightrating_hgv_supported_way_n": 1,
                "maxweightrating_hgv_unlimited_way_n": 0,
                "maxweightrating_hgv_unsupported_way_n": 1,
                "maxweightrating_hgv_segment_n": 4,
            }
        ),
        encoding="utf-8",
    )
    status = routing_graph_status(tmp_path / "data")
    assert status["backend"] == "sqlite"
    assert status["complete"] is True
    assert status["nodes"] == 10
    assert status["edges"] == 20
    assert status["maxweightrating_hgv_way_n"] == 2
    assert status["maxweightrating_hgv_supported_way_n"] == 1
    assert status["maxweightrating_hgv_unsupported_way_n"] == 1
    assert status["maxweightrating_hgv_segment_n"] == 4
    assert status["ferry_schedule_exists"] is False
    assert status["public_holiday_exists"] is False
    (graph / "ferry_schedules.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.ferry-schedules.v1",
                "schedules": [],
            }
        ),
        encoding="utf-8",
    )
    scheduled = routing_graph_status(tmp_path / "data")
    assert scheduled["ferry_schedule_exists"] is True
    assert scheduled["ferry_schedule_contract_status"] == "available"


def test_doctor_detects_supported_optional_source_variants(tmp_path):
    data = tmp_path / "data"
    (data / "lidar").mkdir(parents=True)
    (data / "history").mkdir(parents=True)
    (data / "historical").mkdir(parents=True)
    (data / "review").mkdir(parents=True)
    (data / "lidar" / "surface.tif").write_bytes(b"tif")
    (data / "history" / "osm_history.json").write_text("[]", encoding="utf-8")
    (data / "historical" / "references.csv").write_text("source_type\n", encoding="utf-8")
    (data / "review" / "labels.csv").write_text("osm_id\n", encoding="utf-8")
    result = inspect_project(tmp_path, data_root=data, out_dir=tmp_path / "output")
    optional = {item["label"]: item for item in result["inputs"]["optional"]}
    assert optional["LiDAR/DSM source"]["selected"].endswith("surface.tif")
    assert optional["OSM edit history"]["exists"] is True
    assert optional["historical references"]["exists"] is True
    assert optional["expert review labels"]["exists"] is True


def test_doctor_exposes_local_report_server_capability(tmp_path):
    root = tmp_path
    output = root / "output"
    (root / "scripts").mkdir(parents=True)
    output.mkdir()
    (root / "scripts" / "serve_report.py").write_text("# server\n", encoding="utf-8")
    (output / "report_lazy.html").write_text("<html />", encoding="utf-8")
    (output / "report_data.json").write_text("{}", encoding="utf-8")

    capability = report_server_status(root, output)
    assert capability["status"] == "incomplete"
    assert capability["command"] == "ireland-geometry-serve"
    assert capability["default_bind"] == "127.0.0.1"
    assert capability["gzip_data_pack"] is False
    assert capability["conditional_data_pack"] is False
    assert capability["rejects_symlinks"] is False
    assert capability["checks_manifest_alignment"] is False
    assert capability["report_page_runtime"] is False
    assert capability["report_export_runtime_headers"] is False
    assert capability["report_runtime_api"] is False
    assert capability["report_runtime_snapshot"] is False
    assert capability["report_runtime_source_alignment"] is False
    assert capability["report_source_alignment_surfaces"] is False
    assert capability["explicit_project_root"] is False
    assert capability["route_api"] is False
    assert capability["route_endpoint"] == "/api/route"
    assert capability["query_api"] is False
    assert capability["query_endpoint"] == "/api/query"
    assert capability["query_pagination"] is False
    assert capability["query_cursor"] is False
    assert capability["query_invalid_score_cursor"] is False
    assert capability["query_auto_backend_failover"] is False
    assert capability["query_backend_health"] is False
    assert capability["query_max_limit"] is None
    assert capability["query_max_offset"] is None
    assert capability["query_min_score_finite"] is False
    assert capability["health_readiness"] is False
    assert capability["metadata_api"] is False
    assert capability["metadata_endpoint"] == "/api/metadata"
    assert capability["capabilities_api"] is False
    assert capability["capabilities_endpoint"] == "/api/capabilities"
    assert capability["openapi_api"] is False
    assert capability["openapi_endpoint"] == "/api/openapi.json"
    assert capability["interpretation_api"] is False
    assert capability["interpretation_endpoint"] == "/api/interpretation"
    assert capability["interpretation_available"] is False
    assert capability["query_available"] is False
    (root / "scripts" / "serve_report.py").write_text(
        "ROUTE_API_PATH = '/api/route'\n"
        "QUERY_API_PATH = '/api/query'\n"
        "query_invalid_score_cursor = True\n"
        "query_auto_backend_failover = True\n"
        "query_backend_health query_readable_backends\n"
        "QUERY_MAX_OFFSET = 10000000\n"
        '"ready": ready\n'
        'else "degraded"\n'
        "METADATA_API_PATH = '/api/metadata'\n"
        "CAPABILITIES_API_PATH = '/api/capabilities'\n"
        "OPENAPI_PATH = '/api/openapi.json'\n"
        "OPENAPI_CONTRACT = 'ireland-geometry.openapi.v1'\n"
        "INTERPRETATION_API_PATH = '/api/interpretation'\n"
        "INTERPRETATION_CONTRACT = 'ireland-geometry.interpretation.v1'\n"
        "def _write_capabilities(): pass\n"
        "def openapi_document(): pass\n"
        "def _write_openapi(): pass\n"
        "def _write_interpretation(): pass\n"
        "def _write_gzipped_data_pack(): pass\n"
        "If-None-Match compressed_data_info ETag next_cursor\n"
        "def _output_symlink_paths(): pass\n"
        "def _require_symlink_free_output(): pass\n"
        "Output directory contains a symlink\n"
        "MANIFEST_ALIGNMENT_CONTRACT = 'ireland-geometry.manifest-alignment.v1'\n"
        "def _manifest_artifact_alignment(): pass\n"
        "_sha256_file(candidate)\n"
        "current output hash differs from manifest\n"
            "REPORT_RUNTIME_CONTRACT = 'ireland-geometry.report-runtime.v1'\n"
            "REPORT_RUNTIME_API_PATH = '/api/report/runtime'\n"
            "SOURCE_ALIGNMENT_CONTRACT = 'ireland-geometry.source-alignment.v1'\n"
            "def _report_source_alignment(): pass\n"
            "SOURCE_ALIGNMENT_SURFACES = ()\n"
            '"source_alignment": source_alignment\n'
            '"source_alignment_surfaces": list(SOURCE_ALIGNMENT_SURFACES)\n'
            "source_alignment = _report_source_alignment(\n"
            "manifest_source_alignment(\n"
            "--project-root\n"
            "project_root=active_project_root\n"
            "server.project_root\n"
            "def _report_runtime_state(): pass\n"
        "def _report_runtime_snapshot(): pass\n"
        '"manifest_sha256": manifest_sha256\n'
        '"snapshot": snapshot\n'
        "def _apply_report_runtime(): pass\n"
        "REPORT_RUNTIME_HEADER_NAMES = {}\n"
        "def _report_runtime_headers(): pass\n"
        "def _write_report_runtime(): pass\n"
        "path == REPORT_RUNTIME_API_PATH\n"
        "headers=runtime_headers\n",
        encoding="utf-8",
    )
    (root / "scripts" / "query_data.py").write_text("# incomplete query module\n", encoding="utf-8")
    incomplete_query = report_server_status(root, output)
    assert incomplete_query["status"] == "incomplete"
    assert incomplete_query["query_pagination"] is False
    assert incomplete_query["query_min_score_finite"] is False
    (root / "scripts" / "query_data.py").write_text(
        "import math\n"
        "import heapq\n"
        "class _WorstFirst:\n"
        "    pass\n"
        "def query_rows(offset: int = 0, min_score=None):\n"
        "    if min_score is not None and not math.isfinite(min_score):\n"
        "        raise ValueError('finite')\n"
        "    result = []\n"
        "after_score: float | None = None\n"
        "after_osm_id: str | None = None\n"
        "after_invalid_osm_id: str | None = None\n"
        '"after_invalid_osm_id"\n'
        "not math.isfinite(score)\n"
        'if backend == "auto":\n'
        "_query_backend_rows(\n"
        "Automatic query backend selection failed\n"
        "def _cursor_for_row(row): pass\n"
        "    if limit == 0:\n"
        "        heapq.heappush([], (0, 0))\n"
        "    result = result[offset:]\n",
        encoding="utf-8",
    )
    (output / "analysis_results.jsonl").write_text('{"osm_id":"way/1"}\n', encoding="utf-8")
    complete = report_server_status(root, output)
    assert complete["status"] == "available"
    assert complete["gzip_data_pack"] is True
    assert complete["conditional_data_pack"] is True
    assert complete["rejects_symlinks"] is True
    assert complete["checks_manifest_alignment"] is True
    assert complete["report_page_runtime"] is True
    assert complete["report_export_runtime_headers"] is True
    assert complete["report_runtime_api"] is True
    assert complete["report_runtime_snapshot"] is True
    assert complete["report_runtime_source_alignment"] is True
    assert complete["report_source_alignment_surfaces"] is True
    assert complete["explicit_project_root"] is True
    assert complete["route_api"] is True
    assert complete["query_api"] is True
    assert complete["query_pagination"] is True
    assert complete["query_cursor"] is True
    assert complete["query_invalid_score_cursor"] is True
    assert complete["query_auto_backend_failover"] is True
    assert complete["query_backend_health"] is True
    assert complete["query_max_limit"] == 1000
    assert complete["query_max_offset"] == 10000000
    assert complete["query_min_score_finite"] is True
    assert complete["health_readiness"] is True
    assert complete["metadata_api"] is True
    assert complete["metadata_endpoint"] == "/api/metadata"
    assert complete["capabilities_api"] is True
    assert complete["capabilities_endpoint"] == "/api/capabilities"
    assert complete["openapi_api"] is True
    assert complete["openapi_endpoint"] == "/api/openapi.json"
    assert complete["interpretation_api"] is True
    assert complete["interpretation_endpoint"] == "/api/interpretation"
    assert complete["interpretation_available"] is False
    assert complete["query_available"] is True
    result = inspect_project(root, out_dir=output)
    assert result["capabilities"]["report_server"]["data_pack_exists"] is True
    assert "Capabilities:" in format_report(result)


def test_doctor_exposes_point_to_point_route_query(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "route_query.py").write_text(
            "snap_distance_m estimated_duration_s parse_departure path_node_ids path_segments def _attach_path_constraints( conditional_rules transition_rules road_context\n"
        "def route_geojson(): pass\n"
        "include_ferries ferry_schedule_n shortest_path_metrics public_holiday_contract ROUTE_OBJECTIVES validate_vehicle_weight_t validate_vehicle_rating_t validate_vehicle_height_m validate_vehicle_width_m validate_vehicle_length_m validate_vehicle_axleload_t validate_vehicle_class conditional_access_n conditional_access_direction_n conditional_access_weight_n conditional_access_multiclause_n maxweight_profiles maxheight_profiles maxwidth_profiles maxlength_profiles maxaxleload_profiles maxweight_hgv_profiles maxweightrating_hgv_profiles maxspeed_profiles maxspeed_conditional_profiles oneway_conditional_profiles allow_hgv_destination\n",
        encoding="utf-8",
    )
    (root / "scripts" / "road_routing.py").write_text(
        "def next_active_at(): pass\n",
        encoding="utf-8",
    )
    capability = route_query_status(root)
    assert capability["status"] == "available"
    assert capability["command"] == "ireland-geometry-route"
    assert capability["supports_duration_and_arrival"] is True
    assert capability["supports_departure_profiles"] is True
    assert capability["supports_path_reconstruction"] is True
    assert capability["supports_path_segment_explainability"] is True
    assert capability["supports_path_segment_constraints"] is True
    assert capability["path_segment_constraints"] is True
    assert capability["supports_path_segment_conditional_rules"] is True
    assert capability["path_segment_conditional_rules"] is True
    assert capability["supports_path_segment_transition_rules"] is True
    assert capability["path_segment_transition_rules"] is True
    assert capability["supports_path_segment_way_context"] is True
    assert capability["path_segment_way_context"] is True
    assert capability["supports_geojson"] is True
    assert capability["supports_ferry_geometry"] is True
    assert capability["supports_ferry_schedules"] is True
    assert capability["supports_ferry_waiting"] is True
    assert capability["supports_ferry_durations"] is True
    assert capability["supports_public_holiday_calendars"] is True
    assert capability["supports_route_objectives"] is True
    assert capability["route_objectives"] == ["distance", "duration"]
    assert capability["supports_vehicle_weight_profiles"] is True
    assert capability["vehicle_weight_profiles"] is True
    assert capability["supports_vehicle_rating_profiles"] is True
    assert capability["vehicle_rating_profiles"] is True
    assert capability["supports_vehicle_height_profiles"] is True
    assert capability["vehicle_height_profiles"] is True
    assert capability["supports_vehicle_width_profiles"] is True
    assert capability["vehicle_width_profiles"] is True
    assert capability["supports_vehicle_length_profiles"] is True
    assert capability["vehicle_length_profiles"] is True
    assert capability["supports_vehicle_axleload_profiles"] is True
    assert capability["vehicle_axleload_profiles"] is True
    assert capability["supports_vehicle_class_profiles"] is True
    assert capability["vehicle_class_profiles"] is True
    assert capability["vehicle_classes"] == ["general", "delivery", "hgv", "psv", "taxi"]
    assert capability["supports_conditional_access_profiles"] is True
    assert capability["conditional_access_profiles"] is True
    assert capability["supports_directional_conditional_access_profiles"] is True
    assert capability["directional_conditional_access_profiles"] is True
    assert capability["supports_vehicle_weight_conditional_access_profiles"] is True
    assert capability["vehicle_weight_conditional_access_profiles"] is True
    assert capability["supports_multi_clause_conditional_access_profiles"] is True
    assert capability["multi_clause_conditional_access_profiles"] is True
    assert capability["supports_maxweight_profiles"] is True
    assert capability["maxweight_profiles"] is True
    assert capability["maxheight_profiles"] is True
    assert capability["maxwidth_profiles"] is True
    assert capability["maxlength_profiles"] is True
    assert capability["maxaxleload_profiles"] is True
    assert capability["supports_hgv_maxweight_profiles"] is True
    assert capability["hgv_maxweight_profiles"] is True
    assert capability["supports_hgv_maxweightrating_profiles"] is True
    assert capability["maxweightrating_hgv_profiles"] is True
    assert capability["supports_hgv_destination_profiles"] is True
    assert capability["hgv_destination_profiles"] is True
    assert capability["supports_maxspeed_profiles"] is True
    assert capability["maxspeed_profiles"] is True
    assert capability["supports_maxspeed_conditional_profiles"] is True
    assert capability["maxspeed_conditional_profiles"] is True
    assert capability["supports_oneway_conditional_profiles"] is True
    assert capability["oneway_conditional_profiles"] is True
    assert capability["ferry_schedules_modeled"] is True
    assert capability["ferry_waiting_modeled"] is True
    assert capability["ferry_schedule_contract"] == "ireland-geometry.ferry-schedules.v1"
    assert capability["public_holiday_contract"] == "ireland-geometry.public-holidays.v1"


def test_doctor_rejects_symlinked_report_output_parent(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="doctor output parent directory must not be a symlink"):
        doctor_main(
            [
                "--project-root",
                str(tmp_path / "project"),
                "--out",
                str(linked_parent / "doctor.json"),
                "--json",
            ]
        )


def test_doctor_exposes_lidar_and_3d_adapter_capability():
    root = Path(__file__).resolve().parents[1]
    capability = geo3d_capability_status(root)
    assert capability["accepted_sources"] == [
        "normalized CSV/JSON/GeoJSON",
        "GeoTIFF DSM/DTM",
        "geographic LAS/LAZ",
    ]
    assert capability["adapters"]["normalized"] is True
    assert capability["adapters"]["geotiff"] == dependency_status("rasterio", required=False)["available"]
    assert capability["adapters"]["las_laz"] == dependency_status("laspy", required=False)["available"]
    assert capability["status"] in {"available", "incomplete"}


def test_doctor_exposes_analysis_export_query_capability(tmp_path):
    root = tmp_path
    output = root / "output"
    (root / "scripts").mkdir(parents=True)
    output.mkdir()
    (root / "scripts" / "query_data.py").write_text(
        "import math\n"
        "import heapq\n"
        "class _WorstFirst:\n"
        "    pass\n"
        "def query_rows(offset: int = 0, min_score=None):\n"
        "    if min_score is not None and not math.isfinite(min_score):\n"
        "        raise ValueError('finite')\n"
        "    result = []\n"
        "after_score: float | None = None\n"
        "after_osm_id: str | None = None\n"
        "after_invalid_osm_id: str | None = None\n"
        '"after_invalid_osm_id"\n'
        "not math.isfinite(score)\n"
        'if backend == "auto":\n'
        "_query_backend_rows(\n"
        "Automatic query backend selection failed\n"
        "def _cursor_for_row(row): pass\n"
        "    if limit == 0:\n"
        "        heapq.heappush([], (0, 0))\n"
        "    result = result[offset:]\n",
        encoding="utf-8",
    )
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    capability = query_data_status(root, output)
    assert capability["status"] == "available"
    assert capability["command"] == "ireland-geometry-query"
    assert capability["default_limit"] == 100
    assert "min_score" in capability["filters"]
    assert "offset" in capability["filters"]
    assert capability["supports_pagination"] is True
    assert capability["supports_cursor"] is True
    assert capability["supports_invalid_score_cursor"] is True
    assert capability["auto_backend_failover"] is True
    assert capability["readable_backends"] is True
    assert capability["backend_health"]["csv"]["readable"] is True
    assert capability["max_page_size"] == 1000
    assert capability["min_score_finite"] is True
    assert capability["bounded_fallback"] is True
    assert capability["backends"]["csv"] is True


def test_doctor_exposes_portable_artifact_bundle_capability(tmp_path):
    root = tmp_path
    output = root / "output"
    (root / "scripts").mkdir(parents=True)
    output.mkdir()
    (root / "scripts" / "bundle.py").write_text(
        "BUNDLE_CONTRACT = 'ireland-geometry.bundle.v1'\n"
        "def build_bundle(): pass\n"
        "def verify_bundle(): pass\n"
        "def extract_bundle(): pass\n"
        "ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)\n"
        "DEFAULT_EXCLUDES = ('doctor.json',)\n"
        "VALIDATION_FILES = ('schema_validation.json', 'reproducibility.json')\n"
        "source_validation schema_validation.json reproducibility.json\n"
        "archive.relative_to(output) outside the output directory\n"
        "Bundle archive input Extraction parent directory\n"
        "--require-verified\n"
        "--json\n",
        encoding="utf-8",
    )

    capability = bundle_capability_status(root, output)

    assert capability["status"] == "available"
    assert capability["command"] == "ireland-geometry-bundle"
    assert capability["contract"] == "ireland-geometry.bundle.v1"
    assert capability["deterministic_zip"] is True
    assert capability["verifies_archives"] is True
    assert capability["extracts_archives"] is True
    assert capability["requires_verified_guard"] is True
    assert capability["requires_complete_validation"] is True
    assert capability["rejects_archive_inside_output"] is True
    assert capability["rejects_archive_input_symlink"] is True
    assert capability["rejects_extraction_parent_symlink"] is True
    assert capability["json_output"] is True
    assert capability["manifest_exists"] is False


def test_doctor_does_not_advertise_incomplete_query_contract(tmp_path):
    root = tmp_path
    output = root / "output"
    (root / "scripts").mkdir(parents=True)
    output.mkdir()
    (root / "scripts" / "query_data.py").write_text("# query module present\n", encoding="utf-8")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    capability = query_data_status(root, output)

    assert capability["status"] == "incomplete"
    assert capability["supports_pagination"] is False
    assert capability["supports_cursor"] is False
    assert capability["supports_invalid_score_cursor"] is False
    assert capability["auto_backend_failover"] is False
    assert capability["readable_backends"] is True
    assert capability["min_score_finite"] is False
    assert capability["bounded_fallback"] is False
    assert capability["backends"]["csv"] is True


def test_doctor_exposes_resumable_expert_review_workflow(tmp_path):
    root = tmp_path
    output = root / "output"
    (root / "scripts").mkdir(parents=True)
    output.mkdir()
    (root / "scripts" / "review.py").write_text("def validate_labels(rows): pass\n", encoding="utf-8")
    (root / "scripts" / "review_ui.py").write_text("# review ui\n", encoding="utf-8")
    (output / "review_queue.csv").write_text(
        "osm_id,label,niah_name,architect,reference_urls,review_warnings\n"
        "way/1,not_reviewed,,,,\n",
        encoding="utf-8",
    )
    (output / "review.html").write_text(
        'id="search" id="statusFilter" localStorage '
        'restoreViewState history.replaceState '
        'aria-label="Search review queue" aria-label="Review label for way/1" '
        'function downloadJson async function importJson '
        'aria-label="Import review labels JSON" '
        'Unsupported JSON backup schema different review queue invalid label records '
        'aria-live="polite" '
        'data-edit="evidence_source" expert-labels.csv '
        'Evidence detail Source links id="targetNotice" not in the current',
        encoding="utf-8",
    )

    capability = review_workflow_status(root, output)

    assert capability["status"] == "available"
    assert capability["queue_rows"] == 1
    assert capability["affordances"]["local_resume"] is True
    assert capability["affordances"]["view_state_restore"] is True
    assert capability["affordances"]["shareable_view_state"] is True
    assert capability["affordances"]["evidence_detail"] is True
    assert capability["affordances"]["target_notice"] is True
    assert capability["affordances"]["label_input_validation"] is True
    assert capability["affordances"]["queue_scope_notice"] is True
    assert capability["queue_evidence_fields"]["reference_urls"] is True


def test_doctor_exposes_dashboard_route_panel_and_overlays(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    report = """id="routeRun"
function runRoute()
routeCoordinates(payload)
id="routeObjective"
objective:$('routeObjective').value
fastest duration
ferry_wait_s
ferry_wait_n
function routeWaitText(route)
ferry_way_ids
ferry_distance_m
ferry_crossing_s
ferry_edge_n
function routeFerryText(route)
function routeSegmentText(route)
path_segment_source
transition_rules
road_context
id="routeWeight"
weight_t:$('routeWeight').value
vehicle-weight
class="offline-route"
routeLine=L.polyline
function renderOfflineMap()
OFFLINE_REQUESTED
function loadMapAssets()
class="sort-button"
function updateSortHeaders
document.addEventListener('keydown'
tabindex="0"
aria-label="Search analyzed targets"
id="reviewState"
not_queued
review_queue_targets
Not in review queue
id="quality"
id="qualityFindings"
data-quality-focus
Inspect duplicate-centroid groups
id="qualityAudit"
id="qualityAuditTable"
Download audit CSV
id="interpretation"
let INTERPRETATION
function renderInterpretation()
id="runtimeStatus"
function applyRuntime(runtime)
    function applyRuntimeHeaders(headers)
    function runtimeIdentityText()
    const snapshot=REPORT_RUNTIME?.snapshot
    const sourceStatus=String(sourceAlignment.status||'not_reported')
    const BASE_INTERPRETATION = PACK.interpretation || {};
INTERPRETATION={...BASE_INTERPRETATION};
function renderRuntimeStatus()
const runtimeChanged=applyRuntime(payload.runtime)
applyRuntimeHeaders(response.headers)
function refreshRuntime()
function startRuntimeRefresh()
setInterval(refreshRuntime,RUNTIME_REFRESH_MS)
function renderMethod()
"""
    for name in ("report.html", "report_lazy.html"):
        (output / name).write_text(report, encoding="utf-8")
    (output / "report_data.json").write_text("{}", encoding="utf-8")

    capability = report_capability_status(tmp_path, output)
    assert capability["status"] == "available"
    assert capability["route_panel"] is True
    assert capability["route_overlay_live"] is True
    assert capability["route_overlay_offline"] is True
    assert capability["route_wait_observability"] is True
    assert capability["route_ferry_breakdown"] is True
    assert capability["route_segment_explainability"] is True
    assert capability["route_transition_rule_provenance"] is True
    assert capability["route_way_context"] is True
    assert capability["route_weight_profile"] is True
    assert capability["review_filter"] is True
    assert capability["review_queue_membership"] is True
    assert capability["accessibility"] is True
    assert capability["quality_panel"] is True
    assert capability["quality_findings"] is True
    assert capability["quality_audit"] is True
    assert capability["interpretation_panel"] is True
    assert capability["live_runtime_refresh"] is True

    for name in ("report.html", "report_lazy.html"):
        (output / name).write_text(report.replace("function renderRuntimeStatus()", ""), encoding="utf-8")
    stale = report_capability_status(tmp_path, output)
    assert stale["live_runtime_refresh"] is False
    assert stale["status"] == "incomplete"


def test_doctor_exposes_pipeline_report_export_and_validation_capabilities(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = inspect_project(root, data_root=tmp_path / "data", out_dir=tmp_path / "output")
    capabilities = result["capabilities"]
    assert capabilities["pipeline"]["status"] == "available"
    assert capabilities["pipeline"]["stage_count"] == 26
    assert capabilities["pipeline"]["supports_dry_run"] is True
    assert capabilities["pipeline"]["supports_dry_run_json"] is True
    assert capabilities["pipeline"]["preserves_diagnostic_manifest_context"] is True
    assert capabilities["pipeline"]["diagnostic_json_outputs"] is True
    assert capabilities["pipeline"]["parameter_validation"] is True
    assert capabilities["reports"]["offline_svg_fallback"] is False
    assert set(capabilities["exports"]["backends"]) == {"csv", "jsonl", "parquet", "duckdb"}
    assert capabilities["exports"]["parity"]["status"] == "not_provided"
    assert capabilities["validation"]["status"] == "incomplete"
    assert capabilities["validation"]["manifest_coverage"]["recursive"] is True
    assert capabilities["validation"]["manifest_coverage"]["rejects_symlinks"] is True
    assert capabilities["validation"]["manifest_coverage"]["status"] == "available"
    assert capabilities["pages"]["status"] == "available"
    assert capabilities["pages"]["audit"]["passed"] is True
    publication = json.loads(
        (root / "docs" / "pages_manifest.json").read_text(encoding="utf-8")
    )
    assert capabilities["pages"]["source_revision"] == publication["source_manifest_revision"]
    assert isinstance(capabilities["pages"]["source_revision"], str)
    assert capabilities["pages"]["source_revision"]
