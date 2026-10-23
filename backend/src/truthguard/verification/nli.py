"""Claim-vs-span entailment scoring.

Default `HeuristicNLI` is deterministic and CPU-free: a claim is entailed when every value it
states appears in the span (after normalization) and the span talks about the same
attribute and subject (using the span's heading context). A different value of the same
kind in the span is scored as contradiction. `CrossEncoderNLI` (nli-deberta-v3-xsmall) is
the optional model-based scorer; gray-zone scores escalate to an LLM judge in live mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from truthguard.ingestion.lexicon import Lexicon
from truthguard.ingestion.normalize import values_in_text


@dataclass(frozen=True)
class NLIScore:
    entailment: float
    contradiction: float


class NLIScorer(Protocol):
    def score(self, claim: str, premise: str, context: str = "") -> NLIScore: ...


class HeuristicNLI:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lex = lexicon

    def score(self, claim: str, premise: str, context: str = "") -> NLIScore:
        full = f"{context}\n{premise}" if context else premise
        c_vals = values_in_text(claim)
        p_vals = values_in_text(premise)
        if c_vals:
            hit = len(c_vals & p_vals) / len(c_vals)
            prefixes_p = {v.split(":")[0] for v in p_vals}
            clash = any(v not in p_vals and v.split(":")[0] in prefixes_p for v in c_vals)
        else:
            hit, clash = 0.5, False
        c_attr = {m.id for m in self.lex.match_attributes(claim)}
        p_attr = {m.id for m in self.lex.match_attributes(full)}
        c_subj = {m.id for m in self.lex.match_subjects(claim)}
        p_subj = {m.id for m in self.lex.match_subjects(full)}
        attr = 1.0 if not c_attr or c_attr & p_attr else 0.3
        subj = 1.0 if not c_subj or c_subj & p_subj or not p_subj else 0.4
        entail = round(0.6 * hit + 0.25 * attr + 0.15 * subj, 3)
        contradiction = round(0.8 if clash and hit < 1.0 else 0.0, 3)
        return NLIScore(entailment=entail, contradiction=contradiction)


class CrossEncoderNLI:  # pragma: no cover - optional heavy dependency
    def __init__(self, model: str) -> None:
        from sentence_transformers import CrossEncoder

        self._m = CrossEncoder(model, device="cpu")

    def score(self, claim: str, premise: str, context: str = "") -> NLIScore:
        import numpy as np

        logits = self._m.predict([(f"{context}\n{premise}", claim)])[0]
        probs = np.exp(logits) / np.exp(logits).sum()
        # label order for nli-deberta-v3: contradiction, entailment, neutral
        return NLIScore(entailment=float(probs[1]), contradiction=float(probs[0]))
