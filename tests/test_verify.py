import json
import os
from pathlib import Path

import pytest

from scripts.holdout import holdout
from scripts.runtime import SOURCE_FRESHNESS_CONTRACT, path_modified_at, sha256_file
from scripts.verify import (
    _resolve_manifest_source_path,
    check_columnar_contract,
    check_dashboard_review_contract,
    check_holdout_contract,
    check_holm_contract,
    check_interpretation_artifact,
    check_manifest_cache_contract,
    check_manifest_contract,
    check_manifest_freshness_contract,
    check_manifest_path_contract,
    check_manifest_runtime_contract,
    check_probabilities,
    check_report_data_contract,
    check_review_page_contract,
    check_review_queue_contract,
)
from scripts.verify import main as verify_main


def test_holm_contract_rejects_tampered_adjusted_value():
    errors = []
    check_holm_contract(
        [
            {"p_value": "0.01", "p_adjusted": "0.02", "test_family": "family", "direction": "1", "verdict": "suggestive"},
            {"p_value": "0.02", "p_adjusted": "0.50", "test_family": "family", "direction": "1", "verdict": "background"},
        ],
        "sample.csv",
        errors,
    )
    assert any("incorrect Holm adjustment" in error for error in errors)


def test_holdout_contract_rejects_changed_split(tmp_path):
    plan = tmp_path / "analysis_plan.json"
    plan.write_text(
        '{"alpha": 0.05, "primary_signals": ["golden_angle"]}',
        encoding="utf-8",
    )
    analysis = [
        {"osm_id": "way/target", "is_control": "0", "group": "worship", "has_golden_angle": "1"},
        {"osm_id": "way/control", "is_control": "1", "group": "control", "has_golden_angle": "0"},
    ]
    seed = 42
    fraction = 0.5
    assignments = [
        {
            "osm_id": row["osm_id"],
            "is_control": row["is_control"],
            "group": row["group"],
            "split": "holdout" if holdout(row, seed, fraction) else "discovery",
            "seed": str(seed),
            "fraction": str(fraction),
            "rule": "sha256(seed:osm_id) < holdout_fraction",
        }
        for row in analysis
    ]
    assignments[0]["split"] = "holdout" if assignments[0]["split"] == "discovery" else "discovery"
    errors = []
    check_holdout_contract(
        analysis,
        assignments,
        [],
        {
            "plan_path": str(plan),
            "plan_sha256": sha256_file(plan),
            "seed": seed,
            "holdout_fraction": fraction,
        },
        tmp_path / "output",
        errors,
    )
    assert any("has split" in error for error in errors)


def test_verifier_source_resolution_prefers_relocated_portable_path(tmp_path):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    old_source = original / "data" / "source.bin"
    current_source = relocated / "data" / "source.bin"
    output = relocated / "output"
    old_source.parent.mkdir(parents=True)
    current_source.parent.mkdir(parents=True)
    output.mkdir()
    old_source.write_bytes(b"old\n")
    current_source.write_bytes(b"new\n")

    resolved = _resolve_manifest_source_path(
        {
            "path": str(old_source),
            "path_base": "project_root",
            "relative_path": "data/source.bin",
        },
        {"project_root": str(original)},
        relocated / "data",
        output,
    )

    assert resolved == current_source


def test_verifier_source_resolution_honors_explicit_project_root_with_custom_layout(tmp_path):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    old_source = original / "data" / "source.bin"
    current_source = relocated / "data" / "source.bin"
    data_root = tmp_path / "isolated-data"
    output = tmp_path / "artifacts" / "output"
    old_source.parent.mkdir(parents=True)
    current_source.parent.mkdir(parents=True)
    data_root.mkdir()
    output.mkdir(parents=True)
    old_source.write_bytes(b"old\n")
    current_source.write_bytes(b"new\n")

    resolved = _resolve_manifest_source_path(
        {
            "path": str(old_source),
            "path_base": "project_root",
            "relative_path": "data/source.bin",
        },
        {"project_root": str(original)},
        data_root,
        output,
        project_root=relocated,
    )

    assert resolved == current_source


