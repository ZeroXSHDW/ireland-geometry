from __future__ import annotations

import io
import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

import run_pipeline
from run_pipeline import (
    STAGES,
    build_bundle_command,
    build_bundle_verify_command,
    build_stage_command,
    default_bundle_archive,
    parse_args,
    selected_stages,
)
from scripts.fetch_geofabrik import main as fetch_geofabrik_main
from scripts.fetch_niah import main as fetch_niah_main
from scripts.fetch_osm import fetch_cell
from scripts.fetch_osm import main as fetch_osm_main
from scripts.fetch_satellite import main as fetch_satellite_main
from scripts.negative_controls import main as negative_controls_main
from scripts.niah import main as niah_main
from scripts.query_data import main as query_data_main
from scripts.road_routing import main as road_routing_main
from scripts.route_query import main as route_query_main
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
        [sys.executable, str(ROOT / "scripts" / "release_check.py")],
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
    assert "--bundle" in result.stdout
    assert "--bundle-archive" in result.stdout
    assert "--project-root" in result.stdout
    assert "--routing-max-ways" in result.stdout
    assert "--routing-departure" in result.stdout
    assert "--routing-include-ferries" in result.stdout
    assert "--routing-vehicle-class" in result.stdout
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


def test_pipeline_bundle_runs_after_the_final_verify(tmp_path, monkeypatch):
    project = tmp_path / "project"
    calls = []
    bundles = []
    monkeypatch.setattr(
        run_pipeline,
        "run_stage",
        lambda name, *args, **kwargs: calls.append(name),
    )
    monkeypatch.setattr(run_pipeline, "write_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run_pipeline,
        "run_bundle",
        lambda output, archive, env: bundles.append((output, archive)),
    )

    run_pipeline.main(
        [
            "--stage",
            "report,verify",
            "--project-root",
            str(project),
            "--data-root",
            str(project / "data"),
            "--out-dir",
            str(project / "output"),
            "--bundle",
            "--bundle-archive",
            "release.zip",
            "--no-network",
        ]
    )

    assert calls == ["report", "verify", "report", "verify"]
    assert bundles == [(project / "output", project / "release.zip")]


def test_bundle_requires_verify_stage(tmp_path):
    with pytest.raises(SystemExit, match="requires the verify stage"):
        run_pipeline.main(
            [
                "--stage",
                "analyze",
                "--project-root",
                str(tmp_path / "project"),
                "--bundle",
            ]
        )


def test_bundle_archive_requires_bundle_flag():
    with pytest.raises(SystemExit):
        parse_args(["--bundle-archive", "release.zip"])


def test_bundle_archive_cannot_be_inside_output(tmp_path):
    project = tmp_path / "project"
    with pytest.raises(SystemExit, match="outside the output directory"):
        run_pipeline.main(
            [
                "--stage",
                "verify",
                "--project-root",
                str(project),
                "--out-dir",
                str(project / "output"),
                "--bundle",
                "--bundle-archive",
                str(project / "output" / "release.zip"),
            ]
        )


def test_pipeline_rejects_symlinked_output_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    output = project / "output"
    output.mkdir()
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(output, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="must not be a symlink"):
        run_pipeline.main(
            [
                "--stage",
                "verify",
                "--project-root",
                str(project),
                "--out-dir",
                str(linked_output),
                "--dry-run-json",
            ]
        )


def test_pipeline_rejects_nested_symlinks_before_dry_run(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    link = data / "combined.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_pipeline.py"),
            "--stage",
            "analyze",
            "--data-root",
            str(data),
            "--out-dir",
            str(tmp_path / "output"),
            "--dry-run",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "data directory contains symlink" in result.stderr


def test_pipeline_rejects_file_output_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    output = project / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="must be a directory"):
        run_pipeline.main(
            [
                "--stage",
                "verify",
                "--project-root",
                str(project),
                "--out-dir",
                str(output),
                "--dry-run-json",
            ]
        )


def test_pipeline_rejects_file_data_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    data = project / "data-file"
    data.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="data directory must be a directory"):
        run_pipeline.main(
            [
                "--stage",
                "verify",
                "--project-root",
                str(project),
                "--data-root",
                str(data),
                "--out-dir",
                str(project / "output"),
                "--dry-run-json",
            ]
        )


def test_direct_stage_rejects_file_output_root_before_reading_inputs(tmp_path):
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        negative_controls_main(["--out-dir", str(output)])


def test_direct_stage_rejects_nested_output_symlink_before_reading_inputs(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "external.csv"
    target.write_text("x\n", encoding="utf-8")
    link = output / "analysis_results.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory contains symlink"):
        negative_controls_main(["--out-dir", str(output)])


