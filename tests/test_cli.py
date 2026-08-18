from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import run_pipeline
from run_pipeline import STAGES, build_stage_command, parse_args, selected_stages
from scripts.runtime import package_version
from scripts.stage_cache import input_paths

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "command",
    (
        [sys.executable, str(ROOT / "run_pipeline.py")],
        [sys.executable, str(ROOT / "scripts" / "doctor.py")],
        [sys.executable, str(ROOT / "scripts" / "query_data.py")],
        [sys.executable, str(ROOT / "scripts" / "route_query.py")],
        [sys.executable, str(ROOT / "scripts" / "serve_report.py")],
        [sys.executable, str(ROOT / "scripts" / "bundle.py")],
        [sys.executable, str(ROOT / "scripts" / "schema_audit.py")],
        [sys.executable, str(ROOT / "scripts" / "repro_check.py")],
    ),
)
def test_console_commands_report_package_version(command):
    result = subprocess.run(
        [*command, "--version"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip().endswith(package_version())


def test_pipeline_help_exits_without_running_work():
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_pipeline.py"), "--help"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--no-network" in result.stdout
    assert "fetch-niah" in result.stdout
    assert "verify" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--dry-run-json" in result.stdout
    assert "--project-root" in result.stdout
    assert "--routing-max-ways" in result.stdout
    assert "--routing-departure" in result.stdout
    assert "--routing-include-ferries" in result.stdout
    assert "===== " not in result.stdout


def test_query_help_exposes_cursor_pagination():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "query_data.py"), "--help"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--after-score" in result.stdout
    assert "--after-osm-id" in result.stdout
    assert "--after-invalid-osm-id" in result.stdout


def test_pipeline_rejects_invalid_parameters_before_running(capsys):
    cases = (
        (["--mc", "0"], "--mc must be positive"),
        (["--bootstrap-iterations", "0"], "--bootstrap-iterations must be positive"),
        (["--holdout-fraction", "nan"], "--holdout-fraction must be finite"),
        (["--holdout-fraction", "1"], "--holdout-fraction must be finite"),
    )
    for argv, message in cases:
        with pytest.raises(SystemExit):
            parse_args(argv)
        assert message in capsys.readouterr().err


def test_invalid_pipeline_parameter_does_not_create_output_directory(tmp_path):
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "analyze",
            "--mc",
            "0",
            "--out-dir",
            str(output),
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--mc must be positive" in result.stderr
    assert not output.exists()


def test_verify_must_be_last_stage():
    assert selected_stages("analyze,verify") == ["analyze", "verify"]
    assert selected_stages("analyze,sensitivity,building-parts,verify") == [
        "analyze",
        "sensitivity",
        "building-parts",
        "verify",
    ]
    with pytest.raises(SystemExit, match="must be last"):
        selected_stages("verify,analyze")


def test_report_and_verify_refresh_the_final_report_pack(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_pipeline,
        "run_stage",
        lambda name, *args, **kwargs: calls.append(name),
    )
    monkeypatch.setattr(run_pipeline, "write_manifest", lambda *args, **kwargs: None)

    run_pipeline.main(
        [
            "--stage",
            "report,verify",
            "--project-root",
            str(tmp_path / "project"),
            "--data-root",
            str(tmp_path / "project" / "data"),
            "--out-dir",
            str(tmp_path / "project" / "output"),
            "--no-network",
        ]
    )

    assert calls == ["report", "verify", "report", "verify"]


def test_empty_stage_selection_is_rejected():
    with pytest.raises(SystemExit, match="At least one pipeline stage"):
        selected_stages("")


def test_quality_audit_runs_after_routing_dependency():
    assert STAGES.index("road-routing") < STAGES.index("quality-audit")


def test_dry_run_plans_without_writing_outputs(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "analyze,verify",
            "--data-root",
            str(tmp_path / "data"),
            "--out-dir",
            str(tmp_path / "output"),
            "--dry-run",
            "--incremental",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[dry-run] analyze: run" in result.stdout
    assert "[dry-run] verify: always-run" in result.stdout
    assert "scripts/verify.py" in result.stdout
    assert "[dry-run] no files written" in result.stdout
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "output" / "manifest.json").exists()


def test_json_dry_run_emits_a_machine_readable_plan_without_writing_outputs(tmp_path):
    project = tmp_path / "project"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "analyze,verify",
            "--data-root",
            str(project / "data"),
            "--out-dir",
            str(project / "output"),
            "--project-root",
            str(project),
            "--incremental",
            "--dry-run-json",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["contract"] == "ireland-geometry.dry-run.v1"
    assert plan["cache_version"] == 4
    assert plan["cache_explanations"] is True
    assert plan["package_version"] == package_version()
    assert plan["dry_run"] is True
    assert plan["no_files_written"] is True
    assert plan["incremental"] is True
    assert plan["project_root"] == str(project)
    assert plan["data_root"] == str(project / "data")
    assert plan["out_dir"] == str(project / "output")
    assert [stage["name"] for stage in plan["stages"]] == ["analyze", "verify"]
    assert plan["stages"][0]["status"] == "run"
    assert plan["stages"][0]["fingerprint"]
    assert plan["stages"][0]["cache"]["reason"] == "cache_missing"
    assert plan["stages"][1]["status"] == "always-run"
    assert plan["stages"][1]["cacheable"] is False
    assert plan["stages"][1]["cache"]["reason"] == "always_run"
    assert not (project / "output").exists()


def test_json_dry_run_advertises_post_validation_report_refresh(tmp_path):
    project = tmp_path / "project"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "report,verify",
            "--project-root",
            str(project),
            "--data-root",
            str(project / "data"),
            "--out-dir",
            str(project / "output"),
            "--dry-run-json",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["post_validation_refresh"]["enabled"] is True
    assert len(plan["post_validation_refresh"]["operations"]) == 2
    assert [stage["name"] for stage in plan["stages"]] == [
        "report",
        "verify",
        "report",
        "verify",
    ]
    assert all(
        stage.get("phase") == "post_validation_refresh"
        for stage in plan["stages"][2:]
    )
    assert plan["stage_count"] == 4
    assert plan["no_files_written"] is True
    assert not (project / "output").exists()


def test_external_project_dry_run_uses_packaged_analysis_plan(tmp_path):
    project = tmp_path / "project"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--project-root",
            str(project),
            "--stage",
            "holdout",
            "--dry-run-json",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    packaged_plan = str(ROOT / "schemas" / "analysis_plan.json")
    assert packaged_plan in plan["stages"][0]["command"]
    assert not project.exists()


def test_diagnostic_rerun_preserves_existing_manifest_build_context(tmp_path):
    project = tmp_path / "project"
    output = project / "output"
    output.mkdir(parents=True)
    original = {
        "manifest_version": 2,
        "schema_version": 3,
        "parameters": {"stage": "all", "seed": 20260816, "mc": 300},
        "sources": [{"kind": "previous-build-source"}],
    }
    (output / "manifest.json").write_text(json.dumps(original), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "verify",
            "--project-root",
            str(project),
            "--no-network",
            "--seed",
            "999",
            "--mc",
            "7",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    updated = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert updated["project_root"] == str(project.resolve())
    assert updated["parameters"] == original["parameters"]
    assert updated["sources"] == original["sources"]
    assert updated["provenance_context_preserved"] is True
    assert updated["last_invocation"]["seed"] == 999


def test_pbf_routing_cache_tracks_the_pbf_input(tmp_path):
    args = parse_args(["--stage", "road-routing", "--road-from-pbf"])
    pbf = tmp_path / "ireland.osm.pbf"
    paths = input_paths(
        "road-routing",
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        pbf=pbf,
        args=args,
        schema_path=ROOT / "schemas" / "artifacts.json",
        root=ROOT,
    )
    assert pbf in paths


def test_optional_cache_inputs_are_limited_to_consuming_stages(tmp_path):
    args = parse_args(
        [
            "--stage",
            "all",
            "--lidar",
            "data/lidar/custom.csv",
            "--review-labels",
            "data/review/custom.csv",
        ]
    )
    kwargs = {
        "data_root": tmp_path / "data",
        "out_dir": tmp_path / "output",
        "pbf": tmp_path / "data" / "raw" / "ireland.osm.pbf",
        "args": args,
        "schema_path": ROOT / "schemas" / "artifacts.json",
        "root": ROOT,
    }
    analyze_paths = input_paths("analyze", **kwargs)
    parts_paths = input_paths("building-parts", **kwargs)
    review_paths = input_paths("review", **kwargs)
    assert ROOT / "data/lidar/custom.csv" not in analyze_paths
    assert ROOT / "data/lidar/custom.csv" in parts_paths
    assert ROOT / "data/review/custom.csv" in review_paths
    assert ROOT / "scripts/review_ui.py" in review_paths


def test_routing_limits_reach_the_pbf_adapter_command():
    args = parse_args(
        [
            "--stage",
            "road-routing",
            "--road-from-pbf",
            "--routing-max-ways",
            "12",
            "--routing-max-pairs",
            "7",
            "--routing-departure",
            "2026-08-17T08:00:00+00:00",
            "--routing-speed-kmh",
            "40",
            "--routing-include-restricted",
            "--routing-include-ferries",
        ]
    )
    command = build_stage_command(
        "road-routing",
        args,
        ROOT / "data",
        ROOT / "output",
        ROOT / "data" / "raw" / "ireland-latest.osm.pbf",
    )
    assert "--max-ways" in command and command[command.index("--max-ways") + 1] == "12"
    assert "--max-pairs" in command and command[command.index("--max-pairs") + 1] == "7"
    assert "--departure" in command and command[command.index("--departure") + 1] == "2026-08-17T08:00:00+00:00"
    assert "--speed-kmh" in command and command[command.index("--speed-kmh") + 1] == "40.0"
    assert "--include-restricted" in command
    assert "--include-ferries" in command


def test_default_routing_cache_tracks_graph_files_not_graph_documentation(tmp_path):
    args = parse_args(["--stage", "road-routing"])
    paths = input_paths(
        "road-routing",
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        pbf=tmp_path / "data" / "raw" / "ireland.osm.pbf",
        args=args,
        schema_path=ROOT / "schemas" / "artifacts.json",
        root=ROOT,
    )
    roads = tmp_path / "data" / "roads"
    assert roads / "road_nodes.csv" in paths
    assert roads / "road_edges.csv" in paths
    assert roads / "README.md" not in paths


def test_custom_routing_graph_replaces_default_graph_dependencies(tmp_path):
    args = parse_args(["--stage", "road-routing", "--road-graph", "data/roads/custom.json"])
    paths = input_paths(
        "road-routing",
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        pbf=tmp_path / "data" / "raw" / "ireland.osm.pbf",
        args=args,
        schema_path=ROOT / "schemas" / "artifacts.json",
        root=ROOT,
    )
    assert ROOT / "data/roads/custom.json" in paths
    assert tmp_path / "data" / "roads" / "road_nodes.csv" not in paths
    assert tmp_path / "data" / "roads" / "road_edges.csv" not in paths
