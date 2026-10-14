"""Formulation Capture: review app.

Run:  streamlit run app.py
The demo paper works offline. Uploading a real paper needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import streamlit as st

from fcap import schema as S
from fcap.pipeline import read_paper, run
from fcap.review import apply_reviews, records_to_csv, save_resolution

st.set_page_config(page_title="Formulation Capture", layout="wide")

st.markdown("""
<style>
:root { --ink:#1d2433; --muted:#667085; --line:#e4e7ec; --blue:#dbeafe; --blue-ink:#1e40af;
        --amber:#fef3c7; --amber-ink:#92400e; --red:#fee2e2; --red-ink:#991b1b; --green:#dcfce7; --green-ink:#166534; }
.paper { font-family: Georgia, serif; font-size: 15px; line-height: 1.65; color: var(--ink);
         background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 20px 24px;
         height: 72vh; overflow-y: auto; white-space: pre-wrap; }
.paper mark.ok { background: var(--blue); color: inherit; border-bottom: 2px solid var(--blue-ink); padding: 0 1px; }
.paper mark.flag { background: var(--amber); color: inherit; border-bottom: 2px solid var(--amber-ink); padding: 0 1px; }
.pagetag { color: var(--muted); font: 12px ui-monospace, monospace; }
.card { border: 1px solid var(--line); border-left: 4px solid var(--amber-ink); border-radius: 8px;
        padding: 12px 14px; margin: 8px 0; background: #fffdf7; }
.card.missing { border-left-color: var(--red-ink); background: #fffafa; }
.card h4 { margin: 0 0 4px 0; font-size: 15px; }
.chip { display:inline-block; font-size: 12px; padding: 2px 8px; border-radius: 999px; margin-right: 6px; }
.chip.ok { background: var(--green); color: var(--green-ink); }
.chip.flag { background: var(--amber); color: var(--amber-ink); }
.chip.miss { background: var(--red); color: var(--red-ink); }
.why { color: var(--muted); font-size: 13px; }
.quote { font-family: Georgia, serif; font-style: italic; color: #344054; font-size: 13px; }
</style>
""", unsafe_allow_html=True)


# ---------- loading -----------------------------------------------------------

def load_demo() -> tuple[dict, str]:
    paper = "samples/demo_paper.txt"
    result = run(paper, "demo", raw_cache="samples/demo_raw_model_output.json")
    return result, read_paper(paper)


def load_uploaded(upload) -> tuple[dict, str]:
    Path("papers").mkdir(exist_ok=True)
    dest = Path("papers") / upload.name
    dest.write_bytes(upload.getbuffer())
    pid = dest.stem
    result = run(dest, pid, raw_cache=f"out/raw/{pid}.json")
    Path("out").mkdir(exist_ok=True)
    Path(f"out/{pid}.json").write_text(json.dumps(result, indent=2))
    return result, read_paper(dest)


def load_saved(pid: str) -> tuple[dict, str]:
    result = json.loads(Path(f"out/{pid}.json").read_text())
    src = next((p for p in Path("papers").glob(f"{pid}.*")), None)
    return result, read_paper(src) if src else "(Paper file not found in papers/.)"


# ---------- highlighting --------------------------------------------------------

def _quote_pattern(quote: str) -> re.Pattern | None:
    words = [re.escape(w) for w in quote.split() if w]
    if len(words) < 2 and len(quote) < 6:
        return None
    return re.compile(r"[\s\-]*".join(words), re.I)


def highlight(text: str, rec: S.FormulationRecord) -> str:
    spans = []  # (start, end, css)
    for fv in rec.fields.values():
        if not fv.quote or fv.status in ("missing", "not_applicable"):
            continue
        pat = _quote_pattern(fv.quote)
        m = pat.search(text) if pat else None
        if m:
            spans.append((m.start(), m.end(), "flag" if fv.status == S.FLAGGED else "ok"))
    spans.sort()
    out, pos = [], 0
    for s, e, css in spans:
        if s < pos:  # overlap: extend previous mark rather than nesting
            continue
        out.append(html.escape(text[pos:s]))
        out.append(f'<mark class="{css}">{html.escape(text[s:e])}</mark>')
        pos = e
    out.append(html.escape(text[pos:]))
    body = "".join(out)
    return re.sub(r"\[PAGE (\d+)\]", r'<span class="pagetag">page \1</span>', body)



LABELS = {"drug_name": "Drug", "drug_load_pct": "Drug load (% w/w)", "carrier_polymer": "Carrier polymer",
          "other_excipients": "Other excipients", "method": "Method", "process_temperature_c": "Process temperature (°C)",
          "screw_speed_rpm": "Screw speed (rpm)", "solvent": "Solvent", "tg_c": "Tg (°C)",
          "amorphous_confirmed": "Amorphous confirmed", "confirmation_method": "Confirmed by",
          "stability_condition": "Stability condition", "stability_duration_weeks": "Stability duration (weeks)",
          "stability_outcome": "Stability outcome", "dissolution_outcome": "Dissolution outcome"}


def show(v) -> str:
    if isinstance(v, list):
        return "none" if not v else ", ".join(
            f"{x.get('name')}" + (f" ({x['pct']:g}%)" if x.get("pct") is not None else "") for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)

# ---------- UI ------------------------------------------------------------------

st.title("Formulation Capture")
st.caption("Paper in, structured ASD formulation records out. Every value carries its source quote; "
           "every gap is flagged for a person to resolve.")

with st.sidebar:
    st.subheader("Source")
    saved = sorted(p.stem for p in Path("out").glob("*.json")) if Path("out").exists() else []
    choice = st.radio("Load", ["Demo paper (offline)", "Upload a paper", "Saved extraction"],
                      index=0, label_visibility="collapsed")
    upload, pick = None, None
    if choice == "Upload a paper":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            st.warning("Set ANTHROPIC_API_KEY to extract new papers.")
        upload = st.file_uploader("PDF or text", type=["pdf", "txt"])
    elif choice == "Saved extraction":
        if saved:
            pick = st.selectbox("Paper", saved)
        else:
            st.info("No saved extractions in out/ yet.")
    st.divider()
    st.markdown("**Legend**")
    st.markdown('<span class="chip ok">extracted</span><span class="chip flag">flagged</span>'
                '<span class="chip miss">missing</span>', unsafe_allow_html=True)

try:
    if choice == "Demo paper (offline)":
        result, text = load_demo()
    elif choice == "Upload a paper" and upload is not None:
        with st.spinner("Extracting formulations..."):
            result, text = load_uploaded(upload)
    elif choice == "Saved extraction" and pick:
        result, text = load_saved(pick)
    else:
        st.stop()
except Exception as e:  # show the reason, do not hide it
    st.error(f"Could not load this paper: {e}")
    st.stop()

records = apply_reviews(result)
if not records:
    st.warning("No formulations found in this paper.")
    st.stop()

if result.get("paper_notes"):
    st.info(result["paper_notes"])

labels = [r.label for r in records]
sel = st.segmented_control("Formulation", labels, default=labels[0]) or labels[0]
rec = records[labels.index(sel)]

counts = {s: sum(1 for f in rec.fields.values() if f.status == s)
          for s in (S.EXTRACTED, S.FLAGGED, S.MISSING)}

left, right = st.columns([1.1, 1], gap="large")

with left:
    st.markdown(f'<div class="paper">{highlight(text, rec)}</div>', unsafe_allow_html=True)

with right:
    st.markdown(f'<span class="chip ok">{counts[S.EXTRACTED]} extracted</span>'
                f'<span class="chip flag">{counts[S.FLAGGED]} flagged</span>'
                f'<span class="chip miss">{counts[S.MISSING]} missing</span>', unsafe_allow_html=True)

    open_items = [(n, rec.fields[n]) for n in S.FIELD_NAMES
                  if rec.fields[n].status in (S.FLAGGED, S.MISSING)]
    if not open_items:
        st.success("Every field is extracted or resolved. This record is ready.")

    for name, fv in open_items:
        meta = S.FIELD_META[name]
        missing = fv.status == S.MISSING
        reason = ", ".join(fv.flags) or fv.status
        val = "not stated" if missing else html.escape(show(fv.value))
        quote = f'<div class="quote">“{html.escape(fv.quote)}”{f" (page {fv.page})" if fv.page else ""}</div>' if fv.quote else ""
        note = f"<div>{html.escape(fv.note)}</div>" if fv.note else ""
        st.markdown(
            f'<div class="card {"missing" if missing else ""}"><h4>{LABELS[name]}: {val}</h4>'
            f'<div><b>{reason}</b></div>{note}{quote}<div class="why">{html.escape(meta["why"])}</div></div>',
            unsafe_allow_html=True)
        with st.expander(f"Resolve {LABELS[name].lower()}"):
            c1, c2 = st.columns([1, 1])
            new_val = c1.text_input("Correct value", key=f"v_{rec.record_id}_{name}")
            comment = c2.text_input("Why (optional)", key=f"c_{rec.record_id}_{name}")
            b1, b2, b3 = st.columns(3)
            if not missing and b1.button("Accept as is", key=f"a_{rec.record_id}_{name}"):
                save_resolution(rec.paper_id, rec.record_id, name, "accept", comment=comment)
                st.rerun()
            if b2.button("Save correction", key=f"s_{rec.record_id}_{name}", disabled=not new_val):
                v = float(new_val) if re.fullmatch(r"-?\d+(\.\d+)?", new_val.strip()) else new_val
                save_resolution(rec.paper_id, rec.record_id, name, "correct", v, comment)
                st.rerun()
            if b3.button("Confirm not stated", key=f"n_{rec.record_id}_{name}"):
                save_resolution(rec.paper_id, rec.record_id, name, "not_stated", comment=comment)
                st.rerun()

    with st.expander("Full record", expanded=False):
        rows = []
        for n in S.FIELD_NAMES:
            fv = rec.fields[n]
            rows.append({"field": LABELS[n], "value": "" if fv.value is None else show(fv.value),
                         "unit": fv.unit, "status": fv.status + (" (reviewer)" if fv.resolved_by else ""),
                         "page": "" if fv.page is None else str(fv.page), "quote": fv.quote or ""})
        st.dataframe(rows, hide_index=True, width="stretch")

st.divider()
d1, d2 = st.columns(2)
d1.download_button("Export CSV (all formulations, reviewed)", records_to_csv(records),
                   file_name=f"{result['paper_id']}_records.csv", mime="text/csv")
d2.download_button("Export JSON", json.dumps([r.to_dict() for r in records], indent=2),
                   file_name=f"{result['paper_id']}_records.json", mime="application/json")
