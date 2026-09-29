import io
import json
import os
from pathlib import Path

import pytest

from scripts import runtime
from scripts.runtime import (
    atomic_write_stream,
    atomic_write_text,
    build_manifest,
    default_analysis_plan_path,
    manifest_source_alignment,
    output_counts,
    package_version,
    packaged_analysis_plan_path,
    path_metadata_sha256,
    path_symlink_paths,
    project_data_path,
    project_data_tree_path,
    project_input_path,
    project_output_file_path,
    project_output_path,
    project_output_tree_path,
    reject_symlink_path,
    reject_symlink_tree,
    runtime_signature,
    sha256_path,
)


def test_default_project_root_uses_caller_directory_for_wheel_like_install(tmp_path, monkeypatch):
    package_root = tmp_path / "site-packages"
    project_root = tmp_path / "project"
    package_root.mkdir()
    project_root.mkdir()
    monkeypatch.setattr(runtime, "PACKAGE_ROOT", package_root)
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("IRELAND_GEOMETRY_PROJECT_ROOT", raising=False)
    assert runtime.default_project_root() == project_root


def test_configured_project_root_overrides_wheel_default(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    monkeypatch.setenv("IRELAND_GEOMETRY_PROJECT_ROOT", str(configured))
    assert runtime.default_project_root() == configured.resolve()


def test_project_output_path_rejects_existing_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    output = project / "output"
    output.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(runtime, "ROOT", project)

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        project_output_path(None)


def test_project_output_path_rejects_symlinked_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    real_output = project / "real-output"
    real_output.mkdir()
    linked_output = project / "output"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(runtime, "ROOT", project)

    with pytest.raises(SystemExit, match="output directory must not be a symlink"):
        project_output_path(None)


def test_project_data_path_rejects_existing_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    data = project / "data"
    data.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(runtime, "ROOT", project)

    with pytest.raises(SystemExit, match="data directory must be a directory"):
        project_data_path(None)


def test_project_data_path_rejects_symlinked_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    real_data = project / "real-data"
    real_data.mkdir()
    linked_data = project / "data"
    try:
        linked_data.symlink_to(real_data, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(runtime, "ROOT", project)

    with pytest.raises(SystemExit, match="data directory must not be a symlink"):
        project_data_path(None)


def test_project_data_tree_path_rejects_nested_symlink(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    data = project / "data"
    data.mkdir()
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    link = data / "nested.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setattr(runtime, "ROOT", project)

    with pytest.raises(SystemExit, match="data directory contains symlink"):
        project_data_tree_path(None)


def test_project_input_path_rejects_symlinked_file(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="analysis input must not be a symlink"):
        project_input_path(link, "unused.json", label="analysis input")


def test_project_input_path_rejects_nested_symlinked_directory(tmp_path):
    root = tmp_path / "graph"
    root.mkdir()
    target = tmp_path / "external-graph"
    target.mkdir()
    link = root / "external"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="road graph input contains symlink"):
        project_input_path(root, "unused", label="road graph input")


def test_project_output_tree_path_rejects_nested_symlink(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "external.csv"
    target.write_text("x\n", encoding="utf-8")
    link = output / "linked.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory contains symlink"):
        project_output_tree_path(output)


def test_project_output_file_path_rejects_directories_and_symlink_files(tmp_path):
    directory = tmp_path / "route.json"
    directory.mkdir()

    with pytest.raises(SystemExit, match="route output must be a file"):
        project_output_file_path(directory, label="route output")

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "linked-route.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="route output must not be a symlink"):
        project_output_file_path(link, label="route output")


def test_project_output_file_path_rejects_symlinked_parent_tree(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="route output parent directory must not be a symlink"):
        project_output_file_path(linked_parent / "route.json", label="route output")


def test_project_output_file_path_allows_canonical_macos_tmp_alias():
    tmp_alias = Path("/tmp")
    if not (tmp_alias.is_symlink() and tmp_alias.resolve() == Path("/private/tmp")):
        pytest.skip("the canonical macOS /tmp alias is unavailable")

    destination = project_output_file_path(
        tmp_alias / "ireland-geometry-runtime-test.json",
        label="route output",
    )
    assert destination == tmp_alias / "ireland-geometry-runtime-test.json"


def test_reject_symlink_path_rejects_individual_file_symlink(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="cache file must not be a symlink"):
        reject_symlink_path(link, label="cache file")


def test_reject_symlink_tree_rejects_nested_symlink(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    link = root / "nested.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="data directory contains symlink"):
        reject_symlink_tree(root, label="data directory")


def test_atomic_file_writers_replace_symlinks_without_touching_targets(tmp_path):
    text_target = tmp_path / "text-target.txt"
    text_target.write_text("original", encoding="utf-8")
    text_link = tmp_path / "text-link.txt"
    try:
        text_link.symlink_to(text_target)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    atomic_write_text(text_link, "replacement")

    assert text_link.read_text(encoding="utf-8") == "replacement"
    assert not text_link.is_symlink()
    assert text_target.read_text(encoding="utf-8") == "original"

    stream_target = tmp_path / "stream-target.bin"
    stream_target.write_bytes(b"original")
    stream_link = tmp_path / "stream-link.bin"
    stream_link.symlink_to(stream_target)
    written = atomic_write_stream(stream_link, io.BytesIO(b"streamed"), chunk_size=2)

    assert written == len(b"streamed")
    assert stream_link.read_bytes() == b"streamed"
    assert not stream_link.is_symlink()
    assert stream_target.read_bytes() == b"original"


def test_atomic_write_stream_accepts_chunk_iterables(tmp_path):
    destination = tmp_path / "iterable.bin"
    chunks = (chunk for chunk in (b"ab", b"", b"cde"))

    written = atomic_write_stream(destination, chunks)

    assert written == 5
    assert destination.read_bytes() == b"abcde"

    destination.write_bytes(b"previous")

    def failing_chunks():
        yield b"partial"
        raise RuntimeError("simulated download failure")

    with pytest.raises(RuntimeError, match="simulated download failure"):
        atomic_write_stream(destination, failing_chunks())

    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_git_worktree_status_reports_bounded_path_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "check_output",
        lambda *_args, **_kwargs: " M tracked.py\n?? new.txt\nR  old.py -> renamed.py\n",
    )

    result = runtime.git_worktree_status(tmp_path, max_paths=2)

    assert result == {
        "available": True,
        "dirty": True,
        "path_count": 3,
        "changed_path_count": 2,
        "untracked_path_count": 1,
        "paths": [
            {"status": "??", "path": "new.txt"},
            {"status": "R ", "path": "renamed.py", "original_path": "old.py"},
        ],
        "paths_truncated": True,
    }


def test_git_worktree_status_fails_closed_when_git_is_unavailable(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise runtime.subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(runtime.subprocess, "check_output", unavailable)

    result = runtime.git_worktree_status(tmp_path)

    assert result["available"] is False
    assert result["dirty"] is None
    assert result["paths"] == []


def test_manifest_source_alignment_checks_metadata_and_optional_full_hashes(tmp_path):
    project = tmp_path / "project"
    source = project / "data" / "source.bin"
    output = project / "output"
    source.parent.mkdir(parents=True)
    output.mkdir()
    source.write_bytes(b"original")
    original_mtime = source.stat().st_mtime
    expected = {
        "source_kind": "required_source",
        "path": str(source),
        "path_base": "project_root",
        "relative_path": "data/source.bin",
        "sha256": runtime.sha256_path(source),
        "bytes": source.stat().st_size,
        "modified_at": runtime.path_modified_at(source),
    }
    manifest = {"project_root": str(project), "sources": [expected]}

    metadata = manifest_source_alignment(
        manifest,
        project_root=project,
        data_root=project / "data",
        out_dir=output,
    )
    assert metadata["status"] == "pass"
    assert metadata["mode"] == "metadata"
    assert metadata["hash_checked_count"] == 0
    assert metadata["metadata_checked_count"] == 0

    source.write_bytes(b"tampered")
    os.utime(source, (original_mtime, original_mtime))
    deep = manifest_source_alignment(
        manifest,
        project_root=project,
        data_root=project / "data",
        out_dir=output,
        check_hashes=True,
    )
    assert deep["status"] == "fail"
    assert deep["hash_checked_count"] == 1
    assert any("hash mismatch" in error for error in deep["errors"])


def test_manifest_source_alignment_prefers_current_portable_path_after_move(tmp_path):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    old_source = original / "data" / "source.bin"
    current_source = relocated / "data" / "source.bin"
    output = relocated / "output"
    old_source.parent.mkdir(parents=True)
    current_source.parent.mkdir(parents=True)
    output.mkdir()
    old_source.write_bytes(b"old\n")
    current_source.write_bytes(b"new\n")
    manifest = {
        "project_root": str(original),
        "sources": [
            {
                "source_kind": "required_source",
                "path": str(old_source),
                "path_base": "project_root",
                "relative_path": "data/source.bin",
                "sha256": sha256_path(current_source),
                "bytes": current_source.stat().st_size,
                "modified_at": runtime.path_modified_at(current_source),
            }
        ],
    }

    result = manifest_source_alignment(
        manifest,
        project_root=relocated,
        data_root=relocated / "data",
        out_dir=output,
        check_hashes=True,
    )

    assert result["status"] == "pass"
    assert result["sources"][0]["path"] == str(current_source)


def test_directory_source_metadata_alignment_catches_nested_file_change(tmp_path):
    project = tmp_path / "project"
    source_tree = project / "data" / "roads"
    output = project / "output"
    source_tree.mkdir(parents=True)
    output.mkdir()
    nested = source_tree / "nodes.csv"
    nested.write_bytes(b"nodes-v1")
    root_mtime_ns = source_tree.stat().st_mtime_ns
    manifest = {
        "project_root": str(project),
        "sources": [
            {
                "source_kind": "optional_road_graph",
                "path": str(source_tree),
                "path_base": "project_root",
                "relative_path": "data/roads",
                "sha256": sha256_path(source_tree),
                "metadata_sha256": path_metadata_sha256(source_tree),
                "modified_at": runtime.path_modified_at(source_tree),
            }
        ],
    }

    nested.write_bytes(b"nodes-v2")
    os.utime(source_tree, ns=(root_mtime_ns, root_mtime_ns))

    result = manifest_source_alignment(
        manifest,
        project_root=project,
        data_root=project / "data",
        out_dir=output,
    )

    assert result["status"] == "fail"
    assert result["metadata_checked_count"] == 1
    assert result["sources"][0]["metadata_checked"] is True
    assert any("metadata hash mismatch" in error for error in result["errors"])


def test_manifest_source_alignment_fails_when_hashed_source_is_missing(tmp_path):
    project = tmp_path / "project"
    output = project / "output"
    output.mkdir(parents=True)
    result = manifest_source_alignment(
        {
            "project_root": str(project),
            "sources": [
                {
                    "source_kind": "required_source",
                    "path_base": "project_root",
                    "relative_path": "data/missing.bin",
                    "sha256": "a" * 64,
                }
            ],
        },
        project_root=project,
        data_root=project / "data",
        out_dir=output,
    )
    assert result["status"] == "fail"
    assert "unavailable" in result["errors"][0]


def test_source_hashing_and_alignment_reject_nested_symlinks(tmp_path):
    project = tmp_path / "project"
    source_tree = project / "data" / "roads"
    outside = tmp_path / "outside"
    output = project / "output"
    source_tree.mkdir(parents=True)
    outside.mkdir()
    output.mkdir()
    (source_tree / "nodes.csv").write_text("node_id\n1\n", encoding="utf-8")
    (outside / "secret.csv").write_text("secret\n", encoding="utf-8")
    try:
        (source_tree / "external").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    assert path_symlink_paths(source_tree) == ["external"]
    assert sha256_path(source_tree) is None

    manifest = {
        "project_root": str(project),
        "sources": [
            {
                "source_kind": "road_graph",
                "path": str(source_tree),
                "sha256": "a" * 64,
            }
        ],
    }
    result = manifest_source_alignment(
        manifest,
        project_root=project,
        data_root=project / "data",
        out_dir=output,
    )
    assert result["status"] == "fail"
    assert result["symlink_count"] == 1
    assert result["sources"][0]["symlink_paths"] == ["external"]
    assert "contains symlink" in result["errors"][0]


def test_build_manifest_preserves_source_symlink_diagnostic(tmp_path):
    source_tree = tmp_path / "roads"
    source_tree.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("external\n", encoding="utf-8")
    (source_tree / "nodes.csv").write_text("node_id\n1\n", encoding="utf-8")
    try:
        (source_tree / "external.csv").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        parameters={},
        sources=[{"kind": "road_graph", "path": source_tree}],
    )
    source = manifest["sources"][0]
    assert source["symlink_paths"] == ["external.csv"]
    assert source["sha256"] is None
    (source_tree / "external.csv").unlink()
    alignment = manifest_source_alignment(
        manifest,
        project_root=tmp_path,
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
    )
    assert alignment["status"] == "fail"
    assert "recorded with symlink" in alignment["errors"][0]


def test_default_analysis_plan_prefers_project_override_and_packages_fallback(tmp_path):
    root = tmp_path / "package"
    (root / "schemas").mkdir(parents=True)
    packaged = root / "schemas" / "analysis_plan.json"
    packaged.write_text('{"plan_id":"packaged","version":1}', encoding="utf-8")
    assert default_analysis_plan_path(tmp_path, package_root=root) == packaged

    project = tmp_path / "analysis_plan.json"
    project.write_text('{"plan_id":"override","version":2}', encoding="utf-8")
    assert default_analysis_plan_path(tmp_path, package_root=root) == project
    assert packaged_analysis_plan_path(root) == packaged


def test_package_version_reads_declared_source_metadata(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "PACKAGE_ROOT", tmp_path)
    assert runtime.package_version() == "9.9.9"


def test_manifest_counts_csv_records_not_physical_lines(tmp_path):
    (tmp_path / "architects_evidence.csv").write_text(
        'id,evidence\n1,"first line\nsecond line"\n2,"single line"\n',
        encoding="utf-8",
    )
    counts = output_counts(tmp_path)
    assert counts["architects_evidence.csv"] == 2


def test_manifest_counts_scale_exports_and_geojson_features(tmp_path):
    (tmp_path / "analysis_results.csv").write_text("osm_id\nway/1\nway/2\n", encoding="utf-8")
    (tmp_path / "analysis_results.jsonl").write_text('{"osm_id":"way/1"}\n', encoding="utf-8")
    (tmp_path / "analysis_results.parquet").write_bytes(b"parquet")
    (tmp_path / "analysis.duckdb").write_bytes(b"duckdb")
    (tmp_path / "ireland_buildings.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature"}]}),
        encoding="utf-8",
    )
    counts = output_counts(tmp_path)
    assert counts["analysis_results.jsonl"] == 2
    assert counts["analysis_results.parquet"] == 2
    assert counts["analysis.duckdb"] == 2
    assert counts["ireland_buildings.geojson"] == 1