def test_direct_niah_rejects_nested_data_symlink_before_reading_inputs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    link = data / "combined.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "niah.py",
            "--data-root",
            str(data),
            "--out-dir",
            str(tmp_path / "output"),
        ],
    )
    with pytest.raises(SystemExit, match="data directory contains symlink"):
        niah_main()


def test_analyze_rejects_symlinked_data_override_before_reading(tmp_path):
    target = tmp_path / "external.json"
    target.write_text('{"elements": []}', encoding="utf-8")
    link = tmp_path / "combined.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "analyze.py"),
            "--data",
            str(link),
            "--out",
            str(tmp_path / "output"),
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "analysis input must not be a symlink" in result.stderr


def test_route_query_rejects_nested_data_symlink_before_graph_read(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "external-roads"
    external.mkdir()
    link = data / "roads"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="data directory contains symlink"):
        route_query_main(
            [
                "--data-root",
                str(data),
                "--start-lat",
                "53.0",
                "--start-lon",
                "-6.0",
                "--goal-lat",
                "53.1",
                "--goal-lon",
                "-6.1",
            ]
        )


def test_query_data_rejects_nested_output_symlink_before_reading(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "external.csv"
    target.write_text("x\n", encoding="utf-8")
    link = output / "analysis_results.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory contains symlink"):
        query_data_main(["--out-dir", str(output)])


def test_direct_routing_rejects_file_graph_root_before_materializing(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    output = project / "output"
    output.mkdir()
    graph = project / "graph"
    graph.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit):
        road_routing_main(
            [
                "--from-pbf",
                "--pbf",
                str(project / "missing.osm.pbf"),
                "--write-graph",
                str(graph),
                "--out-dir",
                str(output),
            ]
        )

    assert "road graph directory must be a directory" in capsys.readouterr().err


def test_direct_routing_rejects_nested_graph_output_symlink_before_materializing(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    output = project / "output"
    output.mkdir()
    graph = project / "graph"
    graph.mkdir()
    external = tmp_path / "external-graph"
    external.mkdir()
    try:
        (graph / "nested").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit):
        road_routing_main(
            [
                "--from-pbf",
                "--pbf",
                str(project / "missing.osm.pbf"),
                "--write-graph",
                str(graph),
                "--graph-format",
                "sqlite",
                "--out-dir",
                str(output),
            ]
        )
    assert "road graph directory contains symlink" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("fetch_main", "extra_args"),
    ((fetch_osm_main, ("--data-dir",)), (fetch_niah_main, ("--data-root", "--no-network"))),
)
def test_ingestion_clis_reject_file_data_root(tmp_path, monkeypatch, fetch_main, extra_args):
    data = tmp_path / "data-file"
    data.write_text("not a directory", encoding="utf-8")
    argv = ["fetch.py"]
    for argument in extra_args:
        if argument.startswith("--") and argument not in {"--no-network"}:
            argv.extend((argument, str(data)))
        else:
            argv.append(argument)
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="data directory must be a directory"):
        fetch_main()


def test_satellite_fetch_rejects_nested_output_symlink_before_download(tmp_path, monkeypatch):
    satellite = tmp_path / "data" / "satellite"
    satellite.mkdir(parents=True)
    external = tmp_path / "external-satellite"
    external.mkdir()
    try:
        (satellite / "nested").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    monkeypatch.setattr("scripts.runtime.ROOT", tmp_path)
    monkeypatch.setattr("scripts.fetch_satellite.get_token", lambda _user, _pwd: "token")
    monkeypatch.setattr("scripts.fetch_satellite.list_tiles", lambda _token, _date, _cloud: [])
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_satellite.py", "--download", "--user", "user", "--pass", "password"],
    )

    with pytest.raises(ValueError, match="satellite data directory contains symlink"):
        fetch_satellite_main()


