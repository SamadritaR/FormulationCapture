"""Deterministic normalization. The model reports what the paper says;
this module decides what it means. Kept out of the prompt on purpose so
the rules are inspectable and testable.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Canonical material -> aliases seen in papers (lowercased, punctuation stripped on match)
POLYMER_ALIASES: dict[str, list[str]] = {
    "copovidone (PVP VA 64)": ["copovidone", "kollidon va64", "kollidon va 64", "pvp-va", "pvpva",
                               "pvp va64", "pvp/va", "pvp-va64", "pvpva64", "plasdone s-630",
                               "plasdone s630", "vinylpyrrolidone vinyl acetate copolymer"],
    "povidone (PVP)": ["povidone", "pvp", "pvp k30", "pvp k-30", "pvp k25", "pvp k90",
                       "kollidon 30", "kollidon 25", "kollidon 90f", "kollidon 12 pf",
                       "kollidon 17 pf", "polyvinylpyrrolidone", "plasdone k-29/32"],
    "Soluplus": ["soluplus", "polyvinyl caprolactam polyvinyl acetate polyethylene glycol graft copolymer",
                 "pcl-pvac-peg"],
    "HPMCAS": ["hpmcas", "hpmc-as", "hpmc as", "hypromellose acetate succinate",
               "hydroxypropyl methylcellulose acetate succinate", "aqoat", "shin-etsu aqoat",
               "affinisol hpmcas", "aquasolve", "hpmcas-l", "hpmcas-m", "hpmcas-h",
               "hpmcas lf", "hpmcas mf", "hpmcas hf", "hpmcas-lg", "hpmcas-mg", "hpmcas-hg"],
    "HPMC": ["hpmc", "hypromellose", "hydroxypropyl methylcellulose", "methocel",
             "pharmacoat", "affinisol hpmc", "hpmc e5", "hpmc e3", "hpmc e15"],
    "HPC": ["hpc", "hydroxypropyl cellulose", "klucel", "hpc-ssl", "hpc ssl"],
    "Eudragit E PO": ["eudragit e po", "eudragit epo", "eudragit e100", "eudragit e 100",
                      "basic butylated methacrylate copolymer", "aminoalkyl methacrylate copolymer e"],
    "Eudragit L100 55": ["eudragit l100-55", "eudragit l 100-55", "eudragit l100 55"],
    "Eudragit L100": ["eudragit l100", "eudragit l 100"],
    "PEG": ["peg", "polyethylene glycol", "macrogol", "peg 6000", "peg 8000", "peg 4000"],
    "Poloxamer 407": ["poloxamer 407", "pluronic f127", "kolliphor p407"],
    "Poloxamer 188": ["poloxamer 188", "pluronic f68", "kolliphor p188"],
    "Vitamin E TPGS": ["tpgs", "vitamin e tpgs", "d-alpha-tocopheryl polyethylene glycol succinate",
                       "tocofersolan"],
    "Polysorbate 80": ["polysorbate 80", "tween 80"],
    "Sodium lauryl sulfate": ["sls", "sodium lauryl sulfate", "sodium dodecyl sulfate", "sds"],
    "Triethyl citrate": ["tec", "triethyl citrate"],
}

# HPMCAS grade is real information; keep it alongside the canonical name.
_HPMCAS_GRADE = re.compile(r"\b(?:hpmc[\s-]?as|aqoat|affinisol)[\s-]*(?:as[\s-]*)?(l|m|h)[fgm]?\b", re.I)


def _key(s: str) -> str:
    s = s.lower().replace("®", "").replace("™", "")
    s = re.sub(r"[\(\)\[\],]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_ALIAS_INDEX: dict[str, str] = {}
for canon, aliases in POLYMER_ALIASES.items():
    _ALIAS_INDEX[_key(canon)] = canon
    for a in aliases:
        _ALIAS_INDEX[_key(a)] = canon


def normalize_material(name: Optional[str]) -> tuple[Optional[str], bool]:
    """Return (canonical name, resolved?). Unresolved names come back unchanged."""
    if not name:
        return None, False
    if re.fullmatch(r"HPMCAS \([LMH] grade\)", name.strip()):
        return name.strip(), True  # already canonical; keep idempotent
    k = _key(name)
    if k in _ALIAS_INDEX:
        canon = _ALIAS_INDEX[k]
    else:
        # longest alias contained in the string, e.g. "Kollidon VA64 (BASF)"
        hits = [a for a in _ALIAS_INDEX if len(a) > 2 and re.search(rf"\b{re.escape(a)}\b", k)]
        if not hits:
            return name, False
        canon = _ALIAS_INDEX[max(hits, key=len)]
    if canon == "HPMCAS":
        m = _HPMCAS_GRADE.search(name)
        if m:
            canon = f"HPMCAS ({m.group(1).upper()} grade)"
    return canon, True


_RATIO = re.compile(r"^\s*\d+(?:\.\d+)?(?:\s*[:/]\s*\d+(?:\.\d+)?)+\s*$")

# Phrases saying the carrier comes first in a ratio, e.g. "3:1 polymer to API".
_CARRIER_FIRST = re.compile(
    r"\b(?:polymer|carrier|excipient|matrix)s?[\s\-/:]*(?:to)?[\s\-/:]*(?:drug|api|active|compound)\b", re.I)


def normalize_drug_load(value: Any, unit: Optional[str], quote: Optional[str],
                        note: Optional[str] = None) -> dict:
    """Turn a stated drug load into % w/w.

    Returns {"value": float|None, "ambiguous": bool, "note": str|None}.
    Ratios: the drug is assumed to be the first number unless the text says
    the carrier comes first ("3:1 polymer to API"). A ratio with no stated
    weight basis is converted but marked ambiguous, because 1:3 by weight and
    1:3 molar are different formulations. A molar ratio is never converted.
    """
    # Basis (weight vs molar) comes only from the paper's own words: the unit and
    # the quote. The model's note can say "weight or molar" while asking, so it is
    # used only for component order, never for the basis.
    text = f"{unit or ''} {quote or ''}".lower()
    order_text = f"{text} {note or ''}".lower()
    by_weight = any(t in text for t in ["w/w", "wt%", "wt %", "wt.", "weight", "by mass", "% w", "mass ratio"])
    molar = any(t in text for t in ["molar", "mol/mol", "mole ratio", "mol ratio"])

    if value is None:
        return {"value": None, "ambiguous": False, "note": None}
    if isinstance(value, (int, float)):
        v = float(value)
        if unit and "%" not in unit and "w/w" not in unit.lower() and not by_weight:
            return {"value": v, "ambiguous": True,
                    "note": f"Number given with unit '{unit}'; could not confirm it is % w/w."}
        if not 0 < v <= 100:
            return {"value": v, "ambiguous": True, "note": "Drug load outside 0 to 100%."}
        return {"value": round(v, 2), "ambiguous": False, "note": None}

    if _RATIO.match(str(value)):
        parts = [float(x) for x in re.split(r"\s*[:/]\s*", str(value).strip())]
        total = sum(parts)
        if not total:
            return {"value": None, "ambiguous": True, "note": f"Ratio {value} sums to zero."}
        carrier_first = bool(_CARRIER_FIRST.search(order_text))
        if carrier_first and len(parts) == 2:
            drug, order = parts[1], "carrier:drug"
        else:
            drug, order = parts[0], "drug first"
        pct = round(100 * drug / total, 2)
        if molar:
            return {"value": None, "ambiguous": True,
                    "note": f"Ratio {value} is molar; converting to % w/w needs both molecular weights."}
        notes, ambiguous = [], False
        if not by_weight:
            ambiguous = True
            notes.append("the paper does not state the basis (weight or molar)")
        if carrier_first and len(parts) > 2:
            ambiguous = True
            notes.append("the carrier is named first but there are more than two parts")
        if len(parts) > 2 and not carrier_first:
            notes.append("assumed the first number is the drug; check the component order")
            ambiguous = ambiguous or not re.search(r"(?:drug|api)\s*/", order_text)
        summary = f"Ratio {value} read as {order} by weight ({pct}%)"
        if notes:
            return {"value": pct, "ambiguous": ambiguous, "note": summary + ", but " + "; ".join(notes) + "."}
        return {"value": pct, "ambiguous": False, "note": summary + "."}

    try:
        v = float(str(value).replace("%", "").strip())
        return normalize_drug_load(v, unit or "%", quote, note)
    except ValueError:
        return {"value": None, "ambiguous": True, "note": f"Could not read '{value}' as a drug load."}


def normalize_method(value: Any) -> Optional[str]:
    if value is None:
        return None
    v = str(value).lower()
    if "extru" in v or "hme" in v or "melt" in v:
        return "hme"
    if "spray" in v or "sdd" in v:
        return "spray_drying"
    return "other"


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def first_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    m = _NUM.search(str(value))
    return float(m.group()) if m else None


def to_weeks(value: Any, unit: Optional[str]) -> Optional[float]:
    n = first_number(value)
    if n is None:
        return None
    u = f"{unit or ''} {value}".lower()
    if "month" in u:
        return round(n * 4.345, 1)
    if "day" in u:
        return round(n / 7, 1)
    if "year" in u:
        return round(n * 52.14, 1)
    return n
