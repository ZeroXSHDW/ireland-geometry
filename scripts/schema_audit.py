#!/usr/bin/env python3
"""Validate key CSV artifacts against the tracked schema registry."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

try:
    from runtime import (
        PACKAGE_ROOT,
        atomic_write_json,
        git_revision,
        package_version,
        project_input_path,
        project_output_tree_path,
        utc_now,
    )
except ImportError:
    from scripts.runtime import (
        PACKAGE_ROOT,
        atomic_write_json,
        git_revision,
        package_version,
        project_input_path,
        project_output_tree_path,
        utc_now,
    )


def validate_csv(path: Path, spec: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    row_n = 0
    fieldnames: list[str] = []
    if not path.exists() or not path.is_file():
        return {"path": str(path), "passed": False, "row_n": 0, "errors": ["missing artifact"]}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            required = {str(value) for value in spec.get("required_columns", [])}
            missing = sorted(required - set(fieldnames))
            if missing:
                errors.append("missing columns: " + ", ".join(missing))
            for alternatives in spec.get("required_any_columns", []):
                choices = [str(value) for value in alternatives]
                if not any(choice in fieldnames for choice in choices):
                    errors.append("missing one of: " + ", ".join(choices))
            numeric = [str(value) for value in spec.get("numeric_columns", [])]
            seen: set[str] = set()
            unique_id = str(spec["unique_id"]) if spec.get("unique_id") else None
            for row_index, row in enumerate(reader, 2):
                row_n += 1
                if unique_id:
                    identifier = row.get(unique_id, "")
                    if identifier in seen:
                        errors.append(f"duplicate {unique_id} at row {row_index}: {identifier}")
                    seen.add(identifier)
                for field in numeric:
                    raw = row.get(field, "").strip()
                    if not raw:
                        continue
                    try:
                        value = float(raw)
                    except ValueError:
                        errors.append(f"row {row_index} has non-numeric {field}: {raw!r}")
                        continue
                    if not math.isfinite(value):
                        errors.append(f"row {row_index} has non-finite {field}")
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"could not read CSV: {exc}")
    if row_n == 0 and not spec.get("allow_empty"):
        errors.append("artifact has no data rows")
    return {
        "path": str(path),
        "passed": not errors,
        "row_n": row_n,
        "columns": fieldnames,
        "errors": errors,
    }


def audit(schema_path: Path, out_dir: Path) -> dict[str, object]:
    try:
        registry = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema_path": str(schema_path),
            "schema_version": None,
            "passed": False,
            "errors": [f"invalid schema registry: {exc}"],
            "artifacts": {},
            "checked_at": utc_now(),
            "git_revision": git_revision(),
        }
    artifacts = {
        name: validate_csv(out_dir / name, spec)
        for name, spec in registry.get("artifacts", {}).items()
    }
    errors = [
        f"{name}: {error}"
        for name, result in artifacts.items()
        for error in result.get("errors", [])
    ]
    return {
        "schema_path": str(schema_path),
        "schema_version": registry.get("schema_version"),
        "passed": not errors,
        "errors": errors,
        "artifacts": artifacts,
        "checked_at": utc_now(),
        "git_revision": git_revision(),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--schema", default=None, help="schema registry JSON path")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the audit result as machine-readable JSON",
    )
    args = parser.parse_args(argv)
    out = project_output_tree_path(args.out_dir)
    schema = (
        project_input_path(
            args.schema,
            "schemas/artifacts.json",
            label="schema input",
        )
        if args.schema
        else PACKAGE_ROOT / "schemas" / "artifacts.json"
    )
    result = audit(schema, out)
    atomic_write_json(out / "schema_validation.json", result, indent=2)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if not result["passed"]:
            raise SystemExit(1)
        return
    if result["passed"]:
        print(f"[schema-audit] PASS ({len(result['artifacts'])} artifacts)")
        return
    print("[schema-audit] FAIL")
    for error in result["errors"]:
        print(f"[schema-audit]   {error}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
