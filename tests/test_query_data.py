import csv
import io
import json

import pytest

from scripts.columnar import main as columnar_main
from scripts.query_data import (
    _cursor_for_row,
    _sort_and_limit,
    probe_backend,
    query_rows,
    select_backend,
    write_rows,
)


def write_analysis(path, rows):
    fields = ["osm_id", "group", "is_control", "score"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_query_rows_auto_selects_csv_and_applies_filters(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    write_analysis(
        out / "analysis_results.csv",
        [
            {"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "91"},
            {"osm_id": "way/2", "group": "worship", "is_control": "0", "score": "72"},
            {"osm_id": "way/3", "group": "control", "is_control": "1", "score": "99"},
        ],
    )
    assert select_backend(out) == "csv"
    backend, rows = query_rows(
        out,
        osm_id=None,
        group="worship",
        is_control="0",
        min_score=75,
        limit=10,
    )
    assert backend == "csv"
    assert [row["osm_id"] for row in rows] == ["way/1"]


def test_query_rows_supports_jsonl_and_bounded_sorted_output(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    rows = [
        {"osm_id": "way/low", "score": "12", "group": "worship", "is_control": "0"},
        {"osm_id": "way/high", "score": "88", "group": "worship", "is_control": "0"},
    ]
    (out / "analysis_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    backend, result = query_rows(out, backend="jsonl", limit=1)
    assert backend == "jsonl"
    assert [row["osm_id"] for row in result] == ["way/high"]
    backend, result = query_rows(out, backend="jsonl", limit=1, offset=1)
    assert backend == "jsonl"
    assert [row["osm_id"] for row in result] == ["way/low"]
    with pytest.raises(ValueError, match="offset must be zero or greater"):
        query_rows(out, backend="jsonl", offset=-1)
    with pytest.raises(ValueError, match="min_score must be finite"):
        query_rows(out, backend="jsonl", min_score=float("nan"))
    with pytest.raises(ValueError, match="min_score must be finite"):
        query_rows(out, backend="jsonl", min_score=float("inf"))


def test_query_rows_auto_falls_back_from_unreadable_preferred_backend(tmp_path):
    pytest.importorskip("duckdb")
    out = tmp_path / "output"
    out.mkdir()
    write_analysis(
        out / "analysis_results.csv",
        [{"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "91"}],
    )
    import duckdb

    connection = duckdb.connect(str(out / "analysis.duckdb"))
    connection.close()

    backend_errors = []
    backend, rows = query_rows(
        out,
        backend="auto",
        backend_errors=backend_errors,
        limit=1,
    )

    assert backend == "csv"
    assert [row["osm_id"] for row in rows] == ["way/1"]
    assert backend_errors and backend_errors[0].startswith("duckdb:")
    assert probe_backend(out, "duckdb")["readable"] is False
    assert probe_backend(out, "csv")["readable"] is True
    with pytest.raises(duckdb.Error):
        query_rows(out, backend="duckdb", limit=1)


def test_query_rows_supports_bounded_cursor_pagination(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    write_analysis(
        out / "analysis_results.csv",
        [
            {"osm_id": "way/a", "group": "worship", "is_control": "0", "score": "91"},
            {"osm_id": "way/b", "group": "worship", "is_control": "0", "score": "91"},
            {"osm_id": "way/c", "group": "worship", "is_control": "0", "score": "88"},
            {"osm_id": "way/d", "group": "worship", "is_control": "0", "score": "72"},
        ],
    )
    backend, first = query_rows(out, limit=2)
    assert backend == "csv"
    assert [row["osm_id"] for row in first] == ["way/a", "way/b"]
    assert _cursor_for_row(first[-1]) == {"after_score": 91.0, "after_osm_id": "way/b"}
    _, second = query_rows(
        out,
        after_score=91,
        after_osm_id="way/b",
        limit=2,
    )
    assert [row["osm_id"] for row in second] == ["way/c", "way/d"]
    with pytest.raises(ValueError, match="provided together"):
        query_rows(out, after_score=91, limit=1)
    with pytest.raises(ValueError, match="offset cannot be combined"):
        query_rows(out, after_score=91, after_osm_id="way/b", offset=1)
    with pytest.raises(ValueError, match="after_score must be finite"):
        query_rows(out, after_score=float("nan"), after_osm_id="way/b")


def test_query_rows_supports_invalid_score_cursor_pagination_across_fallbacks(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    rows = [
        {"osm_id": "way/finite", "group": "worship", "is_control": "0", "score": "91"},
        {"osm_id": "way/invalid-a", "group": "worship", "is_control": "0", "score": "NaN"},
        {"osm_id": "way/invalid-b", "group": "worship", "is_control": "0", "score": "not-a-number"},
    ]
    write_analysis(out / "analysis_results.csv", rows)
    (out / "analysis_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    for backend in ("csv", "jsonl"):
        selected, first = query_rows(out, backend=backend, limit=2)
        assert selected == backend
        assert [row["osm_id"] for row in first] == ["way/finite", "way/invalid-a"]
        assert _cursor_for_row(first[-1]) == {"after_invalid_osm_id": "way/invalid-a"}
        selected, second = query_rows(
            out,
            backend=backend,
            after_invalid_osm_id="way/invalid-a",
            limit=2,
        )
        assert selected == backend
        assert [row["osm_id"] for row in second] == ["way/invalid-b"]

    with pytest.raises(ValueError, match="after_invalid_osm_id must be non-empty"):
        query_rows(out, after_invalid_osm_id="", limit=1)
    with pytest.raises(ValueError, match="cannot be combined"):
        query_rows(
            out,
            after_score=91,
            after_osm_id="way/finite",
            after_invalid_osm_id="way/invalid-a",
            limit=1,
        )
    with pytest.raises(ValueError, match="offset cannot be combined"):
        query_rows(out, after_invalid_osm_id="way/invalid-a", offset=1)


def test_bounded_sort_keeps_exact_page_order_with_ties_and_invalid_scores():
    rows = [
        {"osm_id": "way/z", "score": "91"},
        {"osm_id": "way/b", "score": "91"},
        {"osm_id": "way/a", "score": "91"},
        {"osm_id": "way/c", "score": "72"},
        {"osm_id": "way/invalid", "score": "nan"},
    ]

    page = _sort_and_limit((row for row in rows), limit=2, offset=1)

    assert [row["osm_id"] for row in page] == ["way/b", "way/z"]
    assert [row["osm_id"] for row in _sort_and_limit(rows, limit=0)] == [
        "way/a",
        "way/b",
        "way/z",
        "way/c",
        "way/invalid",
    ]


def test_write_rows_emits_machine_readable_formats():
    rows = [{"osm_id": "way/1", "score": 91, "invalid": float("nan")}]
    json_stream = io.StringIO()
    write_rows(rows, "json", json_stream)
    assert json.loads(json_stream.getvalue()) == [
        {"osm_id": "way/1", "score": 91, "invalid": None}
    ]

    jsonl_stream = io.StringIO()
    write_rows([{"osm_id": "way/1", "invalid": float("inf")}], "jsonl", jsonl_stream)
    assert json.loads(jsonl_stream.getvalue()) == {"osm_id": "way/1", "invalid": None}

    csv_stream = io.StringIO()
    write_rows([{"osm_id": "way/1", "score": 91}], "csv", csv_stream)
    assert "osm_id,score" in csv_stream.getvalue()
    assert "way/1,91" in csv_stream.getvalue()


def test_query_rows_reads_optional_columnar_backends_when_available(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    out = tmp_path / "output"
    out.mkdir()
    write_analysis(
        out / "analysis_results.csv",
        [
            {"osm_id": "way/1", "group": "worship", "is_control": "0", "score": "91"},
            {"osm_id": "way/3", "group": "worship", "is_control": "0", "score": "72"},
            {"osm_id": "way/2", "group": "control", "is_control": "1", "score": "12"},
            {"osm_id": "way/invalid-a", "group": "control", "is_control": "1", "score": "NaN"},
            {"osm_id": "way/invalid-b", "group": "control", "is_control": "1", "score": "oops"},
        ],
    )
    columnar_main(["--out-dir", str(out)])
    for backend in ("parquet", "duckdb"):
        selected, rows = query_rows(out, backend=backend, is_control="target", limit=0)
        assert selected == backend
        assert [row["osm_id"] for row in rows] == ["way/1", "way/3"]
        selected, rows = query_rows(
            out, backend=backend, is_control="target", limit=1, offset=1
        )
        assert selected == backend
        assert [row["osm_id"] for row in rows] == ["way/3"]
        selected, invalid_page = query_rows(out, backend=backend, limit=4)
        assert selected == backend
        assert invalid_page[-1]["osm_id"] == "way/invalid-a"
        selected, invalid_continuation = query_rows(
            out,
            backend=backend,
            after_invalid_osm_id="way/invalid-a",
            limit=1,
        )
        assert selected == backend
        assert [row["osm_id"] for row in invalid_continuation] == ["way/invalid-b"]
        selected, rows = query_rows(
            out,
            backend=backend,
            is_control="target",
            after_score=91,
            after_osm_id="way/1",
            limit=1,
        )
        assert selected == backend
        assert [row["osm_id"] for row in rows] == ["way/3"]
