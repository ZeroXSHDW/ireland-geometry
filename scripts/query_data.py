#!/usr/bin/env python3
"""Run bounded, read-only queries over the generated analysis exports."""

from __future__ import annotations

import argparse
import csv
import heapq
import importlib.util
import json
import math
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

try:
    from runtime import package_version, project_output_tree_path
except ImportError:
    from scripts.runtime import package_version, project_output_tree_path


BACKEND_FILES = {
    "duckdb": "analysis.duckdb",
    "parquet": "analysis_results.parquet",
    "csv": "analysis_results.csv",
    "jsonl": "analysis_results.jsonl",
}
BACKENDS = tuple(BACKEND_FILES)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def available_backends(out_dir: Path) -> dict[str, bool]:
    """Return backends that have both an artifact and a usable reader."""
    return {
        "duckdb": (out_dir / BACKEND_FILES["duckdb"]).is_file() and _module_available("duckdb"),
        "parquet": (out_dir / BACKEND_FILES["parquet"]).is_file()
        and (_module_available("pyarrow") or _module_available("duckdb")),
        "csv": (out_dir / BACKEND_FILES["csv"]).is_file(),
        "jsonl": (out_dir / BACKEND_FILES["jsonl"]).is_file(),
    }


def probe_backend(out_dir: Path, backend: str) -> dict[str, Any]:
    """Check one export's readability without sorting or materializing rows."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {', '.join(BACKENDS)}")
    result: dict[str, Any] = {
        "available": available_backends(out_dir)[backend],
        "readable": False,
        "error": None,
    }
    if not result["available"]:
        return result
    path = out_dir / BACKEND_FILES[backend]
    connection = None
    try:
        if backend == "duckdb":
            import duckdb

            connection = duckdb.connect(str(path), read_only=True)
            connection.execute('SELECT 1 FROM "analysis_results" LIMIT 1').fetchone()
        elif backend == "parquet" and _module_available("duckdb"):
            import duckdb

            connection = duckdb.connect(":memory:")
            connection.execute(
                "SELECT 1 FROM read_parquet(?) LIMIT 1",
                [str(path)],
            ).fetchone()
        elif backend == "parquet":
            from pyarrow import parquet

            parquet.read_schema(path)
        elif backend == "csv":
            with path.open(newline="", encoding="utf-8") as handle:
                if next(csv.reader(handle), None) is None:
                    raise ValueError("CSV export is empty")
        else:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise TypeError("JSONL row is not an object")
                        break
    except Exception as exc:  # noqa: BLE001 - optional backend readers expose different errors
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if connection is not None:
            connection.close()
    result["readable"] = True
    return result


def select_backend(out_dir: Path, requested: str = "auto") -> str:
    """Select a usable backend, preferring queryable formats over row streams."""
    available = available_backends(out_dir)
    if requested == "auto":
        for name in ("duckdb", "parquet", "csv", "jsonl"):
            if available[name]:
                return name
        raise FileNotFoundError(f"No analysis export is available in {out_dir}")
    if requested not in BACKENDS:
        raise ValueError(f"Unknown backend {requested!r}; choose from {', '.join(BACKENDS)}")
    if not available[requested]:
        if requested in {"duckdb", "parquet"}:
            extra = "Install the declared data extra: python -m pip install -e '.[data]'"
        else:
            extra = "Run the columnar stage to generate the export"
        raise FileNotFoundError(
            f"Backend {requested!r} is unavailable in {out_dir}. {extra}."
        )
    return requested


def _score(row: dict[str, Any]) -> float:
    try:
        value = float(row.get("score", ""))
    except (TypeError, ValueError):
        return float("-inf")
    return value if math.isfinite(value) else float("-inf")


def _matches(
    row: dict[str, Any],
    *,
    osm_id: str | None,
    group: str | None,
    is_control: str | None,
    min_score: float | None,
    after_score: float | None,
    after_osm_id: str | None,
    after_invalid_osm_id: str | None,
) -> bool:
    if osm_id and str(row.get("osm_id", "")) != osm_id:
        return False
    if group and str(row.get("group", "")) != group:
        return False
    if is_control is not None and str(row.get("is_control", "")) != is_control:
        return False
    score = _score(row)
    if min_score is not None and score < min_score:
        return False
    if after_invalid_osm_id is not None:
        return not math.isfinite(score) and str(row.get("osm_id", "")) > after_invalid_osm_id
    if after_score is None:
        return True
    return score < after_score or (
        score == after_score and str(row.get("osm_id", "")) > (after_osm_id or "")
    )


class _WorstFirst:
    """Heap key whose smallest item is the worst retained query row."""

    __slots__ = ("osm_id", "score")

    def __init__(self, row: dict[str, Any]):
        self.score = _score(row)
        self.osm_id = str(row.get("osm_id", ""))

    def __lt__(self, other: _WorstFirst) -> bool:
        if self.score != other.score:
            return self.score < other.score
        # For equal scores, lexicographically larger IDs sort later and are
        # therefore the worse rows under the public deterministic ordering.
        return self.osm_id > other.osm_id


def _query_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    return (-_score(row), str(row.get("osm_id", "")))


def _cursor_for_row(row: dict[str, Any]) -> dict[str, float | str] | None:
    """Return the public cursor for a row with a usable OSM ID."""
    score = _score(row)
    osm_id = str(row.get("osm_id", ""))
    if not osm_id:
        return None
    if not math.isfinite(score):
        return {"after_invalid_osm_id": osm_id}
    return {"after_score": score, "after_osm_id": osm_id}


def _json_safe(value: Any) -> Any:
    """Normalize non-finite numbers so CLI JSON remains standards-compliant."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _sort_and_limit(
    rows: Iterable[dict[str, Any]], limit: int, offset: int = 0
) -> list[dict[str, Any]]:
    """Sort rows deterministically while bounding work for paged queries.

    ``limit=0`` intentionally preserves the CLI's all-matches behavior. For a
    bounded page, only the first ``offset + limit`` rows are retained in a
    heap, avoiding a full materialized sort for CSV/JSONL fallback backends.
    """
    if limit == 0:
        return sorted(rows, key=_query_sort_key)[offset:]

    keep = offset + limit
    if keep <= 0:
        return []
    retained: list[tuple[_WorstFirst, int, dict[str, Any]]] = []
    for sequence, row in enumerate(rows):
        key = _WorstFirst(row)
        item = (key, sequence, row)
        if len(retained) < keep:
            heapq.heappush(retained, item)
        elif retained[0][0] < key:
            heapq.heapreplace(retained, item)
    result = [item[2] for item in retained]
    result.sort(key=_query_sort_key)
    return result[offset:]


