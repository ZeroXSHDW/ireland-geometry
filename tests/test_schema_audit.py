import json
from importlib.resources import files
from pathlib import Path

import pytest

from scripts import stage_cache
from scripts.runtime import sha256_file
from scripts.schema_audit import audit, main, validate_csv
from scripts.stage_cache import (
    can_reuse,
    fingerprint,
    fingerprint_from_payload,
    fingerprint_payload,
    local_module_paths,
    record_output_hashes,
    reuse_diagnostics,
    save_record,
    stage_outputs,
)


def test_schema_registry_is_available_as_a_package_resource():
    resource = files("schemas").joinpath("artifacts.json")
    assert resource.is_file()
    payload = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(payload.get("artifacts"), dict)
    packaged_plan = files("schemas").joinpath("analysis_plan.json")
    assert packaged_plan.is_file()
    assert json.loads(packaged_plan.read_text(encoding="utf-8"))["plan_id"] == "ireland-geometry-v1"
    source_plan = Path(__file__).resolve().parents[1] / "analysis_plan.json"
    assert packaged_plan.read_bytes() == source_plan.read_bytes()
    ferry_schema = files("schemas").joinpath("ferry_schedules.json")
    assert ferry_schema.is_file()
    assert json.loads(ferry_schema.read_text(encoding="utf-8"))["$id"] == "ireland-geometry.ferry-schedules.v1"
    holiday_schema = files("schemas").joinpath("public_holidays.json")
    assert holiday_schema.is_file()
    assert json.loads(holiday_schema.read_text(encoding="utf-8"))["$id"] == "ireland-geometry.public-holidays.v1"


