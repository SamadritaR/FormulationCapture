import json
from pathlib import Path

import pytest

from fcap import schema as S
from fcap.normalize import normalize_drug_load, normalize_material, to_weeks
from fcap.pipeline import locate_quote, read_paper, run, verify
from fcap.evaluate import evaluate_paper, field_correct
from fcap.audit import audit

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "samples" / "demo_paper.txt"
RAW = ROOT / "samples" / "demo_raw_model_output.json"


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # reviews/ and out/ written here, not in the repo


@pytest.fixture
def demo():
    return run(DEMO, "demo", raw_cache=RAW)


def rec(result, label):
    return next(S.FormulationRecord.from_dict(r) for r in result["records"] if r["label"] == label)


# normalization ---------------------------------------------------------------

@pytest.mark.parametrize("name,canon", [
    ("Kollidon VA64", "copovidone (PVP VA 64)"),
    ("PVP-VA 64", "copovidone (PVP VA 64)"),
    ("Kollidon® VA 64 (BASF, Germany)", "copovidone (PVP VA 64)"),
    ("HPMCAS-MG", "HPMCAS (M grade)"),
    ("AQOAT AS-LF", "HPMCAS"),
    ("Soluplus®", "Soluplus"),
    ("PVP K30", "povidone (PVP)"),
])
def test_aliases_resolve(name, canon):
    out, ok = normalize_material(name)
    assert ok and out.startswith(canon.split(" (")[0])
    if "grade" in canon:
        assert out == canon


def test_unknown_material_is_not_guessed():
    out, ok = normalize_material("Polymer Q-77")
    assert not ok and out == "Polymer Q-77"


def test_ratio_without_basis_is_ambiguous():
    r = normalize_drug_load("1:3", None, "drug and polymer at a ratio of 1:3 in acetone")
    assert r["value"] == 25.0 and r["ambiguous"]


def test_ratio_by_weight_converts_cleanly():
    r = normalize_drug_load("1:4", None, "a drug to polymer weight ratio of 1:4")
    assert r["value"] == 20.0 and not r["ambiguous"]


def test_molar_ratio_is_not_converted():
    r = normalize_drug_load("1:2", None, "at a 1:2 molar ratio")
    assert r["value"] is None and r["ambiguous"]


def test_durations_convert_to_weeks():
    assert to_weeks(3, "months") == 13.0
    assert to_weeks(28, "days") == 4.0


# quote verification -------------------------------------------------------------

def test_quote_found_with_page():
    text = read_paper(DEMO)
    assert locate_quote("inlet temperature of 85 °C", text) == 2


def test_quote_tolerates_line_breaks_and_dashes():
    text = "[PAGE 1]\nstored at 40 °C /\n75% RH for six‑ months"
    assert locate_quote("stored at 40 °C / 75% RH for six- months", text) == 1


def test_invented_quote_is_flagged():
    raw = json.loads(RAW.read_text())
    raw["formulations"][0]["fields"]["tg_c"]["quote"] = "Tg was 140 °C for F1"
    raw["formulations"][0]["fields"]["tg_c"]["value"] = 140
    recs = verify(raw, read_paper(DEMO), "demo")
    tg = recs[0].fields["tg_c"]
    assert tg.status == S.FLAGGED and S.QUOTE_NOT_FOUND in tg.flags


# end to end on the demo paper -------------------------------------------------

def test_demo_finds_four_formulations(demo):
    assert [r["label"] for r in demo["records"]] == ["F1", "F2", "F3", "F4"]


def test_demo_flags_the_planted_problems(demo):
    assert S.CONFLICTING in rec(demo, "F2").fields["drug_load_pct"].flags
    assert S.AMBIGUOUS in rec(demo, "F3").fields["drug_load_pct"].flags
    assert S.UNNORMALIZED in rec(demo, "F4").fields["other_excipients"].flags
    assert rec(demo, "F4").fields["stability_outcome"].status == S.MISSING


def test_method_specific_fields_are_not_gaps(demo):
    assert rec(demo, "F1").fields["solvent"].status == "not_applicable"
    assert rec(demo, "F3").fields["screw_speed_rpm"].status == "not_applicable"


def test_clean_record_has_no_flags(demo):
    f1 = rec(demo, "F1")
    assert f1.flag_count() == 0
    assert f1.fields["carrier_polymer"].value == "copovidone (PVP VA 64)"


# evaluation and audit ------------------------------------------------------------

def test_field_correct_rules():
    assert field_correct("drug_load_pct", 25.0, 25)
    assert not field_correct("drug_load_pct", 30.0, 25)
    assert field_correct("carrier_polymer", "copovidone (PVP VA 64)", "Kollidon VA64")
    assert field_correct("tg_c", None, None)
    assert not field_correct("tg_c", 98, None)


def test_flagged_error_is_not_silent(demo):
    truth = json.loads((ROOT / "ground_truth" / "demo.json").read_text())
    rows = evaluate_paper(demo, truth)
    f2_load = next(r for r in rows if r["label"] == "F2" and r["field"] == "drug_load_pct")
    assert f2_load["outcome"] == "flagged_error"


def test_audit_counts(demo):
    a = audit([demo])
    assert a["formulations"] == 4
    assert a["per_field"]["stability_outcome"]["missing"] == 1
    assert "Lutrol F68" in a["unresolved_names"]


def test_normalization_is_idempotent():
    for name in ["HPMCAS-MG", "Kollidon VA64", "Soluplus", "AQOAT AS-LF"]:
        once, _ = normalize_material(name)
        assert normalize_material(once)[0] == once


@pytest.mark.parametrize("value,quote,note,expected,ambiguous", [
    ("1:3", "a 1:3 drug-to-polymer weight ratio", None, 25.0, False),
    ("3:1", "a 3:1 polymer-to-API weight ratio", None, 25.0, False),       # carrier first
    ("3:1", "spray dried at 3:1 (w/w)", "polymer:drug", 25.0, False),        # order given in note
    ("15:75:10", "15:75:10 w/w/w ritonavir/PVPVA/Span 20", None, 15.0, True),  # order assumed, flagged
    ("20:80", "drug-to-polymer ratios of 20:80 (w/w)", None, 20.0, False),
])
def test_ratio_order(value, quote, note, expected, ambiguous):
    r = normalize_drug_load(value, None, quote, note)
    assert r["value"] == expected and r["ambiguous"] == ambiguous


def test_model_note_cannot_make_a_ratio_molar():
    # regression: a note asking "weight or molar?" must not count as the paper saying molar
    r = normalize_drug_load("1:3", None, "at a ratio of 1:3 in acetone",
                            "Ratio given without saying whether it is by weight or molar.")
    assert r["value"] == 25.0 and r["ambiguous"]


def test_csv_carries_flags_and_notes(demo):
    import csv, io
    from fcap.review import apply_reviews, records_to_csv
    rows = list(csv.DictReader(io.StringIO(records_to_csv(apply_reviews(demo)))))
    f3 = next(r for r in rows if r["label"] == "F3")
    assert f3["drug_load_pct"] == "25.0"
    assert "ambiguous" in f3["drug_load_pct__flags"] and f3["drug_load_pct__note"]
