import csv
import json
import shutil
import subprocess
import sys

import pytest

from scripts.report import (
    build_field_walk,
    build_interpretation,
    build_lazy_report,
    build_pattern_catalog,
    build_report,
    build_report_data,
    interpretation_artifact,
    report_filter_options,
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
assert page["page"]["next_offset"] is None
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
    assert "function atlasNavStatusText(key)" in html
    assert "CULTURE_LENS_LABELS[culture]||culture" in html
    assert "if(status) status.textContent=atlasNavStatusText(active);" in html
    assert "function renderOfflineMap()" in html
    assert 'id="fieldWalkGrid"' in html
    assert 'class="field-walk-shape"' in html
    assert "function fieldWalkShapeSvg(row)" in html
    assert "mapped outline" in html
    assert "scroll-margin-top:54px" in html
    assert "const FIELD_WALK = PACK.field_walk || [];" in html
    assert "function renderFieldWalk()" in html
    assert "data-field-walk-id" in html
    assert 'id="fieldWalkControls"' in html
    assert 'data-field-walk-nav="previous"' in html
    assert 'data-field-walk-nav="next"' in html
    assert "function moveFieldWalk(delta)" in html
    assert "fieldWalkIndexForId" in html
    assert "function targetRowForId(id)" in html
    assert "Offline map fallback" in html
    assert "OFFLINE_REQUESTED" in html
    assert "function loadMapAssets()" in html
    assert "World_Imagery/MapServer/tile" in html
    assert 'data-map-layer="hybrid"' in html
    assert 'id="mapFit"' in html
    assert 'id="mapLoading"' in html
    assert "function selectMapTarget" in html
    assert 'id="focusSelectionMap"' in html
    assert 'class="selection-focus-bar"' in html
    assert "let selectionOutlineLayer = null;" in html
    assert "function clearSelectionMapOutline()" in html
    assert "function renderSelectionMapOutline(row)" in html
    assert "setAtlasNavActive('filters');" in html
    assert "function mapFocusPanOffset()" in html
    assert "map.panBy(offset,{animate:false});" in html
    assert "const selectedDossierVisible=Boolean(selectionCard&&!selectionCard.hidden&&(selectedMarkerId||offlineSelection)" in html
    assert "setAtlasNavActive('filters'); return;" in html
    assert "selected-footprint-outline" in html
    assert "offline-selection-outline" in html
    assert "let offlineMapFocus = false;" in html
    assert "Focused place view · source outline shown" in html
    assert "transform=\"translate(-230 0)\"" in html
    assert "function focusSelectedMap()" in html
    assert "Focus in list" in html
    assert "A place is more than a pattern." in html
    assert 'id="cultureLens"' in html
    assert 'id="county"' in html
    assert 'id="countyChips"' in html
    assert 'id="countyFieldNote"' in html
    assert "function renderCultureAtlas" in html
    assert 'id="cultureReadingContext"' in html
    assert 'id="cultureReadingContextTitle"' in html
    assert 'id="cultureReadingReturn"' in html
    assert "function renderCultureReadingContext()" in html
    assert "function renderCountyFieldNote()" in html
    assert 'id="heritageTimeline"' in html
    assert 'id="heritageTimelineHeading"' in html
    assert 'id="heritageTimelineReadoutTitle"' in html
    assert 'class="heritage-type-field"' in html
    assert 'id="heritageTypeGrid"' in html
    assert 'id="heritageTypeReadoutTitle"' in html
    assert "function renderHeritageTypology()" in html
    assert "function setHeritageType(key)" in html
    assert "data-heritage-type" in html
    assert 'class="place-braid-field"' in html
    assert 'id="placeBraidGrid"' in html
    assert 'id="placeBraidReadoutTitle"' in html
    assert "function renderPlaceBraid()" in html
    assert "function setPlaceBraidType(county,type)" in html
    assert "data-braid-type" in html
    assert 'id="landFieldHeading"' in html
    assert 'id="landGroupGrid"' in html
    assert 'id="landDensityPanel"' in html
    assert 'id="placeNameHeading"' in html
    assert 'id="placeNameChips"' in html
    assert 'id="placeNameStat"' in html
    assert "function renderHeritageTimeline()" in html
    assert "function setHeritageEra(key)" in html
    assert "function renderLandField()" in html
    assert "function setLandGroup(group)" in html
    assert "function renderPlaceNameField()" in html
    assert "function setPlaceName(name)" in html
    assert 'class="makers-field"' in html
    assert 'id="makersBinary"' in html
    assert 'id="makersGrid"' in html
    assert "const ARCHITECTS = PACK.architects || [];" in html
    assert "const ARCHITECTS_BINARY = PACK.architects_binary || [];" in html
    assert "function renderMakersField()" in html
    assert "function setMakerQuery(name)" in html
    assert "data-maker-name" in html
    assert "ROAD_PROXIMITY" in html
    assert "SPATIAL_COVARIATES" in html
    assert "heritageCenturyForDecade" in html
    assert "CULTURE_LENS_LABELS" in html
    assert "Oidhreacht" in html
    assert "teanglann.ie" in html
    assert 'id="selectionCard"' in html
    assert "scroll-margin-top:54px" in html
    assert "const selectionCard=$('selectionCard');" in html
    assert "const sections=links.map(link=>$(link.dataset.navSection)).filter(Boolean);" in html
    assert "setAtlasNavActive(visible.target===selectionCard?'filters':visible.target.id);" in html
    assert "if(selectionCard) observer.observe(selectionCard);" in html
    assert "const stickyOffset=Math.max(54,Math.round($('atlasNav')?.getBoundingClientRect().height||0));" in html
    assert "selection-card-arrived" in html
    assert "function revealPanelTarget(target)" in html
    assert "function revealSelectionCard(selection)" in html
    assert "panel.contains(target)" in html
    assert "panel.scrollTo({top:Math.max(0,target.offsetTop-stickyOffset),behavior:'smooth'})" in html
    assert "revealPanelTarget($('studioReference'))" in html
    assert "revealPanelTarget($('studioPairReference'))" in html
    assert 'id="selectionHeritage"' in html
    assert 'id="selectionMath"' in html
    assert 'id="selectionMathRead"' in html
    assert 'data-maths-read="maths"' in html
    assert 'id="selectionCultureTrace"' in html
    assert 'id="selectionCultureRead"' in html
    assert 'data-culture-read="culture"' in html
    assert "const mathsReturn=event.target.closest?.('#mathsReadingReturn');" in html
    assert "const returnToDossier=event.target.closest?.('#cultureReadingReturn');" in html
    assert "function focusAtlasSection(key)" in html
    assert "Re-assert the destination once it is in view" in html
    assert "Filter this lens" in html
    assert "function renderSelectionCulturalTrace(row)" in html
    assert 'id="selectionFingerprint"' in html
    assert 'id="selectionFingerprintLabel"' in html
    assert 'id="copySelectionLink"' in html
    assert 'id="addSelectionCompare"' in html
    assert 'id="comparisonTray"' in html
    assert 'id="comparisonContent"' in html
    assert 'id="comparisonRelation"' in html
    assert 'id="comparisonRelationPlot"' in html
    assert 'id="comparisonRelationMetrics"' in html
    assert 'id="carryComparisonToStudio"' in html
    assert "function renderComparisonRelation(a,b)" in html
    assert "function drawComparisonRelationPlot(a,b)" in html
    assert "function comparisonBearingDegrees(a,b)" in html
    assert "function comparisonMapRows()" in html
    assert "function renderComparisonMapLayer()" in html
    assert "comparisonLine=L.polyline" in html
    assert "offline-comparison-chord" in html
    assert "if(comparisonLine) bounds.extend(comparisonLine.getBounds())" in html
    assert 'id="copyComparisonLink"' in html
    assert 'id="comparisonShareStatus"' in html
    assert "function renderComparisonTray()" in html
    assert "function addComparisonTarget(id)" in html
    assert "function removeComparisonTarget(id)" in html
    assert "function restoreComparisonState()" in html
    assert "function copyComparisonLink()" in html
    assert "params.set('compare_a'" in html
    assert "params.set('compare_b'" in html
    assert "params.get('compare_a')" in html
    assert "function renderSelectionCard" in html
    assert "function selectionPlaceText" in html
    assert "function selectionMathText" in html
    assert "function drawSelectionFingerprint(row)" in html
    assert "function geometryOuterRing(feature)" in html
    assert "function syncFocusState(id)" in html
    assert "function restoreFocusedTarget()" in html
    assert "function copySelectionLink()" in html
    assert "function clearSelection" in html
    assert "data-selection-culture" in html
    assert 'id="atlasNav"' in html
    assert "#panel > * { flex:0 0 auto; }" in html
    assert "#panel > .table-wrap { flex:0 0 auto; }" in html
    assert 'data-nav-section="culture"' in html
    assert 'data-nav-section="maths"' in html
    assert 'id="maths"' in html
    assert 'id="mathsIndex"' in html
    assert 'id="mathsReadingContext"' in html
    assert 'id="mathsReadingContextTitle"' in html
    assert 'id="mathsReadingReturn"' in html
    assert 'id="mathsReadoutTitle"' in html
    assert 'class="maths-card"' in html
    assert "MATHS_INDEX" in html
    assert 'id="copyStudioLink"' in html
    assert 'id="studioShareStatus"' in html
    assert 'id="skyField"' in html
    assert 'id="skyPlot"' in html
    assert 'id="skyDaylight"' in html
    assert 'id="waterField"' in html
    assert 'id="rainEvent"' in html
    assert 'id="waterEquation"' in html
    assert 'id="waterCapturedVolume"' in html
    assert 'id="countyPulseGrid"' in html
    assert 'id="countyPulseNote"' in html
    assert 'class="rhythm-field"' in html
    assert 'id="rhythmGrid"' in html
    assert 'id="rhythmCount"' in html
    assert "function renderSpatialRhythm()" in html
    assert "function setRhythmGroup(group)" in html
    assert "data-rhythm-group" in html
    assert "Moran's I" in html
    assert 'class="scale-field"' in html
    assert 'id="scaleGrid"' in html
    assert 'id="scaleCount"' in html
    assert "const RIPLEY = PACK.ripley || [];" in html
    assert "function renderScaleField()" in html
    assert "function scaleRadiusLabel(value)" in html
    assert "data-scale-group" in html
    assert 'class="alignment-field"' in html
    assert 'id="alignmentGrid"' in html
    assert 'id="alignmentCount"' in html
    assert "const POINT_PATTERN = PACK.point_pattern || [];" in html
    assert "function renderAlignmentField()" in html
    assert "function alignmentPercent(value)" in html
    assert "data-alignment-group" in html
    assert 'class="source-field"' in html
    assert 'id="sourceGrid"' in html
    assert 'id="sourceCount"' in html
    assert "const SOURCE_REGISTER = PACK.historical_source_register || [];" in html
    assert "const QUALITY_SUMMARY = PACK.data_quality_summary || {};" in html
    assert "function renderSourceRoots()" in html
    assert "function sourceRootLabel(value)" in html
    assert 'class="trust-field"' in html
    assert 'id="trustGrid"' in html
    assert 'id="trustCount"' in html
    assert "const HOLDOUT = PACK.holdout || [];" in html
    assert "const REVIEW_CALIBRATION = PACK.review_calibration || [];" in html
    assert "function renderTrustField()" in html
    assert "function trustStatusKind(value)" in html
    assert 'id="selectionWeave"' in html
    assert 'id="selectionWeaveLabel"' in html
    assert 'id="downloadSelectionPassport"' in html
    assert 'id="selectionPassportStatus"' in html
    assert "function passportPathData(row)" in html
    assert "function downloadSelectionPassport()" in html
    assert "function contextTitle(row)" in html
    assert "set('studioReferenceTitle',contextTitle(row));" in html
    assert "const title=contextTitle(row); return `<tr" in html
    assert "<h3>${esc(contextTitle(row))}</h3>" in html
    assert "${esc(contextTitle(row))} · ${esc(row.group)}" in html
    assert "function decorateMapAccessibility()" in html
    assert "Map cluster with" in html
    assert 'class="selection-evidence"' in html
    assert 'id="selectionEvidenceGrid"' in html
    assert 'id="selectionEvidenceStatus"' in html
    assert "function renderSelectionEvidence(row)" in html
    assert "function selectionEvidenceStatusKind(value)" in html
    assert "function evidenceReadableLabel(value)" in html
    assert "return kind==='available'?'present':kind==='missing'?'not provided':'review';" in html
    assert "${checks} need review" in html
    assert 'class="selection-context"' in html
    assert 'id="selectionContextPlot"' in html
    assert 'id="selectionContextList"' in html
    assert 'id="selectionContextStatus"' in html
    assert "function contextDistanceMeters(a,b)" in html
    assert "function renderSelectionContext(row)" in html
    assert "data-context-focus" in html
    assert "function renderMathsIndex()" in html
    assert "function renderMathsReadingContext()" in html
    assert "function selectMathCard(key)" in html
    assert "Cruth — Ireland Field Atlas V2" in html
    assert 'id="siteIntro"' in html
    assert 'class="intro-proof"' in html
    assert 'id="introTargetCount"' in html
    assert 'id="introNiahCount"' in html
    assert 'id="introSignalCount"' in html
    assert 'set(\'introTargetCount\'' in html
    assert 'id="field"' in html
    assert 'id="fieldTitle"' in html
    assert 'class="field-sequence"' in html
    assert 'data-sequence-target="fieldWalk"' in html
    assert 'data-sequence-section="culture"' in html
    assert "function focusAtlasTarget(targetKey,navKey=targetKey)" in html
    assert "data-sequence-section" in html
    assert "<strong>Maths</strong>" in html
    assert "<strong>Civic possibility</strong>" in html
    assert 'data-field-signal="golden_ratio"' in html
    assert 'data-field-signal="golden_angle"' in html
    assert 'data-field-signal="reflective_symmetry"' in html
    assert 'data-field-signal="orthogonal"' in html
    assert 'class="field-signal-detail"' in html
    assert 'id="fieldSignalDetailTitle"' in html
    assert 'class="measure-ledger"' in html
    assert "FIELD_SIGNAL_META" in html
    assert "function selectFieldSignal(key)" in html
    assert "function initFieldAtlas()" in html
    assert "function coordinateLabel(value,positive,negative)" in html
    assert "function renderFieldCoordinate(row)" in html
    assert 'id="coordinatePlot"' in html
    assert 'id="coordinatePlotReadout"' in html
    assert 'id="coordinateNote"' in html
    assert "--coordinate-x" in html
    assert "Selected coordinate" in html
    assert "function updateMapStamp()" in html
    assert 'id="mapStamp"' in html
    assert 'id="mapConstellationScope"' in html
    assert 'id="mapRatioSignal"' in html
    assert "function renderMapConstellation()" in html
    assert "function rowHasSignal(row,key)" in html
    assert "map.on('moveend zoomend',()=>{ updateMapHud(); decorateMapAccessibility(); })" in html
    assert 'data-quick-view="signals"' in html
    assert "function initAtlasNav" in html
    assert "function applyQuickView" in html
    assert "function renderQuickViews" in html
    assert "function restoreViewState()" in html
    assert "function syncViewState()" in html
    assert "const STUDIO_STATE_KEYS" in html
    assert "function restoreStudioState()" in html
    assert "function syncStudioState(force=false)" in html
    assert "function copyStudioLink()" in html
    assert "const SKY_DECLINATION" in html
    assert "function skyFieldMetrics()" in html
    assert "function renderSkyField()" in html
    assert "const RAIN_EVENTS" in html
    assert "function waterFieldMetrics(values=scenarioValues())" in html
    assert "function renderWaterField()" in html
    assert "studio_rain_event" in html
    assert "function renderCountyPulse()" in html
    assert "class=\"county-pulse\"" in html
    assert "renderSpatialRhythm();" in html
    assert "renderScaleField();" in html
    assert "renderAlignmentField();" in html
    assert "renderSourceRoots();" in html
    assert "renderMakersField();" in html
    assert "function drawSelectionWeave(row)" in html
    assert "studio_ref" in html
    assert "studio_pair_a" in html
    assert "studio_pair_b" in html
    assert "id=\"reportLoadError\"" in html
    assert "id=\"reportRetry\"" in html
    assert "function showReportError(error" in html
    assert "function revealReportError()" in html
    assert "document.body.classList.remove('intro-open')" in html
    assert "function retryReportRequest()" in html
    assert "X-Ireland-Geometry-Runtime-Status" in lazy_html
    assert "function applyRuntime(runtime)" in lazy_html
    assert "function runtimeDataIdentity(runtime)" in lazy_html
    assert "function applyRuntimeHeaders(headers)" in lazy_html
    assert "function runtimeIdentityText()" in lazy_html
    assert "const snapshot=REPORT_RUNTIME?.snapshot" in lazy_html
    assert "const BASE_INTERPRETATION = PACK.interpretation || {};" in lazy_html
    assert "INTERPRETATION={...BASE_INTERPRETATION};" in lazy_html
    assert "const GEOJSON_BY_ID = new Map" in lazy_html
    assert "function drawSelectionFingerprint(row)" in lazy_html
    assert "function renderSelectionContext(row)" in lazy_html
    assert "function renderComparisonRelation(a,b)" in lazy_html
    assert "function renderStudioPairReference()" in lazy_html
    assert "const ROAD_PROXIMITY = PACK.road_proximity || [];" in lazy_html
    assert "const SPATIAL_COVARIATES = PACK.spatial_covariates_summary || [];" in lazy_html
    assert "function renderLandField()" in lazy_html
    assert "function renderPlaceNameField()" in lazy_html
    assert "function renderHeritageTypology()" in lazy_html
    assert "function setHeritageType(key)" in lazy_html
    assert "function renderPlaceBraid()" in lazy_html
    assert "function setPlaceBraidType(county,type)" in lazy_html
    assert "function renderSpatialRhythm()" in lazy_html
    assert "function setRhythmGroup(group)" in lazy_html
    assert "function renderScaleField()" in lazy_html
    assert "const RIPLEY = PACK.ripley || [];" in lazy_html
    assert "function renderAlignmentField()" in lazy_html
    assert "const POINT_PATTERN = PACK.point_pattern || [];" in lazy_html
    assert "function renderSourceRoots()" in lazy_html
    assert "const SOURCE_REGISTER = PACK.historical_source_register || [];" in lazy_html
    assert "function renderTrustField()" in lazy_html
    assert "const HOLDOUT = PACK.holdout || [];" in lazy_html
    assert "const REVIEW_CALIBRATION = PACK.review_calibration || [];" in lazy_html
    assert "function renderMakersField()" in lazy_html
    assert "function setMakerQuery(name)" in lazy_html
    assert "const ARCHITECTS = PACK.architects || [];" in lazy_html
    assert "const ARCHITECTS_BINARY = PACK.architects_binary || [];" in lazy_html
    assert "function restoreStudioState()" in lazy_html
    assert "function copyStudioLink()" in lazy_html
    assert "function renderSkyField()" in lazy_html
    assert "function renderWaterField()" in lazy_html
    assert "function renderCountyPulse()" in lazy_html
    assert "function drawSelectionWeave(row)" in lazy_html
    assert "function renderSelectionEvidence(row)" in lazy_html
    assert "function renderRuntimeStatus()" in lazy_html
    assert "function renderRuntimeReloadNotice()" in lazy_html
    assert "function refreshRuntime()" in lazy_html
    assert "function startRuntimeRefresh()" in lazy_html
    assert "setInterval(refreshRuntime,RUNTIME_REFRESH_MS)" in lazy_html
    assert "function renderMethod()" in lazy_html
    assert "function reportRequestBody(params, extra={})" in lazy_html
    assert "body:JSON.stringify(reportRequestBody(params,{initial:false}))" in lazy_html
    assert "body:JSON.stringify(reportRequestBody(params,{format}))" in lazy_html
    assert "id=\"runtimeStatus\"" in lazy_html
    assert "id=\"runtimeReloadNotice\"" in lazy_html
    assert "id=\"runtimeReload\"" in lazy_html
    assert "id=\"reportLoadError\"" in lazy_html
    assert "id=\"reportRetry\"" in lazy_html
    assert "function showInitialReportError(error)" in lazy_html
    assert "loadPack().catch(showInitialReportError)" in lazy_html
    assert "document.body.classList.remove('intro-open')" in lazy_html
    assert "classList.add('is-dismissed')" in lazy_html
    assert "function showReportError(error" in lazy_html
    assert "function revealReportError()" in lazy_html
    assert "function retryReportRequest()" in lazy_html
    assert "reportRetry'" in lazy_html
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
    assert "maneuver_n" in html
    assert "maneuvers" in html
    assert 'id="routeManeuvers"' in html
    assert "function renderRouteManeuvers(payload)" in html
    assert "focusRouteManeuver" in html
    assert 'id="routeSegments"' in html
    assert "function renderRouteSegments(payload)" in html
    assert "routeSegmentChecksText" in html
    assert "function routeSegmentChecksHtml(segment)" in html
    assert "route-segment-check-list" in html
    assert "ROUTE_SEGMENT_DISPLAY_LIMIT = 250" in html
    assert "showing ${visible.length} of ${total} mapped segments" in html
    assert 'id="routeCopyLink"' in html
    assert "fetch('/api/route'" in html
    assert "method:'POST'" in html
    assert "Content-Type':'application/json" in html
    assert "function restoreRouteState()" in html
    assert "function syncRouteState(fields)" in html
    assert 'id="routeDownloadJson"' in html
    assert 'id="routeDownloadGeojson"' in html
    assert "function downloadRouteResponse(format)" in html
    assert "function routeGeojsonPayload(payload)" in html
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
    assert 'id="routeCompareRun"' in html
    assert 'id="routeCompareProfiles"' in html
    assert "function runRouteComparison()" in html
    assert "fetch('/api/route/compare'" in html
    assert "method:'POST'" in html
    assert "Content-Type':'application/json" in html
    assert "delta_from_baseline" in html
    assert "function selectRouteComparisonProfile(index)" in html
    assert "params.set('compare','1')" in html
    assert 'id="routeCompareDownloadJson"' in html
    assert 'id="routeMatrixRun"' in html
    assert 'id="routeMatrixOrigins"' in html
    assert 'id="routeMatrixDestinations"' in html
    assert "function runRouteMatrix()" in html
    assert "fetch('/api/route/matrix'" in html
    assert "method:'POST'" in html
    assert "Content-Type':'application/json" in html
    assert "function selectRouteMatrixPair(index)" in html
    assert "params.set('matrix','1')" in html
    assert 'id="routeMatrixDownloadJson"' in html
    assert 'id="grammar"' in html
    assert "DESIGN_GRAMMARS" in html
    assert "Mirror symmetry" in html
    assert "Orthogonal grid" in html
    assert "Cruciform plan" in html
    assert 'id="windShelter"' in html
    assert 'id="rainCapture"' in html
    assert 'id="accessWidth"' in html
    assert 'id="futurePhases"' in html
    assert 'id="publicMix"' in html
    assert 'id="buildingLevels"' in html
    assert 'id="designSchedule"' in html
    assert 'id="designBrief"' in html
    assert 'id="copyBrief"' in html
    assert 'id="downloadBrief"' in html
    assert 'id="studioReference"' in html
    assert 'id="studioReferenceDimensions"' in html
    assert 'id="studioUseScale"' in html
    assert 'id="carrySelectionToStudio"' in html
    assert 'id="studioPairReference"' in html
    assert 'id="studioPairSpan"' in html
    assert 'id="studioUsePairBearing"' in html
    assert 'id="clearStudioPairReference"' in html
    assert "function renderStudioReference()" in html
    assert "function carrySelectionToStudio(id)" in html
    assert "function carryComparisonToStudio()" in html
    assert "function renderStudioPairReference()" in html
    assert "function useStudioPairBearing()" in html
    assert "function useStudioReferenceScale()" in html
    assert "FIELD REFERENCE" in html
    assert "function designMetrics" in html
    assert "function buildDesignBrief" in html
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


def test_field_walk_selects_distinct_data_derived_waypoints():
    def row(osm_id, *, score, group, name, ratio=False, angle=False, heritage=False):
        return {
            "osm_id": osm_id,
            "score": score,
            "group": group,
            "name": name,
            "has_golden_ratio": ratio,
            "has_golden_angle": angle,
            "niah": {"reg_no": "N1" if heritage else "", "name": name if heritage else ""},
        }

    waypoints = build_field_walk(
        [
            row("way/proportion", score=90, group="historic", name="Mill", ratio=True, heritage=True),
            row("way/angle", score=80, group="worship", name="Church", angle=True),
            row("way/memory", score=70, group="historic", name="Abbey", heritage=True),
            row("way/civic", score=60, group="civic", name="Hall", heritage=True),
        ]
    )

    assert [item["key"] for item in waypoints] == ["proportion", "angle", "memory", "civic"]
    assert len({item["row"]["osm_id"] for item in waypoints}) == 4
    assert [item["step"] for item in waypoints] == ["01", "02", "03", "04"]


def test_report_sorting_uses_osm_id_as_a_deterministic_tie_breaker():
    rows = [
        {"osm_id": "way/z", "score": 90, "name": "Same"},
        {"osm_id": "way/a", "score": 90, "name": "Same"},
        {"osm_id": "way/m", "score": 80, "name": "Other"},
    ]
    data = {"targets": rows}

    assert [row["osm_id"] for row in report_matching_targets(data)] == [
        "way/a",
        "way/z",
        "way/m",
    ]
    assert [row["osm_id"] for row in report_matching_targets(data, sort_desc=False)] == [
        "way/m",
        "way/a",
        "way/z",
    ]


def test_cultural_lenses_filter_data_derived_place_contexts():
    rows = [
        {
            "osm_id": "way/named",
            "group": "worship",
            "score": 90,
            "spatial": {"settlement_class": "named_place", "county": "Dublin"},
            "niah": {"reg_no": "N1"},
        },
        {
            "osm_id": "way/civic",
            "group": "civic",
            "score": 80,
            "spatial": {"settlement_class": "unknown", "county": "Cork"},
            "niah": {"reg_no": ""},
        },
        {
            "osm_id": "way/other",
            "group": "historic",
            "score": 70,
            "spatial": {"settlement_class": "unknown", "county": "Galway"},
            "niah": {"reg_no": ""},
        },
    ]
    data = {"targets": rows}

    assert [row["osm_id"] for row in report_matching_targets(data, culture="named")] == ["way/named"]
    assert [row["osm_id"] for row in report_matching_targets(data, culture="heritage")] == ["way/named"]
    assert [row["osm_id"] for row in report_matching_targets(data, culture="pobal")] == ["way/named", "way/civic"]
    assert [row["osm_id"] for row in report_matching_targets(data, culture="civic")] == ["way/civic"]
    assert [row["osm_id"] for row in report_matching_targets(data, county="Dublin")] == ["way/named"]
    assert report_filter_options(data)["county"] == ["Cork", "Dublin", "Galway"]
    with pytest.raises(ValueError, match="culture must be one of"):
        report_matching_targets(data, culture="folklore")


def test_generated_runtime_recovery_restores_baseline_interpretation(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for generated dashboard runtime testing")
    html = build_lazy_report(tmp_path)
    start = html.index("const BASE_INTERPRETATION")
    declarations_end = html.index("const SIG", start)
    function_start = html.index("function runtimeDataIdentity(runtime)", declarations_end)
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
const SERVER_MODE = true;
let SUMMARY = {};
let runtimeReloadRequired = false;
let reportRuntimeIdentity = null;
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
applyRuntime({
  contract: 'ireland-geometry.report-runtime.v1',
  status: 'pass',
  analysis_ready: true,
  validation: {status: 'pass'},
  manifest_alignment: {status: 'pass'},
  snapshot: {available: true, manifest_sha256: 'first-manifest'}
});
if (runtimeReloadRequired) throw new Error('initial runtime incorrectly requested reload');
applyRuntime({
  contract: 'ireland-geometry.report-runtime.v1',
  status: 'pass',
  analysis_ready: true,
  validation: {status: 'pass'},
  manifest_alignment: {status: 'pass'},
  snapshot: {available: true, manifest_sha256: 'second-manifest'}
});
if (!runtimeReloadRequired) throw new Error('changed runtime did not request reload');
"""
    result = subprocess.run(
        ["node", "-e", script], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