def test_schema_audit_checks_required_columns_and_numeric_values(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text("osm_id,area_m2\nway/1,12.5\n", encoding="utf-8")
    result = validate_csv(
        path,
        {"required_columns": ["osm_id", "area_m2"], "numeric_columns": ["area_m2"], "unique_id": "osm_id"},
    )
    assert result["passed"] is True
    assert result["row_n"] == 1


def test_schema_audit_allows_empty_review_diagnostic(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "empty.csv": {
                        "required_columns": ["lat", "lon"],
                        "allow_empty": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "empty.csv").write_text("lat,lon\n", encoding="utf-8")
    result = audit(schema, tmp_path)
    assert result["passed"] is True


def test_schema_audit_json_mode_emits_the_written_contract(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "sample.csv": {
                        "required_columns": ["osm_id"],
                        "unique_id": "osm_id",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "sample.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    main(["--json", "--out-dir", str(tmp_path), "--schema", str(schema)])

    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["schema_version"] == 1
    assert json.loads((tmp_path / "schema_validation.json").read_text(encoding="utf-8")) == result


def test_schema_audit_rejects_symlinked_output_root(tmp_path):
    target = tmp_path / "target-output"
    target.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory must not be a symlink"):
        main(["--out-dir", str(linked)])


def test_schema_audit_rejects_file_output_root(tmp_path):
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        main(["--out-dir", str(output)])


def test_schema_audit_rejects_symlinked_schema_input(tmp_path):
    target = tmp_path / "target-schema.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "schema.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="schema input must not be a symlink"):
        main(["--out-dir", str(tmp_path / "output"), "--schema", str(link)])


def test_stage_cache_rejects_changed_output(tmp_path):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    payload = fingerprint_payload(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    fp = fingerprint_from_payload(payload)
    cache = {"cache_version": stage_cache.CACHE_VERSION, "runtime": stage_cache.runtime_signature(), "stages": {}}
    save_record(cache, "sample", fp, record_output_hashes([output]), fingerprint_inputs=payload)
    assert can_reuse(cache, "sample", fp, [output]) is True
    output.write_text("value\n2\n", encoding="utf-8")
    assert can_reuse(cache, "sample", fp, [output]) is False


def test_stage_cache_persists_fingerprint_inputs_and_explains_reuse(tmp_path):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    payload = fingerprint_payload(
        "sample",
        command=["sample", "--flag"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    fp = fingerprint_from_payload(payload)
    cache = {
        "cache_version": stage_cache.CACHE_VERSION,
        "runtime": stage_cache.runtime_signature(),
        "cache_status": "valid",
        "stages": {},
    }
    save_record(
        cache,
        "sample",
        fp,
        record_output_hashes([output]),
        fingerprint_inputs=payload,
    )
    assert cache["stages"]["sample"]["fingerprint_inputs"] == payload
    diagnostics = reuse_diagnostics(cache, "sample", fp, [output])
    assert diagnostics["reusable"] is True
    assert diagnostics["reason"] == "cache_hit"
    assert diagnostics["fingerprint_inputs_present"] is True
    output.write_text("value\n2\n", encoding="utf-8")
    diagnostics = reuse_diagnostics(cache, "sample", fp, [output])
    assert diagnostics["reusable"] is False
    assert diagnostics["reason"] == "output_hash_mismatch"


def test_stage_cache_fingerprint_ignores_git_and_controller_only_changes(tmp_path, monkeypatch):
    output = tmp_path / "artifact.csv"
    controller = tmp_path / "run_pipeline.py"
    output.write_text("value\n1\n", encoding="utf-8")
    controller.write_text("def write_manifest(): pass\n", encoding="utf-8")
    monkeypatch.setattr(stage_cache, "git_revision", lambda: "first-revision")
    first = fingerprint_payload(
        "sample",
        command=["sample", "--flag"],
        script_path=output,
        controller_path=controller,
        input_signatures={str(output): sha256_file(output)},
    )
    controller.write_text("def write_manifest(): pass  # provenance-only edit\n", encoding="utf-8")
    monkeypatch.setattr(stage_cache, "git_revision", lambda: "second-revision")
    second = fingerprint_payload(
        "sample",
        command=["sample", "--flag"],
        script_path=output,
        controller_path=controller,
        input_signatures={str(output): sha256_file(output)},
    )
    assert first == second
    assert "revision" not in first
    assert "controller_sha256" not in first


def test_stage_cache_fingerprint_changes_when_effective_command_changes(tmp_path):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    first = fingerprint(
        "sample",
        command=["sample", "--flag"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    second = fingerprint(
        "sample",
        command=["sample", "--different-flag"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    assert first != second


def test_stage_cache_explains_runtime_mismatch_without_a_stage_record(tmp_path):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    fp = fingerprint(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={},
    )
    diagnostics = reuse_diagnostics(
        {
            "cache_version": stage_cache.CACHE_VERSION,
            "runtime": stage_cache.runtime_signature(),
            "cache_status": "runtime_mismatch",
            "stages": {},
        },
        "sample",
        fp,
        [output],
    )
    assert diagnostics["reason"] == "cache_runtime_mismatch"
    assert diagnostics["reusable"] is False
    legacy_record = {
        "cache_version": stage_cache.CACHE_VERSION,
        "runtime": stage_cache.runtime_signature(),
        "cache_status": "valid",
        "stages": {
            "sample": {"fingerprint": fp, "outputs": record_output_hashes([output])}
        },
    }
    diagnostics = reuse_diagnostics(legacy_record, "sample", fp, [output])
    assert diagnostics["reason"] == "fingerprint_inputs_missing"


def test_stage_cache_fingerprint_includes_runtime(tmp_path, monkeypatch):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    base_runtime = {
        "python": "3.11.15",
        "implementation": "CPython",
        "platform": "Darwin",
        "machine": "arm64",
        "cache_tag": "cpython-311",
        "distributions": {"numpy": "2.4.6"},
    }
    monkeypatch.setattr(stage_cache, "runtime_signature", lambda: base_runtime)
    first = fingerprint(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    changed_runtime = {**base_runtime, "distributions": {"numpy": "2.4.7"}}
    monkeypatch.setattr(stage_cache, "runtime_signature", lambda: changed_runtime)
    second = fingerprint(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={str(output): sha256_file(output)},
    )
    assert first != second


def test_stage_cache_fingerprint_includes_shared_runtime_module(tmp_path, monkeypatch):
    output = tmp_path / "artifact.csv"
    output.write_text("value\n1\n", encoding="utf-8")
    runtime_module = tmp_path / "runtime.py"
    runtime_module.write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(stage_cache, "RUNTIME_MODULE_PATH", runtime_module)
    first = fingerprint(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={},
    )
    runtime_module.write_text("VERSION = 2\n", encoding="utf-8")
    second = fingerprint(
        "sample",
        command=["sample"],
        script_path=output,
        controller_path=output,
        input_signatures={},
    )
    assert first != second


def test_stage_cache_fingerprint_includes_imported_local_helpers(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    stage = scripts / "stage.py"
    helper = scripts / "helper.py"
    stage.write_text("from helper import VALUE\n", encoding="utf-8")
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    assert helper in local_module_paths(stage)
    first = fingerprint(
        "sample",
        command=["sample"],
        script_path=stage,
        controller_path=stage,
        input_signatures={},
    )
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    second = fingerprint(
        "sample",
        command=["sample"],
        script_path=stage,
        controller_path=stage,
        input_signatures={},
    )
    assert first != second


def test_stage_cache_follows_analytical_import_closure_without_server_modules():
    paths = set(local_module_paths(stage_cache.SOURCE_ROOT / "scripts" / "analyze.py"))
    assert stage_cache.SOURCE_ROOT / "scripts" / "geometry.py" in paths
    assert stage_cache.SOURCE_ROOT / "scripts" / "stats.py" in paths
    assert stage_cache.SOURCE_ROOT / "scripts" / "serve_report.py" not in paths


def test_columnar_cache_tracks_available_optional_outputs(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    for name in ("analysis_results.jsonl", "columnar_status.json", "analysis_results.parquet", "analysis.duckdb"):
        (out / name).write_text(name, encoding="utf-8")
    (out / "columnar_status.json").write_text(
        json.dumps(
            {
                "parquet": {"status": "available"},
                "duckdb": {"status": "available"},
            }
        ),
        encoding="utf-8",
    )
    outputs = stage_outputs("columnar", tmp_path / "data", out)
    assert out / "analysis_results.parquet" in outputs
    assert out / "analysis.duckdb" in outputs
    payload = fingerprint_payload(
        "columnar",
        command=["columnar"],
        script_path=out / "analysis_results.jsonl",
        controller_path=out / "analysis_results.jsonl",
        input_signatures={},
    )
    fp = fingerprint_from_payload(payload)
    cache = {"cache_version": stage_cache.CACHE_VERSION, "runtime": stage_cache.runtime_signature(), "stages": {}}
    save_record(cache, "columnar", fp, record_output_hashes(outputs), fingerprint_inputs=payload)
    assert can_reuse(cache, "columnar", fp, outputs) is True
    (out / "analysis_results.parquet").unlink()
    assert can_reuse(cache, "columnar", fp, stage_outputs("columnar", tmp_path / "data", out)) is False


def test_columnar_cache_rejects_stale_unavailable_outputs(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    for name in ("analysis_results.jsonl", "columnar_status.json"):
        (out / name).write_text(name, encoding="utf-8")
    (out / "columnar_status.json").write_text(
        json.dumps(
            {
                "parquet": {"status": "not_installed"},
                "duckdb": {"status": "not_installed"},
            }
        ),
        encoding="utf-8",
    )
    outputs = stage_outputs("columnar", tmp_path / "data", out)
    payload = fingerprint_payload(
        "columnar",
        command=["columnar"],
        script_path=out / "analysis_results.jsonl",
        controller_path=out / "analysis_results.jsonl",
        input_signatures={},
    )
    fp = fingerprint_from_payload(payload)
    cache = {"cache_version": stage_cache.CACHE_VERSION, "runtime": stage_cache.runtime_signature(), "stages": {}}
    save_record(cache, "columnar", fp, record_output_hashes(outputs), fingerprint_inputs=payload)
    (out / "analysis_results.parquet").write_text("stale", encoding="utf-8")
    assert can_reuse(cache, "columnar", fp, stage_outputs("columnar", tmp_path / "data", out)) is False
