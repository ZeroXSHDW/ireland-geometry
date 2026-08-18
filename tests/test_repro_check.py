import json

import pytest

from scripts.repro_check import context_differences, hashes, main, provenance_context


def test_reproducibility_excludes_timestamped_doctor_diagnostic(tmp_path):
    (tmp_path / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    (tmp_path / "doctor.json").write_text('{"generated_at":"now"}', encoding="utf-8")

    result = hashes(tmp_path)

    assert "analysis_results.csv" in result
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


def test_repro_check_json_mode_emits_the_written_contract(tmp_path, capsys):
    output = tmp_path / "output"
    _write_manifest(output, source_path=tmp_path / "data" / "combined.json")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    main(["--json", "--out-dir", str(output)])

    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["artifact_count"] == 1
    assert json.loads((output / "reproducibility.json").read_text(encoding="utf-8")) == result


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
