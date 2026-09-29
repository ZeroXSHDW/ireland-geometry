#!/usr/bin/env python3
"""Publish a verified pipeline report into the committed GitHub Pages site."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from pages_audit import PAGES_PUBLISH_CONTRACT, audit_site
except ImportError:
    from scripts.pages_audit import PAGES_PUBLISH_CONTRACT, audit_site

try:
    from runtime import reject_symlink_root
except ImportError:
    from scripts.runtime import reject_symlink_root


REPORT_REDIRECT = """<!doctype html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=./">
<link rel="canonical" href="./">
<title>Ireland Geometric Pattern Scan</title>
<p><a href="./">Open the Ireland Geometric Pattern Scan dashboard</a>.</p>
"""
VALIDATION_RECORDS = {
    "schema_validation": "schema_validation.json",
    "reproducibility": "reproducibility.json",
}


class PublishError(RuntimeError):
    """Raised when the source output cannot safely become a Pages site."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublishError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PublishError(f"{label} must be a JSON object")
    return payload


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _required_file(root: Path, name: str, label: str) -> Path:
    path = root / name
    if not path.is_file() or path.is_symlink():
        raise PublishError(f"{label} is missing: {path}")
    return path


def _embedded_manifest(report_bytes: bytes) -> dict[str, Any]:
    try:
        text = report_bytes.decode("utf-8")
        marker = "const PACK = "
        start = text.index(marker) + len(marker)
        pack, _end = json.JSONDecoder().raw_decode(text, start)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PublishError(f"report.html has no valid embedded report pack: {exc}") from exc
    if not isinstance(pack, dict) or not isinstance(pack.get("manifest"), dict):
        raise PublishError("report.html embedded pack has no manifest object")
    return pack["manifest"]


def _validation_provenance(
    records: dict[str, dict[str, Any]], paths: dict[str, Path]
) -> dict[str, dict[str, Any]]:
    """Return compact, hashed provenance for the complete validation gate."""
    result: dict[str, dict[str, Any]] = {}
    for key, payload in records.items():
        path = paths[key]
        record: dict[str, Any] = {
            "path": path.name,
            "sha256": _sha256_file(path),
            "status": "pass",
            "passed": True,
        }
        for field in ("artifact_count", "schema_version"):
            value = payload.get(field)
            if type(value) is int and value >= 0:
                record[field] = value
        result[key] = record
    return result


