from __future__ import annotations

import hashlib
import json

import pytest

from scripts.pages_audit import audit_site


def write_site(tmp_path, *, summary_targets: int = 2, review_ids: list[str] | None = None) -> None:
    review_ids = review_ids if review_ids is not None else ["way/1"]
    target_ids = ["way/1", "way/2"]
    targets = [{"osm_id": osm_id} for osm_id in target_ids]
    pack = {
        "targets": targets,
        "summary": {
            "targets": summary_targets,
            "review_queue_targets": len(review_ids),
            "analysis_ready": True,
        },
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"osm_id": osm_id}}
                for osm_id in target_ids
            ],
        },
        "manifest": {"generated_at": "2026-08-18T00:00:00+00:00", "git_revision": "abc123"},
        "pattern_catalog": [{"key": "golden_ratio"}],
        "scoring": {
            "contract": "ireland-geometry.exploratory-score.v1",
            "version": 1,
            "config_sha256": "a" * 64,
        },
        "interpretation": {"caveats": []},
    }
    review = [{"osm_id": osm_id} for osm_id in review_ids]
    site = tmp_path / "docs"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><script>const PACK = "
        + json.dumps(pack)
        + ";</script>",
        encoding="utf-8",
    )
    (site / "review.html").write_text(
        "<script>const DATA="
        + json.dumps(review)
        + ";const TARGET_ID='';const QUEUE_KEY='queue';"
        "const BACKUP_SCHEMA='ireland-geometry-review-v1';"
        "localStorage;function downloadJson(){}async function importJson(){}</script>",
        encoding="utf-8",
    )
    (site / "report.html").write_text(
        '<meta http-equiv="refresh" content="0; url=./">', encoding="utf-8"
    )
    files = {}
    for name in ("index.html", "review.html", "report.html"):
        path = site / name
        files[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    (site / "pages_manifest.json").write_text(
        json.dumps(
            {
                "contract": "ireland-geometry.pages-publish.v1",
                "version": 1,
                "source_manifest_revision": "abc123",
                "source_manifest_generated_at": "2026-08-18T00:00:00+00:00",
                "source_output_manifest_generated_at": "2026-08-18T00:00:00+00:00",
                "source_manifest_sha256": "a" * 64,
                "source_verification_sha256": "b" * 64,
                "source_verification": {
                    "passed": True,
                    "manifest_cache_alignment": {
                        "contract": "ireland-geometry.manifest-cache.v1",
                        "status": "pass",
                    },
                },
                "source_validation": {
                    "schema_validation": {
                        "path": "schema_validation.json",
                        "sha256": "c" * 64,
                        "status": "pass",
                        "passed": True,
                    },
                    "reproducibility": {
                        "path": "reproducibility.json",
                        "sha256": "d" * 64,
                        "status": "pass",
                        "passed": True,
                    },
                },
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def test_pages_audit_accepts_aligned_embedded_site(tmp_path):
    write_site(tmp_path)

    result = audit_site(tmp_path / "docs")

    assert result["contract"] == "ireland-geometry.pages-audit.v1"
    assert result["passed"] is True
    assert result["errors"] == []
    assert result["pages"]["index"]["target_count"] == 2
    assert result["pages"]["review"]["queue_count"] == 1
    assert (
        result["pages"]["publication"]["source_output_manifest_generated_at"]
        == "2026-08-18T00:00:00+00:00"
    )
    assert result["pages"]["publication"]["source_validation"]["schema_validation"]["status"] == "pass"


def test_pages_audit_rejects_count_and_scope_mismatch(tmp_path):
    write_site(tmp_path, summary_targets=1, review_ids=["way/999"])

    result = audit_site(tmp_path / "docs")

    assert result["passed"] is False
    assert any("summary targets" in error for error in result["errors"])
    assert any("outside the dashboard target set" in error for error in result["errors"])


def test_pages_audit_rejects_invalid_source_hash(tmp_path):
    write_site(tmp_path)
    manifest_path = tmp_path / "docs" / "pages_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest_sha256"] = "not-a-sha256"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_site(tmp_path / "docs")

    assert result["passed"] is False
    assert any("invalid source_manifest_sha256" in error for error in result["errors"])


def test_pages_audit_rejects_symlinked_page(tmp_path):
    write_site(tmp_path)
    site = tmp_path / "docs"
    (site / "index.html").unlink()
    (site / "index.html").symlink_to("review.html")

    result = audit_site(site)

    assert result["passed"] is False
    assert any("dashboard page must not be a symlink" in error for error in result["errors"])


def test_pages_audit_rejects_symlinked_site_root(tmp_path):
    write_site(tmp_path)
    linked_site = tmp_path / "docs-link"
    try:
        linked_site.symlink_to(tmp_path / "docs", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    result = audit_site(linked_site)

    assert result["passed"] is False
    assert any("Pages site directory must not be a symlink" in error for error in result["errors"])


def test_pages_audit_rejects_file_site_root(tmp_path):
    site = tmp_path / "file-site"
    site.write_text("not a directory", encoding="utf-8")

    result = audit_site(site)

    assert result["passed"] is False
    assert any("Pages site directory must be a directory" in error for error in result["errors"])
