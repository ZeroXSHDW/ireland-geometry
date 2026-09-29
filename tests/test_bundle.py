import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts.bundle import BUNDLE_CONTRACT, build_bundle, extract_bundle, main, verify_bundle


def _write_portable_manifest(output):
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


def _write_passing_validation_records(output):
    (output / "schema_validation.json").write_text(
        json.dumps({"passed": True, "schema_version": 1}), encoding="utf-8"
    )
    (output / "reproducibility.json").write_text(
        json.dumps({"passed": True, "artifact_count": 1}), encoding="utf-8"
    )


def _add_manifest_artifacts(output):
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = []
    for name in (
        "analysis_results.csv",
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
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_bundle_is_deterministic_and_records_portable_artifact_hashes(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id,score\nway/1,91\n", encoding="utf-8")
    (output / "doctor.json").write_text('{"checked_at":"now"}', encoding="utf-8")
    (output / "stage_cache.json").write_text('{"version":4}', encoding="utf-8")

    first = build_bundle(output, tmp_path / "first.zip")
    build_bundle(output, tmp_path / "second.zip")

    assert (tmp_path / "first.zip").read_bytes() == (tmp_path / "second.zip").read_bytes()
    assert first["contract"] == BUNDLE_CONTRACT
    assert first["manifest_path_contract"] == {"status": "portable", "version": 1}
    assert first["source_manifest"]["included"] is True
    assert first["source_verification"]["status"] == "not_provided"
    assert first["artifact_count"] == 2
    assert first["archive_bytes"] == (tmp_path / "first.zip").stat().st_size
    verified = verify_bundle(tmp_path / "first.zip")
    assert verified["passed"] is True
    assert verified["artifact_count"] == first["artifact_count"]
    with zipfile.ZipFile(tmp_path / "first.zip") as archive:
        assert archive.namelist() == ["bundle.json", "analysis_results.csv", "manifest.json"]
        bundle_manifest = json.loads(archive.read("bundle.json"))
        assert bundle_manifest == {key: first[key] for key in bundle_manifest}
        assert all("/" not in item["path"] for item in bundle_manifest["artifacts"])
        assert archive.read("analysis_results.csv") == b"osm_id,score\nway/1,91\n"


def test_bundle_excludes_custom_patterns(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    (output / "large.parquet").write_bytes(b"optional")
    archive_path = tmp_path / "bundle.zip"

    result = build_bundle(output, archive_path, excludes=("*.parquet",))

    assert result["artifact_count"] == 2
    with zipfile.ZipFile(archive_path) as archive:
        assert "large.parquet" not in archive.namelist()
        assert "manifest.json" in archive.namelist()


def test_bundle_rejects_archive_inside_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the output directory"):
        build_bundle(output, output / "bundle.zip")


def test_bundle_verifier_rejects_symlinked_archive_input(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    build_bundle(output, archive)
    linked_archive = tmp_path / "linked-bundle.zip"
    try:
        linked_archive.symlink_to(archive)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="Bundle archive input must not be a symlink"):
        verify_bundle(linked_archive)


def test_bundle_reports_legacy_manifest_without_rejecting_artifacts(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "manifest.json").write_text("{}", encoding="utf-8")

    result = build_bundle(output, tmp_path / "bundle.zip")

    assert result["manifest_path_contract"] == {"status": "legacy", "version": None}


def test_bundle_can_require_a_passing_verification_record(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs a passing"):
        build_bundle(output, tmp_path / "unverified.zip", require_verified=True)
    (output / "verification.json").write_text(
        json.dumps({"passed": True, "analysis_rows": 1, "target_rows": 1, "control_rows": 0}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="passing validation records"):
        build_bundle(output, tmp_path / "missing-validation.zip", require_verified=True)
    _write_passing_validation_records(output)
    _add_manifest_artifacts(output)

    result = build_bundle(output, tmp_path / "verified.zip", require_verified=True)

    assert result["require_verified"] is True
    assert result["source_verification"] == {
        "path": "verification.json",
        "available": True,
        "included": True,
        "sha256": result["source_verification"]["sha256"],
        "status": "pass",
        "passed": True,
        "analysis_rows": 1,
        "target_rows": 1,
        "control_rows": 0,
    }
    assert all(
        record["status"] == "pass" and record["included"]
        for record in result["source_validation"].values()
    )
    assert verify_bundle(tmp_path / "verified.zip")["passed"] is True
    with pytest.raises(ValueError, match="cannot exclude"):
        build_bundle(
            output,
            tmp_path / "excluded.zip",
            excludes=("verification.json",),
            require_verified=True,
        )


def test_bundle_verified_gate_requires_the_source_manifest(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "verification.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="needs output/manifest.json"):
        build_bundle(output, tmp_path / "bundle.zip", require_verified=True)


def test_bundle_marks_non_boolean_verification_as_invalid(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "verification.json").write_text(
        json.dumps({"passed": "yes"}),
        encoding="utf-8",
    )
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")

    result = build_bundle(output, tmp_path / "bundle.zip")

    assert result["source_verification"]["status"] == "invalid"
    assert result["source_verification"]["passed"] is None
    assert verify_bundle(tmp_path / "bundle.zip")["passed"] is True
    with pytest.raises(ValueError, match="needs a passing"):
        build_bundle(output, tmp_path / "verified.zip", require_verified=True)


def test_bundle_verifier_rejects_tampered_payload(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    build_bundle(output, tmp_path / "valid.zip")
    with zipfile.ZipFile(tmp_path / "valid.zip") as source, zipfile.ZipFile(
        tmp_path / "tampered.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as destination:
        for name in source.namelist():
            content = source.read(name)
            if name == "analysis_results.csv":
                content = b"osm_id\nway/2\n"
            destination.writestr(name, content)

    with pytest.raises(ValueError, match="hash differs"):
        verify_bundle(tmp_path / "tampered.zip")


def test_bundle_verifier_rejects_inconsistent_provenance_members(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "verification.json").write_text(
        json.dumps({"passed": True, "analysis_rows": 1}),
        encoding="utf-8",
    )
    _write_passing_validation_records(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    _add_manifest_artifacts(output)
    build_bundle(output, tmp_path / "valid.zip", require_verified=True)

    with zipfile.ZipFile(tmp_path / "valid.zip") as source:
        members = {name: source.read(name) for name in source.namelist()}
    bundle_manifest = json.loads(members["bundle.json"])
    bundle_manifest["source_verification"]["included"] = False
    members["bundle.json"] = json.dumps(bundle_manifest).encode("utf-8")
    with zipfile.ZipFile(tmp_path / "inconsistent.zip", "w", compression=zipfile.ZIP_DEFLATED) as destination:
        for name, content in members.items():
            destination.writestr(name, content)

    with pytest.raises(ValueError, match="provenance member is unexpected"):
        verify_bundle(tmp_path / "inconsistent.zip")


def test_bundle_cli_verifies_existing_archive(tmp_path, capsys):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    build_bundle(output, archive)

    main(["--verify", str(archive)])

    assert "[bundle] PASS" in capsys.readouterr().out


def test_bundle_cli_json_mode_covers_build_verify_and_extract(tmp_path, capsys):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "verification.json").write_text(
        json.dumps({"passed": True, "analysis_rows": 1, "target_rows": 1}),
        encoding="utf-8",
    )
    _write_passing_validation_records(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    _add_manifest_artifacts(output)
    archive = tmp_path / "bundle.zip"

    main(["--json", "--out-dir", str(output), "--archive", str(archive), "--require-verified"])
    built = json.loads(capsys.readouterr().out)
    assert built["contract"] == BUNDLE_CONTRACT
    assert built["require_verified"] is True
    assert built["source_verification"]["status"] == "pass"

    main(["--json", "--verify", str(archive)])
    verified = json.loads(capsys.readouterr().out)
    assert verified["passed"] is True
    assert verified["require_verified"] is True
    assert verified["source_manifest"]["included"] is True

    destination = tmp_path / "restored"
    main(["--json", "--extract", str(archive), "--destination", str(destination)])
    extracted = json.loads(capsys.readouterr().out)
    assert extracted["extracted"] is True
    assert extracted["destination"] == str(destination.resolve())


def test_bundle_verified_gate_rejects_stale_manifest_artifact(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "verification.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )
    _write_passing_validation_records(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    _add_manifest_artifacts(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="byte count differs from manifest|hash differs from manifest"):
        build_bundle(output, tmp_path / "stale.zip", require_verified=True)


def test_bundle_extracts_only_after_verification_into_new_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    build_bundle(output, archive)

    result = extract_bundle(archive, tmp_path / "restored")

    restored = tmp_path / "restored"
    assert result["extracted"] is True
    assert (restored / "bundle.json").is_file()
    assert (restored / "manifest.json").read_text(encoding="utf-8") == (
        output / "manifest.json"
    ).read_text(encoding="utf-8")
    assert (restored / "analysis_results.csv").read_text(encoding="utf-8") == (
        output / "analysis_results.csv"
    ).read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        extract_bundle(archive, restored)


def test_bundle_rejects_symlinked_extraction_parent(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    build_bundle(output, archive)
    extraction_root = Path(tempfile.mkdtemp(prefix="ireland-geometry-extraction-parent-"))
    real_parent = extraction_root / "real-extract-parent"
    real_parent.mkdir()
    linked_parent = extraction_root / "linked-extract-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="Extraction parent directory must not be a symlink"):
        extract_bundle(archive, linked_parent / "restored")


def test_bundle_rejects_missing_or_empty_output(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_bundle(tmp_path / "missing", tmp_path / "bundle.zip")
    output = tmp_path / "empty"
    output.mkdir()
    with pytest.raises(ValueError, match="No bundleable files"):
        build_bundle(output, tmp_path / "bundle.zip")


def test_bundle_rejects_output_file_symlink(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    target = tmp_path / "target.csv"
    target.write_text("value\n1\n", encoding="utf-8")
    try:
        (output / "linked.csv").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="Output contains a symlink"):
        build_bundle(output, tmp_path / "bundle.zip")


def test_bundle_rejects_output_directory_symlink(tmp_path):
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    _write_portable_manifest(real_output)
    (real_output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="Output directory must not be a symlink"):
        build_bundle(linked_output, tmp_path / "bundle.zip")


def test_bundle_rejects_output_file_root(tmp_path):
    output = tmp_path / "file-output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="Output directory must be a directory"):
        build_bundle(output, tmp_path / "bundle.zip")


def test_bundle_rejects_symlinked_archive_parent(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    real_parent = tmp_path / "real-archive-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-archive-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="Bundle archive parent directory must not be a symlink"):
        build_bundle(output, linked_parent / "bundle.zip")


@pytest.mark.parametrize("symlink_kind", ("output", "archive"))
def test_bundle_cli_preserves_symlink_rejection(tmp_path, capsys, symlink_kind):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    if symlink_kind == "output":
        linked_output = tmp_path / "linked-output"
        try:
            linked_output.symlink_to(output, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable in this test environment")
        out_arg = linked_output
        archive_arg = archive
    else:
        archive_target = tmp_path / "archive-target.zip"
        archive_target.write_bytes(b"old archive")
        try:
            archive.symlink_to(archive_target)
        except OSError:
            pytest.skip("symlinks are unavailable in this test environment")
        out_arg = output
        archive_arg = archive

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--json",
                "--out-dir",
                str(out_arg),
                "--archive",
                str(archive_arg),
            ]
        )

    assert exc_info.value.code == 2
    assert "symlink" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("unsafe_path", ("../escape.txt", r"..\\escape.txt", "C:/escape.txt"))
def test_bundle_verifier_rejects_traversal_and_drive_paths(tmp_path, unsafe_path):
    archive = tmp_path / "unsafe.zip"
    manifest = {
        "contract": BUNDLE_CONTRACT,
        "artifact_count": 1,
        "artifact_bytes": 1,
        "artifacts": [{"path": unsafe_path, "bytes": 1, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(archive, "w") as destination:
        destination.writestr("bundle.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="relative POSIX|unsafe bundle"):
        verify_bundle(archive)
