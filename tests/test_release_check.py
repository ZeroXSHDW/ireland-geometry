from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import release_check
from scripts.bundle import build_bundle
from scripts.runtime import path_modified_at, sha256_path


def _doctor_snapshot(root: Path, *, strict_ready: bool = True) -> dict:
    return {
        "project_root": str(root.resolve()),
        "summary": {
            "strict_ready": strict_ready,
            "status": "pass" if strict_ready else "warning",
            "missing_required_inputs": 0,
            "missing_required_outputs": 0,
            "stale_required_inputs": 0,
        },
        "git": {"revision": "abc123", "dirty": not strict_ready},
        "capabilities": {"validation": {"status": "pass" if strict_ready else "incomplete"}},
    }


def _write_current_publication(root: Path) -> dict:
    output = root / "output"
    site = root / "docs"
    output.mkdir(parents=True)
    site.mkdir()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "git_revision": "abc123",
                "generated_at": "2026-08-18T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    for name, payload in {
        "verification.json": {"passed": True},
        "schema_validation.json": {"passed": True},
        "reproducibility.json": {"passed": True},
    }.items():
        (output / name).write_text(json.dumps(payload), encoding="utf-8")
    (output / "report.html").write_text("report", encoding="utf-8")
    (output / "review.html").write_text("review", encoding="utf-8")
    (site / "index.html").write_text("report", encoding="utf-8")
    (site / "review.html").write_text("review", encoding="utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = [
        {
            "relative_path": name,
            "path_base": "output_dir",
            "bytes": (output / name).stat().st_size,
            "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
        }
        for name in (
            "report.html",
            "review.html",
            "verification.json",
            "schema_validation.json",
            "reproducibility.json",
        )
    ]
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    publication = {
        "available": True,
        "source_revision": "abc123",
        "source_output_manifest_generated_at": "2026-08-18T00:00:00+00:00",
        "source_manifest_sha256": hashlib.sha256(
            (output / "manifest.json").read_bytes()
        ).hexdigest(),
        "source_verification_sha256": hashlib.sha256(
            (output / "verification.json").read_bytes()
        ).hexdigest(),
        "source_validation": {
            key: {
                "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
            }
            for key, name in {
                "schema_validation": "schema_validation.json",
                "reproducibility": "reproducibility.json",
            }.items()
        },
    }
    return publication


