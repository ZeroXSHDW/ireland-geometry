#!/usr/bin/env python3
"""Build historical-validation records and review-ready candidate dossiers.

The statistical pipeline can identify mapped geometric associations; it cannot
establish what a building was intended to mean.  This stage turns each target
into a traceable evidence record using the available NIAH inventory, OSM
heritage references, architect evidence, and an optional manually curated
reference CSV.  Missing historical sources are represented as missing rather
than silently treated as negative evidence.

Optional reference CSV columns: ``osm_id``, ``reg_no``, ``source``,
``source_type``, ``source_url``, ``archive_ref``, ``verified``, ``independent``,
``year``, ``evidence_text``, ``image_path``, ``plan_path`` and ``notes``.

Reads:  data/combined.json, output/analysis_results.csv, output/niah_join.csv,
        output/architects_evidence.csv, optional historical reference CSV
Writes: output/historical_validation.csv
        output/candidate_dossiers.csv
        output/historical_source_register.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

try:
    from runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_csv,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_references(path: Path | None) -> dict[str, list[dict[str, str]]]:
    if path is None or not path.exists():
        return {}
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(path):
        for key in (row.get("osm_id"), row.get("reg_no")):
            if key:
                out[key].append(row)
    return out


def evidence_status(row: dict, tags: dict, references: list[dict], architect: dict | None) -> tuple[str, str]:
    sources = []
    if row.get("reg_no"):
        sources.append("NIAH inventory match")
    if architect:
        sources.append("NIAH architect evidence")
    if tags.get("ref:IE:niah") or tags.get("heritage") or tags.get("wikidata"):
        sources.append("OSM heritage metadata")
    if references:
        source_types = sorted({ref.get("source_type", "curated historical reference") for ref in references})
        sources.append("archival evidence: " + ", ".join(source_types))
    independent = any(
        ref.get("independent", "").strip().lower() in {"1", "true", "yes", "y"}
        or ref.get("verified", "").strip().lower() in {"1", "true", "yes", "y"}
        for ref in references
    )
    if references or architect:
        status = "independent_reference" if references and independent else "curated_reference_unverified" if references else "inventory_and_architect_evidence"
    elif row.get("reg_no"):
        status = "inventory_matched"
    elif tags.get("ref:IE:niah") or tags.get("heritage") or tags.get("wikidata"):
        status = "osm_heritage_tagged"
    else:
        status = "manual_review_required"
    warnings = []
    if not row.get("reg_no"):
        warnings.append("no NIAH match")
    if row.get("match_mode") == "near":
        warnings.append("NIAH match is nearest-point fallback")
    if not architect:
        warnings.append("no architect attribution extracted")
    if not references:
        warnings.append("no independent reference supplied")
    summary = "; ".join(sources)
    if warnings:
        summary += (" | " if summary else "") + "warnings: " + "; ".join(warnings)
    return status, summary


def build_validation(
    analysis: list[dict],
    elements: list[dict],
    joins: list[dict],
    evidence: list[dict],
    references: dict[str, list[dict]],
) -> list[dict]:
    tags_by_id = {
        f"{element.get('osm_type', 'way')}/{element['id']}": element.get("tags", {})
        for element in elements
    }
    joins_by_id = {row["osm_id"]: row for row in joins}
    architects_by_id: dict[str, dict] = {}
    for row in evidence:
        architects_by_id.setdefault(row["osm_id"], row)
    out = []
    for row in analysis:
        if row.get("is_control") == "1":
            continue
        join = joins_by_id.get(row["osm_id"], {})
        tags = tags_by_id.get(row["osm_id"], {})
        architect = architects_by_id.get(row["osm_id"])
        refs = references.get(row["osm_id"], []) + references.get(join.get("reg_no", ""), [])
        status, evidence_summary = evidence_status({**join, "score": row.get("score")}, tags, refs, architect)
        score = float(row.get("score") or 0)
        angle = row.get("has_golden_angle") == "1"
        if score >= 75 or (score >= 55 and angle and join):
            priority = "high"
        elif score >= 55 or join:
            priority = "medium"
        else:
            priority = "low"
        warnings = []
        if row.get("repaired") == "1":
            warnings.append("geometry repaired")
        if row.get("multipart") == "1":
            warnings.append("multipart footprint")
        if not row.get("name"):
            warnings.append("unnamed OSM feature")
        if join.get("match_mode") == "near":
            warnings.append("near NIAH join")
        out.append(
            {
                "osm_id": row["osm_id"],
                "name": row.get("name", ""),
                "group": row.get("group", ""),
                "score": row.get("score", ""),
                "flags": row.get("flags", ""),
                "has_golden_angle": row.get("has_golden_angle", "0"),
                "geometry_quality": "repaired" if row.get("repaired") == "1" else "mapped_valid",
                "reg_no": join.get("reg_no", ""),
                "niah_name": join.get("niah_name", ""),
                "source_region": join.get("source_region", ""),
                "county": join.get("county", ""),
                "rating": join.get("rating", ""),
                "niah_type": join.get("niah_type", ""),
                "date_mid": join.get("date_mid", ""),
                "century": join.get("century", ""),
                "match_mode": join.get("match_mode", ""),
                "dist_m": join.get("dist_m", ""),
                "architect": architect.get("architect", "") if architect else "",
                "architect_confidence": architect.get("confidence", "") if architect else "",
                "architect_evidence": architect.get("evidence", "") if architect else "",
                "osm_niah_ref": tags.get("ref:IE:niah", ""),
                "osm_heritage": tags.get("heritage", ""),
                "wikidata": tags.get("wikidata", ""),
                "reference_count": len(refs),
                "reference_sources": "; ".join(sorted({ref.get("source", "") for ref in refs if ref.get("source")})),
                "reference_types": "; ".join(sorted({ref.get("source_type", "") for ref in refs if ref.get("source_type")})),
                "reference_urls": "; ".join(sorted({ref.get("source_url", "") for ref in refs if ref.get("source_url")})),
                "archive_refs": "; ".join(sorted({ref.get("archive_ref", "") for ref in refs if ref.get("archive_ref")})),
                "verified_reference_count": sum(
                    ref.get("verified", "").strip().lower() in {"1", "true", "yes", "y"} for ref in refs
                ),
                "independent_reference_count": sum(
                    ref.get("independent", "").strip().lower() in {"1", "true", "yes", "y"} for ref in refs
                ),
                "evidence_text": " | ".join(ref.get("evidence_text", "") for ref in refs if ref.get("evidence_text")),
                "image_paths": "; ".join(ref.get("image_path", "") for ref in refs if ref.get("image_path")),
                "plan_paths": "; ".join(ref.get("plan_path", "") for ref in refs if ref.get("plan_path")),
                "validation_status": status,
                "evidence_summary": evidence_summary,
                "review_priority": priority,
                "review_warnings": "; ".join(warnings),
            }
        )
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--references", default=None, help="optional curated historical CSV")
    parser.add_argument("--top-n", type=int, default=1000)
    args = parser.parse_args(argv)
    data = project_data_tree_path(args.data_root)
    out_dir = project_output_tree_path(args.out_dir)
    combined = data / "combined.json"
    results = out_dir / "analysis_results.csv"
    if not combined.exists() or not results.exists():
        raise SystemExit("Missing combined.json or analysis_results.csv. Run fetch and analyze first.")
    elements = json.loads(combined.read_text(encoding="utf-8"))["elements"]
    analysis = read_csv(results)
    joins = read_csv(out_dir / "niah_join.csv")
    evidence = read_csv(out_dir / "architects_evidence.csv")
    reference_path = (
        project_input_path(
            args.references,
            "data/historical/references.csv",
            label="historical references",
        )
        if args.references
        else data / "historical" / "references.csv"
    )
    validation = build_validation(analysis, elements, joins, evidence, read_references(reference_path))
    fields = list(validation[0]) if validation else ["osm_id", "validation_status"]
    atomic_write_csv(out_dir / "historical_validation.csv", fields, validation)
    dossiers = sorted(
        validation,
        key=lambda row: (-float(row.get("score") or 0), row.get("osm_id", "")),
    )[: max(1, args.top_n)]
    dossier_fields = [
        "osm_id",
        "name",
        "group",
        "score",
        "flags",
        "geometry_quality",
        "reg_no",
        "niah_name",
        "source_region",
        "county",
        "rating",
        "niah_type",
        "date_mid",
        "century",
        "match_mode",
        "dist_m",
        "architect",
        "architect_confidence",
        "architect_evidence",
        "reference_count",
        "reference_sources",
        "reference_types",
        "reference_urls",
        "archive_refs",
        "verified_reference_count",
        "independent_reference_count",
        "evidence_text",
        "image_paths",
        "plan_paths",
        "validation_status",
        "evidence_summary",
        "review_priority",
        "review_warnings",
        "osm_niah_ref",
        "osm_heritage",
        "wikidata",
    ]
    atomic_write_csv(out_dir / "candidate_dossiers.csv", dossier_fields, dossiers, extrasaction="ignore")
    register = [
        {
            "source_type": "OpenStreetMap",
            "source": "data/combined.json",
            "status": "available",
            "coverage": "all analyzed mapped footprints",
            "notes": "community-mapped geometry and tags; not architectural evidence",
        },
        {
            "source_type": "NIAH",
            "source": "data/niah/niah.json",
            "status": "available" if (data / "niah" / "niah.json").exists() else "missing",
            "coverage": "Republic of Ireland heritage inventory",
            "notes": "spatial match mode and distance retained per record",
        },
        {
            "source_type": "Architect evidence",
            "source": "output/architects_evidence.csv",
            "status": "available" if evidence else "missing",
            "coverage": "NIAH free-text attributions accepted by validated regexes",
            "notes": "exploratory attribution, not independent authorship proof",
        },
        {
            "source_type": "Curated historical references",
            "source": str(reference_path),
            "status": "available" if reference_path.exists() else "not_provided",
            "coverage": "user-supplied records only",
            "notes": "supports historic maps, plans, archive records, and photographs; verification and independence are retained per row",
        },
    ]
    atomic_write_csv(out_dir / "historical_source_register.csv", list(register[0]), register)
    statuses = defaultdict(int)
    for row in validation:
        statuses[row["validation_status"]] += 1
    print(f"[history] wrote {len(validation):,} validation rows and {len(dossiers):,} dossiers")
    print("[history] statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))


if __name__ == "__main__":
    main()
