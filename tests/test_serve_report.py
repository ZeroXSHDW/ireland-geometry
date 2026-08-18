import gzip
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from scripts.road_routing import write_sqlite_graph
from scripts.runtime import package_version, runtime_signature
from scripts.serve_report import (
    CAPABILITIES_API_PATH,
    CAPABILITIES_CONTRACT,
    HEALTH_CONTRACT,
    INTERPRETATION_API_PATH,
    INTERPRETATION_CONTRACT,
    METADATA_CONTRACT,
    OPENAPI_CONTRACT,
    OPENAPI_PATH,
    QUERY_CONTRACT,
    REPORT_EXPORT_API_PATH,
    REPORT_EXPORT_CONTRACT,
    REPORT_PAGE_API_PATH,
    REPORT_PAGE_CONTRACT,
    ROUTE_API_PATH,
    ROUTE_CONTRACT,
    create_server,
    report_url,
)


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
        assert capabilities["validation"]["status"] == "not_provided"
        assert capabilities["validation"]["passed"] is False
        assert capabilities["validation"]["manifest_available"] is False
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
        }
        assert capabilities["endpoints"]["query"]["contract"] == QUERY_CONTRACT
        assert capabilities["endpoints"]["query"]["readable_backends"] == ["csv"]
        assert capabilities["endpoints"]["route"] == {
            "path": ROUTE_API_PATH,
            "method": "GET",
            "contract": ROUTE_CONTRACT,
            "available": False,
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
        assert "ValidationStatus" in document["components"]["schemas"]
        assert "SourceFreshness" in document["components"]["schemas"]
        freshness_schema = document["components"]["schemas"]["SourceFreshness"]
        assert (
            freshness_schema["properties"]["contract"]["const"]
            == "ireland-geometry.freshness.v1"
        )
        assert set(freshness_schema["required"]) == {"contract", "observed_at", "sources"}
        assert "source_freshness" in document["components"]["schemas"]["MetadataResponse"]["required"]
        assert "source_freshness" in document["components"]["schemas"]["CapabilitiesResponse"]["required"]
        assert set(document["paths"]) == {
            "/__health",
            "/api/capabilities",
            "/api/openapi.json",
            "/api/metadata",
            "/api/interpretation",
            "/api/query",
            "/api/route",
            REPORT_PAGE_API_PATH,
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
        assert {"start_lat", "start_lon", "goal_lat", "goal_lon", "format"} <= route_parameters
        report_parameters = {
            item["name"] for item in document["paths"][REPORT_PAGE_API_PATH]["get"]["parameters"]
        }
        assert {"q", "score", "limit", "offset", "initial", "sort"} <= report_parameters
        assert document["paths"][REPORT_EXPORT_API_PATH]["get"]["operationId"] == "exportFilteredReport"
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
        assert metadata["status"] == "ok"
        assert metadata["contract"] == METADATA_CONTRACT
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
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = report_url(server, server.report_name).rsplit("/", 1)[0]
        with urllib.request.urlopen(f"{base}/__health", timeout=2) as response:
            health = json.load(response)
        assert health["ready"] is True
        assert health["analysis_ready"] is True
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
            {"u": "a", "v": "b", "length_m": "10"},
            {"u": "b", "v": "c", "length_m": "10"},
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
                "departure": "2026-08-17T10:00:00+00:00",
                "include_path": "1",
            }
        )
        with urllib.request.urlopen(f"{base}/api/route?{params}", timeout=2) as response:
            assert response.status == 200
            route = json.load(response)
        assert route["reachable"] is True
        assert route["contract"] == ROUTE_CONTRACT
        assert route["route_distance_m"] == 20.0
        assert route["path_node_ids"] == ["a", "b", "c"]
        assert route["speed_kmh"] == 36.0
        assert route["arrival"] == "2026-08-17T10:00:02+00:00"
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
        assert page["summary"] == {"targets": 2}
        with urllib.request.urlopen(
            f"{base}{REPORT_EXPORT_API_PATH}?format=csv&group=worship&score=80", timeout=2
        ) as response:
            csv_body = response.read().decode("utf-8")
            assert response.headers["Content-Disposition"].endswith("ireland-geometry-filtered.csv\"")
        assert csv_body.splitlines()[0].startswith("osm_id,name,group")
        assert "way/1" in csv_body and "way/2" not in csv_body
        with urllib.request.urlopen(
            f"{base}{REPORT_EXPORT_API_PATH}?format=geojson&group=worship", timeout=2
        ) as response:
            geojson = json.load(response)
        assert geojson["contract"] == REPORT_EXPORT_CONTRACT
        assert [feature["properties"]["osm_id"] for feature in geojson["features"]] == ["way/1"]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}{REPORT_PAGE_API_PATH}?limit=101", timeout=2)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
