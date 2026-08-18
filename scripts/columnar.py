#!/usr/bin/env python3
"""Export analysis rows to scalable columnar/queryable formats when available."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from runtime import atomic_write_json, project_path
except ImportError:
    from scripts.runtime import atomic_write_json, project_path


PARQUET_BATCH_SIZE = 10_000
COLUMNAR_CONTRACT = "ireland-geometry.columnar.v1"
NONFINITE_TOKENS = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


def read_csv(path: Path) -> list[dict[str, str]]:
    """Compatibility helper for callers that explicitly need all rows."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def iter_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def iter_csv_batches(path: Path, batch_size: int) -> Iterator[list[dict[str, str]]]:
    batch: list[dict[str, str]] = []
    for row in iter_csv_rows(path):
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def write_jsonl(path: Path, rows: Iterable[dict[str, str]]) -> int:
    """Stream JSONL through an atomic sibling file and return its row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        remove_artifact(temporary)
    return count


def csv_fieldnames(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def _numeric_token(value: Any) -> tuple[bool, str | None]:
    """Return a stable numeric representation without conflating text columns."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True, None
    text = str(value).strip()
    if text.lower() in NONFINITE_TOKENS:
        return True, text.lower()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return False, None
    if not number.is_finite():
        return True, text.lower()
    if number == 0:
        number = Decimal(0)
    return True, format(number.normalize(), "f")


def infer_field_kinds(path: Path, fields: list[str]) -> dict[str, str]:
    """Infer enough type information to compare typed and string exports safely."""
    kinds = {field: "numeric" for field in fields}
    for row in iter_csv_rows(path):
        for field in fields:
            is_numeric, _ = _numeric_token(row.get(field))
            if not is_numeric:
                kinds[field] = "string"
    return kinds


def schema_sha256(fields: list[str], kinds: dict[str, str]) -> str:
    payload = json.dumps(
        {"columns": fields, "field_kinds": {field: kinds.get(field, "string") for field in fields}},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_cell(value: Any, kind: str) -> list[str | None]:
    if kind == "numeric":
        is_numeric, token = _numeric_token(value)
        if is_numeric:
            return ["number", token]
    if value is None or (isinstance(value, str) and not value):
        return ["text", None]
    return ["text", str(value)]


def _canonical_row(row: dict[str, Any], fields: list[str], kinds: dict[str, str]) -> str:
    values = [
        _canonical_cell(row.get(field), kinds.get(field, "string"))
        for field in fields
    ]
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def row_digest(
    rows: Iterable[dict[str, Any]], fields: list[str], kinds: dict[str, str]
) -> tuple[int, str, list[str], list[str]]:
    """Return an order-independent multiset digest and observed schema issues."""
    hashes: list[str] = []
    issues: set[str] = set()
    observed_fields: list[str] | None = None
    for row in rows:
        current_fields = [str(key) for key in row]
        if observed_fields is None:
            observed_fields = current_fields
        elif current_fields != observed_fields:
            issues.add("inconsistent column order")
        issues.update(f"unexpected column: {key}" for key in row if key not in fields)
        issues.update(f"missing column: {field}" for field in fields if field not in row)
        hashes.append(_canonical_row(row, fields, kinds))
    hashes.sort()
    digest = hashlib.sha256()
    for item in hashes:
        digest.update(item.encode("ascii"))
        digest.update(b"\n")
    return len(hashes), digest.hexdigest(), sorted(issues), observed_fields or []


def _iter_jsonl_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"JSONL row {line_number} is not an object")
            yield value


def _iter_parquet_rows(path: Path) -> Iterator[dict[str, Any]]:
    from pyarrow import parquet

    parquet_file = parquet.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=PARQUET_BATCH_SIZE):
        yield from batch.to_pylist()


def _iter_duckdb_rows(path: Path) -> Iterator[dict[str, Any]]:
    import duckdb

    connection = duckdb.connect(str(path), read_only=True)
    try:
        cursor = connection.execute('SELECT * FROM "analysis_results"')
        fields = [description[0] for description in cursor.description]
        while batch := cursor.fetchmany(PARQUET_BATCH_SIZE):
            for values in batch:
                yield dict(zip(fields, values))
    finally:
        connection.close()


def _backend_rows(path: Path, backend: str) -> Iterator[dict[str, Any]]:
    if backend == "csv":
        yield from iter_csv_rows(path)
    elif backend == "jsonl":
        yield from _iter_jsonl_rows(path)
    elif backend == "parquet":
        yield from _iter_parquet_rows(path)
    elif backend == "duckdb":
        yield from _iter_duckdb_rows(path)
    else:
        raise ValueError(f"Unknown export backend {backend!r}")


def audit_backend(
    path: Path,
    backend: str,
    fields: list[str],
    kinds: dict[str, str],
    expected_schema: str,
) -> dict[str, Any]:
    """Hash one export's normalized rows for cross-backend parity checks."""
    count, digest, issues, observed_fields = row_digest(
        _backend_rows(path, backend), fields, kinds
    )
    observed_kinds = {field: kinds.get(field, "string") for field in observed_fields}
    actual_schema = schema_sha256(observed_fields, observed_kinds)
    result: dict[str, Any] = {
        "status": "available",
        "path": str(path),
        "rows": count,
        "schema_sha256": actual_schema,
        "schema_matches_expected": actual_schema == expected_schema,
        "row_digest": digest,
    }
    if issues:
        result["schema_issues"] = issues
    return result


