"""Blend detection, run on TruthGuard drafts AND on baseline answers (the red overlay in the
split screen). A stated value is classified against the slot's assertions:

  winner       matches a value that survived resolution
  stale        matches only a value the resolver eliminated (cites old or lower-authority data)
  quarantined  matches only a value from a prompt-injection chunk
  blended      matches no single source at all (merged / computed / hallucinated)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from truthguard.domain.enums import RuleId
from truthguard.domain.models import Assertion, Resolution

Verdict = Literal["winner", "position", "stale", "quarantined", "blended"]


@dataclass(frozen=True)
class ValueVerdict:
    value_norm: str
    verdict: Verdict
    assertion_ids: list[str]
    rule: RuleId | None = None


def classify_value(
    value_norm: str, assertions: list[Assertion], resolution: Resolution | None
) -> ValueVerdict:
    matching = [a for a in assertions if a.value_norm == value_norm]
    if not matching:
        return ValueVerdict(value_norm, "blended", [])
    ids = [a.id for a in matching]
    if all(a.quarantined for a in matching):
        return ValueVerdict(value_norm, "quarantined", ids, RuleId.QUARANTINE)
    if resolution is None:
        return ValueVerdict(value_norm, "winner", ids)
    if resolution.status.value == "unresolved":
        return ValueVerdict(value_norm, "position", ids)
    alive = [a for a in matching if a.id not in resolution.eliminated_by]
    if alive:
        return ValueVerdict(value_norm, "winner", ids)
    rule = resolution.eliminated_by.get(matching[0].id)
    return ValueVerdict(value_norm, "stale", ids, rule)