def _iter_flat(path: Path, *, jsonl: bool) -> Iterator[dict[str, Any]]:
    if jsonl:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL row {line_number} in {path}: {exc}") from exc
                if not isinstance(row, dict):
                    raise TypeError(f"JSONL row {line_number} in {path} is not an object")
                yield row
        return
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def _query_duckdb(
    path: Path,
    *,
    parquet: bool,
    osm_id: str | None,
    group: str | None,
    is_control: str | None,
    min_score: float | None,
    after_score: float | None,
    after_osm_id: str | None,
    after_invalid_osm_id: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - guarded by select_backend
        raise RuntimeError("DuckDB is required for this query backend") from exc

    connection = duckdb.connect(":memory:") if parquet else duckdb.connect(str(path), read_only=True)
    try:
        parameters: list[Any] = []
        if parquet:
            source = "read_parquet(?)"
            parameters.append(str(path))
        else:
            source = '"analysis_results"'
        clauses = ["1 = 1"]
        score_value = 'TRY_CAST("score" AS DOUBLE)'
        score_order = f"CASE WHEN isfinite({score_value}) THEN {score_value} ELSE NULL END"
        if osm_id:
            clauses.append('CAST("osm_id" AS VARCHAR) = ?')
            parameters.append(osm_id)
        if group:
            clauses.append('CAST("group" AS VARCHAR) = ?')
            parameters.append(group)
        if is_control is not None:
            clauses.append('TRY_CAST("is_control" AS INTEGER) = ?')
            parameters.append(is_control)
        if min_score is not None:
            clauses.append(f"{score_order} >= ?")
            parameters.append(min_score)
        if after_invalid_osm_id is not None:
            clauses.append(
                f"{score_order} IS NULL AND CAST(\"osm_id\" AS VARCHAR) > ?"
            )
            parameters.append(after_invalid_osm_id)
        elif after_score is not None:
            clauses.append(
                f"({score_order} < ? OR {score_order} IS NULL OR "
                f"({score_order} = ? AND CAST(\"osm_id\" AS VARCHAR) > ?))"
            )
            parameters.extend([after_score, after_score, after_osm_id])
        sql = (
            f"SELECT * FROM {source} WHERE {' AND '.join(clauses)} "
            f"ORDER BY {score_order} DESC NULLS LAST, CAST(\"osm_id\" AS VARCHAR)"
        )
        if limit:
            sql += " LIMIT ?"
            parameters.append(limit)
        elif offset:
            sql += " LIMIT ALL"
        if offset:
            sql += " OFFSET ?"
            parameters.append(offset)
        cursor = connection.execute(sql, parameters)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _query_parquet_with_pyarrow(
    path: Path,
    *,
    osm_id: str | None,
    group: str | None,
    is_control: str | None,
    min_score: float | None,
    after_score: float | None,
    after_osm_id: str | None,
    after_invalid_osm_id: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    try:
        from pyarrow import parquet
    except ImportError as exc:  # pragma: no cover - guarded by select_backend
        raise RuntimeError("PyArrow is required for this query backend") from exc
    rows = parquet.read_table(path).to_pylist()
    matches = (
        row
        for row in rows
        if _matches(
            row,
            osm_id=osm_id,
            group=group,
            is_control=is_control,
            min_score=min_score,
            after_score=after_score,
            after_osm_id=after_osm_id,
            after_invalid_osm_id=after_invalid_osm_id,
        )
    )
    return _sort_and_limit(matches, limit, offset)


def _query_backend_rows(
    out_dir: Path,
    selected: str,
    *,
    osm_id: str | None,
    group: str | None,
    is_control: str | None,
    min_score: float | None,
    after_score: float | None,
    after_osm_id: str | None,
    after_invalid_osm_id: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Query one already-selected backend without changing selection policy."""
    path = out_dir / BACKEND_FILES[selected]
    if selected == "duckdb":
        return _query_duckdb(
            path,
            parquet=False,
            osm_id=osm_id,
            group=group,
            is_control=is_control,
            min_score=min_score,
            after_score=after_score,
            after_osm_id=after_osm_id,
            after_invalid_osm_id=after_invalid_osm_id,
            limit=limit,
            offset=offset,
        )
    if selected == "parquet" and _module_available("duckdb"):
        return _query_duckdb(
            path,
            parquet=True,
            osm_id=osm_id,
            group=group,
            is_control=is_control,
            min_score=min_score,
            after_score=after_score,
            after_osm_id=after_osm_id,
            after_invalid_osm_id=after_invalid_osm_id,
            limit=limit,
            offset=offset,
        )
    if selected == "parquet":
        return _query_parquet_with_pyarrow(
            path,
            osm_id=osm_id,
            group=group,
            is_control=is_control,
            min_score=min_score,
            after_score=after_score,
            after_osm_id=after_osm_id,
            after_invalid_osm_id=after_invalid_osm_id,
            limit=limit,
            offset=offset,
        )
    matches = (
        row
        for row in _iter_flat(path, jsonl=selected == "jsonl")
        if _matches(
            row,
            osm_id=osm_id,
            group=group,
            is_control=is_control,
            min_score=min_score,
            after_score=after_score,
            after_osm_id=after_osm_id,
            after_invalid_osm_id=after_invalid_osm_id,
        )
    )
    return _sort_and_limit(matches, limit, offset)


def query_rows(
    out_dir: Path,
    *,
    backend: str = "auto",
    backend_errors: list[str] | None = None,
    osm_id: str | None = None,
    group: str | None = None,
    is_control: str | None = None,
    min_score: float | None = None,
    after_score: float | None = None,
    after_osm_id: str | None = None,
    after_invalid_osm_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(backend, rows)`` for a bounded analysis query."""
    if limit < 0:
        raise ValueError("limit must be zero or greater; use 0 for all matching rows")
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if min_score is not None and not math.isfinite(min_score):
        raise ValueError("min_score must be finite")
    has_finite_cursor = after_score is not None or after_osm_id is not None
    if has_finite_cursor and (after_score is None or after_osm_id is None):
        raise ValueError("after_score and after_osm_id must be provided together")
    if has_finite_cursor and after_invalid_osm_id is not None:
        raise ValueError("finite cursor and invalid-score cursor cannot be combined")
    if after_score is not None and not math.isfinite(after_score):
        raise ValueError("after_score must be finite")
    if after_score is not None and not str(after_osm_id).strip():
        raise ValueError("after_osm_id must be non-empty")
    if after_invalid_osm_id is not None and not str(after_invalid_osm_id).strip():
        raise ValueError("after_invalid_osm_id must be non-empty")
    if (after_score is not None or after_invalid_osm_id is not None) and offset:
        raise ValueError("offset cannot be combined with cursor pagination")
    if is_control in {"target", "control"}:
        is_control = "0" if is_control == "target" else "1"
    elif is_control not in {None, "0", "1"}:
        raise ValueError("is_control must be target, control, 0, or 1")
    if backend == "auto":
        available = available_backends(out_dir)
        candidates = [name for name in BACKENDS if available[name]]
        if not candidates:
            select_backend(out_dir, backend)
    else:
        candidates = [select_backend(out_dir, backend)]
    errors: list[str] = []
    for selected in candidates:
        try:
            rows = _query_backend_rows(
                out_dir,
                selected,
                osm_id=osm_id,
                group=group,
                is_control=is_control,
                min_score=min_score,
                after_score=after_score,
                after_osm_id=after_osm_id,
                after_invalid_osm_id=after_invalid_osm_id,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            if backend != "auto":
                raise
            errors.append(f"{selected}: {exc}")
            continue
        if backend_errors is not None:
            backend_errors.extend(errors)
        return selected, rows
    detail = "; ".join(errors) or "no backend was available"
    if backend_errors is not None:
        backend_errors.extend(errors)
    raise RuntimeError(f"Automatic query backend selection failed: {detail}")


def write_rows(rows: list[dict[str, Any]], output_format: str, stream: TextIO) -> None:
    """Write query results as JSON, JSONL, or CSV to ``stream``."""
    if output_format == "json":
        json.dump(
            _json_safe(rows),
            stream,
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        )
        stream.write("\n")
    elif output_format == "jsonl":
        stream.writelines(
            json.dumps(
                _json_safe(row), ensure_ascii=False, default=str, allow_nan=False
            )
            + "\n"
            for row in rows
        )
    else:
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for field in row:
                if field not in seen:
                    seen.add(field)
                    fieldnames.append(field)
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--out-dir", default=None, help="directory containing generated exports")
    parser.add_argument("--backend", choices=("auto",) + BACKENDS, default="auto")
    parser.add_argument("--osm-id", help="exact OSM ID, such as way/2113323140")
    parser.add_argument("--group", help="exact analysis group, such as worship")
    parser.add_argument("--is-control", choices=("target", "control"), help="restrict row class")
    parser.add_argument("--min-score", type=float, help="minimum candidate score")
    parser.add_argument(
        "--after-score",
        type=float,
        help="cursor score from the last row of the previous page",
    )
    parser.add_argument(
        "--after-osm-id",
        help="cursor OSM ID from the last row of the previous page",
    )
    parser.add_argument(
        "--after-invalid-osm-id",
        help="invalid-score cursor OSM ID from the last row of the previous page",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum rows to return; use 0 for all matching rows (default: 100)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="number of sorted matching rows to skip before applying --limit (default: 0)",
    )
    parser.add_argument("--format", choices=("json", "jsonl", "csv"), default="json")
    args = parser.parse_args(argv)
    control_value = {"target": "0", "control": "1"}.get(args.is_control)
    out_dir = project_output_tree_path(args.out_dir)
    backend_errors: list[str] = []
    try:
        backend, rows = query_rows(
            out_dir,
            backend=args.backend,
            backend_errors=backend_errors,
            osm_id=args.osm_id,
            group=args.group,
            is_control=control_value,
            min_score=args.min_score,
            after_score=args.after_score,
            after_osm_id=args.after_osm_id,
            after_invalid_osm_id=args.after_invalid_osm_id,
            limit=args.limit,
            offset=args.offset,
        )
    except (FileNotFoundError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if backend_errors:
        print(
            "[query] automatic backend fallback: " + "; ".join(backend_errors),
            file=sys.stderr,
        )
    print(f"[query] backend={backend}; rows={len(rows):,}", file=sys.stderr)
    write_rows(rows, args.format, sys.stdout)


if __name__ == "__main__":
    main()
