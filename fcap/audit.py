"""Corpus gap audit: across every extracted paper, how often is each field a
model would need actually stated?

This is the finding, not the extraction accuracy. Run it on reviewed records
(model output with your resolutions applied) so the numbers describe the
papers, not the model's mistakes.

Run:  python -m fcap.audit
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import schema as S
from .review import apply_reviews


def audit(results: list[dict]) -> dict:
    records = [r for res in results for r in apply_reviews(res)]
    n = len(records)
    per_field = {}
    for name in S.FIELD_NAMES:
        applicable = [r for r in records if r.get(name).status != "not_applicable"]
        m = sum(r.get(name).status == S.MISSING for r in applicable)
        f = sum(r.get(name).status == S.FLAGGED for r in applicable)
        per_field[name] = {"applicable": len(applicable), "missing": m, "flagged": f}
    complete = sum(all(r.get(c).status == S.EXTRACTED for c in S.CRITICAL_FIELDS) for r in records)
    reasons = Counter(fl for r in records for fv in r.fields.values() for fl in fv.flags
                      if fv.status == S.FLAGGED)
    unresolved_names = Counter()
    for r in records:
        for name in ("carrier_polymer", "other_excipients"):
            fv = r.get(name)
            if S.UNNORMALIZED in fv.flags:
                vals = fv.value if isinstance(fv.value, list) else [{"name": fv.value}]
                for v in vals:
                    unresolved_names[str(v.get("name"))] += 1
    return {"papers": len(results), "formulations": n, "model_ready": complete,
            "per_field": per_field, "flag_reasons": dict(reasons),
            "unresolved_names": dict(unresolved_names)}


def to_markdown(a: dict) -> str:
    n = a["formulations"] or 1
    lines = ["# Corpus gap audit", "",
             f"{a['formulations']} formulations from {a['papers']} papers.", "",
             f"**{a['model_ready']} of {a['formulations']} ({a['model_ready'] / n:.0%}) have every critical field "
             f"stated clearly.** Critical fields: {', '.join(S.CRITICAL_FIELDS)}.", "",
             "| Field | Not stated | Unclear (flagged) |", "|---|---|---|"]
    for name, d in a["per_field"].items():
        k = d["applicable"] or 1
        star = " *" if name in S.CRITICAL_FIELDS else ""
        lines.append(f"| {name}{star} | {d['missing']}/{d['applicable']} ({d['missing'] / k:.0%}) | "
                     f"{d['flagged']}/{d['applicable']} ({d['flagged'] / k:.0%}) |")
    lines += ["", "\\* critical field", ""]
    if a["flag_reasons"]:
        lines += ["## Why fields were flagged", ""] + [f"* {k}: {v}" for k, v in
                                                      sorted(a["flag_reasons"].items(), key=lambda x: -x[1])]
    if a["unresolved_names"]:
        lines += ["", "## Material names not in the alias map", ""] + \
                 [f"* {k} ({v})" for k, v in sorted(a["unresolved_names"].items(), key=lambda x: -x[1])]
    return "\n".join(lines)


def main() -> None:
    results = [json.loads(p.read_text()) for p in sorted(Path("out").glob("*.json"))]
    if not results:
        print("No extractions in out/. Run the pipeline first.")
        return
    a = audit(results)
    md = to_markdown(a)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/gap_audit.md").write_text(md)
    Path("reports/gap_audit.json").write_text(json.dumps(a, indent=2))
    print(md)


if __name__ == "__main__":
    main()
