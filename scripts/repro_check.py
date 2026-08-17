#!/usr/bin/env python3
"""Check deterministic artifact hashes and optionally compare two output runs."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from runtime import atomic_write_json, project_path, sha256_file, utc_now
except ImportError:
    from scripts.runtime import atomic_write_json, project_path, sha256_file, utc_now


VOLATILE = {"manifest.json", "verification.json", "reproducibility.json", "report.html", "report_lazy.html", "report_data.json"}


def hashes(out: Path) -> dict[str, str]:
    return {
        path.name: digest
        for path in sorted(out.iterdir())
        if path.is_file() and path.name not in VOLATILE and (digest := sha256_file(path))
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--reference-out-dir", default=None, help="optional second run to compare")
    args = parser.parse_args(argv)
    out = project_path(args.out_dir, "output")
    current = hashes(out)
    errors = []
    reference = None
    if args.reference_out_dir:
        reference = hashes(project_path(args.reference_out_dir, "output"))
        names = sorted(set(current) | set(reference))
        for name in names:
            if current.get(name) != reference.get(name):
                errors.append(f"deterministic artifact differs: {name}")
    result = {
        "checked_at": utc_now(),
        "passed": not errors,
        "errors": errors,
        "artifact_count": len(current),
        "artifacts": current,
        "reference_comparison": bool(reference is not None),
        "notes": "Time-stamped manifests, verification files, and HTML report packs are excluded from byte-for-byte comparison.",
    }
    atomic_write_json(out / "reproducibility.json", result, indent=2)
    if errors:
        for error in errors:
            print(f"[repro] {error}")
        raise SystemExit(1)
    print(f"[repro] PASS ({len(current):,} stable artifacts)")


if __name__ == "__main__":
    main()