def test_niah_fetch_streams_download_and_zip_members_atomically(tmp_path, monkeypatch):
    csv_payload = (
        "REG_NO,NAME,TOWN,TOWNLAND,COUNTY,RATING,ORIGINAL_TYPE,DATEFROM,DATETO,"
        "LATITUDE,LONGITUDE,COMPOSITION,APPRAISAL\n"
        "D-1,Example House,Dublin,Example, Dublin,National,house,1850,1860,"
        "53.35,-6.26,stone,good\n"
    ).encode("latin-1")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/Dublin.csv", csv_payload)
    archive_payload = archive_buffer.getvalue()

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 1024 * 1024
            for start in range(0, len(archive_payload), 7):
                yield archive_payload[start : start + 7]

        def close(self):
            self.closed = True

    response = FakeResponse()
    def fake_get(url, **kwargs):
        assert kwargs["stream"] is True
        return response

    requests_stub = types.SimpleNamespace(get=fake_get)
    monkeypatch.setitem(sys.modules, "requests", requests_stub)
    monkeypatch.setattr("scripts.fetch_niah.REGIONS", {"Dublin": "https://example.test/Dublin.zip"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_niah.py", "--refresh", "--data-root", str(tmp_path / "data")],
    )

    fetch_niah_main()

    data = tmp_path / "data" / "niah"
    assert (data / "Dublin.zip").read_bytes() == archive_payload
    assert (data / "Dublin" / "Dublin.csv").read_bytes() == csv_payload
    records = json.loads((data / "niah.json").read_text(encoding="utf-8"))
    assert records[0]["reg_no"] == "D-1"
    assert response.closed is True


def test_overpass_fetch_replaces_symlinked_file_without_touching_target(tmp_path, monkeypatch):
    data = tmp_path / "data"
    raw = data / "raw"
    raw.mkdir(parents=True)
    target = tmp_path / "external.json"
    target.write_text("external", encoding="utf-8")
    linked = raw / "worship__custom.json"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(
        "scripts.fetch_osm.post_overpass",
        lambda *_args: {"elements": [{"type": "way", "id": 1}]},
    )

    count = fetch_cell("worship", ("custom", (51.0, -10.0, 52.0, -9.0)), data, True)

    assert count == 1
    assert linked.is_file()
    assert not linked.is_symlink()
    assert "way" in linked.read_text(encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "external"


def test_overpass_fetch_rejects_symlinked_raw_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "external-raw"
    external.mkdir()
    raw = data / "raw"
    try:
        raw.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_osm.py",
            "--data-dir",
            str(data),
            "--groups",
            "worship",
            "--bbox",
            "0,0,1,1",
        ],
    )

    with pytest.raises(SystemExit, match="OSM raw data directory must not be a symlink"):
        fetch_osm_main()


def test_overpass_fetch_rejects_symlinked_raw_cache_file(tmp_path, monkeypatch):
    data = tmp_path / "data"
    raw = data / "raw"
    raw.mkdir(parents=True)
    target = tmp_path / "external.json"
    target.write_text('{"elements": []}', encoding="utf-8")
    linked = raw / "worship__custom.json"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_osm.py",
            "--data-dir",
            str(data),
            "--groups",
            "worship",
            "--bbox",
            "0,0,1,1",
        ],
    )

    with pytest.raises(SystemExit, match="OSM raw cache file must not be a symlink"):
        fetch_osm_main()


