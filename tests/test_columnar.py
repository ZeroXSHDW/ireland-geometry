from __future__ import annotations

import json

import pytest

from scripts.columnar import COLUMNAR_CONTRACT
from scripts.columnar import main as columnar_main
from scripts.verify import check_columnar_contract


def _write_analysis(path, rows):
    path.write_text(
        "osm_id,group,is_control,score,label\n"
        + "\n".join(
            f"{row['osm_id']},{row['group']},{row['is_control']},{row['score']},{row['label']}"
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def test_columnar_stage_records_cross_backend_parity_contract(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_analysis(
        output / "analysis_results.csv",
        [
            {"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "91.0", "label": "target"},
            {"osm_id": "way/2", "group": "control", "is_control": "1", "score": "nan", "label": "ordinary"},
        ],
    )

    columnar_main(["--out-dir", str(output)])
    status = json.loads((output / "columnar_status.json").read_text(encoding="utf-8"))

    assert status["contract"] == COLUMNAR_CONTRACT
    assert status["rows"] == 2
    assert status["parity"]["status"] == "pass"
    assert {"csv", "jsonl"} <= set(status["parity"]["checked_backends"])
    assert status["schema_sha256"]
    assert status["row_digest"]
    for name in ("csv", "jsonl", "parquet", "duckdb"):
        if status[name]["status"] == "available":
            assert status[name]["rows"] == 2
            assert status[name]["schema_sha256"] == status["schema_sha256"]
            assert status[name]["row_digest"] == status["row_digest"]


def test_columnar_verifier_detects_tampered_export_digest(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_analysis(
        output / "analysis_results.csv",
        [{"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "91", "label": "target"}],
    )
    columnar_main(["--out-dir", str(output)])

    (output / "analysis_results.jsonl").write_text(
        json.dumps(
            {"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "12", "label": "target"}
        )
        + "\n",
        encoding="utf-8",
    )
    status = json.loads((output / "columnar_status.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []
    check_columnar_contract(status, output, 1, errors, warnings)

    assert any("jsonl row digest does not match source" in error for error in errors)


def test_columnar_rejects_symlinked_output_root(tmp_path):
    target = tmp_path / "target-output"
    target.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory must not be a symlink"):
        columnar_main(["--out-dir", str(linked)])


def test_columnar_rejects_file_output_root(tmp_path):
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit, match="output directory must be a directory"):
        columnar_main(["--out-dir", str(output)])


def test_columnar_rejects_nested_output_symlink(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (output / "linked-input.csv").symlink_to(target / "input.csv")
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(SystemExit, match="output directory contains symlink"):
        columnar_main(["--out-dir", str(output)])
