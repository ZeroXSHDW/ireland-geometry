import csv
import json
import shutil
import subprocess
import sys

import pytest

from scripts.report import (
    build_interpretation,
    build_lazy_report,
    build_pattern_catalog,
    build_report,
    build_report_data,
    interpretation_artifact,
    report_matching_targets,
    source_freshness_summary,
)
from scripts.report import main as report_main


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_report_page_helpers_do_not_eagerly_require_geometry_dependency():
    code = r'''
import builtins

original_import = builtins.__import__

def blocked(name, *args, **kwargs):
    if name == "shapely" or name.startswith("shapely."):
        raise ModuleNotFoundError("shapely intentionally blocked")
    return original_import(name, *args, **kwargs)

builtins.__import__ = blocked
from scripts.report import report_csv, report_geojson, report_page_payload

data = {"targets": [], "geojson": {"type": "FeatureCollection", "features": []}}
page = report_page_payload(data)
assert page["page"]["total"] == 0
assert page["endpoints"]["runtime"] == "/api/report/runtime"
assert report_csv(data).startswith("osm_id,name,group")
assert report_geojson(data)["type"] == "FeatureCollection"
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_report_rejects_symlinked_output_root(tmp_path):
    target = tmp_path / "target-output"
    target.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory must not be a symlink"):
        report_main(["--out-dir", str(linked)])


def test_report_rejects_file_output_root(tmp_path):
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        report_main(["--out-dir", str(output)])


def test_report_rejects_nested_output_symlink(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (output / "linked-input.csv").symlink_to(target / "input.csv")
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory contains symlink"):
        report_main(["--out-dir", str(output)])


def test_report_is_data_driven_and_replaces_template_tokens(tmp_path):
    analysis_fields = [
        "osm_id",
        "name",
        "group",
        "subtype",
        "lat",
        "lon",
        "score",
        "area_m2",
        "perimeter_m",
        "length_m",
        "width_m",
        "aspect_ratio",
        "n_vertices",
        "convexity",
        "circularity",
        "has_golden_angle",
        "golden_ratio_err_pct",
        "fib_ratio_err_pct",
        "valid",
        "repaired",
        "multipart",
        "hole_count",
        "geometry_warning",
        "flags",
    ]
    write_csv(
        tmp_path / "analysis_results.csv",
        analysis_fields,
        [
            {
                "osm_id": "way/1",
                "name": "Test Chapel",
                "group": "worship",
                "subtype": "church",
                "lat": 53.0,
                "lon": -8.0,
                "score": 72,
                "area_m2": 100,
                "perimeter_m": 40,
                "length_m": 12,
                "width_m": 8,
                "aspect_ratio": 1.5,
                "n_vertices": 4,
                "convexity": 1,
                "circularity": 0.78,
                "has_golden_angle": 0,
                "golden_ratio_err_pct": 7,
                "fib_ratio_err_pct": 4,
                "valid": 1,
                "repaired": 0,
                "multipart": 0,
                "hole_count": 0,
                "geometry_warning": "",
                "flags": "orthogonal",
            }
        ],
    )
    (tmp_path / "ireland_buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"osm_id": "way/1"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-8.0, 53.0],
                                    [-7.999, 53.0],
                                    [-7.999, 53.001],
                                    [-8.0, 53.001],
                                    [-8.0, 53.0],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "test",
                "source_freshness": {
                    "contract": "ireland-geometry.freshness.v1",
                    "observed_at": "2026-08-18T00:00:00+00:00",
                    "sources": [{"age_seconds": 86_400}],
                },
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path / "data_quality.csv",
        [
            "scope",
            "group",
            "field",
            "row_n",
            "missing_n",
            "missing_pct",
            "unique_n",
            "invalid_n",
            "quality_status",
            "notes",
        ],
        [
            {
                "scope": "source",
                "group": "all",
                "field": "lidar_coverage",
                "row_n": 1,
                "missing_n": 1,
                "missing_pct": 100,
                "unique_n": 1,
                "invalid_n": 0,
                "quality_status": "not_provided:1",
                "notes": "source coverage/status distribution",
            }
        ],
    )
    write_csv(
        tmp_path / "review_queue.csv",
        ["osm_id", "label", "reviewer"],
        [{"osm_id": "way/1", "label": "not_reviewed", "reviewer": ""}],
    )

    html = build_report(tmp_path)
    lazy_html = build_lazy_report(tmp_path)
    data = build_report_data(tmp_path)

    assert "__DATA__" not in html
    assert "__MARKER_LIMIT__" not in html
    assert "filtered.slice(0,800)" in html
    assert "Test Chapel" in html
    assert "const PACK =" in html
    assert "const MATCHED =" in html
    assert "function renderAll()" in html
    assert "function renderOfflineMap()" in html
    assert "Offline map fallback" in html
    assert "OFFLINE_REQUESTED" in html
    assert "function loadMapAssets()" in html
    assert "World_Imagery/MapServer/tile" in html
    assert 'data-map-layer="hybrid"' in html
    assert 'id="mapFit"' in html
    assert 'id="mapLoading"' in html
    assert "function selectMapTarget" in html
    assert "Focus in list" in html
    assert "function restoreViewState()" in html
    assert "function syncViewState()" in html
    assert "X-Ireland-Geometry-Runtime-Status" in lazy_html
    assert "function applyRuntime(runtime)" in lazy_html
    assert "function applyRuntimeHeaders(headers)" in lazy_html
    assert "function runtimeIdentityText()" in lazy_html
    assert "const snapshot=REPORT_RUNTIME?.snapshot" in lazy_html
    assert "const BASE_INTERPRETATION = PACK.interpretation || {};" in lazy_html
    assert "INTERPRETATION={...BASE_INTERPRETATION};" in lazy_html
    assert "function renderRuntimeStatus()" in lazy_html
    assert "function refreshRuntime()" in lazy_html
    assert "function startRuntimeRefresh()" in lazy_html
    assert "setInterval(refreshRuntime,RUNTIME_REFRESH_MS)" in lazy_html
    assert "function renderMethod()" in lazy_html
    assert "id=\"runtimeStatus\"" in lazy_html
    assert "id=\"routeRun\"" in html
    assert "function runRoute()" in html
    assert "function routeCoordinates(payload)" in html
    assert "offline-route" in html
    assert "Offline route view" in html
    assert "routeLine=L.polyline" in html
    assert "include_ferries" in html
    assert "routeFormat" in html
    assert "function routeWaitText(route)" in html
    assert "ferry_wait_s" in html
    assert "ferry_wait_n" in html
    assert "function routeFerryText(route)" in html
    assert "function routeSegmentText(route)" in html
    assert "path_segment_source" in html
    assert "path_segment_total_distance_m" in html
    assert "conditional_rules" in html
    assert "ferry_way_ids" in html
    assert "ferry_distance_m" in html
    assert "ferry_crossing_s" in html
    assert "ferry_edge_n" in html
    assert 'id="routeObjective"' in html
    assert "objective:$('routeObjective').value" in html
    assert 'id="routeWeight"' in html
    assert "weight_t:$('routeWeight').value" in html
    assert "function routeWeightText(route)" in html
    assert 'id="routeRating"' in html
    assert "rating_t:$('routeRating').value" in html
    assert "function routeRatingText(route)" in html
    assert 'id="routeHeight"' in html
    assert "height_m:$('routeHeight').value" in html
    assert "function routeHeightText(route)" in html
    assert 'id="routeWidth"' in html
    assert "width_m:$('routeWidth').value" in html
    assert "function routeWidthText(route)" in html
    assert 'id="routeLength"' in html
    assert "length_m:$('routeLength').value" in html
    assert "function routeLengthText(route)" in html
    assert 'id="routeAxleload"' in html
    assert "axleload_t:$('routeAxleload').value" in html
    assert "function routeAxleloadText(route)" in html
    assert 'id="routeIncludePath" type="checkbox" checked' in html
    assert "file mode has no route API" in html
    assert 'id="grammar"' in html
    assert "DESIGN_GRAMMARS" in html
    assert "Mirror symmetry" in html
    assert "Orthogonal grid" in html
    assert "Cruciform plan" in html
    assert 'id="windShelter"' in html
    assert 'id="rainCapture"' in html
    assert 'id="accessWidth"' in html
    assert 'id="futurePhases"' in html
    assert "Library courtyard" in html
    assert "Museum loop" in html
    assert "function renderPerformanceOverlay" in html
    assert "function renderGrammarOverlay" in html
    assert 'href="review.html"' in html
    assert "function reviewHref(row)" in html
    assert "function reviewFilterState(row)" in html
    assert "not_queued" in html
    assert "review_queue_targets" in html
    assert "Not in review queue" in html
    assert "Review queue" in html
    assert '<th scope="col">Review</th>' in html
    assert 'id="reviewState"' in html
    assert "reviewState" in html
    assert "history.replaceState" in html
    assert 'class="sort-button"' in html
    assert "function updateSortHeaders" in html
    assert "document.addEventListener('keydown'" in html
    assert 'tabindex="0"' in html
    assert 'aria-label="Search analyzed targets"' in html
    assert 'id="qualityFindings"' in html
    assert "Inspect duplicate-centroid groups" in html
    assert "data-quality-focus" in html
    assert 'id="qualityAudit"' in html
    assert "Inspect field and source audit" in html
    assert "Download audit CSV" in html
    assert '<script src="https://unpkg.com/leaflet' not in html
    assert "Source readiness:" in html
    assert "Input freshness:" in html
    assert "ireland-geometry.freshness.v1" in html
    assert "function renderOfflineMap()" in lazy_html
    assert data["summary"]["source_status"]["lidar"] == "not_provided"
    assert data["summary"]["source_status"]["verification"] == "not_provided"
    assert data["summary"]["source_freshness"]["oldest_age_days"] == 1.0
    assert data["summary"]["analysis_ready"] is False
    assert data["interpretation"]["status"] == "not_provided"
    assert "No primary golden-angle comparison" in data["interpretation"]["headline"]
    assert data["data_quality"][0]["field"] == "lidar_coverage"
    assert data["targets"][0]["review"]["in_queue"] is True
    artifact = interpretation_artifact(data)
    assert artifact["contract"] == "ireland-geometry.interpretation.v1"
    assert artifact["status"] == "not_provided"
    assert artifact["available"] is False
    assert artifact["interpretation"] == data["interpretation"]


def test_report_interpretation_is_derived_from_result_rows():
    summary = {
        "analysis_ready": True,
        "source_status": {"lidar": "not_provided", "historical_references": "not_provided"},
    }
    validation = {
        "status": "pass",
        "passed": True,
        "manifest_available": True,
        "records": {},
    }
    significance = [
        {
            "signal": "golden_angle",
            "group": "worship",
            "observed_rate": "8.00",
            "control_rate": "2.00",
            "risk_difference": "6.00",
            "p_adjusted": "0.004",
            "verdict": "SIGNAL",
        },
        {
            "signal": "golden_ratio",
            "group": "worship",
            "observed_rate": "3.00",
            "control_rate": "4.00",
            "risk_difference": "-1.00",
            "p_adjusted": "0.2",
            "verdict": "background",
        },
    ]
    negative_controls = [
        {
            "signal": "has_60_angle",
            "group": "worship",
            "p_adjusted": "0.01",
            "verdict": "suggestive",
        }
    ]
    matched = [
        {
            "signal": "golden_angle",
            "target_group": "worship",
            "target_rate": "7.00",
            "matched_control_rate": "3.00",
            "risk_difference_pp": "4.00",
            "p_adjusted": "0.02",
            "verdict": "suggestive",
        }
    ]
    holdout = [
        {
            "signal": "golden_angle",
            "target_group": "worship",
            "target_rate": "6.00",
            "control_rate": "2.00",
            "risk_difference_pp": "4.00",
            "p_value": "0.01",
            "alpha": "0.05",
        }
    ]

    interpretation = build_interpretation(
        summary,
        significance,
        negative_controls,
        [],
        matched,
        [],
        [],
        holdout,
        validation,
    )

    assert interpretation["focus_group"] == "worship"
    assert "8.00% versus 2.00%" in interpretation["headline"]
    assert any(item["id"] == "negative_controls" for item in interpretation["findings"])
    assert any(item["status"] == "supportive" for item in interpretation["findings"])
    assert any("LiDAR is not provided" in caveat for caveat in interpretation["caveats"])

    significance[0]["observed_rate"] = "3.00"
    changed = build_interpretation(
        summary,
        significance,
        negative_controls,
        [],
        matched,
        [],
        [],
        holdout,
        validation,
    )
    assert "3.00% versus 2.00%" in changed["headline"]


def test_source_freshness_summary_reports_cache_age_without_an_age_policy():
    summary = source_freshness_summary(
        {
            "source_freshness": {
                "contract": "ireland-geometry.freshness.v1",
                "observed_at": "2026-08-18T00:00:00+00:00",
                "sources": [
                    {"age_seconds": 86_400},
                    {"age_seconds": 3_600},
                    {"age_seconds": "invalid"},
                ],
            }
        }
    )
    assert summary == {
        "status": "reported",
        "contract": "ireland-geometry.freshness.v1",
        "observed_at": "2026-08-18T00:00:00+00:00",
        "source_count": 3,
        "oldest_age_days": 1.0,
        "newest_age_days": round(3_600 / 86_400, 3),
    }


def test_pattern_catalog_lists_every_flag_and_filters_targets():
    rows = [
        {"osm_id": "way/1", "flags": ["golden_angle", "orthogonal"]},
        {"osm_id": "way/2", "flags": ["circular"]},
    ]

    catalog = build_pattern_catalog(rows)
    by_key = {item["key"]: item for item in catalog}
    assert by_key["golden_angle"]["count"] == 1
    assert by_key["orthogonal"]["count"] == 1
    assert by_key["circular"]["pct"] == 50.0
    assert by_key["cruciform_candidate"]["count"] == 0

    matches = report_matching_targets({"targets": rows}, pattern="golden_angle")
    assert [row["osm_id"] for row in matches] == ["way/1"]


def test_generated_runtime_recovery_restores_baseline_interpretation(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for generated dashboard runtime testing")
    html = build_lazy_report(tmp_path)
    start = html.index("const BASE_INTERPRETATION")
    declarations_end = html.index("const SIG", start)
    function_start = html.index("function applyRuntime(runtime)", declarations_end)
    function_end = html.index("function applyRuntimeHeaders", function_start)
    runtime_functions = html[start:declarations_end] + html[function_start:function_end]
    script = """
const baseline = {
  status: 'available',
  headline: 'Baseline headline',
  findings: [{id: 'validation', status: 'pass', text: 'Baseline text'}],
  caveats: ['Original caveat']
};
const PACK = {interpretation: baseline};
let SUMMARY = {};
""" + runtime_functions + """
applyRuntime({
  contract: 'ireland-geometry.report-runtime.v1',
  status: 'fail',
  analysis_ready: false,
  validation: {status: 'pass'},
  manifest_alignment: {status: 'fail'}
});
if (INTERPRETATION.findings[0].status !== 'fail') throw new Error('stale runtime was not applied');
if (!INTERPRETATION.caveats.some(item => item.includes('not aligned'))) throw new Error('stale caveat missing');
applyRuntime({
  contract: 'ireland-geometry.report-runtime.v1',
  status: 'pass',
  analysis_ready: true,
  validation: {status: 'pass'},
  manifest_alignment: {status: 'pass'}
});
if (JSON.stringify(INTERPRETATION) !== JSON.stringify(baseline)) throw new Error('baseline interpretation was not restored');
"""
    result = subprocess.run(
        ["node", "-e", script], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