def write_parquet(path: Path, source: Path, *, batch_size: int = PARQUET_BATCH_SIZE) -> int:
    """Write a string-typed CSV projection to Parquet in bounded batches."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    fieldnames = csv_fieldnames(source)
    schema = pa.schema([pa.field(field, pa.string()) for field in fieldnames])
    temporary = temporary_output_path(path)
    count = 0
    writer = None
    try:
        writer = pq.ParquetWriter(temporary, schema, compression="zstd")
        for batch in iter_csv_batches(source, max(1, batch_size)):
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            count += len(batch)
        writer.close()
        writer = None
        os.replace(temporary, path)
    finally:
        if writer is not None:
            writer.close()
        remove_artifact(temporary)
    return count


def remove_artifact(path: Path) -> None:
    """Remove a generated optional artifact before recalculating its status."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def temporary_output_path(destination: Path) -> Path:
    """Return a non-existent sibling path suitable for atomic replacement."""
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    remove_artifact(temporary)
    return temporary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    source = out / "analysis_results.csv"
    if not source.exists():
        raise SystemExit(f"Missing {source}. Run analyze.py first.")
    fields = csv_fieldnames(source)
    if not fields:
        raise SystemExit(f"Missing CSV header in {source}.")
    kinds = infer_field_kinds(source, fields)
    expected_schema = schema_sha256(fields, kinds)
    expected_rows, expected_digest, source_issues, _source_fields = row_digest(
        iter_csv_rows(source), fields, kinds
    )
    if source_issues:
        raise SystemExit(f"Invalid source schema: {', '.join(source_issues)}")
    row_count = write_jsonl(out / "analysis_results.jsonl", iter_csv_rows(source))
    if row_count != expected_rows:
        raise SystemExit(
            f"JSONL row count changed while exporting: {row_count} != {expected_rows}"
        )
    parquet_path = out / "analysis_results.parquet"
    duckdb_path = out / "analysis.duckdb"
    remove_artifact(parquet_path)
    remove_artifact(duckdb_path)
    status = {
        "contract": COLUMNAR_CONTRACT,
        "rows": row_count,
        "columns": fields,
        "field_kinds": kinds,
        "schema_sha256": expected_schema,
        "row_digest": expected_digest,
        "parity": {
            "status": "pending",
            "expected_row_digest": expected_digest,
            "checked_backends": [],
            "mismatches": [],
        },
        "csv": {
            "status": "available",
            "path": str(source),
            "rows": expected_rows,
            "schema_sha256": expected_schema,
            "row_digest": expected_digest,
        },
        "jsonl": {
            "status": "available",
            "path": str(out / "analysis_results.jsonl"),
        },
        "parquet": {"status": "not_installed", "path": str(parquet_path)},
        "duckdb": {"status": "not_installed", "path": str(duckdb_path)},
        "method": "JSONL fallback is always available; Parquet and DuckDB use optional dependencies.",
    }
    try:
        write_parquet(parquet_path, source)
        status["parquet"] = {
            "status": "available",
            "path": str(parquet_path),
            "engine": "pyarrow",
            "batch_size": PARQUET_BATCH_SIZE,
        }
    except ImportError:
        pass
    except (OSError, ValueError) as exc:
        status["parquet"] = {"status": "error", "path": str(parquet_path), "error": str(exc)}
    try:
        import duckdb

        temporary = temporary_output_path(duckdb_path)
        connection = None
        try:
            connection = duckdb.connect(str(temporary))
            connection.execute("CREATE OR REPLACE TABLE analysis_results AS SELECT * FROM read_csv_auto(?)", [str(source)])
            connection.execute("CHECKPOINT")
            connection.close()
            connection = None
            os.replace(temporary, duckdb_path)
        finally:
            if connection is not None:
                connection.close()
            remove_artifact(temporary)
            remove_artifact(Path(f"{temporary}.wal"))
        status["duckdb"] = {"status": "available", "path": str(duckdb_path), "engine": "duckdb"}
    except ImportError:
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        status["duckdb"] = {"status": "error", "path": str(duckdb_path), "error": str(exc)}

    parity = status["parity"]
    for backend in ("csv", "jsonl", "parquet", "duckdb"):
        info = status[backend]
        if info.get("status") != "available":
            continue
        path = Path(str(info["path"]))
        try:
            audited = audit_backend(path, backend, fields, kinds, expected_schema)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ImportError, RuntimeError) as exc:
            info["status"] = "error"
            info["error"] = f"{type(exc).__name__}: {exc}"
            parity["mismatches"].append(f"{backend}: {info['error']}")
            continue
        info.update(audited)
        parity["checked_backends"].append(backend)
        if audited["rows"] != expected_rows:
            parity["mismatches"].append(
                f"{backend}: row count {audited['rows']} != {expected_rows}"
            )
        if audited["schema_sha256"] != expected_schema:
            parity["mismatches"].append(f"{backend}: schema digest mismatch")
        if audited["row_digest"] != expected_digest:
            parity["mismatches"].append(f"{backend}: row digest mismatch")
        if audited.get("schema_issues"):
            parity["mismatches"].append(
                f"{backend}: schema issues {audited['schema_issues']}"
            )
    parity["status"] = "pass" if not parity["mismatches"] else "fail"
    parity["coverage"] = (
        "full"
        if all(status[name].get("status") == "available" for name in ("csv", "jsonl", "parquet", "duckdb"))
        else "partial"
    )
    atomic_write_json(out / "columnar_status.json", status, indent=2)
    print(
        f"[columnar] rows={row_count:,}; parity={parity['status']}/{parity['coverage']}; "
        f"parquet={status['parquet']['status']}; duckdb={status['duckdb']['status']}"
    )


if __name__ == "__main__":
    main()
