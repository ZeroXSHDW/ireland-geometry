import gzip
import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from scripts.road_routing import write_sqlite_graph
from scripts.runtime import package_version, path_modified_at, runtime_signature, sha256_path
from scripts.serve_report import (
    CAPABILITIES_API_PATH,
    CAPABILITIES_CONTRACT,
    HEALTH_CONTRACT,
    INTERPRETATION_API_PATH,
    INTERPRETATION_CONTRACT,
    METADATA_API_PATH,
    METADATA_CONTRACT,
    OPENAPI_CONTRACT,
    OPENAPI_PATH,
    QUERY_CONTRACT,
    REPORT_EXPORT_API_PATH,
    REPORT_EXPORT_CONTRACT,
    REPORT_PAGE_API_PATH,
    REPORT_PAGE_CONTRACT,
    REPORT_RUNTIME_API_PATH,
    REPORT_RUNTIME_CONTRACT,
    REPORT_RUNTIME_HEADER_NAMES,
    ROUTE_API_PATH,
    ROUTE_CONTRACT,
    SOURCE_ALIGNMENT_SURFACES,
    create_server,
    report_url,
)


def _manifest_artifacts(root):
    records = []
    for path in sorted(root.iterdir()):
        if path.name == "manifest.json" or not path.is_file():
            continue
        records.append(
            {
                "relative_path": path.name,
                "path_base": "output_dir",
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return records


def test_report_server_serves_lazy_report_and_health(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name)
        with urllib.request.urlopen(base, timeout=2) as response:
            assert response.status == 200
            assert response.read() == b"<html>lazy</html>"
        with urllib.request.urlopen(f"{base.rsplit('/', 1)[0]}/__health", timeout=2) as response:
            health = json.load(response)
        assert health == {
            "contract": HEALTH_CONTRACT,
            "status": "ok",
            "ready": True,
            "analysis_ready": False,
            "output_symlink_guard": True,
            "manifest_alignment": {
                "contract": "ireland-geometry.manifest-alignment.v1",
                "status": "not_provided",
                "passed": False,
                "checked_count": 0,
                "manifest_artifact_count": 0,
                "actual_file_count": 0,
                "symlink_count": 0,
                "unlisted_count": 0,
                "errors": ["manifest.json is unavailable"],
            },
            "source_alignment": {
                "contract": "ireland-geometry.source-alignment.v1",
                "status": "not_provided",
                "passed": False,
                "mode": "metadata",
                "checked_count": 0,
                "unavailable_count": 0,
                "unhashed_count": 0,
                "hash_checked_count": 0,
                "metadata_checked_count": 0,
                "symlink_count": 0,
                "errors": [],
                "sources": [],
            },
            "validation": {
                "status": "not_provided",
                "passed": False,
                "manifest_available": False,
                "records": {
                    name: {
                        "available": False,
                        "status": "not_provided",
                        "passed": False,
                        "error": None,
                    }
                    for name in ("verification", "schema_validation", "reproducibility")
                },
            },
            "report": "report_lazy.html",
            "report_exists": True,
            "data_pack_exists": True,
            "data_pack_required": True,
            "data_pack_gzip": True,
            "routing_available": False,
            "route_endpoint": "/api/route",
            "query_available": False,
            "query_endpoint": "/api/query",
            "query_backend_health": {
                "duckdb": {"available": False, "readable": False, "error": None},
                "parquet": {"available": False, "readable": False, "error": None},
                "csv": {"available": False, "readable": False, "error": None},
                "jsonl": {"available": False, "readable": False, "error": None},
            },
            "query_readable_backends": [],
            "query_pagination": True,
            "query_max_limit": 1000,
            "query_max_offset": 10000000,
            "query_min_score_finite": True,
            "query_cursor": True,
            "query_invalid_score_cursor": True,
            "query_auto_backend_failover": True,
            "metadata_available": False,
            "metadata_endpoint": "/api/metadata",
            "interpretation_available": False,
            "interpretation_endpoint": INTERPRETATION_API_PATH,
            "capabilities_endpoint": CAPABILITIES_API_PATH,
        }
        request = urllib.request.Request(
            f"{base.rsplit('/', 1)[0]}/report_data.json",
            headers={"Accept-Encoding": "gzip"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.headers["Content-Encoding"] == "gzip"
            data_pack_etag = response.headers["ETag"]
            assert data_pack_etag.startswith('"')
            assert json.loads(gzip.decompress(response.read())) == {"targets": []}
        conditional = urllib.request.Request(
            f"{base.rsplit('/', 1)[0]}/report_data.json",
            headers={"Accept-Encoding": "gzip", "If-None-Match": data_pack_etag},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == data_pack_etag
        assert error.value.headers["Content-Encoding"] == "gzip"
        assert error.value.read() == b""
        (tmp_path / "report_data.json").write_text('{"targets": [1]}', encoding="utf-8")
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert response.headers["ETag"] != data_pack_etag
            assert json.loads(gzip.decompress(response.read())) == {"targets": [1]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_capabilities_api_describes_contracts_and_runtime_state(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (tmp_path / "analysis_results.csv").write_text(
        "osm_id,score\nway/1,91\n", encoding="utf-8"
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}{CAPABILITIES_API_PATH}", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-cache"
            capabilities_etag = response.headers["ETag"]
            capabilities = json.load(response)
        assert capabilities["contract"] == CAPABILITIES_CONTRACT
        assert capabilities["status"] == "ok"
        assert capabilities["ready"] is True
        assert capabilities["analysis_ready"] is False
        assert capabilities["output_symlink_guard"] is True
        assert capabilities["validation"]["status"] == "not_provided"
        assert capabilities["validation"]["passed"] is False
        assert capabilities["validation"]["manifest_available"] is False
        assert capabilities["manifest_alignment"]["status"] == "not_provided"
        assert capabilities["source_alignment"]["status"] == "not_provided"
        assert capabilities["source_alignment_surfaces"] == list(SOURCE_ALIGNMENT_SURFACES)
        assert capabilities["exports"] == {
            "available": False,
            "contract": None,
            "parity": None,
            "rows": None,
        }
        assert capabilities["contracts"] == {
            "health": HEALTH_CONTRACT,
            "capabilities": CAPABILITIES_CONTRACT,
            "metadata": METADATA_CONTRACT,
            "query": QUERY_CONTRACT,
            "route": ROUTE_CONTRACT,
            "openapi": OPENAPI_CONTRACT,
            "interpretation": INTERPRETATION_CONTRACT,
            "report_page": REPORT_PAGE_CONTRACT,
            "report_export": REPORT_EXPORT_CONTRACT,
            "report_runtime": REPORT_RUNTIME_CONTRACT,
        }
        assert capabilities["report"] == {
            "name": "report_lazy.html",
            "exists": True,
            "data_pack_exists": True,
            "data_pack_required": True,
            "data_pack_gzip": True,
        }
        assert capabilities["endpoints"]["health"] == {
            "path": "/__health",
            "method": "GET",
            "contract": HEALTH_CONTRACT,
            "available": True,
        }
        assert capabilities["endpoints"]["capabilities"] == {
            "path": CAPABILITIES_API_PATH,
            "method": "GET",
            "contract": CAPABILITIES_CONTRACT,
            "available": True,
        }
        assert capabilities["endpoints"]["metadata"]["contract"] == METADATA_CONTRACT
        assert capabilities["endpoints"]["metadata"]["available"] is False
        assert capabilities["endpoints"]["interpretation"] == {
            "path": INTERPRETATION_API_PATH,
            "method": "GET",
            "contract": INTERPRETATION_CONTRACT,
            "available": False,
        }
        assert capabilities["endpoints"]["report_page"] == {
            "path": REPORT_PAGE_API_PATH,
            "method": "GET",
            "contract": REPORT_PAGE_CONTRACT,
            "available": True,
            "pagination": True,
            "max_limit": 100,
        }
        assert capabilities["endpoints"]["report_export"] == {
            "path": REPORT_EXPORT_API_PATH,
            "method": "GET",
            "contract": REPORT_EXPORT_CONTRACT,
            "available": True,
            "formats": ["csv", "geojson"],
            "runtime_contract": REPORT_RUNTIME_CONTRACT,
            "runtime_headers": dict(REPORT_RUNTIME_HEADER_NAMES),
        }
        assert capabilities["endpoints"]["report_runtime"] == {
            "path": REPORT_RUNTIME_API_PATH,
            "method": "GET",
            "contract": REPORT_RUNTIME_CONTRACT,
            "available": True,
            "conditional": True,
        }
        assert capabilities["endpoints"]["query"]["contract"] == QUERY_CONTRACT
        assert capabilities["endpoints"]["query"]["readable_backends"] == ["csv"]
        assert capabilities["endpoints"]["route"] == {
            "path": ROUTE_API_PATH,
            "method": "GET",
            "contract": ROUTE_CONTRACT,
                "available": False,
                "objectives": ["distance", "duration"],
                "vehicle_weight_profiles": True,
                "vehicle_rating_profiles": True,
                "vehicle_height_profiles": True,
                "vehicle_width_profiles": True,
                "vehicle_length_profiles": True,
                "vehicle_axleload_profiles": True,
                "maxweight_profiles": True,
                "maxweightrating_hgv_profiles": True,
                "hgv_destination_profiles": True,
                "hgv_destination_delivery_profiles": True,
                "maxheight_profiles": True,
                "maxwidth_profiles": True,
                "maxlength_profiles": True,
                "maxaxleload_profiles": True,
                "maxspeed_profiles": True,
                "maxspeed_conditional_profiles": True,
                "oneway_conditional_profiles": True,
                "path_segment_explainability": True,
                "path_segment_constraints": True,
                "path_segment_conditional_rules": True,
                "path_segment_transition_rules": True,
                "path_segment_way_context": True,
                "vehicle_weight_conditional_access_profiles": True,
                "conditional_access_profiles": True,
                "directional_conditional_access_profiles": True,
                "multi_clause_conditional_access_profiles": True,
                "vehicle_class_profiles": True,
                "vehicle_classes": ["general", "delivery", "hgv", "psv", "taxi"],
            }
        assert capabilities["endpoints"]["openapi"] == {
            "path": OPENAPI_PATH,
            "method": "GET",
            "contract": OPENAPI_CONTRACT,
            "available": True,
        }
        conditional = urllib.request.Request(
            f"{base}{CAPABILITIES_API_PATH}",
            headers={"If-None-Match": capabilities_etag},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == capabilities_etag
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_openapi_document_describes_read_endpoints(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}{OPENAPI_PATH}", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-cache"
            etag = response.headers["ETag"]
            document = json.load(response)
        assert document["openapi"] == "3.1.0"
        assert document["x-contract"] == OPENAPI_CONTRACT
        assert document["info"]["version"] == package_version()
        assert {"analysis_ready", "validation"} <= set(
            document["components"]["schemas"]["HealthResponse"]["required"]
        )
        assert "source_alignment" in document["components"]["schemas"]["HealthResponse"]["required"]
        assert "ValidationStatus" in document["components"]["schemas"]
        assert "ManifestAlignment" in document["components"]["schemas"]
        assert "manifest_alignment" in document["components"]["schemas"]["HealthResponse"]["required"]
        assert "ReportRuntime" in document["components"]["schemas"]
        assert "ReportRuntimeSnapshot" in document["components"]["schemas"]
        assert "SourceAlignment" in document["components"]["schemas"]
        source_schema = document["components"]["schemas"]["SourceAlignment"]
        assert "metadata_checked_count" in source_schema["required"]
        assert "metadata_checked_count" in source_schema["properties"]
        assert "source_alignment" in document["components"]["schemas"]["ReportRuntime"]["required"]
        assert "snapshot" in document["components"]["schemas"]["ReportRuntime"]["required"]
        assert "runtime" in document["components"]["schemas"]["ReportPageResponse"]["required"]
        assert "SourceFreshness" in document["components"]["schemas"]
        freshness_schema = document["components"]["schemas"]["SourceFreshness"]
        assert (
            freshness_schema["properties"]["contract"]["const"]
            == "ireland-geometry.freshness.v1"
        )
        assert set(freshness_schema["required"]) == {"contract", "observed_at", "sources"}
        assert "source_freshness" in document["components"]["schemas"]["MetadataResponse"]["required"]
        assert "source_freshness" in document["components"]["schemas"]["CapabilitiesResponse"]["required"]
        assert "source_alignment" in document["components"]["schemas"]["MetadataResponse"]["required"]
        assert "source_alignment" in document["components"]["schemas"]["CapabilitiesResponse"]["required"]
        assert "source_alignment_surfaces" in document["components"]["schemas"]["CapabilitiesResponse"]["required"]
        assert "project_root" in document["components"]["schemas"]["CapabilitiesResponse"]["required"]
        assert "source_alignment" in document["components"]["schemas"]["InterpretationResponse"]["required"]
        assert set(document["paths"]) == {
            "/__health",
            "/api/capabilities",
            "/api/openapi.json",
            "/api/metadata",
            "/api/interpretation",
            "/api/query",
            "/api/route",
            REPORT_PAGE_API_PATH,
            REPORT_RUNTIME_API_PATH,
            REPORT_EXPORT_API_PATH,
        }
        assert document["x-response-contracts"]["interpretation"] == INTERPRETATION_CONTRACT
        assert document["paths"][INTERPRETATION_API_PATH]["get"]["operationId"] == "getInterpretation"
        assert "InterpretationResponse" in document["components"]["schemas"]
        query_parameters = {
            item["name"] for item in document["paths"]["/api/query"]["get"]["parameters"]
        }
        assert {"limit", "offset", "min_score", "after_invalid_osm_id"} <= query_parameters
        route_parameters = {
            item["name"] for item in document["paths"]["/api/route"]["get"]["parameters"]
        }
        assert {
            "start_lat",
            "start_lon",
            "goal_lat",
            "goal_lon",
            "format",
            "objective",
            "weight_t",
            "rating_t",
            "height_m",
            "width_m",
            "length_m",
            "axleload_t",
            "allow_hgv_destination",
        } <= route_parameters
        vehicle_parameter = next(
            item
            for item in document["paths"]["/api/route"]["get"]["parameters"]
            if item["name"] == "vehicle_class"
        )
        assert vehicle_parameter["schema"]["enum"] == ["general", "delivery", "hgv", "psv", "taxi"]
        assert "psv" in vehicle_parameter["description"]
        assert "taxi" in vehicle_parameter["description"]
        assert "hgv" in vehicle_parameter["description"]
        rating_parameter = next(
            item
            for item in document["paths"]["/api/route"]["get"]["parameters"]
            if item["name"] == "rating_t"
        )
        assert "maxweightrating:goods" in rating_parameter["description"]
        destination_parameter = next(
            item
            for item in document["paths"]["/api/route"]["get"]["parameters"]
            if item["name"] == "allow_hgv_destination"
        )
        assert "destination-only" in destination_parameter["description"]
        ferry_parameter = next(
            item
            for item in document["paths"]["/api/route"]["get"]["parameters"]
            if item["name"] == "include_ferries"
        )
        assert "service windows" in ferry_parameter["description"]
        assert "next-opening waits" in ferry_parameter["description"]
        assert "crossing durations" in ferry_parameter["description"]
        path_parameter = next(
            item
            for item in document["paths"]["/api/route"]["get"]["parameters"]
            if item["name"] == "include_path"
        )
        assert "way/segment" in path_parameter["description"]
        route_schema = document["components"]["schemas"]["RouteResponse"]
        assert {
            "objective",
            "vehicle_weight_t",
            "vehicle_rating_t",
            "vehicle_height_m",
            "vehicle_width_m",
            "vehicle_length_m",
            "vehicle_axleload_t",
            "vehicle_class",
            "allow_hgv_destination",
            "ferry_wait_s",
            "ferry_wait_n",
            "ferry_way_ids",
            "ferry_distance_m",
            "ferry_crossing_s",
            "ferry_edge_n",
        } <= set(route_schema["required"])
        assert {
            "path_node_n",
            "path_node_ids",
            "path_coordinates",
            "path_segment_n",
            "path_segment_total_distance_m",
            "path_segment_total_duration_s",
            "path_segment_total_wait_s",
            "path_segment_source",
            "path_way_ids",
            "path_segments",
        } <= set(route_schema["properties"])
        assert "from_node" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "duration_s" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "ferry" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "road_context" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "constraints" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "conditional_rules" in route_schema["properties"]["path_segments"]["items"]["required"]
        assert "transition_rules" in route_schema["properties"]["path_segments"]["items"]["required"]
        constraint_schema = route_schema["properties"]["path_segments"]["items"]["properties"]["constraints"]["items"]
        assert set(constraint_schema["required"]) == {
            "key",
            "value",
            "unit",
            "status",
            "evaluated",
            "profile",
        }
        conditional_schema = route_schema["properties"]["path_segments"]["items"]["properties"]["conditional_rules"]["items"]
        assert set(conditional_schema["required"]) == {
            "key",
            "value",
            "unit",
            "condition",
            "mode",
            "direction",
            "vehicle_class",
            "status",
            "evaluated",
            "active",
            "applied",
            "profile",
        }
        transition_schema = route_schema["properties"]["path_segments"]["items"]["properties"]["transition_rules"]["items"]
        assert set(transition_schema["required"]) == {
            "key",
            "relation_id",
            "via_node",
            "from_way",
            "to_way",
            "via_way_ids",
            "kind",
            "condition",
            "status",
            "evaluated",
            "active",
            "applied",
            "selected",
            "profile",
        }
        road_context_schema = route_schema["properties"]["path_segments"]["items"]["properties"]["road_context"]
        assert set(road_context_schema["required"]) == {
            "name",
            "ref",
            "highway",
            "route",
            "oneway",
        }
        assert "way_id" in route_schema["properties"]["path_segments"]["description"]
        report_parameters = {
            item["name"] for item in document["paths"][REPORT_PAGE_API_PATH]["get"]["parameters"]
        }
        assert {"q", "score", "limit", "offset", "initial", "sort"} <= report_parameters
        assert document["paths"][REPORT_EXPORT_API_PATH]["get"]["operationId"] == "exportFilteredReport"
        assert document["paths"][REPORT_RUNTIME_API_PATH]["get"]["operationId"] == "getReportRuntime"
        assert (
            document["paths"][REPORT_RUNTIME_API_PATH]["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ReportRuntime"
        )
        export_headers = document["paths"][REPORT_EXPORT_API_PATH]["get"]["responses"]["200"]["headers"]
        assert set(export_headers) == set(REPORT_RUNTIME_HEADER_NAMES.values())
        assert (
            export_headers[REPORT_RUNTIME_HEADER_NAMES["contract"]]["schema"]["const"]
            == REPORT_RUNTIME_CONTRACT
        )
        assert "ReportPageResponse" in document["components"]["schemas"]
        conditional = urllib.request.Request(
            f"{base}{OPENAPI_PATH}", headers={"If-None-Match": etag}
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == etag
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_interpretation_api_serves_compact_sidecar(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    for name in ("verification", "schema_validation", "reproducibility"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    sidecar = {
        "contract": INTERPRETATION_CONTRACT,
        "status": "ok",
        "available": True,
        "analysis_ready": False,
        "validation": {"status": "not_provided"},
        "summary": {"targets": 12, "controls": 8, "focus_group": "worship"},
        "interpretation": {
            "status": "available",
            "focus_group": "worship",
            "headline": "Derived headline.",
            "findings": [],
            "caveats": [],
        },
        "source": "/interpretation.json",
    }
    (tmp_path / "interpretation.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifacts": _manifest_artifacts(tmp_path)}), encoding="utf-8"
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}{INTERPRETATION_API_PATH}", timeout=2) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-cache"
            etag = response.headers["ETag"]
            payload = json.load(response)
        assert payload["contract"] == INTERPRETATION_CONTRACT
        assert payload["status"] == "ok"
        assert payload["available"] is True
        assert payload["analysis_ready"] is True
        assert payload["manifest_alignment"]["status"] == "pass"
        assert payload["source_alignment"]["status"] == "not_provided"
        assert payload["validation"]["status"] == "pass"
        assert payload["summary"]["focus_group"] == "worship"
        assert payload["interpretation"]["headline"] == "Derived headline."
        conditional = urllib.request.Request(
            f"{base}{INTERPRETATION_API_PATH}", headers={"If-None-Match": etag}
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == etag
        sidecar["interpretation"]["headline"] = "Updated headline."
        (tmp_path / "interpretation.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        with urllib.request.urlopen(f"{base}{INTERPRETATION_API_PATH}", timeout=2) as response:
            assert response.headers["ETag"] != etag
            assert json.load(response)["interpretation"]["headline"] == "Updated headline."
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_interpretation_api_reports_missing_sidecar(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}{INTERPRETATION_API_PATH}", timeout=2)
        assert error.value.code == 404
        payload = json.loads(error.value.read())
        assert payload["contract"] == INTERPRETATION_CONTRACT
        assert payload["status"] == "not_provided"
        assert payload["available"] is False
        assert payload["source_alignment"]["status"] == "not_provided"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_readiness_surfaces_recheck_source_alignment(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    for name in ("verification", "schema_validation", "reproducibility"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
    (tmp_path / "interpretation.json").write_text(
        json.dumps(
            {
                "contract": INTERPRETATION_CONTRACT,
                "status": "ok",
                "available": True,
                "analysis_ready": True,
                "validation": {"status": "pass"},
                "summary": {"targets": 0},
                "interpretation": {
                    "status": "available",
                    "findings": [
                        {
                            "id": "validation",
                            "status": "pass",
                            "text": "All validation gates pass.",
                        }
                    ],
                    "caveats": [],
                },
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path.parent / f"source-{tmp_path.name}.bin"
    source.write_bytes(b"source")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "project_root": str(tmp_path.parent),
                "sources": [
                    {
                        "source_kind": "required_source",
                        "path": str(source),
                        "path_base": "project_root",
                        "relative_path": source.name,
                        "sha256": sha256_path(source),
                        "bytes": source.stat().st_size,
                        "modified_at": path_modified_at(source),
                    }
                ],
                "artifacts": _manifest_artifacts(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]

        def read(path):
            with urllib.request.urlopen(f"{base}{path}", timeout=2) as response:
                return json.load(response)

        passing = {
            "health": read("/__health"),
            "capabilities": read(CAPABILITIES_API_PATH),
            "metadata": read(METADATA_API_PATH),
            "interpretation": read(INTERPRETATION_API_PATH),
        }
        assert all(item["source_alignment"]["status"] == "pass" for item in passing.values())
        assert passing["health"]["analysis_ready"] is True
        assert passing["capabilities"]["analysis_ready"] is True
        assert passing["metadata"]["status"] == "ok"
        assert passing["interpretation"]["analysis_ready"] is True

        source.write_bytes(b"changed!")
        failed = {
            "health": read("/__health"),
            "capabilities": read(CAPABILITIES_API_PATH),
            "metadata": read(METADATA_API_PATH),
            "interpretation": read(INTERPRETATION_API_PATH),
        }
        assert all(item["source_alignment"]["status"] == "fail" for item in failed.values())
        assert failed["health"]["analysis_ready"] is False
        assert failed["capabilities"]["analysis_ready"] is False
        assert failed["metadata"]["status"] == "degraded"
        assert failed["interpretation"]["analysis_ready"] is False
        finding = next(
            item
            for item in failed["interpretation"]["interpretation"]["findings"]
            if item["id"] == "validation"
        )
        assert finding["status"] == "fail"
        assert "input source alignment" in finding["text"]
        assert any(
            "input sources" in caveat
            for caveat in failed["interpretation"]["interpretation"]["caveats"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_health_reports_degraded_lazy_readiness(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["status"] == "degraded"
        assert health["ready"] is False
        assert health["analysis_ready"] is False
        assert health["validation"]["status"] == "not_provided"
        assert health["source_alignment"]["status"] == "not_provided"
        assert health["validation"]["manifest_available"] is False
        assert health["report_exists"] is True
        assert health["data_pack_exists"] is False
        assert health["data_pack_required"] is True
        assert health["data_pack_gzip"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_metadata_api_exposes_compact_build_contract(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (tmp_path / "analysis_results.csv").write_text(
        "osm_id,score\nway/1,91\n", encoding="utf-8"
    )
    freshness = {
        "contract": "ireland-geometry.freshness.v1",
        "observed_at": "2026-08-18T00:00:00+00:00",
        "sources": [
            {
                "source_kind": "file",
                "path": "/tmp/combined.json",
                "path_base": "external",
                "modified_at": "2026-08-17T23:00:00+00:00",
                "age_seconds": 3600,
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "package_version": package_version(),
                "runtime": runtime_signature(),
                "generated_at": "2026-08-18T00:00:00+00:00",
                "git_revision": "abc1234",
                "git_dirty": False,
                "parameters": {"seed": 7},
                "counts": {"analysis_results.csv": 3},
                "sources": [{"kind": "file"}],
                "source_freshness": freshness,
                "artifacts": [{"path": "analysis_results.csv"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "verification.json").write_text(
        json.dumps(
            {
                "passed": True,
                "analysis_rows": 3,
                "target_rows": 2,
                "control_rows": 1,
                "errors": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "columnar_status.json").write_text(
        json.dumps(
            {
                "rows": 3,
                "jsonl": {"status": "available"},
                "parquet": {"status": "available", "engine": "pyarrow"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "schema_validation.json").write_text(
        json.dumps({"passed": True, "schema_version": 1, "artifacts": {"a.csv": {}}}),
        encoding="utf-8",
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/api/metadata", timeout=2) as response:
            metadata_etag = response.headers["ETag"]
            assert response.headers["Cache-Control"] == "no-cache"
            metadata = json.load(response)
        assert metadata["status"] == "degraded"
        assert metadata["contract"] == METADATA_CONTRACT
        assert metadata["manifest_alignment"]["status"] == "fail"
        assert metadata["source_freshness"] == freshness
        assert metadata["manifest"] == {
            "available": True,
            "url": "/manifest.json",
            "package_version": package_version(),
            "runtime": runtime_signature(),
            "schema_version": 3,
            "generated_at": "2026-08-18T00:00:00+00:00",
            "git_revision": "abc1234",
            "git_dirty": False,
            "path_contract": None,
            "data_root_relative": None,
            "output_dir_relative": None,
            "parameters": {"seed": 7},
            "counts": {"analysis_results.csv": 3},
            "source_count": 1,
            "artifact_count": 1,
            "portable_artifact_count": 0,
            "portable_source_count": 0,
            "external_source_count": 0,
        }
        assert metadata["verification"]["status"] == "pass"
        assert metadata["verification"]["target_rows"] == 2
        assert metadata["exports"]["backends"]["parquet"]["engine"] == "pyarrow"
        assert metadata["exports"]["backends"]["parquet"]["status"] == "available"
        assert metadata["exports"]["backends"]["parquet"]["available"] is False
        assert metadata["exports"]["backends"]["parquet"]["readable"] is False
        assert metadata["exports"]["backends"]["csv"]["readable"] is True
        assert metadata["exports"]["readable_backends"] == ["csv"]
        assert metadata["schema_validation"]["artifact_count"] == 1
        assert metadata["reproducibility"] == {
            "available": False,
            "url": "/reproducibility.json",
            "status": "not_provided",
            "artifact_count": None,
        }
        assert metadata["links"]["manifest"] == "/manifest.json"
        assert metadata["links"]["reproducibility"] == "/reproducibility.json"
        (tmp_path / "reproducibility.json").write_text(
            json.dumps({"passed": True, "artifact_count": 7}), encoding="utf-8"
        )
        with urllib.request.urlopen(f"{base}/api/metadata", timeout=2) as response:
            refreshed_metadata_etag = response.headers["ETag"]
            refreshed_metadata = json.load(response)
        assert refreshed_metadata_etag != metadata_etag
        assert refreshed_metadata["reproducibility"] == {
            "available": True,
            "url": "/reproducibility.json",
            "status": "pass",
            "artifact_count": 7,
        }
        conditional = urllib.request.Request(
            f"{base}/api/metadata",
            headers={"If-None-Match": refreshed_metadata_etag},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == refreshed_metadata_etag
        assert error.value.read() == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_health_does_not_require_data_pack_for_embedded_report(tmp_path):
    (tmp_path / "report.html").write_text("<html>embedded</html>", encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0, report="report.html")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["status"] == "ok"
        assert health["ready"] is True
        assert health["analysis_ready"] is False
        assert health["validation"]["status"] == "not_provided"
        assert health["source_alignment"]["status"] == "not_provided"
        assert health["validation"]["manifest_available"] is False
        assert health["data_pack_exists"] is False
        assert health["data_pack_required"] is False
        assert health["data_pack_gzip"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_exposes_passed_analysis_validation(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    for name in ("verification", "schema_validation", "reproducibility"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifacts": _manifest_artifacts(tmp_path)}), encoding="utf-8"
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["ready"] is True
        assert health["analysis_ready"] is True
        assert health["manifest_alignment"]["status"] == "pass"
        assert health["validation"]["status"] == "pass"
        assert health["validation"]["manifest_available"] is True
        assert all(
            record["status"] == "pass"
            for record in health["validation"]["records"].values()
        )
        with urllib.request.urlopen(f"{base}{CAPABILITIES_API_PATH}", timeout=2) as response:
            capabilities = json.load(response)
        assert capabilities["analysis_ready"] is True
        assert capabilities["validation"]["passed"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_health_rejects_stale_manifest_artifact(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text(
        json.dumps(
            {
                "targets": [],
                "summary": {"analysis_ready": True},
                "interpretation": {
                    "findings": [
                        {
                            "id": "validation",
                            "status": "pass",
                            "text": "Manifest, verification, schema validation, and reproducibility records all pass.",
                        }
                    ],
                    "caveats": [],
                },
            }
        ),
        encoding="utf-8",
    )
    for name in ("verification", "schema_validation", "reproducibility"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifacts": _manifest_artifacts(tmp_path)}), encoding="utf-8"
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    (tmp_path / "report_data.json").write_text(
        json.dumps(
            {
                "targets": [],
                "summary": {"analysis_ready": True},
                "interpretation": {
                    "findings": [
                        {
                            "id": "validation",
                            "status": "pass",
                            "text": "Manifest, verification, schema validation, and reproducibility records all pass.",
                        }
                    ],
                    "caveats": [],
                },
                "changed": True,
            }
        ),
        encoding="utf-8",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["ready"] is True
        assert health["analysis_ready"] is False
        assert health["manifest_alignment"]["status"] == "fail"
        assert any("hash differs" in error for error in health["manifest_alignment"]["errors"])
        with urllib.request.urlopen(
            f"{base}{REPORT_PAGE_API_PATH}?initial=1&limit=1", timeout=2
        ) as response:
            page = json.load(response)
        assert page["runtime"]["contract"] == REPORT_RUNTIME_CONTRACT
        assert page["runtime"]["status"] == "fail"
        assert page["runtime"]["analysis_ready"] is False
        assert page["summary"]["analysis_ready"] is False
        assert page["summary"]["manifest_alignment"]["status"] == "fail"
        finding = next(item for item in page["interpretation"]["findings"] if item["id"] == "validation")
        assert finding["status"] == "fail"
        assert "artifact alignment" in finding["text"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_runtime_endpoint_rechecks_alignment_and_etag(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    for name in ("verification", "schema_validation", "reproducibility"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
    source = tmp_path.parent / "data" / "source.bin"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"source")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-18T18:00:00+00:00",
                "git_revision": "snapshot-test",
                "git_dirty": False,
                "package_version": "0.4.0",
                "schema_version": 3,
                "project_root": str(tmp_path.parent),
                "sources": [
                    {
                        "source_kind": "required_source",
                        "path": str(source),
                        "path_base": "project_root",
                        "relative_path": "data/source.bin",
                        "sha256": sha256_path(source),
                        "bytes": source.stat().st_size,
                        "modified_at": path_modified_at(source),
                    }
                ],
                "artifacts": _manifest_artifacts(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        runtime_url = f"{base}{REPORT_RUNTIME_API_PATH}"
        with urllib.request.urlopen(runtime_url, timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-cache"
            first_etag = response.headers["ETag"]
            runtime = json.load(response)
        assert runtime["contract"] == REPORT_RUNTIME_CONTRACT
        assert runtime["status"] == "pass"
        assert runtime["analysis_ready"] is True
        assert runtime["validation"]["status"] == "pass"
        assert runtime["manifest_alignment"]["status"] == "pass"
        assert runtime["source_alignment"]["status"] == "pass"
        assert runtime["snapshot"]["available"] is True
        assert runtime["snapshot"]["generated_at"] == "2026-08-18T18:00:00+00:00"
        assert runtime["snapshot"]["git_revision"] == "snapshot-test"
        assert runtime["snapshot"]["git_dirty"] is False
        assert runtime["snapshot"]["package_version"] == "0.4.0"
        assert runtime["snapshot"]["schema_version"] == 3
        assert runtime["snapshot"]["manifest_sha256"] == hashlib.sha256(
            (tmp_path / "manifest.json").read_bytes()
        ).hexdigest()

        (tmp_path / "report_data.json").write_text('{"targets": [1]}', encoding="utf-8")
        with urllib.request.urlopen(runtime_url, timeout=2) as response:
            second_etag = response.headers["ETag"]
            stale_runtime = json.load(response)
        assert second_etag != first_etag
        assert stale_runtime["status"] == "fail"
        assert stale_runtime["analysis_ready"] is False
        assert stale_runtime["manifest_alignment"]["status"] == "fail"

        conditional = urllib.request.Request(
            runtime_url, headers={"If-None-Match": second_etag}
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers["ETag"] == second_etag
        assert error.value.read() == b""

        source.write_bytes(b"tampered")
        with urllib.request.urlopen(runtime_url, timeout=2) as response:
            source_runtime = json.load(response)
        assert source_runtime["status"] == "fail"
        assert source_runtime["source_alignment"]["status"] == "fail"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_source_alignment_honors_explicit_project_root_with_custom_layout(tmp_path):
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
    (output / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (output / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "project_root": str(original),
                "sources": [
                    {
                        "source_kind": "required_source",
                        "path": str(old_source),
                        "path_base": "project_root",
                        "relative_path": "data/source.bin",
                        "sha256": sha256_path(current_source),
                        "bytes": current_source.stat().st_size,
                        "modified_at": path_modified_at(current_source),
                    }
                ],
                "artifacts": _manifest_artifacts(output),
            }
        ),
        encoding="utf-8",
    )
    server = create_server(
        output,
        project_root=relocated,
        data_root=data_root,
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]

        def read(path):
            with urllib.request.urlopen(f"{base}{path}", timeout=2) as response:
                return response, json.load(response)

        health_response, health = read("/__health")
        capabilities_response, capabilities = read(CAPABILITIES_API_PATH)
        runtime_response, runtime = read(REPORT_RUNTIME_API_PATH)
        metadata_response, metadata = read(METADATA_API_PATH)
        with urllib.request.urlopen(
            f"{base}{REPORT_EXPORT_API_PATH}?format=csv&limit=1", timeout=2
        ) as response:
            export_headers = dict(response.headers)
            response.read()

        assert health_response.status == 200
        assert capabilities_response.status == 200
        assert runtime_response.status == 200
        assert metadata_response.status == 200
        for payload in (health, capabilities, runtime, metadata):
            assert payload["source_alignment"]["status"] == "pass"
            assert payload["source_alignment"]["sources"][0]["path"] == str(
                current_source.resolve()
            )
        assert capabilities["project_root"] == str(relocated.resolve())
        assert export_headers[REPORT_RUNTIME_HEADER_NAMES["source_alignment"]] == "pass"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_route_api_serves_json_and_geojson(tmp_path):
    output = tmp_path / "output"
    data = tmp_path / "data"
    output.mkdir()
    (output / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (output / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    write_sqlite_graph(
        data / "roads",
        {
            "a": (53.0, -8.0),
            "b": (53.0, -8.001),
            "c": (53.0, -8.002),
        },
        [
            {
                "u": "a",
                "v": "b",
                "length_m": "10",
                "hgv": "destination",
                "way_id": "way/a-b",
            },
            {"u": "b", "v": "c", "length_m": "10", "way_id": "way/b-c"},
        ],
        source="fixture.pbf",
    )
    server = create_server(output, data_root=data, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        params = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.002",
                "speed_kmh": "36",
                "objective": "duration",
                "departure": "2026-08-17T10:00:00+00:00",
                "include_path": "1",
            }
        )
        with urllib.request.urlopen(f"{base}/api/route?{params}", timeout=2) as response:
            assert response.status == 200
            route = json.load(response)
        assert route["reachable"] is True
        assert route["contract"] == ROUTE_CONTRACT
        assert route["objective"] == "duration"
        assert route["vehicle_weight_t"] is None
        assert route["vehicle_rating_t"] is None
        assert route["vehicle_height_m"] is None
        assert route["vehicle_width_m"] is None
        assert route["vehicle_length_m"] is None
        assert route["vehicle_axleload_t"] is None
        assert route["vehicle_class"] == "general"
        assert route["allow_hgv_destination"] is False
        assert route["route_distance_m"] == 20.0
        assert route["path_node_ids"] == ["a", "b", "c"]
        assert route["path_way_ids"] == ["way/a-b", "way/b-c"]
        assert route["path_segment_n"] == 2
        assert route["path_segment_source"] == "sqlite_edges"
        assert route["path_segment_total_distance_m"] == 20.0
        assert route["path_segment_total_duration_s"] == 2.0
        assert route["path_segment_total_wait_s"] == 0.0
        assert route["path_segments"] == [
            {
                "from_node": "a",
                "to_node": "b",
                "way_id": "way/a-b",
                "distance_m": 10.0,
                "duration_s": 1.0,
                "wait_s": 0.0,
                "ferry": False,
                "road_context": {
                    "name": None,
                    "ref": None,
                    "highway": None,
                    "route": None,
                    "oneway": None,
                },
                "constraints": [
                    {
                        "key": "hgv",
                        "value": "destination",
                        "unit": None,
                        "status": "supported",
                        "evaluated": False,
                        "profile": "vehicle_class=hgv + allow_hgv_destination",
                    }
                ],
                "conditional_rules": [],
                "transition_rules": [],
            },
            {
                "from_node": "b",
                "to_node": "c",
                "way_id": "way/b-c",
                "distance_m": 10.0,
                "duration_s": 1.0,
                "wait_s": 0.0,
                "ferry": False,
                "road_context": {
                    "name": None,
                    "ref": None,
                    "highway": None,
                    "route": None,
                    "oneway": None,
                },
                "constraints": [],
                "conditional_rules": [],
                "transition_rules": [],
            },
        ]
        assert route["speed_kmh"] == 36.0
        assert route["arrival"] == "2026-08-17T10:00:02+00:00"
        assert route["ferry_wait_s"] == 0.0
        assert route["ferry_wait_n"] == 0
        assert route["ferry_way_ids"] == []
        assert route["ferry_distance_m"] == 0.0
        assert route["ferry_crossing_s"] == 0.0
        assert route["ferry_edge_n"] == 0
        hgv_params = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.001",
                "vehicle_class": "hgv",
            }
        )
        with urllib.request.urlopen(f"{base}/api/route?{hgv_params}", timeout=2) as response:
            hgv_route = json.load(response)
        assert hgv_route["reachable"] is False
        assert hgv_route["allow_hgv_destination"] is False
        hgv_allowed_params = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.001",
                "vehicle_class": "hgv",
                "allow_hgv_destination": "true",
            }
        )
        with urllib.request.urlopen(
            f"{base}/api/route?{hgv_allowed_params}", timeout=2
        ) as response:
            hgv_allowed_route = json.load(response)
        assert hgv_allowed_route["reachable"] is True
        assert hgv_allowed_route["allow_hgv_destination"] is True
        geo_params = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.002",
                "format": "geojson",
            }
        )
        with urllib.request.urlopen(f"{base}/api/route?{geo_params}", timeout=2) as response:
            assert response.headers["Content-Type"].startswith("application/geo+json")
            feature = json.load(response)
        assert feature["type"] == "Feature"
        assert feature["contract"] == ROUTE_CONTRACT
        assert feature["properties"]["contract"] == ROUTE_CONTRACT
        assert feature["geometry"]["type"] == "LineString"
        assert feature["geometry"]["coordinates"] == [[-8.0, 53.0], [-8.001, 53.0], [-8.002, 53.0]]
        assert "path_way_ids" not in feature["properties"]
        assert "path_segments" not in feature["properties"]
        assert "path_segment_total_distance_m" not in feature["properties"]
        assert "path_segment_total_duration_s" not in feature["properties"]
        assert "path_segment_total_wait_s" not in feature["properties"]
        assert "path_segment_source" not in feature["properties"]
        invalid_params = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.002",
                "objective": "scenic",
            }
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/route?{invalid_params}", timeout=2)
        assert error.value.code == 400
        assert "objective must be one of" in json.loads(error.value.read())["error"]
        invalid_weight = urllib.parse.urlencode(
            {
                "start_lat": "53",
                "start_lon": "-8",
                "goal_lat": "53",
                "goal_lon": "-8.002",
                "weight_t": "0",
            }
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/route?{invalid_weight}", timeout=2)
        assert error.value.code == 400
        assert "weight_t must be a finite positive" in json.loads(error.value.read())["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_health_detects_jsonl_query_backend(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (tmp_path / "analysis_results.jsonl").write_text(
        '{"osm_id":"way/invalid-a","score":NaN}\n'
        '{"osm_id":"way/invalid-b","score":"oops"}\n',
        encoding="utf-8",
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["query_available"] is True
        assert health["query_backend_health"]["jsonl"]["readable"] is True
        assert health["query_readable_backends"] == ["jsonl"]
        with urllib.request.urlopen(f"{base}/api/query?backend=jsonl&limit=1", timeout=2) as response:
            payload = json.load(response)
        assert payload["rows"][0]["score"] is None
        assert payload["next_cursor"] == {"after_invalid_osm_id": "way/invalid-a"}
        next_cursor_params = urllib.parse.urlencode(payload["next_cursor"] | {"limit": 1})
        with urllib.request.urlopen(
            f"{base}/api/query?backend=jsonl&{next_cursor_params}", timeout=2
        ) as response:
            continuation = json.load(response)
        assert continuation["rows"][0]["osm_id"] == "way/invalid-b"
        assert continuation["next_cursor"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_health_rejects_unreadable_query_backend(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (tmp_path / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (tmp_path / "analysis_results.jsonl").write_text("not-json\n", encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["query_available"] is False
        assert health["query_readable_backends"] == []
        assert health["query_backend_health"]["jsonl"]["available"] is True
        assert health["query_backend_health"]["jsonl"]["readable"] is False
        assert "JSONDecodeError" in health["query_backend_health"]["jsonl"]["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_query_api_is_bounded_and_uses_query_cli_backends(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    (output / "report_data.json").write_text('{"targets": []}', encoding="utf-8")
    (output / "analysis_results.csv").write_text(
        "osm_id,group,is_control,score,name\n"
        "way/1,worship,0,91,Test Chapel\n"
        "way/2,worship,1,88,Control Hall\n"
        "way/3,civic,0,72,Civic Hall\n",
        encoding="utf-8",
    )
    server = create_server(output, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        params = urllib.parse.urlencode(
            {"group": "worship", "is_control": "target", "min_score": "80", "limit": "1"}
        )
        with urllib.request.urlopen(f"{base}/api/query?{params}", timeout=2) as response:
            assert response.status == 200
            payload = json.load(response)
        assert payload["status"] == "ok"
        assert payload["contract"] == QUERY_CONTRACT
        assert payload["backend"] == "csv"
        assert payload["count"] == 1
        assert payload["backend_fallbacks"] is None
        assert payload["offset"] == 0
        assert payload["has_more"] is False
        assert payload["next_offset"] is None
        assert payload["rows"][0]["osm_id"] == "way/1"
        assert payload["filters"]["is_control"] == "target"
        page_params = urllib.parse.urlencode({"limit": "1", "offset": "1"})
        with urllib.request.urlopen(f"{base}/api/query?{page_params}", timeout=2) as response:
            page = json.load(response)
        assert page["count"] == 1
        assert page["offset"] == 1
        assert page["has_more"] is True
        assert page["next_offset"] == 2
        assert page["rows"][0]["osm_id"] == "way/2"
        cursor_params = urllib.parse.urlencode(
            {"after_score": "91", "after_osm_id": "way/1", "limit": "1"}
        )
        with urllib.request.urlopen(f"{base}/api/query?{cursor_params}", timeout=2) as response:
            cursor_page = json.load(response)
        assert cursor_page["offset"] == 0
        assert cursor_page["has_more"] is True
        assert cursor_page["next_offset"] is None
        assert cursor_page["cursor"] == {"after_score": 91.0, "after_osm_id": "way/1"}
        assert cursor_page["next_cursor"] == {"after_score": 88.0, "after_osm_id": "way/2"}
        next_cursor_params = urllib.parse.urlencode(cursor_page["next_cursor"] | {"limit": 1})
        with urllib.request.urlopen(
            f"{base}/api/query?{next_cursor_params}", timeout=2
        ) as response:
            next_cursor_page = json.load(response)
        assert next_cursor_page["has_more"] is False
        assert next_cursor_page["next_cursor"] is None
        assert next_cursor_page["rows"][0]["osm_id"] == "way/3"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/query?limit=1001", timeout=2)
        assert error.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/query?offset=10000001", timeout=2)
        assert error.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/query?min_score=nan", timeout=2)
        assert error.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/query?after_score=91", timeout=2)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_rejects_missing_or_outside_report(tmp_path):
    (tmp_path / "report.html").write_text("ok", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        create_server(tmp_path, report="report_lazy.html")
    with pytest.raises(ValueError):
        create_server(tmp_path, report="../secret.html")


def test_report_server_rejects_output_symlinks(tmp_path):
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    (real_output / "report_lazy.html").write_text("ok", encoding="utf-8")
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        create_server(linked_output)

    (real_output / "linked.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError, match="symlink"):
        create_server(real_output)

    (real_output / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        create_server(real_output)


def test_report_server_rejects_data_and_graph_symlinks(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "report_lazy.html").write_text("ok", encoding="utf-8")

    data = tmp_path / "data"
    data.mkdir()
    external_data = tmp_path / "external-data.json"
    external_data.write_text("{}", encoding="utf-8")
    linked_data = data / "combined.json"
    try:
        linked_data.symlink_to(external_data)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    with pytest.raises(ValueError, match="Data directory contains symlink"):
        create_server(output, data_root=data)

    clean_data = tmp_path / "clean-data"
    clean_data.mkdir()
    external_graph = tmp_path / "external-graph"
    external_graph.mkdir()
    linked_graph = tmp_path / "linked-graph"
    linked_graph.symlink_to(external_graph, target_is_directory=True)
    with pytest.raises(ValueError, match="Road graph input must not be a symlink"):
        create_server(output, data_root=clean_data, road_graph=linked_graph)


def test_report_server_rejects_file_output_root(tmp_path):
    output = tmp_path / "file-output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="Output directory must be a directory"):
        create_server(output)


def test_report_server_blocks_static_symlinks_added_after_start(tmp_path):
    (tmp_path / "report_lazy.html").write_text("ok", encoding="utf-8")
    server = create_server(tmp_path, port=0)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "leak.txt").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "report_data.json").symlink_to(tmp_path / "outside.txt")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/leak.txt", timeout=2)
        assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/report_data.json", timeout=2)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_report_server_allows_full_embedded_report(tmp_path):
    (tmp_path / "report.html").write_text("ok", encoding="utf-8")
    server = create_server(tmp_path, port=0, report="report.html")
    try:
        assert report_url(server, server.report_name).endswith("/report.html")
    finally:
        server.server_close()


def test_report_server_pages_and_exports_filtered_targets(tmp_path):
    (tmp_path / "report_lazy.html").write_text("<html>lazy</html>", encoding="utf-8")
    targets = [
        {
            "osm_id": "way/1",
            "name": "Alpha Chapel",
            "group": "worship",
            "subtype": "chapel",
            "address_city": "Dublin",
            "score": 91,
            "area_m2": 120,
            "aspect_ratio": 1.2,
            "convexity": 0.9,
            "circularity": 0.8,
            "flags": ["circular"],
            "has_golden_angle": 1,
            "has_golden_ratio": 1,
            "multipart": 0,
            "repaired": 0,
            "niah": {"reg_no": "R1", "name": "Alpha", "county": "Dublin", "type": "church", "century": "18th", "rating": "Regional"},
            "history": {"status": "", "architect": ""},
            "review": {"in_queue": True, "label": "supportive"},
        },
        {
            "osm_id": "way/2",
            "name": "Beta Hall",
            "group": "other",
            "subtype": "hall",
            "address_city": "Cork",
            "score": 20,
            "area_m2": 80,
            "aspect_ratio": 2.0,
            "convexity": 0.7,
            "circularity": 0.4,
            "flags": [],
            "has_golden_angle": 0,
            "has_golden_ratio": 0,
            "multipart": 1,
            "repaired": 0,
            "niah": {"reg_no": "", "name": "", "county": "Cork", "type": "", "century": "", "rating": ""},
            "history": {"status": "", "architect": ""},
            "review": {"in_queue": False},
        },
    ]
    (tmp_path / "report_data.json").write_text(
        json.dumps(
            {
                "targets": targets,
                "summary": {"targets": 2},
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "geometry": None, "properties": {"osm_id": "way/1"}},
                        {"type": "Feature", "geometry": None, "properties": {"osm_id": "way/2"}},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        page_url = f"{base}{REPORT_PAGE_API_PATH}?initial=1&limit=1&group=worship&score=80&pattern=circular"
        with urllib.request.urlopen(page_url, timeout=2) as response:
            page = json.load(response)
        assert page["contract"] == REPORT_PAGE_CONTRACT
        assert page["initial"] is True
        assert page["filters"]["pattern"] == "circular"
        assert page["page"] == {
            "limit": 1,
            "offset": 0,
            "count": 1,
            "total": 1,
            "has_more": False,
            "matching_golden_angle": 1,
            "matching_niah": 1,
        }
        assert [row["osm_id"] for row in page["targets"]] == ["way/1"]
        assert page["summary"]["targets"] == 2
        assert page["summary"]["analysis_ready"] is False
        assert page["summary"]["manifest_alignment"]["status"] == "not_provided"
        assert page["runtime"] == {
            "contract": REPORT_RUNTIME_CONTRACT,
            "status": "incomplete",
            "analysis_ready": False,
            "validation": {
                "status": "not_provided",
                "passed": False,
                "manifest_available": False,
                "records": {
                    name: {
                        "available": False,
                        "status": "not_provided",
                        "passed": False,
                        "error": None,
                    }
                    for name in ("verification", "schema_validation", "reproducibility")
                },
            },
            "manifest_alignment": {
                "contract": "ireland-geometry.manifest-alignment.v1",
                "status": "not_provided",
                "passed": False,
                "checked_count": 0,
                "manifest_artifact_count": 0,
                "actual_file_count": 0,
                "symlink_count": 0,
                "unlisted_count": 0,
                "errors": ["manifest.json is unavailable"],
            },
            "source_alignment": {
                "contract": "ireland-geometry.source-alignment.v1",
                "status": "not_provided",
                "passed": False,
                "mode": "metadata",
                "checked_count": 0,
                "unavailable_count": 0,
                "unhashed_count": 0,
                "hash_checked_count": 0,
                "metadata_checked_count": 0,
                "symlink_count": 0,
                "errors": [],
                "sources": [],
            },
            "snapshot": {
                "available": False,
                "generated_at": None,
                "git_revision": None,
                "git_dirty": None,
                "package_version": None,
                "schema_version": None,
                "manifest_sha256": None,
                "error": "manifest.json is unavailable",
            },
        }
        with urllib.request.urlopen(
            f"{base}{REPORT_EXPORT_API_PATH}?format=csv&group=worship&score=80", timeout=2
        ) as response:
            csv_body = response.read().decode("utf-8")
            csv_etag = response.headers["ETag"]
            assert response.headers["Content-Disposition"].endswith("ireland-geometry-filtered.csv\"")
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["contract"]] == REPORT_RUNTIME_CONTRACT
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["status"]] == "incomplete"
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["analysis_ready"]] == "false"
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["validation"]] == "not_provided"
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["manifest_alignment"]] == "not_provided"
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["source_alignment"]] == "not_provided"
        assert csv_body.splitlines()[0].startswith("osm_id,name,group")
        assert "way/1" in csv_body and "way/2" not in csv_body
        conditional = urllib.request.Request(
            f"{base}{REPORT_EXPORT_API_PATH}?format=csv&group=worship&score=80",
            headers={"If-None-Match": csv_etag},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(conditional, timeout=2)
        assert error.value.code == 304
        assert error.value.headers[REPORT_RUNTIME_HEADER_NAMES["status"]] == "incomplete"
        with urllib.request.urlopen(
            f"{base}{REPORT_EXPORT_API_PATH}?format=geojson&group=worship", timeout=2
        ) as response:
            geojson = json.load(response)
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["status"]] == "incomplete"
            assert response.headers[REPORT_RUNTIME_HEADER_NAMES["analysis_ready"]] == "false"
        assert geojson["contract"] == REPORT_EXPORT_CONTRACT
        assert [feature["properties"]["osm_id"] for feature in geojson["features"]] == ["way/1"]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}{REPORT_PAGE_API_PATH}?limit=101", timeout=2)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