def _validate_manifest_artifacts(
    manifest: dict[str, Any], output: Path, names: tuple[str, ...]
) -> None:
    """Require every publication input to match its current manifest record."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise PublishError("refusing to publish: manifest has no artifact inventory")
    records: dict[str, dict[str, Any]] = {}
    for record in artifacts:
        if not isinstance(record, dict):
            continue
        relative = record.get("relative_path")
        if record.get("path_base") == "output_dir" and isinstance(relative, str):
            if relative in records:
                raise PublishError(
                    f"refusing to publish: manifest has duplicate artifact record for {relative}"
                )
            records[relative] = record
    for name in names:
        record = records.get(name)
        if record is None:
            raise PublishError(f"refusing to publish: manifest has no artifact record for {name}")
        path = output / name
        expected_bytes = record.get("bytes")
        expected_hash = record.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 0:
            raise PublishError(f"refusing to publish: manifest has invalid byte count for {name}")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise PublishError(f"refusing to publish: manifest has invalid SHA-256 for {name}")
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            raise PublishError(f"refusing to publish: {name} byte count differs from manifest")
        if _sha256_file(path) != expected_hash:
            raise PublishError(f"refusing to publish: {name} hash differs from manifest")


def publish_pages(output_dir: str | Path = "output", site_dir: str | Path = "docs") -> dict[str, Any]:
    """Publish verified output artifacts and return the publication record."""
    try:
        output_input = reject_symlink_root(
            Path(output_dir).expanduser(),
            label="refusing to publish: output directory",
        )
        site_input = reject_symlink_root(
            Path(site_dir).expanduser(),
            label="refusing to publish: site directory",
        )
    except ValueError as exc:
        raise PublishError(str(exc)) from exc
    output = output_input.resolve()
    site = site_input.resolve()
    try:
        site.relative_to(output)
    except ValueError:
        pass
    else:
        raise PublishError(
            "refusing to publish: site directory must be outside the output directory"
        )
    report_path = _required_file(output, "report.html", "standalone report")
    review_path = _required_file(output, "review.html", "review page")
    manifest_path = _required_file(output, "manifest.json", "provenance manifest")
    verification_path = _required_file(output, "verification.json", "verification record")
    validation_paths = {
        key: _required_file(output, name, f"{key} record")
        for key, name in VALIDATION_RECORDS.items()
    }
    manifest = _read_json(manifest_path, "manifest.json")
    verification = _read_json(verification_path, "verification.json")
    validation = {
        key: _read_json(path, f"{key}.json") for key, path in validation_paths.items()
    }
    report_bytes = report_path.read_bytes()
    embedded_manifest = _embedded_manifest(report_bytes)

    if verification.get("passed") is not True:
        raise PublishError("refusing to publish: verification.json does not pass")
    for key, payload in validation.items():
        if payload.get("passed") is not True:
            raise PublishError(f"refusing to publish: {VALIDATION_RECORDS[key]} does not pass")
    alignment = verification.get("manifest_cache_alignment")
    if not isinstance(alignment, dict) or alignment.get("status") != "pass":
        raise PublishError(
            "refusing to publish: manifest/cache alignment is not a full pass"
        )
    revision = manifest.get("git_revision")
    generated_at = manifest.get("generated_at")
    if not isinstance(revision, str) or not revision:
        raise PublishError("refusing to publish: manifest has no git_revision")
    if not isinstance(generated_at, str) or not generated_at:
        raise PublishError("refusing to publish: manifest has no generated_at")
    if embedded_manifest.get("git_revision") != revision:
        raise PublishError("refusing to publish: report and output manifest revisions differ")
    embedded_generated_at = embedded_manifest.get("generated_at")
    if not isinstance(embedded_generated_at, str) or not embedded_generated_at:
        raise PublishError("refusing to publish: embedded report manifest has no generated_at")

    review_bytes = review_path.read_bytes()
    site.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=site.parent, prefix=f".{site.name}.publish-") as directory:
        staging = Path(directory)
        _atomic_write(staging / "index.html", report_bytes)
        _atomic_write(staging / "review.html", review_bytes)
        _atomic_write(staging / "report.html", REPORT_REDIRECT.encode("utf-8"))

        page_files = {}
        for name in ("index.html", "review.html", "report.html"):
            path = staging / name
            page_files[name] = {
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        publication = {
            "contract": PAGES_PUBLISH_CONTRACT,
            "version": 1,
            "source_manifest_revision": revision,
            "source_manifest_generated_at": embedded_generated_at,
            "source_output_manifest_generated_at": generated_at,
            "source_manifest_sha256": _sha256_file(manifest_path),
            "source_verification_sha256": _sha256_file(verification_path),
            "source_verification": {
                "passed": True,
                "manifest_cache_alignment": alignment,
                "analysis_rows": verification.get("analysis_rows"),
                "target_rows": verification.get("target_rows"),
            },
            "source_validation": _validation_provenance(validation, validation_paths),
            "files": page_files,
        }
        _atomic_write(
            staging / "pages_manifest.json",
            (json.dumps(publication, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        audit = audit_site(staging)
        if not audit.get("passed"):
            raise PublishError(
                "published site failed audit: " + "; ".join(audit.get("errors", []))
            )
        _validate_manifest_artifacts(
            manifest,
            output,
            ("report.html", "review.html", "verification.json", *VALIDATION_RECORDS.values()),
        )
        for name in ("index.html", "review.html", "report.html", "pages_manifest.json"):
            _atomic_write(site / name, (staging / name).read_bytes())

    audit = audit_site(site)
    if not audit.get("passed"):
        raise PublishError(
            "published site failed audit: " + "; ".join(audit.get("errors", []))
        )
    return {
        "contract": PAGES_PUBLISH_CONTRACT,
        "published": True,
        "source_manifest_revision": revision,
        "source_manifest_generated_at": embedded_generated_at,
        "source_output_manifest_generated_at": generated_at,
        "site_dir": str(site),
        "audit": audit,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output", help="verified pipeline output directory")
    parser.add_argument("--site-dir", default="docs", help="committed Pages directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        result = publish_pages(args.output_dir, args.site_dir)
    except (OSError, PublishError) as exc:
        if args.json:
            print(json.dumps({"contract": PAGES_PUBLISH_CONTRACT, "published": False, "error": str(exc)}))
        else:
            print(f"[pages-publish] FAIL: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "[pages-publish] PASS: "
            f"{result['source_manifest_revision']} -> {result['site_dir']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