def test_manifest_freshness_uses_explicit_project_root_with_custom_layout(tmp_path):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    old_source = original / "data" / "combined.json"
    current_source = relocated / "data" / "combined.json"
    data_root = tmp_path / "isolated-data"
    output = tmp_path / "artifacts" / "output"
    old_source.parent.mkdir(parents=True)
    current_source.parent.mkdir(parents=True)
    data_root.mkdir()
    output.mkdir(parents=True)
    old_source.write_text('{"old": true}', encoding="utf-8")
    current_source.write_text('{"current": true}', encoding="utf-8")
    fixed_mtime = 1_600_000_000
    os.utime(current_source, (fixed_mtime, fixed_mtime))
    modified_at = path_modified_at(current_source)
    assert modified_at is not None

    manifest = {
        "schema_version": 3,
        "project_root": str(original),
        "sources": [
            {
                "source_kind": "combined_osm_json",
                "path": str(old_source),
                "path_base": "project_root",
                "relative_path": "data/combined.json",
                "modified_at": modified_at,
            }
        ],
        "source_freshness": {
            "contract": SOURCE_FRESHNESS_CONTRACT,
            "observed_at": "2020-09-13T12:27:40+00:00",
            "sources": [
                {
                    "source_kind": "combined_osm_json",
                    "path": str(old_source),
                    "path_base": "project_root",
                    "relative_path": "data/combined.json",
                    "modified_at": modified_at,
                    "age_seconds": 60.0,
                }
            ],
        },
    }
    errors = []
    warnings = []

    check_manifest_freshness_contract(
        manifest,
        data_root,
        output,
        errors,
        warnings,
        project_root=relocated,
    )

    assert errors == []
    assert warnings == []


def test_probability_contract_rejects_literal_zero():
    errors = []
    check_probabilities(
        [{"p_value": "0", "p_adjusted": "0.01", "method": "test", "verdict": "SIGNAL"}],
        "sample.csv",
        errors,
    )
    assert any("invalid p_value" in error for error in errors)


def test_probability_contract_accepts_decade_p_column():
    errors = []
    check_probabilities(
        [{"p": "0.02", "p_adjusted": "0.04", "method": "test", "verdict": "suggestive"}],
        "decades.csv",
        errors,
    )
    assert errors == []


def test_columnar_contract_rejects_stale_unavailable_artifacts(tmp_path):
    stale = tmp_path / "analysis_results.parquet"
    stale.write_text("stale", encoding="utf-8")
    errors = []
    warnings = []
    check_columnar_contract(
        {
            "rows": 2,
            "parquet": {"status": "not_installed", "path": str(stale)},
            "duckdb": {"status": "not_installed", "path": str(tmp_path / "analysis.duckdb")},
        },
        tmp_path,
        2,
        errors,
        warnings,
    )
    assert any("exists while status is not_installed" in error for error in errors)


def test_columnar_contract_rejects_row_count_mismatch(tmp_path):
    errors = []
    warnings = []
    check_columnar_contract(
        {
            "rows": 3,
            "parquet": {"status": "not_installed", "path": str(tmp_path / "analysis_results.parquet")},
            "duckdb": {"status": "not_installed", "path": str(tmp_path / "analysis.duckdb")},
        },
        tmp_path,
        2,
        errors,
        warnings,
    )
    assert any("rows 3 != analysis rows 2" in error for error in errors)


def test_manifest_contract_rejects_unlisted_output(tmp_path):
    (tmp_path / "listed.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "unlisted.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "doctor.json").write_text('{"generated_at":"now"}', encoding="utf-8")
    errors = []
    check_manifest_contract({"counts": {}, "artifacts": []}, tmp_path, errors)
    assert any("does not list output artifact" in error for error in errors)
    assert not any("doctor.json" in error for error in errors)


