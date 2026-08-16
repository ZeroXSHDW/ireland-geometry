#!/usr/bin/env python3
"""Architect correlation (v4b): who designed the golden-angle churches?

The NIAH appraisal/composition free text names the architect for many
churches and country houses ("to a design by William Henry Byrne
(1844-1917) of Suffolk Street, Dublin"). This stage:

  1. extracts the architect from the NIAH text (validated regexes),
  2. joins it to the analyzed footprints (via output/niah_join.csv),
  3. tests: do architect-designed churches show the golden angle MORE
     than anonymous ones? Per-architect rate table for the report.

Reads:  data/niah/niah.json, output/niah_join.csv,
        output/analysis_results.csv
Writes: output/architects.csv (per-architect golden-angle rates)
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

try:
    from runtime import atomic_write_csv, project_path
    from stats import compare_proportions, wilson_interval
except ImportError:
    from scripts.runtime import atomic_write_csv, project_path
    from scripts.stats import compare_proportions, wilson_interval

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

NIAH_JSON = DATA / "niah" / "niah.json"
JOIN = OUT / "niah_join.csv"
RESULTS = OUT / "analysis_results.csv"

NAME_RE = re.compile(
    r"(?:to\s+(?:a\s+)?design(?:s)?|design(?:ed|s)?|works)\s+"
    r"(?:signed\s+|exhibited\s+|attributed\s+)?"
    r"(?:to\s+)?(?:by\s+|of\s+)?"
    r"(?:the\s+(?:firm\s+of\s+|architect(?:s)?\s+)?)?"
    r"([A-Z][A-Za-z'\.\-]*(?:\s+(?:and\s+|&)?[A-Z][A-Za-z'\.\-]*){0,4})"
)
SIMPLE_RE = re.compile(
    r"\bby\s+(?:the\s+(?:architect\s+)?)?"
    r"([A-Z][A-Za-z'\.\-]*(?:\s+(?:and\s+|&)?[A-Z][A-Za-z'\.\-]*){1,3})"
)
STOPWORDS = {
    "subscription",
    "catholic",
    "church",
    "chapel",
    "reverend",
    "rev",
    "bally",
    "the",
    "a",
    "an",
    "parochial",
    "grant",
    "voluntary",
    "lord",
    "bishop",
    "clergy",
    "congregation",
    "parishioners",
    "donations",
    "means",
    "local",
    "board",
    "commissioners",
    "subscriptions",
    "ecclesiastical",
    "presbyterian",
    "order",
    "monastery",
    "abbey",
    "religious",
    "funds",
    "support",
    "assistance",
    "labour",
    "tithe",
    "cemetery",
    "graveyard",
    "school",
    "house",
    "brothers",
    "sisters",
    "christian",
    "baptist",
    "methodist",
    "congregational",
    "quaker",
    "ordnance",
    "survey",
    "office",
    "public",
    "council",
    "committee",
    "submission",
    "proposal",
    "estimate",
    "contractor",
    "builder",
    "councils",
    "commission",
    "commissioner",
    "ecumenical",
    "roman",
    "of",
}


def clean_name(raw: str) -> str | None:
    """Normalize and validate an extracted architect name."""
    name = raw.strip()
    # cut at trailing context like ", of Dublin" or " (1844-1917)"
    name = re.split(r",\s|\(|'s\s", name)[0].strip()
    # cut at a sentence boundary ". NextWord", but never inside initials
    # ("J.J. McCarthy" keeps its initials; "J.J. McCarthy. Comprising"
    #  drops the trailing sentence)
    for m in re.finditer(r"\.\s+[A-Z]", name):
        prev = name[m.start() - 1] if m.start() > 0 else ""
        if prev.isupper() or prev == ".":
            continue  # "J." or "J.J." initials, not a boundary
        name = name[: m.start() + 1].strip()  # keep trailing period
        break
    name = name.rstrip(".")
    name = re.sub(r"\s+", " ", name)
    words = name.split()
    if len(words) < 2:  # need surname
        return None
    lowered = [word.lower().strip(".,") for word in words]
    if any(word in STOPWORDS for word in lowered):
        return None
    if words[0] in ("Sir", "Lady", "Lord", "St.", "Saint") and len(words) < 3:
        return None
    # drop "and Son" -> keep firm name
    name = re.sub(r"\s+(and|&)\s+(Son|Sons|Co\.?)\s*$", "", name)
    words = name.split()
    if len(words) < 2 or words[-1].lower().strip(".") in STOPWORDS:
        return None
    return name


def surname_key(name: str) -> tuple:
    """Group variants of the same person: (surname, initial-tuple).
    Dotted initials are expanded, so "J.J. McCarthy" and
    "James Joseph McCarthy" merge, but "James McCarthy" (a different
    person) does not."""
    toks = [t for t in re.sub(r"\.", " ", name).split() if t]
    if not toks:
        return ("", ())
    surname = toks[-1]
    initials = tuple(t[0].upper() for t in toks[:-1])
    return (surname, initials)


def extract_architect(rec: dict) -> str | None:
    detail = extract_architect_detail(rec)
    return detail["name"] if detail else None


def extract_architect_detail(rec: dict) -> dict | None:
    """Return a validated name plus the exact source evidence."""
    txt = f"{rec.get('composition', '')} {rec.get('appraisal', '')}"
    for m in NAME_RE.finditer(txt):
        name = clean_name(m.group(1))
        if name:
            return {"name": name, "evidence": m.group(0).strip(), "confidence": "high"}
    for m in SIMPLE_RE.finditer(txt):
        name = clean_name(m.group(1))
        if name and not any(w.lower().strip(".,") in STOPWORDS for w in name.split()):
            return {"name": name, "evidence": m.group(0).strip(), "confidence": "medium"}
    return None


def main() -> None:
    import argparse

    global DATA, OUT, NIAH_JSON, JOIN, RESULTS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    ap.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    args = ap.parse_args()
    DATA = project_path(args.data_root, "data")
    OUT = project_path(args.out_dir, "output")
    NIAH_JSON = DATA / "niah" / "niah.json"
    JOIN = OUT / "niah_join.csv"
    RESULTS = OUT / "analysis_results.csv"

    if not (NIAH_JSON.exists() and JOIN.exists() and RESULTS.exists()):
        raise SystemExit("Missing inputs. Run fetch_niah + niah + analyze first.")

    niah = json.loads(NIAH_JSON.read_text())
    joined = {j["reg_no"]: j for j in csv.DictReader(JOIN.open())}
    rows = list(csv.DictReader(RESULTS.open()))
    row_by_osm = {r["osm_id"]: r for r in rows}

    # map reg_no -> architect evidence (canonical display name via surname-key merge)
    raw_arch: dict[str, dict] = {}
    for rec in niah:
        detail = extract_architect_detail(rec)
        if detail:
            raw_arch[rec["reg_no"]] = detail
    display_name: dict[str, str] = {}
    for detail in raw_arch.values():
        name = detail["name"]
        key = surname_key(name)
        display_name.setdefault(key, name)
    arch_by_reg = {
        reg: {**detail, "name": display_name[surname_key(detail["name"])]}
        for reg, detail in raw_arch.items()
    }

    # join: reg_no -> osm_id -> analysis row
    n_named = n_anon = 0
    per_arch: dict[str, list] = defaultdict(list)
    evidence_rows: list[dict] = []
    for reg_no, j in joined.items():
        arch = arch_by_reg.get(reg_no)
        row = row_by_osm.get(j["osm_id"])
        if row is None or row["is_control"] == "1":
            continue
        if arch:
            per_arch[arch["name"]].append(row)
            evidence_rows.append(
                {
                    "osm_id": j["osm_id"],
                    "reg_no": reg_no,
                    "architect": arch["name"],
                    "confidence": arch["confidence"],
                    "evidence": arch["evidence"],
                }
            )
            n_named += 1
        else:
            n_anon += 1

    print(f"[arch] NIAH records with named architect: {len(arch_by_reg)}")
    print(f"[arch] joined footprints: named={n_named} anonymous={n_anon}")

    def ga_rate(rs):
        return sum(1 for r in rs if r["has_golden_angle"] == "1") / len(rs)

    binary_rows: list[dict] = []

    def bin_test(label, cls_rows):
        named = [
            r
            for r in cls_rows
            if rows_by_osm_id.get(r["osm_id"]) is not None
            and rows_by_osm_id[r["osm_id"]][0] in arch_by_reg
        ]
        anon = [r for r in cls_rows if r not in named]
        if len(named) >= 10 and len(anon) >= 10:
            p1, p2 = ga_rate(named), ga_rate(anon)
            result = compare_proportions(
                sum(r["has_golden_angle"] == "1" for r in named),
                len(named),
                sum(r["has_golden_angle"] == "1" for r in anon),
                len(anon),
            )
            print(
                f"[arch] {label}: named={len(named)} "
                f"({p1 * 100:.2f}%) vs anonymous={len(anon)} "
                f"({p2 * 100:.2f}%)  z={result['z']:.2f} "
                f"p={result['p_value']:.4f}"
            )
            binary_rows.append(
                {
                    "class": label,
                    "named_n": len(named),
                    "named_rate": round(p1 * 100, 2),
                    "anon_n": len(anon),
                    "anon_rate": round(p2 * 100, 2),
                    "named_ci_low": round(result["rate1_ci_low"] * 100, 2),
                    "named_ci_high": round(result["rate1_ci_high"] * 100, 2),
                    "anon_ci_low": round(result["rate2_ci_low"] * 100, 2),
                    "anon_ci_high": round(result["rate2_ci_high"] * 100, 2),
                    "risk_difference": round(result["risk_difference"] * 100, 2),
                    "odds_ratio": round(result["odds_ratio"], 4),
                    "z": round(result["z"], 3),
                    "p": result["p_value"],
                    "method": result["method"],
                }
            )
        return named, anon

    # ---- binary test per class -------------------------------------------
    rows_by_osm_id = {j["osm_id"]: (reg, j) for reg, j in joined.items()}
    worship_rows = [r for r in rows if r["group"] == "worship" and r["is_control"] == "0"]
    bin_test("worship", worship_rows)

    # country houses
    ch_rows = [
        r
        for r in rows
        if r["is_control"] == "0"
        and r["osm_id"] in rows_by_osm_id
        and joined[rows_by_osm_id[r["osm_id"]][0]]["niah_type"] == "country house"
    ]
    bin_test("country house", ch_rows)

    # persist binary tests for the report
    binary_fields = [
        "class",
        "named_n",
        "named_rate",
        "anon_n",
        "anon_rate",
        "named_ci_low",
        "named_ci_high",
        "anon_ci_low",
        "anon_ci_high",
        "risk_difference",
        "odds_ratio",
        "z",
        "p",
        "method",
    ]
    atomic_write_csv(
        OUT / "architects_binary.csv", binary_fields, binary_rows, extrasaction="ignore"
    )
    print(f"[arch] wrote architects_binary.csv ({len(binary_rows)} tests)")

    # ---- per-architect table ----------------------------------------------
    arch_rows = sorted(
        ((a, rs) for a, rs in per_arch.items() if len(rs) >= 4),
        key=lambda kv: (-ga_rate(kv[1]), -len(kv[1])),
    )
    architect_fields = [
        "architect",
        "n",
        "golden_angle_n",
        "golden_angle_rate",
        "ci_low",
        "ci_high",
        "note",
    ]
    architect_output = []
    for a, rs in arch_rows:
        n_ga = sum(1 for r in rs if r["has_golden_angle"] == "1")
        ci_low, ci_high = wilson_interval(n_ga, len(rs))
        architect_output.append(
            {
                "architect": a,
                "n": len(rs),
                "golden_angle_n": n_ga,
                "golden_angle_rate": round(ga_rate(rs) * 100, 2),
                "ci_low": round(ci_low * 100, 2),
                "ci_high": round(ci_high * 100, 2),
                "note": "exploratory; n<30 and multiple names tested",
            }
        )
    atomic_write_csv(OUT / "architects.csv", architect_fields, architect_output)
    print(f"[arch] wrote architects.csv ({len(arch_rows)} architects, n>=4)")

    atomic_write_csv(
        OUT / "architects_evidence.csv",
        ["osm_id", "reg_no", "architect", "confidence", "evidence"],
        evidence_rows,
    )
    print(f"[arch] wrote architects_evidence.csv ({len(evidence_rows)} rows)")

    print("\n[arch] top architects by golden-angle rate (n>=4):")
    for a, rs in arch_rows[:12]:
        print(f"  {ga_rate(rs) * 100:5.1f}%  n={len(rs):3d}  {a}")


if __name__ == "__main__":
    main()
