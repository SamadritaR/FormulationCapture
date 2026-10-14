"""Reviewer decisions, stored apart from model output so the two never blur.

A resolution records who decided, what they decided, and when. The model
output in out/ is never edited; the reviewed view is model output plus
these decisions applied on top.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import schema as S

REVIEW_DIR = Path("reviews")


def load_reviews(paper_id: str) -> dict:
    p = REVIEW_DIR / f"{paper_id}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_resolution(paper_id: str, record_id: str, field: str, action: str,
                    value: Any = None, comment: str = "") -> None:
    """action: accept | correct | not_stated"""
    REVIEW_DIR.mkdir(exist_ok=True)
    reviews = load_reviews(paper_id)
    reviews.setdefault(record_id, {})[field] = {
        "action": action, "value": value, "comment": comment,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (REVIEW_DIR / f"{paper_id}.json").write_text(json.dumps(reviews, indent=2))


def apply_reviews(result: dict) -> list[S.FormulationRecord]:
    reviews = load_reviews(result["paper_id"])
    recs = [S.FormulationRecord.from_dict(r) for r in result["records"]]
    for rec in recs:
        for field, dec in reviews.get(rec.record_id, {}).items():
            fv = rec.fields.get(field)
            if fv is None:
                continue
            fv.resolved_by = "reviewer"
            if dec["action"] == "accept":
                fv.status = S.EXTRACTED
            elif dec["action"] == "correct":
                fv.value = dec["value"]
                fv.status = S.EXTRACTED
            elif dec["action"] == "not_stated":
                fv.value = None
                fv.status = S.MISSING
            if dec.get("comment"):
                fv.note = f"Reviewer: {dec['comment']}"
    return recs


def records_to_csv(records: list[S.FormulationRecord]) -> str:
    buf = io.StringIO()
    cols = ["record_id", "paper_id", "label"]
    for n in S.FIELD_NAMES:
        cols += [n, f"{n}__status", f"{n}__flags", f"{n}__note", f"{n}__quote", f"{n}__page"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in records:
        row = {"record_id": r.record_id, "paper_id": r.paper_id, "label": r.label}
        for n in S.FIELD_NAMES:
            fv = r.get(n)
            v = fv.value
            if isinstance(v, list):
                v = "; ".join(f"{x.get('name')} {x.get('pct') if x.get('pct') is not None else ''}".strip()
                              for x in v) if v else "none"
            status = fv.status + (" (reviewer)" if fv.resolved_by else "")
            row[n], row[f"{n}__status"], row[f"{n}__quote"] = v, status, fv.quote
            row[f"{n}__flags"] = "; ".join(fv.flags)
            row[f"{n}__note"] = fv.note
            row[f"{n}__page"] = fv.page
        w.writerow(row)
    return buf.getvalue()
