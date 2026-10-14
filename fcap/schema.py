"""Record schema for one amorphous solid dispersion (ASD) formulation.

Every field is captured as a FieldValue: the value, its unit, the exact
source quote, the page it came from, and a status. Nothing enters a record
without a quote that can be found in the paper.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# Field statuses
EXTRACTED = "extracted"  # value found, quote verified, nothing to flag
FLAGGED = "flagged"      # value found but needs a human decision
MISSING = "missing"      # the paper does not state it

# Flag reasons
MISSING_REASON = "missing"
AMBIGUOUS = "ambiguous"            # stated, but the basis or unit is unclear
CONFLICTING = "conflicting"        # two places in the paper disagree
UNNORMALIZED = "unnormalized"      # name not in the alias map, could not resolve
QUOTE_NOT_FOUND = "quote_not_found"  # model quote does not appear in the text
LOW_CONFIDENCE = "low_confidence"

FLAG_REASONS = [MISSING_REASON, AMBIGUOUS, CONFLICTING, UNNORMALIZED,
                QUOTE_NOT_FOUND, LOW_CONFIDENCE]

# (field name, group, unit, why it matters to a formulation model)
FIELDS: list[tuple[str, str, Optional[str], str]] = [
    ("drug_name", "Composition", None,
     "Without the API there is nothing to learn from."),
    ("drug_load_pct", "Composition", "% w/w",
     "Drug load drives recrystallization risk. A record without it cannot train a load model."),
    ("carrier_polymer", "Composition", None,
     "Trade names must resolve to one material, or the model sees two polymers where there is one."),
    ("other_excipients", "Composition", None,
     "Surfactants and plasticizers change Tg and release; dropping them hides a variable."),
    ("method", "Process", None,
     "HME and spray drying produce different microstructures from the same composition."),
    ("process_temperature_c", "Process", "°C",
     "Too hot degrades the drug, too cold leaves crystals. It is a critical process parameter."),
    ("screw_speed_rpm", "Process", "rpm",
     "Sets shear and residence time in HME."),
    ("solvent", "Process", None,
     "Solvent choice affects phase separation in spray drying."),
    ("tg_c", "Characterization", "°C",
     "Tg is the main physical stability signal for an ASD."),
    ("amorphous_confirmed", "Characterization", None,
     "If amorphousness was never confirmed, every outcome downstream is suspect."),
    ("confirmation_method", "Characterization", None,
     "XRPD and DSC have different detection limits; the method qualifies the claim."),
    ("stability_condition", "Stability", None,
     "40 °C / 75% RH and 25 °C / 60% RH are not comparable outcomes."),
    ("stability_duration_weeks", "Stability", "weeks",
     "A pass at 2 weeks and a pass at 6 months are different labels."),
    ("stability_outcome", "Stability", None,
     "The label a stability model would learn."),
    ("dissolution_outcome", "Performance", None,
     "The label a solubility model would learn."),
]

FIELD_NAMES = [f[0] for f in FIELDS]
FIELD_META = {f[0]: {"group": f[1], "unit": f[2], "why": f[3]} for f in FIELDS}

# Fields that only apply to one method; missing them is not a gap for the other.
METHOD_SPECIFIC = {"screw_speed_rpm": "hme", "solvent": "spray_drying"}

# Fields counted in the corpus gap audit as "a model needs this"
CRITICAL_FIELDS = ["drug_load_pct", "carrier_polymer", "method",
                   "process_temperature_c", "amorphous_confirmed",
                   "stability_condition", "stability_duration_weeks",
                   "stability_outcome"]


@dataclass
class FieldValue:
    value: Any = None
    unit: Optional[str] = None
    quote: Optional[str] = None
    page: Optional[int] = None
    confidence: str = "high"          # high | medium | low
    status: str = MISSING
    flags: list[str] = field(default_factory=list)
    note: Optional[str] = None        # what is unclear, in plain words
    resolved_by: Optional[str] = None  # None | "reviewer"
    raw_value: Any = None              # value before normalization

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FieldValue":
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        fv = cls(**known)
        fv.flags = list(fv.flags or [])
        return fv


@dataclass
class FormulationRecord:
    record_id: str
    paper_id: str
    label: str  # how the paper names this formulation, e.g. "F3" or "ITZ/HPMCAS 25%"
    fields: dict[str, FieldValue] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "paper_id": self.paper_id,
                "label": self.label,
                "fields": {k: v.to_dict() for k, v in self.fields.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "FormulationRecord":
        return cls(record_id=d["record_id"], paper_id=d["paper_id"],
                   label=d.get("label", ""),
                   fields={k: FieldValue.from_dict(v)
                           for k, v in d.get("fields", {}).items()})

    def get(self, name: str) -> FieldValue:
        return self.fields.get(name) or FieldValue()

    def flag_count(self) -> int:
        return sum(1 for f in self.fields.values() if f.status == FLAGGED)


# JSON schema handed to the model as a tool, so output is structured.
def tool_schema() -> dict:
    field_obj = {
        "type": "object",
        "properties": {
            "value": {"description": "Value as stated, or null if the paper does not state it. "
                                     "Numbers as numbers. other_excipients as a list of {name, pct}."},
            "unit": {"type": ["string", "null"]},
            "quote": {"type": ["string", "null"],
                      "description": "Exact contiguous text copied from the paper that supports the value. "
                                     "Under 40 words. Null only when value is null."},
            "page": {"type": ["integer", "null"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "flags": {"type": "array", "items": {"type": "string",
                      "enum": ["missing", "ambiguous", "conflicting", "low_confidence"]}},
            "note": {"type": ["string", "null"],
                     "description": "If flagged: what exactly is unclear and what a reader would need to know."},
        },
        "required": ["value", "quote", "confidence", "flags"],
    }
    return {
        "name": "record_formulations",
        "description": "Record every distinct ASD formulation described in the paper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "formulations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "fields": {"type": "object",
                                       "properties": {n: field_obj for n in FIELD_NAMES},
                                       "required": FIELD_NAMES},
                        },
                        "required": ["label", "fields"],
                    },
                },
                "paper_notes": {"type": "string",
                                "description": "Anything about the paper as a whole a reviewer should know."},
            },
            "required": ["formulations"],
        },
    }