def test_manifest_hashes_the_complete_road_graph_directory(tmp_path):
    graph = tmp_path / "roads"
    graph.mkdir()
    (graph / "road_nodes.csv").write_text("node_id,lat,lon\na,53,-8\n", encoding="utf-8")
    (graph / "road_edges.csv").write_text("u,v,length_m\na,a,0\n", encoding="utf-8")
    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        parameters={},
        sources=[{"kind": "optional_road_graph", "path": graph}],
    )
    source = manifest["sources"][0]
    assert source["kind"] == "directory"
    assert source["source_kind"] == "optional_road_graph"
    assert source["sha256"] == sha256_path(graph)
    assert source["metadata_sha256"] == path_metadata_sha256(graph)
    assert source["bytes"] is None


def test_manifest_records_source_freshness_contract(tmp_path):
    source_path = tmp_path / "data" / "combined.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}", encoding="utf-8")
    os.utime(source_path, (1_600_000_000, 1_600_000_000))

    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=tmp_path / "output",
        parameters={},
        project_root=tmp_path,
        sources=[{"kind": "combined_osm_json", "path": source_path}],
    )

    assert manifest["source_freshness"]["contract"] == "ireland-geometry.freshness.v1"
    freshness = manifest["source_freshness"]["sources"][0]
    assert freshness["modified_at"].startswith("2020-09-13T12:26:40")
    assert freshness["age_seconds"] > 0
    assert manifest["sources"][0]["modified_at"] == freshness["modified_at"]