def test_manifest_contract_checks_nested_output_files(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    unlisted = nested / "unlisted.csv"
    unlisted.write_text("x\n", encoding="utf-8")

    errors = []
    check_manifest_contract({"counts": {}, "artifacts": []}, tmp_path, errors)

    assert any(str(unlisted) in error for error in errors)


def test_manifest_contract_accepts_a_listed_nested_output_file(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    artifact = nested / "listed.csv"
    artifact.write_text("x\n", encoding="utf-8")
    manifest = {
        "counts": {},
        "artifacts": [
            {
                "path": str(artifact),
                "relative_path": "nested/listed.csv",
                "path_base": "output_dir",
                "sha256": sha256_file(artifact),
                "bytes": artifact.stat().st_size,
            }
        ],
    }

    errors = []
    check_manifest_contract(manifest, tmp_path, errors)

    assert errors == []


def test_manifest_contract_rejects_output_symlink(tmp_path):
    target = tmp_path / "target.csv"
    target.write_text("x\n", encoding="utf-8")
    link = tmp_path / "linked.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    errors = []
    check_manifest_contract({"counts": {}, "artifacts": []}, tmp_path, errors)

    assert any("output contains a symlink" in error for error in errors)


def test_verify_rejects_symlinked_output_root(tmp_path):
    target = tmp_path / "target-output"
    target.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory must not be a symlink"):
        verify_main(["--out-dir", str(linked)])


def test_verify_rejects_file_output_root(tmp_path):
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        verify_main(["--out-dir", str(output)])


def test_verify_rejects_nested_data_symlink_before_reading(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    link = data / "combined.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="data directory contains symlink"):
        from scripts.verify import verify_outputs

        verify_outputs(data, tmp_path / "output")


def test_verify_rejects_nested_output_symlink_before_reading(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "external.csv"
    target.write_text("x\n", encoding="utf-8")
    link = output / "analysis_results.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="output directory contains symlink"):
        from scripts.verify import verify_outputs

        verify_outputs(tmp_path / "data", output)


def test_manifest_contract_rejects_artifact_outside_output(tmp_path):
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("x\n", encoding="utf-8")
    errors = []
    try:
        check_manifest_contract(
            {"counts": {}, "artifacts": [{"path": str(outside)}]}, tmp_path, errors
        )
        assert any("outside output directory" in error for error in errors)
    finally:
        outside.unlink(missing_ok=True)


def test_manifest_contract_resolves_relative_artifact_after_project_move(tmp_path):
    output = tmp_path / "moved" / "output"
    output.mkdir(parents=True)
    artifact = output / "analysis_results.csv"
    artifact.write_text("osm_id\nway/1\n", encoding="utf-8")
    manifest = {
        "path_contract": {
            "version": 1,
            "relative_path_field": "relative_path",
            "source_base": "project_root",
            "artifact_base": "output_dir",
            "absolute_path_field": "path",
        },
        "counts": {},
        "artifacts": [
            {
                "path": "/previous-machine/project/output/analysis_results.csv",
                "relative_path": "analysis_results.csv",
                "path_base": "output_dir",
                "sha256": sha256_file(artifact),
                "bytes": artifact.stat().st_size,
            }
        ],
        "sources": [],
    }
    errors = []
    check_manifest_path_contract(manifest, output, errors)
    check_manifest_contract(manifest, output, errors)
    assert errors == []


def test_manifest_runtime_contract_accepts_current_shape():
    errors = []
    warnings = []
    check_manifest_runtime_contract(
        {
            "package_version": "0.4.0",
            "runtime": {
                "python": "3.11.15",
                "implementation": "CPython",
                "platform": "Darwin",
                "machine": "arm64",
                "cache_tag": "cpython-311",
                "distributions": {"numpy": "2.4.6", "rasterio": None},
            },
        },
        errors,
        warnings,
    )
    assert errors == []
    assert warnings == []


def test_manifest_runtime_contract_warns_for_legacy_shape():
    errors = []
    warnings = []
    check_manifest_runtime_contract({}, errors, warnings)
    assert errors == []
    assert len(warnings) == 2


def _manifest_cache_fixture(tmp_path, *, routing_max_pairs: int = 100):
    plan = tmp_path / "custom-plan.json"
    plan.write_text("{}", encoding="utf-8")
    parameters = {
        "stage": "all",
        "refresh": False,
        "no_network": True,
        "boundaries": None,
        "settlements": None,
        "osm_history": None,
        "lidar": None,
        "historical_references": None,
        "review_labels": None,
        "analysis_plan": plan.name,
        "seed": 42,
        "mc": 7,
        "bootstrap_iterations": 11,
        "holdout_fraction": None,
        "road_graph": None,
        "road_from_pbf": False,
        "routing_include_restricted": False,
        "routing_include_ferries": False,
        "routing_max_ways": 100000,
        "routing_max_pairs": routing_max_pairs,
        "routing_departure": None,
        "routing_speed_kmh": 40.0,
        "routing_weight_t": None,
        "routing_vehicle_class": "general",
    }
    commands = {
        "fetch": ["--no-download"],
        "fetch-niah": ["--no-network"],
        "analyze": ["--plan", str(plan)],
        "spatial-covariates": [],
        "osm-history": [],
        "building-parts": [],
        "historical": [],
        "review": [],
        "point-pattern": ["--seed", "42", "--mc", "7"],
        "spatial-stats": ["--seed", "42", "--mc", "7"],
        "spatial-bootstrap": ["--seed", "42", "--iterations", "11"],
        "road-proximity": ["--seed", "42"],
        "road-routing": ["--max-pairs", "100", "--speed-kmh", "40.0", "--vehicle-class", "general"],
        "holdout": ["--seed", "42", "--plan", str(plan)],
    }
    cache = {
        "stages": {
            stage: {"fingerprint_inputs": {"command": command}}
            for stage, command in commands.items()
        }
    }
    return {"project_root": str(tmp_path), "parameters": parameters}, cache


def test_manifest_cache_contract_accepts_aligned_commands_and_ignores_diagnostic_invocation(
    tmp_path,
):
    manifest, cache = _manifest_cache_fixture(tmp_path)
    manifest["last_invocation"] = {"routing_max_pairs": 5000, "seed": 999}
    errors = []
    warnings = []

    result = check_manifest_cache_contract(manifest, cache, tmp_path / "output", errors, warnings)

    assert result["contract"] == "ireland-geometry.manifest-cache.v1"
    assert result["status"] == "pass"
    assert result["mismatches"] == []
    assert errors == []
    assert warnings == []


def test_manifest_cache_contract_rejects_changed_routing_limit(tmp_path):
    manifest, cache = _manifest_cache_fixture(tmp_path, routing_max_pairs=5000)
    errors = []
    warnings = []

    result = check_manifest_cache_contract(manifest, cache, tmp_path / "output", errors, warnings)

    assert result["status"] == "fail"
    assert any("road-routing.routing_max_pairs" in error for error in errors)
    assert result["mismatches"][0]["expected"] == 5000


def test_manifest_freshness_contract_accepts_aligned_current_source(tmp_path):
    source = tmp_path / "data" / "combined.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    modified_at_epoch = 1_600_000_000
    os.utime(source, (modified_at_epoch, modified_at_epoch))
    modified_at = "2020-09-13T12:26:40+00:00"
    observed_at = "2020-09-13T12:27:40+00:00"
    source_row = {
        "kind": "file",
        "source_kind": "combined_osm_json",
        "path": str(source),
        "path_base": "project_root",
        "relative_path": "data/combined.json",
        "modified_at": modified_at,
    }
    freshness_row = {
        "source_kind": "combined_osm_json",
        "path": str(source),
        "path_base": "project_root",
        "relative_path": "data/combined.json",
        "modified_at": modified_at,
        "age_seconds": 60.0,
    }
    errors = []
    warnings = []
    check_manifest_freshness_contract(
        {
            "schema_version": 3,
            "project_root": str(tmp_path),
            "sources": [source_row],
            "source_freshness": {
                "contract": SOURCE_FRESHNESS_CONTRACT,
                "observed_at": observed_at,
                "sources": [freshness_row],
            },
        },
        tmp_path / "data",
        tmp_path / "output",
        errors,
        warnings,
    )
    assert errors == []
    assert warnings == []


def test_manifest_freshness_contract_rejects_tampered_age_and_source_mtime(tmp_path):
    source = tmp_path / "data" / "combined.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    modified_at_epoch = 1_600_000_000
    os.utime(source, (modified_at_epoch, modified_at_epoch))
    source_row = {
        "source_kind": "combined_osm_json",
        "path": str(source),
        "path_base": "project_root",
        "relative_path": "data/combined.json",
        "modified_at": "2020-09-13T12:26:40+00:00",
    }
    freshness_row = {
        "source_kind": "combined_osm_json",
        "path": str(source),
        "path_base": "project_root",
        "relative_path": "data/combined.json",
        "modified_at": "2020-09-13T12:26:41+00:00",
        "age_seconds": 999.0,
    }
    errors = []
    check_manifest_freshness_contract(
        {
            "schema_version": 3,
            "project_root": str(tmp_path),
            "sources": [source_row],
            "source_freshness": {
                "contract": SOURCE_FRESHNESS_CONTRACT,
                "observed_at": "2020-09-13T12:27:40+00:00",
                "sources": [freshness_row],
            },
        },
        tmp_path / "data",
        tmp_path / "output",
        errors,
        [],
    )
    assert any("age_seconds does not match" in error for error in errors)
    assert any("does not match source_freshness" in error for error in errors)
    assert any("modified_at is stale" in error for error in errors)


def test_manifest_freshness_contract_requires_current_schema_record():
    errors = []
    warnings = []
    check_manifest_freshness_contract(
        {"schema_version": 3, "sources": []},
        Path("/tmp/data"),
        Path("/tmp/output"),
        errors,
        warnings,
    )
    assert any("missing source_freshness" in error for error in errors)
    assert warnings == []


def test_review_queue_contract_rejects_duplicate_and_non_target_ids():
    errors = []
    queue_ids = check_review_queue_contract(
        [
            {"osm_id": "way/1", "label": "not_reviewed"},
            {"osm_id": "way/1", "label": "not_reviewed"},
            {"osm_id": "way/9", "label": "not_reviewed"},
        ],
        {"way/1", "way/2"},
        errors,
    )
    assert queue_ids == {"way/1", "way/9"}
    assert any("duplicate osm_id" in error for error in errors)
    assert any("outside analyzed targets" in error for error in errors)


def test_report_data_contract_aligns_queue_membership_and_coverage(tmp_path):
    path = tmp_path / "report_data.json"
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "targets": 2,
                    "review_queue_targets": 1,
                    "review_queue_coverage_pct": 50.0,
                    "analysis_ready": False,
                    "validation": {
                        "passed": False,
                        "manifest_available": False,
                        "records": {
                            "verification": {},
                            "schema_validation": {},
                            "reproducibility": {},
                        },
                    },
                },
                "targets": [
                    {"osm_id": "way/1", "review": {"in_queue": True}},
                    {"osm_id": "way/2", "review": {"in_queue": False}},
                ],
                "interpretation": {
                    "status": "not_provided",
                    "focus_group": "",
                    "headline": "No primary comparison.",
                    "findings": [],
                    "caveats": [],
                },
            }
        ),
        encoding="utf-8",
    )
    errors = []
    check_report_data_contract(path, {"way/1", "way/2"}, {"way/1"}, errors)
    assert errors == []

    errors = []
    check_report_data_contract(path, {"way/1", "way/2"}, {"way/2"}, errors)
    assert any("membership does not match" in error for error in errors)


