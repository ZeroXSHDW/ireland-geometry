import json

import pytest

from scripts.repro_check import context_differences, hashes, main, provenance_context


def test_reproducibility_excludes_timestamped_doctor_diagnostic(tmp_path):
    (tmp_path / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    (tmp_path / "doctor.json").write_text('{"generated_at":"now"}', encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "stable.csv").write_text("value\n1\n", encoding="utf-8")

    result = hashes(tmp_path)

    assert "analysis_results.csv" in result
    assert "nested/stable.csv" in result
    assert "doctor.json" not in result


def _write_manifest(path, *, source_path, source_hash="abc", seed=1, runtime=None):
    path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 2,
        "schema_version": 3,
        "git_revision": "test-revision",
        "parameters": {
            "seed": seed,
            "incremental": True,
            "no_network": True,
        },
        "sources": [
            {
                "kind": "combined_osm_json",
                "path": str(source_path),
                "sha256": source_hash,
                "bytes": 10,
            }
        ],
    }
    if runtime is not None:
        manifest["runtime"] = runtime
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_provenance_context_ignores_machine_specific_source_paths(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest(first, source_path=tmp_path / "data-a" / "combined.json")
    _write_manifest(second, source_path=tmp_path / "data-b" / "combined.json")

    assert provenance_context(first) == provenance_context(second)
    assert context_differences(provenance_context(first), provenance_context(second)) == []


def test_reproducibility_reports_provenance_context_mismatch(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest(first, source_path=tmp_path / "data-a" / "combined.json")
    _write_manifest(second, source_path=tmp_path / "data-b" / "combined.json", seed=2)
    for output in (first, second):
        (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["--out-dir", str(first), "--reference-out-dir", str(second)])
    result = json.loads((first / "reproducibility.json").read_text(encoding="utf-8"))

    assert "reproducibility context differs: parameters" in result["context_differences"]
    assert any("context differs: parameters" in error for error in result["errors"])


def test_reproducibility_reports_nested_artifact_mismatch(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest(first, source_path=tmp_path / "data-a" / "combined.json")
    _write_manifest(second, source_path=tmp_path / "data-b" / "combined.json")
    for output, value in ((first, "first"), (second, "second")):
        nested = output / "nested"
        nested.mkdir()
        (nested / "stable.csv").write_text(f"value\n{value}\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        main(
            [
                "--json",
                "--out-dir",
                str(first),
                "--reference-out-dir",
                str(second),
            ]
        )
    result = json.loads((first / "reproducibility.json").read_text(encoding="utf-8"))

    assert "deterministic artifact differs: nested/stable.csv" in result["errors"]


def test_repro_check_reports_output_symlink(tmp_path, capsys):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "target.csv"
    target.write_text("value\n1\n", encoding="utf-8")
    try:
        (output / "linked.csv").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit) as exc_info:
        main(["--json", "--out-dir", str(output)])

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["output_directory"]["status"] == "available"
    assert result["symlinks"] == ["linked.csv"]
    assert any("output contains a symlink: linked.csv" in error for error in result["errors"])


def test_repro_check_rejects_symlinked_output_directory(tmp_path, capsys):
    real = tmp_path / "real-output"
    real.mkdir()
    link = tmp_path / "linked-output"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit) as exc_info:
        main(["--json", "--out-dir", str(link)])

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["output_directory"]["status"] == "symlink"
    assert result["symlinks"] == []
    assert not (real / "reproducibility.json").exists()
    assert any("output directory is unavailable" in error for error in result["errors"])


def test_repro_check_json_mode_emits_the_written_contract(tmp_path, capsys):
    output = tmp_path / "output"
    _write_manifest(output, source_path=tmp_path / "data" / "combined.json")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    main(["--json", "--out-dir", str(output)])

    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["artifact_count"] == 1
    assert json.loads((output / "reproducibility.json").read_text(encoding="utf-8")) == result


def test_repro_check_reports_missing_output_as_structured_failure(tmp_path, capsys):
    output = tmp_path / "missing-output"

    with pytest.raises(SystemExit) as exc_info:
        main(["--json", "--out-dir", str(output)])

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False
    assert result["output_directory"]["status"] == "missing"
    assert any("output directory is unavailable" in error for error in result["errors"])
    assert json.loads((output / "reproducibility.json").read_text(encoding="utf-8")) == result


def test_repro_check_reports_missing_reference_directory(tmp_path, capsys):
    output = tmp_path / "output"
    _write_manifest(output, source_path=tmp_path / "data" / "combined.json")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--json",
                "--out-dir",
                str(output),
                "--reference-out-dir",
                str(tmp_path / "missing-reference"),
            ]
        )

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reference_output_directory"]["status"] == "missing"
    assert any("reference output directory is unavailable" in error for error in result["errors"])


def test_repro_check_reports_output_path_that_is_not_a_directory(tmp_path, capsys):
    output = tmp_path / "output-file"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["--json", "--out-dir", str(output)])

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["output_directory"]["status"] == "not_a_directory"
    assert any("output directory is unavailable" in error for error in result["errors"])


def test_reproducibility_context_includes_runtime_identity(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    runtime = {
        "python": "3.11.15",
        "implementation": "CPython",
        "platform": "Darwin",
        "machine": "arm64",
        "cache_tag": "cpython-311",
        "distributions": {"numpy": "2.4.6"},
    }
    _write_manifest(first, source_path=tmp_path / "data-a" / "combined.json", runtime=runtime)
    _write_manifest(
        second,
        source_path=tmp_path / "data-b" / "combined.json",
        runtime={**runtime, "distributions": {"numpy": "2.4.7"}},
    )

    assert "runtime" in context_differences(provenance_context(first), provenance_context(second))
