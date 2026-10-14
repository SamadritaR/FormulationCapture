# Formulation Capture

Turns amorphous solid dispersion (ASD) papers into structured formulation records that a predictive model can trust. Every value carries the exact sentence it came from. Every gap is flagged with what it costs a model. A person resolves the flags before anything counts as data.

## Why

A formulation model is only as good as the records it learns from, and most formulation knowledge lives in papers, reports and lab notebooks, not tables. The same polymer appears as Kollidon VA64, copovidone and PVP VA. Drug loads appear as "1:3" with no basis. Methods text and Table 1 disagree. Stability results get deferred to "a separate report". None of that is visible until someone tries to turn the paper into rows.

This project asks two questions:

1. Can capture be made reliable enough that nothing wrong enters a dataset silently?
2. How much of the published record is actually complete enough to learn from?

## How it works

```
paper (PDF or text)
  → read      page marked text
  → extract   one model call, structured output, a quote for every value
  → verify    code checks every quote exists in the paper, resolves material
               names, converts units, and turns uncertainty into flags
  → review    a person accepts, corrects, or confirms "not stated"
  → export    CSV / JSON, plus a corpus gap audit
```

Design choices that matter:

* **The model reports, code decides.** The prompt forbids unit conversion and name translation. Normalization lives in `fcap/normalize.py`, where it can be read and tested.
* **No quote, no value.** A value whose quote cannot be found in the paper is flagged `quote_not_found`, never accepted.
* **Missing is a finding.** The model is told not to fill gaps from general knowledge. A record with three honest flags beats one that looks complete and is wrong.
* **Reviewer decisions are stored apart from model output** (`reviews/`), so you can always tell which values a person confirmed.
* **Method specific fields are not gaps.** A spray dried formulation has no screw speed; the audit does not count it as missing.
* **The headline metric is silent errors:** wrong values that were not flagged. A flagged error costs a reviewer a minute. A silent one goes into a training set.

## Run it

```
pip install -r requirements.txt
streamlit run app.py
```

The demo paper loads offline. It is synthetic, written to contain one of each problem (a conflict between text and table, a ratio with no basis, an unknown trade name, a missing stability result). It describes no real study.

For real papers:

```
export ANTHROPIC_API_KEY=...
python -m fcap.run_all       # every paper in papers/, then the gap audit and accuracy report
python -m pytest
```

The CSV export carries, for every field, its status, flags, the reason in plain words, the source quote and the page, so a scientist can check a value without opening the app.

The model defaults to `claude-sonnet-5-5`; set `FCAP_MODEL` to change it. Raw model output is cached in `out/raw/`, so reruns of verification and review cost nothing.

## Workflow for the full study

1. The corpus is the 12 papers in `data/papers.csv`: all experimental, all MDPI Pharmaceutics under CC BY, chosen so that each one tests something (a ratio written polymer first, an abstract that disagrees with its methods, unmapped trade names, a large formulation grid). Download each PDF from its page into `papers/`, named by its `paper_id`. Do not commit them.
2. **Before running anything**, hand label the four papers marked `label_first` into `ground_truth/` using `TEMPLATE.json`. Labeling after seeing model output pulls your labels toward the model.
3. Run the pipeline on all papers.
4. Review every flag in the app.
5. Run `fcap.evaluate` for accuracy on the labeled papers and `fcap.audit` for the gap findings across all of them.
6. Add any trade name the audit lists as unresolved to `POLYMER_ALIASES`, with its source.

## Status

| Part | State |
|---|---|
| Extraction, quote verification, normalization, flagging | Built and tested (30 tests) |
| Review app with export | Built; runs offline on the demo paper |
| Accuracy evaluation and corpus gap audit | Built and tested on the demo paper |
| 12 paper corpus | Selected and checked by hand: experimental, CC BY, each chosen for a specific failure mode |
| Ground truth labels and the full run | Next. Results will go here with every error listed, including the ones the tool missed |

The demo paper is synthetic, so its scores are a test of the code, not a result. No accuracy number from it should be quoted.

Two real bugs found and fixed, each now covered by a test:

* A paper that writes its ratio as "3:1 polymer to API" would have been read as 75% drug when the truth is 25%. Ratios now read the component order from the text, and any ratio whose order is assumed is flagged.
* A model note asking "by weight or molar?" was briefly read as the paper saying the ratio is molar, which dropped the value. The basis now comes only from the paper's own words. The evaluation's silent error count is what exposed it.

## Why I built this

Persist AI's formulation AI learns from each customer's own data, and that data mostly lives in reports and papers. This is a working sample of the capture and trust layer that sits in front of a model like that: every value traceable to a sentence, every gap visible, a person in the loop. It is an independent project, not affiliated with Persist AI, and it makes no claims about how their product works.

## What this does not do

* No prediction. It prepares data; it does not model formulations.
* No chemistry. Molecular weights, SMILES and Tg prediction are out of scope.
* Tables that PDF text extraction mangles will produce flags or misses. That is reported, not hidden.
* I am not a formulation scientist. The schema and alias map reflect published conventions as I understand them, and a formulation scientist should check both.

## Layout

```
app.py                 review app
fcap/schema.py         record schema, field meanings, why each field matters
fcap/prompt.py         extraction prompt
fcap/pipeline.py       read, extract, verify
fcap/normalize.py      alias map and unit rules
fcap/review.py         reviewer decisions and export
fcap/evaluate.py       accuracy against ground truth
fcap/audit.py          corpus gap audit
fcap/run_all.py        one command: extract all papers, audit, evaluate
samples/               synthetic demo paper and cached model output
ground_truth/          hand labels (TEMPLATE.json shows the format)
data/papers.csv        the corpus, with what each paper tests
```
