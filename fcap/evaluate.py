"""Field level accuracy against hand labeled ground truth.

Ground truth lives in ground_truth/<paper_id>.json (see ground_truth/TEMPLATE.json).
Label the paper yourself before looking at the model output, or the labels
will drift toward what the model said.

The number that matters most is silent errors: values that are wrong and
were NOT flagged. A wrong value with a flag costs a reviewer a minute. A
wrong value without one goes straight into a training set.

Run:  python -m fcap.evaluate
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import schema as S
from .normalize import normalize_material, first_number, to_weeks

TOLERANCE = {"drug_load_pct": 1.0, "process_temperature_c": 2.0, "tg_c": 2.0,
             "screw_speed_rpm": 5.0, "stability_duration_weeks": 0.6}
TEXT_FIELDS = {"stability_outcome", "dissolution_outcome", "stability_condition", "confirmation_method"}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "of", "at", "and", "for", "in", "by", "was", "were"}


def field_correct(name: str, pred: Any, truth: Any) -> bool:
    if truth is None or truth == "":
        return pred is None or pred == ""
    if pred is None or pred == "":
        return False
    if name in TOLERANCE:
        t = to_weeks(truth, None) if name == "stability_duration_weeks" else first_number(truth)
        p = first_number(pred)
        return t is not None and p is not None and abs(t - p) <= TOLERANCE[name]
    if name == "carrier_polymer":
        return normalize_material(str(pred))[0] == normalize_material(str(truth))[0]
    if name == "other_excipients":
        names = lambda xs: {normalize_material(x["name"] if isinstance(x, dict) else str(x))[0] for x in (xs or [])}
        return names(pred) == names(truth)
    if name in TEXT_FIELDS:
        a, b = _tokens(str(pred)), _tokens(str(truth))
        return bool(b) and len(a & b) / len(b) >= 0.5
    return str(pred).strip().lower() == str(truth).strip().lower()


def _label_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def evaluate_paper(pred_result: dict, truth: list[dict]) -> list[dict]:
    """Return one row per (formulation, field)."""
    preds = {_label_key(r["label"]): S.FormulationRecord.from_dict(r) for r in pred_result["records"]}
    rows = []
    for t in truth:
        key = _label_key(t["label"])
        rec = preds.get(key)
        for name in S.FIELD_NAMES:
            if name not in t["fields"]:
                continue  # not labeled
            tv = t["fields"][name]
            if rec is None:
                rows.append({"paper": pred_result["paper_id"], "label": t["label"], "field": name,
                             "correct": False, "flagged": False, "outcome": "formulation_not_found"})
                continue
            fv = rec.get(name)
            if fv.status == "not_applicable":
                fv_value = None
            else:
                fv_value = fv.value
            ok = field_correct(name, fv_value, tv)
            flagged = fv.status in (S.FLAGGED,)
            outcome = ("correct" if ok else "flagged_error" if flagged else "silent_error")
            rows.append({"paper": pred_result["paper_id"], "label": t["label"], "field": name,
                         "correct": ok, "flagged": flagged, "outcome": outcome,
                         "pred": fv_value, "truth": tv})
    extra = set(preds) - {_label_key(t["label"]) for t in truth}
    for k in extra:
        rows.append({"paper": pred_result["paper_id"], "label": preds[k].label, "field": "*",
                     "correct": False, "flagged": False, "outcome": "extra_formulation"})
    return rows


def summarize(rows: list[dict]) -> str:
    scored = [r for r in rows if r["field"] != "*"]
    n = len(scored)
    if not n:
        return "No labeled fields found. Add files to ground_truth/."
    correct = sum(r["correct"] for r in scored)
    silent = sum(r["outcome"] == "silent_error" for r in scored)
    flagged_err = sum(r["outcome"] == "flagged_error" for r in scored)
    lines = ["# Evaluation", "",
             f"Labeled fields: {n} across {len({(r['paper'], r['label']) for r in scored})} formulations "
             f"in {len({r['paper'] for r in scored})} papers.", "",
             "| Measure | Count | Share |", "|---|---|---|",
             f"| Correct | {correct} | {correct / n:.0%} |",
             f"| Wrong but flagged for review | {flagged_err} | {flagged_err / n:.0%} |",
             f"| Wrong and not flagged (silent) | {silent} | {silent / n:.0%} |", ""]
    extra = sum(r["outcome"] == "extra_formulation" for r in rows)
    lost = len({(r["paper"], r["label"]) for r in rows if r["outcome"] == "formulation_not_found"})
    if extra or lost:
        lines += [f"Formulations invented by the model: {extra}. Formulations it missed: {lost}.", ""]
    lines += ["## By field", "", "| Field | Correct | Silent errors |", "|---|---|---|"]
    for name in S.FIELD_NAMES:
        fr = [r for r in scored if r["field"] == name]
        if fr:
            lines.append(f"| {name} | {sum(r['correct'] for r in fr)}/{len(fr)} | "
                         f"{sum(r['outcome'] == 'silent_error' for r in fr)} |")
    errs = [r for r in scored if not r["correct"]]
    if errs:
        lines += ["", "## Every error", "", "| Paper | Formulation | Field | Model | Truth | Flagged |",
                  "|---|---|---|---|---|---|"]
        for r in errs:
            lines.append(f"| {r['paper']} | {r['label']} | {r['field']} | {r.get('pred')} | "
                         f"{r.get('truth')} | {'yes' if r['flagged'] else 'no'} |")
    return "\n".join(lines)


def main() -> None:
    rows = []
    for gt in sorted(Path("ground_truth").glob("*.json")):
        if gt.stem == "TEMPLATE":
            continue
        pred_path = Path("out") / f"{gt.stem}.json"
        if not pred_path.exists():
            print(f"skip {gt.stem}: no out/{gt.stem}.json")
            continue
        rows += evaluate_paper(json.loads(pred_path.read_text()), json.loads(gt.read_text()))
    report = summarize(rows)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/evaluation.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
