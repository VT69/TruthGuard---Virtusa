"""Entity / attribute / scope registry loaded from lexicon.yaml, with regex matchers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from truthguard.domain.enums import ValueKind


@dataclass(frozen=True)
class Match:
    id: str
    start: int
    end: int
    strong: bool = True

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class SubjectDef:
    id: str
    label: str
    topic: str | None
    aliases: list[re.Pattern[str]]
    generic: bool = False
    phrase: str = ""


@dataclass
class AttributeDef:
    id: str
    label: str
    kind: ValueKind
    synonyms: list[tuple[re.Pattern[str], bool]]
    subjects: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    default_subject: str | None = None
    default_scope: str | None = None
    count_unit: str | None = None
    unit: str | None = None


@dataclass
class ScopeDef:
    id: str
    label: str
    patterns: list[re.Pattern[str]]


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{pattern})(?![\w])", re.IGNORECASE)


class Lexicon:
    def __init__(self, data: dict[str, Any]) -> None:
        self.subjects: dict[str, SubjectDef] = {}
        for sid, s in data["subjects"].items():
            self.subjects[sid] = SubjectDef(
                id=sid,
                label=s["label"],
                topic=s.get("topic"),
                aliases=[_compile(a) for a in s["aliases"]],
                generic=bool(s.get("generic", False)),
                phrase=s.get("phrase") or f"the {s['label']}",
            )
        self.attributes: dict[str, AttributeDef] = {}
        for aid, a in data["attributes"].items():
            syns: list[tuple[re.Pattern[str], bool]] = []
            for syn in a["synonyms"]:
                strong = not syn.startswith("~")
                syns.append((_compile(syn.lstrip("~")), strong))
            self.attributes[aid] = AttributeDef(
                id=aid,
                label=a["label"],
                kind=ValueKind(a["kind"]),
                synonyms=syns,
                subjects=a.get("subjects", []),
                scopes=a.get("scopes", []),
                default_subject=a.get("default_subject"),
                default_scope=a.get("default_scope"),
                count_unit=a.get("count_unit"),
                unit=a.get("unit"),
            )
        self.scopes: dict[str, ScopeDef] = {
            sid: ScopeDef(sid, s["label"], [_compile(p) for p in s["patterns"]])
            for sid, s in data["scopes"].items()
        }
        self.product_nouns: list[str] = data.get("product_nouns", [])
        self.non_product_modifiers: set[str] = set(data.get("non_product_modifiers", []))
        self.topic_owners: dict[str, str] = data.get("topic_owners", {})

    # ------------------------------------------------------------------ matching

    def match_subjects(self, text: str) -> list[Match]:
        cands: list[Match] = []
        for sid, s in self.subjects.items():
            for pat in s.aliases:
                for m in pat.finditer(text):
                    cands.append(Match(sid, m.start(), m.end()))
        return _non_overlapping(cands)

    def match_attributes(self, text: str) -> list[Match]:
        cands: list[Match] = []
        for aid, a in self.attributes.items():
            for pat, strong in a.synonyms:
                for m in pat.finditer(text):
                    cands.append(Match(aid, m.start(), m.end(), strong))
        if any(c.strong for c in cands):
            strong_ids = {c.id for c in cands if c.strong}
            cands = [c for c in cands if c.strong or c.id in strong_ids]
        # a match fully inside another attribute's longer match is subsumed by it
        # ("additional interest rate" beats the "interest rate" inside it)
        kind = {aid: a.kind for aid, a in self.attributes.items()}
        cands = [
            c
            for c in cands
            if not any(
                o.id != c.id
                and kind[o.id] == kind[c.id]
                and o.start <= c.start
                and c.end <= o.end
                and o.length > c.length
                for o in cands
            )
        ]
        # keep the best match per attribute
        best: dict[str, Match] = {}
        for c in cands:
            cur = best.get(c.id)
            if cur is None or (c.strong, c.length) > (cur.strong, cur.length):
                best[c.id] = c
        return sorted(best.values(), key=lambda m: m.start)

    def match_scopes(self, text: str, allowed: list[str] | None = None) -> list[str]:
        found: list[tuple[int, str]] = []
        for sid, s in self.scopes.items():
            if allowed is not None and sid not in allowed:
                continue
            for pat in s.patterns:
                m = pat.search(text)
                if m:
                    found.append((m.start(), sid))
                    break
        return [sid for _, sid in sorted(found)]

    def unknown_products(self, text: str) -> list[str]:
        """Product-like phrases ("Titanium card", "home loan") not covered by any known alias."""
        known_spans = [(m.start, m.end) for m in self.match_subjects(text)]
        nouns = "|".join(re.escape(n) for n in sorted(self.product_nouns, key=len, reverse=True))
        out: list[str] = []
        for m in re.finditer(rf"\b([A-Za-z][A-Za-z-]*)\s+(?:{nouns})s?\b", text, re.IGNORECASE):
            modifier = m.group(1).lower()
            if modifier in self.non_product_modifiers:
                continue
            if any(s <= m.start() and m.end() <= e for s, e in known_spans):
                continue
            if any(s < m.end() and m.start() < e for s, e in known_spans):
                continue
            out.append(m.group(0))
        return out

    # ------------------------------------------------------------------ helpers

    def subject_label(self, sid: str) -> str:
        s = self.subjects.get(sid)
        return s.label if s else sid

    def attribute_label(self, aid: str) -> str:
        a = self.attributes.get(aid)
        return a.label if a else aid

    def slot_label(self, slot_key: str, scope: str | None = None) -> str:
        subject, _, attr = slot_key.partition("|")
        label = f"{self.attribute_label(attr)} — {self.subject_label(subject)}"
        if scope and scope != "all":
            label += f" ({self.scope_label(scope)})"
        return label

    def slot_phrase(self, slot_key: str, scope: str | None = None) -> str:
        """Natural phrase for answers: 'late payment fee for the Platinum Credit Card'."""
        subject, _, attr = slot_key.partition("|")
        a = self.attribute_label(attr)
        s = self.subjects.get(subject)
        text = f"{a[0].lower() + a[1:]} for {s.phrase if s else subject}"
        if scope and scope != "all":
            text += f" ({self.scope_label(scope).lower()})"
        return text

    def scope_label(self, scope: str) -> str:
        s = self.scopes.get(scope)
        return s.label if s else ("All" if scope == "all" else scope)

    def topic_of(self, subject_id: str) -> str | None:
        s = self.subjects.get(subject_id)
        return s.topic if s else None

    def registry_prompt(self) -> tuple[str, str, str]:
        ents = "\n".join(f"- {s.id}: {s.label}" for s in self.subjects.values())
        attrs = "\n".join(f"- {a.id} ({a.kind.value}): {a.label}" for a in self.attributes.values())
        scopes = ", ".join(["all", *self.scopes])
        return ents, attrs, scopes


def _non_overlapping(cands: list[Match]) -> list[Match]:
    chosen: list[Match] = []
    for c in sorted(cands, key=lambda m: (-m.length, m.start)):
        if all(c.end <= o.start or c.start >= o.end for o in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda m: m.start)


def scopes_overlap(a: str | None, b: str | None) -> bool:
    if a in (None, "all") or b in (None, "all"):
        return True
    return a == b


@lru_cache(maxsize=4)
def load_lexicon(path: Path) -> Lexicon:
    return Lexicon(yaml.safe_load(path.read_text(encoding="utf-8")))
