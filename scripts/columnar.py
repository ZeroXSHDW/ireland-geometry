#!/usr/bin/env python3
"""Export analysis rows to scalable columnar/queryable formats when available."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    from runtime import atomic_write_json, atomic_write_text, project_path
except ImportError:
    from scripts.runtime import atomic_write_json, atomic_write_text, project_path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    atomic_write_text(path, text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    source = out / "analysis_results.csv"
    if not source.exists():
        raise SystemExit(f"Missing {source}. Run analyze.py first.")
    rows = read_csv(source)
    write_jsonl(out / "analysis_results.jsonl", rows)
    status = {
        "rows": len(rows),
        "jsonl": {"status": "available", "path": str(out / "analysis_results.jsonl")},
        "parquet": {"status": "not_installed", "path": str(out / "analysis_results.parquet")},
        "duckdb": {"status": "not_installed", "path": str(out / "analysis.duckdb")},
        "method": "JSONL fallback is always available; Parquet and DuckDB use optional dependencies.",
    }
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(rows)
        pq.write_table(table, out / "analysis_results.parquet", compression="zstd")
        status["parquet"] = {"status": "available", "path": str(out / "analysis_results.parquet"), "engine": "pyarrow"}
    except ImportError:
        pass
    except (OSError, ValueError) as exc:
        status["parquet"] = {"status": "error", "path": str(out / "analysis_results.parquet"), "error": str(exc)}
    try:
        import duckdb

        connection = duckdb.connect(str(out / "analysis.duckdb"))
        connection.execute("CREATE OR REPLACE TABLE analysis_results AS SELECT * FROM read_csv_auto(?)", [str(source)])
        connection.execute("CHECKPOINT")
        connection.close()
        status["duckdb"] = {"status": "available", "path": str(out / "analysis.duckdb"), "engine": "duckdb"}
    except ImportError:
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        status["duckdb"] = {"status": "error", "path": str(out / "analysis.duckdb"), "error": str(exc)}
    atomic_write_json(out / "columnar_status.json", status, indent=2)
    print(
        f"[columnar] rows={len(rows):,}; parquet={status['parquet']['status']}; duckdb={status['duckdb']['status']}"
    )


if __name__ == "__main__":
    main()