def test_interpretation_artifact_matches_report_data_contract(tmp_path):
    report_data = {
        "summary": {
            "targets": 2,
            "controls": 1,
            "analysis_ready": False,
            "validation": {
                "status": "not_provided",
                "passed": False,
                "manifest_available": False,
                "records": {},
            },
        },
        "interpretation": {
            "status": "not_provided",
            "focus_group": "",
            "headline": "No primary comparison.",
            "findings": [],
            "caveats": [],
        },
    }
    report_path = tmp_path / "report_data.json"
    report_path.write_text(json.dumps(report_data), encoding="utf-8")
    sidecar = {
        "contract": "ireland-geometry.interpretation.v1",
        "status": "not_provided",
        "available": False,
        "analysis_ready": False,
        "validation": report_data["summary"]["validation"],
        "summary": {"targets": 2, "controls": 1, "focus_group": ""},
        "interpretation": report_data["interpretation"],
        "source": "/interpretation.json",
    }
    sidecar_path = tmp_path / "interpretation.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    errors = []
    check_interpretation_artifact(sidecar_path, report_path, errors)
    assert errors == []

    sidecar["summary"]["targets"] = 99
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    errors = []
    check_interpretation_artifact(sidecar_path, report_path, errors)
    assert any("summary targets" in error for error in errors)


def test_generated_review_ui_contracts_require_scope_and_shareable_state(tmp_path):
    page = tmp_path / "review.html"
    page.write_text(
        'id="targetNotice" not in the current TARGET_ID id="search" '
        'id="statusFilter" restoreViewState syncViewState history.replaceState '
        'aria-label="Search review queue" aria-label="Review label for way/1" '
        'aria-live="polite" '
        "expert-labels.csv function downloadJson async function importJson "
        'aria-label="Import review labels JSON" Unsupported JSON backup schema '
        'different review queue invalid label records',
        encoding="utf-8",
    )
    dashboard = tmp_path / "report.html"
    dashboard.write_text(
        'id="reviewState" function reviewFilterState(row) not_queued '
        'review_queue_targets Not in review queue function reviewHref(row) '
        'class="sort-button" function updateSortHeaders document.addEventListener(\'keydown\' tabindex="0" '
        'aria-label="Search analyzed targets"',
        encoding="utf-8",
    )
    errors = []
    check_review_page_contract(page, errors)
    check_dashboard_review_contract(dashboard, "report", errors)
    assert errors == []

    page.write_text('id="search"', encoding="utf-8")
    check_review_page_contract(page, errors)
    assert any("target scope notice" in error for error in errors)