def test_manifest_uses_explicit_project_root_for_identity(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = build_manifest(
        data_root=project_root / "data",
        out_dir=project_root / "output",
        parameters={},
        project_root=project_root,
    )
    assert manifest["project_root"] == str(project_root.resolve())


def test_manifest_excludes_timestamped_doctor_diagnostic(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "doctor.json").write_text('{"generated_at":"now"}', encoding="utf-8")
    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=output,
        parameters={},
    )
    assert manifest["package_version"] == package_version()
    assert manifest["runtime"] == runtime_signature()
    assert not any(item["path"].endswith("/doctor.json") for item in manifest["artifacts"])


def test_manifest_records_nested_output_artifacts(tmp_path):
    output = tmp_path / "output"
    nested = output / "nested"
    nested.mkdir(parents=True)
    artifact = nested / "analysis.csv"
    artifact.write_text("x\n", encoding="utf-8")

    manifest = build_manifest(
        data_root=tmp_path / "data",
        out_dir=output,
        parameters={},
    )

    record = next(item for item in manifest["artifacts"] if item["path"].endswith("analysis.csv"))
    assert record["relative_path"] == "nested/analysis.csv"
    assert record["path_base"] == "output_dir"


def test_manifest_records_relative_references_for_relocation(tmp_path):
    project = tmp_path / "project"
    data = project / "data"
    output = project / "output"
    data.mkdir(parents=True)
    output.mkdir()
    (data / "combined.json").write_text("{}", encoding="utf-8")
    (output / "analysis_results.csv").write_text("osm_id\nway/1\n", encoding="utf-8")
    external = tmp_path / "external.csv"
    external.write_text("value\n1\n", encoding="utf-8")

    manifest = build_manifest(
        data_root=data,
        out_dir=output,
        parameters={},
        project_root=project,
        sources=[
            {"kind": "combined_osm_json", "path": data / "combined.json"},
            {"kind": "external", "path": external},
        ],
    )

    assert manifest["path_contract"] == {
        "version": 1,
        "relative_path_field": "relative_path",
        "source_base": "project_root",
        "artifact_base": "output_dir",
        "absolute_path_field": "path",
    }
    assert manifest["data_root_relative"] == "data"
    assert manifest["output_dir_relative"] == "output"
    sources = {row["source_kind"]: row for row in manifest["sources"]}
    assert sources["combined_osm_json"]["relative_path"] == "data/combined.json"
    assert sources["combined_osm_json"]["path_base"] == "project_root"
    assert "relative_path" not in sources["external"]
    assert sources["external"]["path_base"] == "external"
    artifact = next(row for row in manifest["artifacts"] if row["path"].endswith("analysis_results.csv"))
    assert artifact["relative_path"] == "analysis_results.csv"
    assert artifact["path_base"] == "output_dir"
