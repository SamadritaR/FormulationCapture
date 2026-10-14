"""Paper text in, verified and normalized formulation records out.

Three stages:
  1. read     PDF or text into page marked text
  2. extract  one model call, structured output via a tool
  3. verify   deterministic checks: every quote must exist in the text,
              names resolve through the alias map, units convert, and
              anything uncertain becomes a flag a human resolves
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from . import schema as S
from .normalize import (normalize_drug_load, normalize_material, normalize_method,
                        first_number, to_weeks)
from .prompt import SYSTEM_PROMPT, user_message

DEFAULT_MODEL = os.environ.get("FCAP_MODEL", "claude-sonnet-5-5")


# 1. read ------------------------------------------------------------------

def read_paper(path: str | Path) -> str:
    """Return text with [PAGE n] markers so quotes can carry a page number."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(f"[PAGE {i + 1}]\n{t}" for i, t in enumerate(pages))
    return path.read_text(encoding="utf-8")


# 2. extract ---------------------------------------------------------------

def call_model(paper_text: str, paper_id: str, model: str = DEFAULT_MODEL) -> dict:
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    tool = S.tool_schema()
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user_message(paper_text, paper_id)}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model did not return structured output.")


# 3. verify ----------------------------------------------------------------

def _canon(s: str) -> str:
    """Whitespace, hyphenation, ligature and dash tolerant form for quote matching."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00ad", "")
    s = re.sub(r"-\s*\n\s*", "", s)                     # line break hyphenation
    s = re.sub(r"[‐-―−]", "-", s)        # dashes and minus signs
    s = re.sub(r"[‘’]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def locate_quote(quote: Optional[str], text: str) -> Optional[int]:
    """Return the page the quote is on, 0 if found but unpaged, None if absent."""
    if not quote:
        return None
    q = _canon(quote)
    if len(q) < 4:
        return None
    pages = re.split(r"\[PAGE (\d+)\]", text)
    if len(pages) > 1:
        # pages = ["", "1", text1, "2", text2, ...]
        for i in range(1, len(pages), 2):
            if q in _canon(pages[i + 1]):
                return int(pages[i])
        # quote may straddle a page break
        joined = _canon(" ".join(pages[2::2]))
        return 0 if q in joined else None
    return 0 if q in _canon(text) else None


def _add_flag(fv: S.FieldValue, flag: str, note: Optional[str] = None) -> None:
    if flag not in fv.flags:
        fv.flags.append(flag)
    if note:
        fv.note = f"{fv.note} {note}".strip() if fv.note and note not in fv.note else note


def _finalize_status(fv: S.FieldValue) -> None:
    if fv.value is None or fv.value == "":
        fv.status = S.MISSING
        if S.MISSING_REASON not in fv.flags:
            fv.flags = [S.MISSING_REASON] + [f for f in fv.flags if f != S.MISSING_REASON]
        return
    fv.flags = [f for f in fv.flags if f != S.MISSING_REASON]
    fv.status = S.FLAGGED if fv.flags else S.EXTRACTED


def verify(raw: dict, paper_text: str, paper_id: str) -> list[S.FormulationRecord]:
    records: list[S.FormulationRecord] = []
    for idx, f in enumerate(raw.get("formulations", []), start=1):
        rec = S.FormulationRecord(record_id=f"{paper_id}#{idx}", paper_id=paper_id,
                                  label=f.get("label") or f"Formulation {idx}")
        raw_fields = f.get("fields", {})
        for name in S.FIELD_NAMES:
            d = raw_fields.get(name) or {}
            fv = S.FieldValue(value=d.get("value"), unit=d.get("unit"), quote=d.get("quote"),
                              page=d.get("page"), confidence=d.get("confidence", "high"),
                              flags=[x for x in (d.get("flags") or []) if x in S.FLAG_REASONS],
                              note=d.get("note"))
            fv.raw_value = fv.value
            if fv.confidence == "low":
                _add_flag(fv, S.LOW_CONFIDENCE)

            # every value needs a quote that really exists
            if fv.value not in (None, "", []):
                found = locate_quote(fv.quote, paper_text)
                if found is None:
                    _add_flag(fv, S.QUOTE_NOT_FOUND,
                              "The supporting quote could not be found in the paper text.")
                elif found > 0:
                    fv.page = found
            _normalize_field(name, fv)
            rec.fields[name] = fv

        # a method specific field is not a gap for the other method
        method = rec.get("method").value
        for fname, only_for in S.METHOD_SPECIFIC.items():
            fv = rec.fields[fname]
            if method and method != only_for and fv.value is None:
                fv.status = "not_applicable"
                fv.flags = []
                continue
        for fv in rec.fields.values():
            if fv.status != "not_applicable":
                _finalize_status(fv)
        records.append(rec)
    return records


def _normalize_field(name: str, fv: S.FieldValue) -> None:
    v = fv.value
    if v in (None, ""):
        fv.value = None
        return
    if name == "drug_load_pct":
        r = normalize_drug_load(v, fv.unit, fv.quote, fv.note)
        fv.value, fv.unit = r["value"], "% w/w"
        if r["ambiguous"]:
            _add_flag(fv, S.AMBIGUOUS, r["note"])
        elif r["note"]:
            fv.note = r["note"]
    elif name == "carrier_polymer":
        canon, ok = normalize_material(str(v))
        fv.value = canon
        if not ok:
            _add_flag(fv, S.UNNORMALIZED, f"'{v}' is not in the alias map; confirm which material this is.")
    elif name == "other_excipients":
        if isinstance(v, list):
            out, unknown = [], []
            for item in v:
                nm = item.get("name") if isinstance(item, dict) else str(item)
                pct = item.get("pct") if isinstance(item, dict) else None
                canon, ok = normalize_material(nm)
                out.append({"name": canon, "pct": first_number(pct)})
                if not ok:
                    unknown.append(nm)
            fv.value = out
            if unknown:
                _add_flag(fv, S.UNNORMALIZED, f"Not in alias map: {', '.join(map(str, unknown))}.")
    elif name == "method":
        fv.value = normalize_method(v)
    elif name in ("process_temperature_c", "tg_c", "screw_speed_rpm"):
        n = first_number(v)
        if n is None:
            _add_flag(fv, S.AMBIGUOUS, f"Could not read a single number from '{v}'.")
        else:
            if isinstance(v, str) and re.search(r"\d\s*(?:-|to|–)\s*\d", v):
                _add_flag(fv, S.AMBIGUOUS, f"Range '{v}' given; recorded the first number.")
            fv.value = n
    elif name == "stability_duration_weeks":
        w = to_weeks(v, fv.unit)
        fv.value, fv.unit = w, "weeks"
        if w is None:
            _add_flag(fv, S.AMBIGUOUS, f"Could not read a duration from '{v}'.")
    elif name == "amorphous_confirmed":
        s = str(v).lower()
        fv.value = "yes" if s.startswith("y") else "no" if s.startswith("n") else "partial"


# orchestration -------------------------------------------------------------

def run(paper_path: str | Path, paper_id: Optional[str] = None,
        raw_cache: Optional[str | Path] = None, model: str = DEFAULT_MODEL) -> dict:
    """Run the pipeline. If raw_cache exists, reuse the model output instead of calling the API."""
    paper_path = Path(paper_path)
    paper_id = paper_id or paper_path.stem
    text = read_paper(paper_path)
    if raw_cache and Path(raw_cache).exists():
        raw = json.loads(Path(raw_cache).read_text())
    else:
        raw = call_model(text, paper_id, model)
        if raw_cache:
            Path(raw_cache).parent.mkdir(parents=True, exist_ok=True)
            Path(raw_cache).write_text(json.dumps(raw, indent=2))
    records = verify(raw, text, paper_id)
    return {"paper_id": paper_id, "model": model, "paper_notes": raw.get("paper_notes"),
            "records": [r.to_dict() for r in records]}


def main(argv: list[str]) -> None:
    """python -m fcap.pipeline papers/foo.pdf  ->  out/foo.json"""
    if not argv:
        print("Usage: python -m fcap.pipeline <paper.pdf or .txt> [more papers]")
        return
    for p in argv:
        pid = Path(p).stem
        result = run(p, pid, raw_cache=f"out/raw/{pid}.json")
        Path("out").mkdir(exist_ok=True)
        Path(f"out/{pid}.json").write_text(json.dumps(result, indent=2))
        n = len(result["records"])
        flags = sum(1 for r in result["records"] for f in r["fields"].values() if f["status"] == "flagged")
        print(f"{pid}: {n} formulations, {flags} flagged fields")


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