def test_niah_fetch_rejects_symlinked_region_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    niah = data / "niah"
    niah.mkdir(parents=True)
    (niah / "Dublin.zip").write_bytes(b"cached archive")
    external = tmp_path / "external-dublin"
    external.mkdir()
    region = niah / "Dublin"
    try:
        region.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr("scripts.fetch_niah.REGIONS", {"Dublin": "https://example.test/Dublin.zip"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_niah.py", "--no-network", "--data-root", str(data)],
    )

    with pytest.raises(SystemExit, match="NIAH Dublin extraction directory must not be a symlink"):
        fetch_niah_main()


def test_niah_fetch_rejects_nested_symlink_in_region_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    niah = data / "niah"
    region = niah / "Dublin"
    region.mkdir(parents=True)
    (niah / "Dublin.zip").write_bytes(b"cached archive")
    external = tmp_path / "external-dublin"
    external.mkdir()
    try:
        (region / "nested").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr("scripts.fetch_niah.REGIONS", {"Dublin": "https://example.test/Dublin.zip"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_niah.py", "--no-network", "--data-root", str(data)],
    )

    with pytest.raises(SystemExit, match="NIAH Dublin extraction directory contains symlink"):
        fetch_niah_main()


def test_niah_fetch_rejects_symlinked_archive(tmp_path, monkeypatch):
    data = tmp_path / "data"
    niah = data / "niah"
    niah.mkdir(parents=True)
    external = tmp_path / "external.zip"
    external.write_bytes(b"cached archive")
    linked = niah / "Dublin.zip"
    try:
        linked.symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr("scripts.fetch_niah.REGIONS", {"Dublin": "https://example.test/Dublin.zip"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_niah.py", "--no-network", "--data-root", str(data)],
    )

    with pytest.raises(SystemExit, match="NIAH Dublin archive must not be a symlink"):
        fetch_niah_main()


def test_geofabrik_fetch_rejects_symlinked_pbf_input(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    target = tmp_path / "external.osm.pbf"
    target.write_bytes(b"not a pbf")
    linked = raw / "ireland-latest.osm.pbf"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_geofabrik.py",
            "--pbf",
            str(linked),
            "--out",
            str(tmp_path / "output" / "combined.json"),
            "--no-download",
        ],
    )

    with pytest.raises(SystemExit, match="PBF input file must not be a symlink"):
        fetch_geofabrik_main()


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
    assert plan["cache_version"] == 5
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


def test_json_dry_run_advertises_post_validation_bundle(tmp_path):
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
            "--bundle",
            "--bundle-archive",
            "release.zip",
            "--dry-run-json",
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["post_validation_bundle"]["enabled"] is True
    assert plan["post_validation_bundle"]["archive"] == str(project / "release.zip")
    assert [stage["name"] for stage in plan["stages"]] == [
        "report",
        "verify",
        "report",
        "verify",
        "bundle",
        "bundle-verify",
    ]
    assert plan["stages"][-1]["phase"] == "post_validation_bundle"
    assert plan["stages"][-1]["cache"]["reason"] == "post_validation_bundle_verify"
    assert plan["post_validation_bundle"]["verification"]["name"] == "bundle-verify"
    assert [
        operation["name"]
        for operation in plan["post_validation_bundle"]["operations"]
    ] == ["bundle", "bundle-verify"]
    assert plan["no_files_written"] is True
    assert not project.exists()


def test_bundle_command_defaults_beside_output(tmp_path, monkeypatch):
    output = tmp_path / "project" / "output"
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", output.parent)
    assert default_bundle_archive(output) == output.parent / "output.bundle.zip"
    assert default_bundle_archive(output, "release.zip") == tmp_path / "project" / "release.zip"
    command = build_bundle_command(output, output.parent / "output.bundle.zip")
    assert "--require-verified" in command
    assert str(output) in command
    verify_command = build_bundle_verify_command(output.parent / "output.bundle.zip")
    assert verify_command[-2:] == ["--verify", str(output.parent / "output.bundle.zip")]


def test_pipeline_main_does_not_leak_custom_project_root(capsys, tmp_path):
    custom = tmp_path / "custom"
    run_pipeline.main(
        [
            "--project-root",
            str(custom),
            "--stage",
            "verify",
            "--dry-run-json",
        ]
    )
    capsys.readouterr()
    run_pipeline.main(["--stage", "verify", "--dry-run-json"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["project_root"] == str(ROOT)
    assert not custom.exists()


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


def test_diagnostic_rerun_merges_freshness_into_preserved_source_rows(tmp_path):
    project = tmp_path / "project"
    output = project / "output"
    source = project / "data" / "combined.json"
    source.parent.mkdir(parents=True)
    output.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    original = {
        "manifest_version": 2,
        "schema_version": 3,
        "parameters": {"stage": "all", "seed": 20260816, "mc": 300},
        "sources": [
            {
                "kind": "combined_osm_json",
                "path": str(source),
                "path_base": "project_root",
                "relative_path": "data/combined.json",
                "sha256": "legacy-hash-preserved",
            }
        ],
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
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    updated = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert updated["sources"][0]["sha256"] == "legacy-hash-preserved"
    assert len(updated["sources"][0]["metadata_sha256"]) == 64
    assert updated["sources"][0]["modified_at"]
    assert updated["source_freshness"]["sources"][0]["relative_path"] == "data/combined.json"


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
    assert ROOT / "analysis_plan.json" in analyze_paths
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
            "--routing-weight-t",
            "10",
            "--routing-rating-t",
            "18",
            "--routing-height-m",
            "3.8",
            "--routing-width-m",
            "2.5",
            "--routing-length-m",
            "12",
            "--routing-axleload-t",
            "10",
            "--routing-vehicle-class",
            "delivery",
            "--routing-allow-hgv-destination",
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
    assert "--weight-t" in command and command[command.index("--weight-t") + 1] == "10.0"
    assert "--rating-t" in command and command[command.index("--rating-t") + 1] == "18.0"
    assert "--height-m" in command and command[command.index("--height-m") + 1] == "3.8"
    assert "--width-m" in command and command[command.index("--width-m") + 1] == "2.5"
    assert "--length-m" in command and command[command.index("--length-m") + 1] == "12.0"
    assert "--axleload-t" in command and command[command.index("--axleload-t") + 1] == "10.0"
    assert "--vehicle-class" in command and command[command.index("--vehicle-class") + 1] == "delivery"
    assert "--allow-hgv-destination" in command
    assert "--include-restricted" in command
    assert "--include-ferries" in command


def test_verify_stage_command_carries_active_project_root():
    args = parse_args(["--stage", "verify"])
    command = build_stage_command(
        "verify",
        args,
        ROOT / "data",
        ROOT / "output",
        ROOT / "data" / "raw" / "ireland-latest.osm.pbf",
    )

    assert command[command.index("--project-root") + 1] == str(run_pipeline.PROJECT_ROOT)


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
    assert roads / "ferry_schedules.json" in paths
    assert roads / "public_holidays.json" in paths
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
