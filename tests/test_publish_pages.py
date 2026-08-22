from __future__ import annotations

import hashlib
import json

import pytest

from scripts.pages_audit import audit_site
from scripts.publish_pages import PublishError, publish_pages


def write_source(tmp_path, *, verified: bool = True):
    output = tmp_path / "output"
    output.mkdir()
    targets = [{"osm_id": "way/1"}]
    pack = {
        "targets": targets,
        "summary": {"targets": 1, "review_queue_targets": 1, "analysis_ready": True},
        "geojson": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"osm_id": "way/1"}}],
        },
        "manifest": {
            "generated_at": "2026-08-18T00:00:00+00:00",
            "git_revision": "abc123",
        },
        "pattern_catalog": [{"key": "golden_ratio"}],
        "scoring": {
            "contract": "ireland-geometry.exploratory-score.v1",
            "version": 1,
            "config_sha256": "a" * 64,
        },
        "interpretation": {"caveats": []},
    }
    (output / "report.html").write_text(
        "<script>const PACK = " + json.dumps(pack) + ";</script>", encoding="utf-8"
    )
    (output / "review.html").write_text(
        "<script>const DATA=[{\"osm_id\":\"way/1\"}];const TARGET_ID='';"
        "const QUEUE_KEY='queue';const BACKUP_SCHEMA='ireland-geometry-review-v1';"
        "localStorage;function downloadJson(){}async function importJson(){}</script>",
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-18T00:00:00+00:00",
                "git_revision": "abc123",
            }
        ),
        encoding="utf-8",
    )
    (output / "verification.json").write_text(
        json.dumps(
            {
                "passed": verified,
                "analysis_rows": 1,
                "target_rows": 1,
                "manifest_cache_alignment": {
                    "contract": "ireland-geometry.manifest-cache.v1",
                    "status": "pass" if verified else "fail",
                    "check_count": 1,
                    "mismatches": [],
                    "unavailable_stages": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (output / "schema_validation.json").write_text(
        json.dumps({"passed": verified, "schema_version": 1}), encoding="utf-8"
    )
    (output / "reproducibility.json").write_text(
        json.dumps({"passed": verified, "artifact_count": 1}), encoding="utf-8"
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = []
    for name in (
        "report.html",
        "review.html",
        "verification.json",
        "schema_validation.json",
        "reproducibility.json",
    ):
        path = output / name
        manifest["artifacts"].append(
            {
                "relative_path": name,
                "path_base": "output_dir",
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return output


def test_publish_pages_copies_verified_output_and_records_provenance(tmp_path):
    output = write_source(tmp_path)
    site = tmp_path / "docs"

    result = publish_pages(output, site)

    assert result["published"] is True
    assert result["contract"] == "ireland-geometry.pages-publish.v1"
    assert result["audit"]["passed"] is True
    assert result["source_manifest_generated_at"] == "2026-08-18T00:00:00+00:00"
    assert result["source_output_manifest_generated_at"] == "2026-08-18T00:00:00+00:00"
    publication = json.loads((site / "pages_manifest.json").read_text(encoding="utf-8"))
    assert publication["source_manifest_revision"] == "abc123"
    assert publication["source_verification"]["manifest_cache_alignment"]["status"] == "pass"
    assert all(
        record["status"] == "pass" and record["passed"] is True
        for record in publication["source_validation"].values()
    )
    assert audit_site(site)["passed"] is True


def test_publish_pages_rejects_site_inside_output(tmp_path):
    output = write_source(tmp_path)

    with pytest.raises(PublishError, match="site directory must be outside"):
        publish_pages(output, output / "docs")


def test_publish_pages_rejects_symlinked_roots(tmp_path):
    output = write_source(tmp_path)
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(output, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(PublishError, match="output directory must not be a symlink"):
        publish_pages(linked_output, tmp_path / "docs")

    site_target = tmp_path / "site-target"
    site_target.mkdir()
    linked_site = tmp_path / "linked-site"
    try:
        linked_site.symlink_to(site_target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(PublishError, match="site directory must not be a symlink"):
        publish_pages(output, linked_site)


def test_publish_pages_rejects_file_roots(tmp_path):
    output = write_source(tmp_path)
    file_output = tmp_path / "file-output"
    file_output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PublishError, match="output directory must be a directory"):
        publish_pages(file_output, tmp_path / "docs")

    file_site = tmp_path / "file-site"
    file_site.write_text("not a directory", encoding="utf-8")
    with pytest.raises(PublishError, match="site directory must be a directory"):
        publish_pages(output, file_site)


def test_publish_pages_refuses_failed_source_verification(tmp_path):
    output = write_source(tmp_path, verified=False)

    with pytest.raises(PublishError, match="verification.json does not pass"):
        publish_pages(output, tmp_path / "docs")


def test_publish_pages_refuses_failed_schema_validation(tmp_path):
    output = write_source(tmp_path)
    (output / "schema_validation.json").write_text(
        json.dumps({"passed": False}), encoding="utf-8"
    )

    with pytest.raises(PublishError, match="schema_validation.json does not pass"):
        publish_pages(output, tmp_path / "docs")


def test_publish_pages_refuses_stale_source_artifact(tmp_path):
    output = write_source(tmp_path)
    with (output / "report.html").open("a", encoding="utf-8") as handle:
        handle.write("\n<!-- changed after verification -->\n")

    with pytest.raises(PublishError, match="report.html byte count differs from manifest"):
        publish_pages(output, tmp_path / "docs")


def test_publish_pages_refuses_duplicate_source_artifact_records(tmp_path):
    output = write_source(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].append(dict(manifest["artifacts"][0]))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PublishError, match="duplicate artifact record for report.html"):
        publish_pages(output, tmp_path / "docs")


def test_publish_pages_preserves_existing_site_when_candidate_audit_fails(tmp_path):
    output = write_source(tmp_path)
    (output / "report.html").write_text(
        "<script>const PACK = "
        + json.dumps({"manifest": {"generated_at": "2026-08-18T00:00:00+00:00", "git_revision": "abc123"}})
        + ";</script>",
        encoding="utf-8",
    )
    site = tmp_path / "docs"
    site.mkdir()
    (site / "index.html").write_text("previous site", encoding="utf-8")

    with pytest.raises(PublishError, match="published site failed audit"):
        publish_pages(output, site)

    assert (site / "index.html").read_text(encoding="utf-8") == "previous site"
    assert not (site / "pages_manifest.json").exists()
