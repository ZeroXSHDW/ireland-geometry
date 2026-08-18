import json
import os
from pathlib import Path

from scripts.holdout import holdout
from scripts.runtime import SOURCE_FRESHNESS_CONTRACT, sha256_file
from scripts.verify import (
    check_columnar_contract,
    check_dashboard_review_contract,
    check_holdout_contract,
    check_holm_contract,
    check_interpretation_artifact,
    check_manifest_contract,
    check_manifest_freshness_contract,
    check_manifest_path_contract,
    check_manifest_runtime_contract,
    check_probabilities,
    check_report_data_contract,
    check_review_page_contract,
    check_review_queue_contract,
)


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
