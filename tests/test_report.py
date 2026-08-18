import csv
import json

from scripts.report import (
    build_interpretation,
    build_lazy_report,
    build_report,
    build_report_data,
    interpretation_artifact,
)


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
    (tmp_path / "manifest.json").write_text(json.dumps({"generated_at": "test"}), encoding="utf-8")
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
    assert "function restoreViewState()" in html
    assert "function syncViewState()" in html
    assert "id=\"routeRun\"" in html
    assert "function runRoute()" in html
    assert "function routeCoordinates(payload)" in html
    assert "offline-route" in html
    assert "Offline route view" in html
    assert "routeLine=L.polyline" in html
    assert "include_ferries" in html
    assert "routeFormat" in html
    assert 'id="routeIncludePath" type="checkbox" checked' in html
    assert "file mode has no route API" in html
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
    assert "function renderOfflineMap()" in lazy_html
    assert data["summary"]["source_status"]["lidar"] == "not_provided"
    assert data["summary"]["source_status"]["verification"] == "not_provided"
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