def test_release_check_can_pass_without_optional_publications(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "analysis_results.csv"
    artifact.write_text("osm_id\nway/1\n", encoding="utf-8")
    validation = {}
    for name in release_check.REQUIRED_OUTPUT_ARTIFACTS:
        path = output / name
        path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        validation[name] = {
            "relative_path": name,
            "path_base": "output_dir",
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "relative_path": "analysis_results.csv",
                        "path_base": "output_dir",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    *validation.values(),
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )

    result = release_check.check_release(tmp_path, skip_pages=True)

    assert result["contract"] == "ireland-geometry.release-check.v1"
    assert result["passed"] is True
    assert result["doctor"]["passed"] is True
    assert result["pages"]["status"] == "skipped"
    assert result["bundle"]["status"] == "not_provided"
    assert result["output"]["required_artifact_count"] == 3

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = manifest["artifacts"][:1]
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    incomplete = release_check.check_release(tmp_path, skip_pages=True)
    assert incomplete["passed"] is False
    assert incomplete["output"]["required_artifact_count"] == 0
    assert any("required validation artifact record" in error for error in incomplete["errors"])


def test_release_check_rejects_unlisted_current_output_artifact(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "analysis_results.csv"
    artifact.write_text("osm_id\nway/1\n", encoding="utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "relative_path": "analysis_results.csv",
                        "path_base": "output_dir",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )

    (output / "unexpected.txt").write_text("not in the manifest", encoding="utf-8")
    result = release_check.check_release(tmp_path, skip_pages=True)

    assert result["passed"] is False
    assert result["output"]["unlisted_count"] == 1
    assert any("unlisted artifact: unexpected.txt" in error for error in result["errors"])


@pytest.mark.parametrize("symlink_name", ("manifest.json", "doctor.json"))
def test_release_check_rejects_symlinked_output_exceptions(tmp_path, monkeypatch, symlink_name):
    output = tmp_path / "output"
    output.mkdir()
    artifacts = []
    for name in release_check.REQUIRED_OUTPUT_ARTIFACTS:
        path = output / name
        path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        artifacts.append(
            {
                "relative_path": name,
                "path_base": "output_dir",
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {"artifacts": artifacts}
    if symlink_name == "manifest.json":
        target = tmp_path / "manifest-target.json"
        target.write_text(json.dumps(manifest), encoding="utf-8")
        (output / symlink_name).symlink_to(target)
    else:
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        target = tmp_path / "doctor-target.json"
        target.write_text(json.dumps({"diagnostic": True}), encoding="utf-8")
        (output / symlink_name).symlink_to(target)
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )

    result = release_check.check_release(tmp_path, skip_pages=True)

    assert result["passed"] is False
    assert result["output"]["symlink_count"] == 1
    assert any(
        f"current output contains a symlink: {symlink_name}" in error
        for error in result["errors"]
    )


def test_release_check_rejects_symlinked_publication_roots(tmp_path, monkeypatch):
    _write_current_publication(tmp_path)
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )
    linked_output = tmp_path / "output-link"
    linked_site = tmp_path / "docs-link"
    try:
        linked_output.symlink_to(tmp_path / "output", target_is_directory=True)
        linked_site.symlink_to(tmp_path / "docs", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    output_result = release_check.check_release(
        tmp_path,
        out_dir=linked_output,
        skip_pages=True,
    )
    assert output_result["passed"] is False
    assert output_result["path_boundary"]["passed"] is False
    assert any("output directory must not be a symlink" in error for error in output_result["errors"])

    site_result = release_check.check_release(
        tmp_path,
        site_dir=linked_site,
        require_pages=True,
    )
    assert site_result["passed"] is False
    assert site_result["path_boundary"]["passed"] is False
    assert any("Pages site directory must not be a symlink" in error for error in site_result["errors"])


def test_release_check_rejects_file_publication_roots(tmp_path, monkeypatch):
    _write_current_publication(tmp_path)
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )
    file_output = tmp_path / "file-output"
    file_output.write_text("not a directory", encoding="utf-8")
    output_result = release_check.check_release(
        tmp_path,
        out_dir=file_output,
        skip_pages=True,
    )
    assert output_result["passed"] is False
    assert output_result["path_boundary"]["passed"] is False
    assert any("output directory must be a directory" in error for error in output_result["errors"])

    file_site = tmp_path / "file-site"
    file_site.write_text("not a directory", encoding="utf-8")
    site_result = release_check.check_release(
        tmp_path,
        site_dir=file_site,
        require_pages=True,
    )
    assert site_result["passed"] is False
    assert site_result["path_boundary"]["passed"] is False
    assert any("Pages site directory must be a directory" in error for error in site_result["errors"])


def test_release_check_requires_pages_to_match_current_output(tmp_path, monkeypatch):
    publication = _write_current_publication(tmp_path)
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )
    monkeypatch.setattr(
        release_check,
        "audit_site",
        lambda _site: {
            "contract": "ireland-geometry.pages-audit.v1",
            "passed": True,
            "errors": [],
            "pages": {"publication": publication},
        },
    )

    result = release_check.check_release(tmp_path, require_pages=True)

    assert result["passed"] is True
    assert result["pages"]["alignment"]["status"] == "pass"

    (tmp_path / "docs" / "index.html").write_text("stale", encoding="utf-8")
    stale = release_check.check_release(tmp_path, require_pages=True)
    assert stale["passed"] is False
    assert any("published page is stale" in error for error in stale["errors"])


def test_release_check_requires_complete_verified_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )
    output = tmp_path / "output"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "path_contract": {
                    "version": 1,
                    "relative_path_field": "relative_path",
                    "artifact_base": "output_dir",
                    "source_base": "project_root",
                }
            }
        ),
        encoding="utf-8",
    )
    (output / "verification.json").write_text(
        json.dumps({"passed": True, "analysis_rows": 1}), encoding="utf-8"
    )
    for name in ("schema_validation.json", "reproducibility.json"):
        (output / name).write_text(
            json.dumps({"passed": True, "artifact_count": 1}), encoding="utf-8"
    )
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = [
        {
            "relative_path": name,
            "path_base": "output_dir",
            "bytes": (output / name).stat().st_size,
            "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
        }
        for name in (
            "analysis_results.csv",
            "verification.json",
            "schema_validation.json",
            "reproducibility.json",
        )
    ]
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "release.bundle.zip"
    build_bundle(output, archive, require_verified=True)

    result = release_check.check_release(
        tmp_path,
        skip_pages=True,
        bundle_path=archive,
    )

    assert result["passed"] is True
    assert result["bundle"]["verification"]["require_verified"] is True
    assert result["bundle"]["alignment"]["status"] == "pass"

    (output / "verification.json").write_text(
        json.dumps({"passed": True, "analysis_rows": 2}), encoding="utf-8"
    )
    stale = release_check.check_release(
        tmp_path,
        skip_pages=True,
        bundle_path=archive,
    )
    assert stale["passed"] is False
    assert any("bundle source hash does not match current output" in error for error in stale["errors"])


def test_release_check_strict_mode_rejects_unready_doctor(tmp_path, monkeypatch):
    snapshot = _doctor_snapshot(tmp_path, strict_ready=False)
    snapshot["summary"]["strict_blockers"] = [
        {
            "code": "git_dirty",
            "message": "The working tree contains 2 changed path(s) (1 tracked, 1 untracked).",
        }
    ]
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: snapshot,
    )

    result = release_check.check_release(tmp_path, skip_pages=True)

    assert any("Doctor gate [git_dirty]" in error for error in result["errors"])

    with pytest.raises(SystemExit):
        release_check.main(["--strict", "--skip-pages", "--project-root", str(tmp_path)])


def test_release_check_can_deep_check_input_source_hashes(tmp_path, monkeypatch):
    _write_current_publication(tmp_path)
    source = tmp_path / "data" / "source.bin"
    source.parent.mkdir()
    source.write_bytes(b"original")
    manifest_path = tmp_path / "output" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_root"] = str(tmp_path)
    manifest["sources"] = [
        {
            "source_kind": "required_source",
            "path": str(source),
            "path_base": "project_root",
            "relative_path": "data/source.bin",
            "sha256": sha256_path(source),
            "bytes": source.stat().st_size,
            "modified_at": path_modified_at(source),
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        release_check,
        "inspect_project",
        lambda *args, **kwargs: _doctor_snapshot(tmp_path),
    )

    passed = release_check.check_release(
        tmp_path,
        skip_pages=True,
        check_input_hashes=True,
    )

    assert passed["passed"] is True
    assert passed["source_alignment"]["mode"] == "hash"
    source.write_bytes(b"tampered")
    failed = release_check.check_release(
        tmp_path,
        skip_pages=True,
        check_input_hashes=True,
    )
    assert failed["passed"] is False
    assert failed["source_alignment"]["status"] == "fail"
    assert any("Source gate: manifest source hash mismatch" in error for error in failed["errors"])
