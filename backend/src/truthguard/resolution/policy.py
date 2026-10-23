"""Precedence policy model (precedence.yaml), validated with Pydantic."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from truthguard.domain.enums import RuleId

ORDERABLE = [RuleId.R0, RuleId.R1, RuleId.R2, RuleId.R3, RuleId.R4, RuleId.R5]


class R2Options(BaseModel):
    escalate_if_lower_tier_newer: bool = True


class R4Options(BaseModel):
    cross_series: bool = False


class RuleOptions(BaseModel):
    R2: R2Options = Field(default_factory=R2Options)
    R4: R4Options = Field(default_factory=R4Options)


class PrecedencePolicy(BaseModel):
    version: int = 1
    order: list[RuleId] = Field(default_factory=lambda: list(ORDERABLE))
    tiers: dict[str, int] = Field(default_factory=dict)
    default_tier: int = 4
    strength: dict[RuleId, float] = Field(default_factory=dict)
    rules: RuleOptions = Field(default_factory=RuleOptions)

    @field_validator("order")
    @classmethod
    def _order_is_permutation(cls, v: list[RuleId]) -> list[RuleId]:
        if RuleId.QUARANTINE in v:
            raise ValueError("Q (quarantine) always runs first and cannot be reordered")
        if len(set(v)) != len(v):
            raise ValueError("rule order contains duplicates")
        if RuleId.R5 in v and v[-1] != RuleId.R5:
            raise ValueError("R5 (no decision) must be last")
        return v

    @field_validator("tiers")
    @classmethod
    def _tiers_positive(cls, v: dict[str, int]) -> dict[str, int]:
        if any(t < 1 for t in v.values()):
            raise ValueError("authority tiers start at 1")
        return v

    def tier_for(self, doc_type: str) -> int:
        return self.tiers.get(doc_type, self.default_tier)

    def strength_of(self, rule: RuleId) -> float:
        default = {
            RuleId.QUARANTINE: 1.0,
            RuleId.R0: 1.0,
            RuleId.R1: 1.0,
            RuleId.R2: 0.75,
            RuleId.R3: 0.75,
            RuleId.R4: 0.55,
            RuleId.R5: 0.0,
        }
        return self.strength.get(rule, default[rule])


def load_policy(path: Path) -> PrecedencePolicy:
    return PrecedencePolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
