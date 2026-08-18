import json
import zipfile

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


def test_bundle_excludes_custom_patterns_and_skips_archive_inside_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_portable_manifest(output)
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    (output / "large.parquet").write_bytes(b"optional")
    archive_path = output / "bundle.zip"

    result = build_bundle(output, archive_path, excludes=("*.parquet",))

    assert result["artifact_count"] == 2
    with zipfile.ZipFile(archive_path) as archive:
        assert "bundle.zip" not in archive.namelist()
        assert "large.parquet" not in archive.namelist()
        assert "manifest.json" in archive.namelist()


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
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
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
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
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
    assert extracted["source_verification"]["status"] == "pass"


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


def test_bundle_rejects_missing_or_empty_output(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_bundle(tmp_path / "missing", tmp_path / "bundle.zip")
    output = tmp_path / "empty"
    output.mkdir()
    with pytest.raises(ValueError, match="No bundleable files"):
        build_bundle(output, tmp_path / "bundle.zip")


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
