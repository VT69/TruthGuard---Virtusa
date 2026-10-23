"""Value normalization: every surface form of a value maps to one canonical `value_norm`.

"1.5%", "1.50%", "1.5 per cent" and "one and a half percent" all become `PCT:1.5`, which
is what makes the numeric-drift control a non-conflict. Durations are converted to the
attribute's unit ("twelve months" -> `YEARS:1` for a years-valued attribute).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from truthguard.domain.enums import Modality, ValueKind

WORD_NUMBERS: dict[str, float] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "eighteen": 18, "twenty": 20, "twenty-one": 21, "twenty-four": 24,
    "thirty": 30, "forty": 40, "forty-five": 45, "forty-eight": 48, "fifty": 50, "sixty": 60,
    "ninety": 90, "hundred": 100,
}  # fmt: skip
_WORD_ALT = "|".join(sorted((re.escape(w) for w in WORD_NUMBERS), key=len, reverse=True))
NUM = rf"(?:\d[\d,]*(?:\.\d+)?|(?:{_WORD_ALT})(?: and a half)?|half an?|a half)"

MONEY_RE = re.compile(r"(?:₹|\bRs\.?\s?|\bINR\s?)\s?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
PERCENT_RE = re.compile(rf"({NUM})\s?(?:%|per\s?cent\b|percent\b)", re.IGNORECASE)
DURATION_RE = re.compile(
    rf"(?:\b({NUM})\s+(?:working\s+|calendar\s+|business\s+)?(days?|months?|years?)\b"
    r"|\b(?:once\s+)?(?:every|a|per)\s+(year|month)\b|\b(annually)\b)",
    re.IGNORECASE,
)
MUST_RE = re.compile(r"\b(must|shall|mandatory|required|compulsory|is to be|are to be)\b", re.IGNORECASE)
MUST_NOT_RE = re.compile(r"\b(must not|shall not|may not|not permitted|prohibited|cannot)\b", re.IGNORECASE)
MAY_RE = re.compile(r"\b(may|optional|can|permitted|allowed|option to)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedValue:
    kind: ValueKind
    value_norm: str
    value_display: str
    value_num: float | None
    unit: str | None
    start: int
    end: int


def parse_number(text: str) -> float | None:
    t = text.strip().lower().replace(",", "")
    if t in ("half a", "half an", "a half"):
        return 0.5
    half = 0.0
    if t.endswith(" and a half"):
        half, t = 0.5, t[: -len(" and a half")]
    if t in WORD_NUMBERS:
        return WORD_NUMBERS[t] + half
    try:
        return float(t) + half
    except ValueError:
        return None


def fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(round(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def display(kind: ValueKind, num: float | None, unit: str | None = None, text: str = "") -> str:
    if num is None:
        return text
    if kind == ValueKind.MONEY:
        return f"₹{num:,.0f}" if num == round(num) else f"₹{num:,.2f}"
    if kind == ValueKind.PERCENT:
        return f"{fmt_num(num)}%"
    if kind in (ValueKind.DAYS, ValueKind.MONTHS, ValueKind.YEARS):
        word = kind.value[:-1] if num == 1 else kind.value
        return f"{fmt_num(num)} {word}"
    if kind == ValueKind.COUNT:
        return f"{fmt_num(num)} {unit}" if unit else fmt_num(num)
    return text


def norm_key(kind: ValueKind, num: float) -> str:
    prefix = {
        ValueKind.MONEY: "INR",
        ValueKind.PERCENT: "PCT",
        ValueKind.DAYS: "DAYS",
        ValueKind.MONTHS: "MONTHS",
        ValueKind.YEARS: "YEARS",
        ValueKind.COUNT: "COUNT",
    }[kind]
    return f"{prefix}:{fmt_num(num)}"


def _convert_duration(num: float, unit: str, target: ValueKind) -> float | None:
    unit = unit.lower().rstrip("s")
    days = {"day": 1.0, "month": 30.0, "year": 365.0}
    if target == ValueKind.DAYS and unit == "day":
        return num
    if target == ValueKind.MONTHS:
        if unit == "month":
            return num
        if unit == "year":
            return num * 12
    if target == ValueKind.YEARS:
        if unit == "year":
            return num
        if unit == "month" and num % 12 == 0:
            return num / 12
    if target == ValueKind.DAYS and unit in days:
        return num * days[unit]
    return None


def find_values(
    text: str, kind: ValueKind, count_unit: str | None = None, unit_label: str | None = None
) -> list[ParsedValue]:
    """All values of `kind` in `text`, in order of appearance."""
    out: list[ParsedValue] = []
    if kind == ValueKind.MONEY:
        for m in MONEY_RE.finditer(text):
            num = parse_number(m.group(1))
            if num is not None:
                out.append(
                    ParsedValue(kind, norm_key(kind, num), display(kind, num), num, "INR", m.start(), m.end())
                )
    elif kind == ValueKind.PERCENT:
        for m in PERCENT_RE.finditer(text):
            num = parse_number(m.group(1))
            if num is not None:
                out.append(
                    ParsedValue(kind, norm_key(kind, num), display(kind, num), num, "%", m.start(), m.end())
                )
    elif kind in (ValueKind.DAYS, ValueKind.MONTHS, ValueKind.YEARS):
        for m in DURATION_RE.finditer(text):
            if m.group(1):
                num, unit = parse_number(m.group(1)), m.group(2)
            elif m.group(3):
                num, unit = 1.0, m.group(3)
            else:
                num, unit = 1.0, "year"
            if num is None:
                continue
            converted = _convert_duration(num, unit, kind)
            if converted is None:
                continue
            out.append(
                ParsedValue(
                    kind,
                    norm_key(kind, converted),
                    display(kind, converted),
                    converted,
                    kind.value,
                    m.start(),
                    m.end(),
                )
            )
    elif kind == ValueKind.COUNT:
        pattern = re.compile(rf"\b({NUM})\s+(?:{count_unit or 'units?'})", re.IGNORECASE)
        for m in pattern.finditer(text):
            num = parse_number(m.group(1))
            if num is not None:
                out.append(
                    ParsedValue(
                        kind,
                        norm_key(kind, num),
                        display(kind, num, unit_label),
                        num,
                        unit_label,
                        m.start(),
                        m.end(),
                    )
                )
    elif kind == ValueKind.MODALITY:
        mod = detect_modality(text)
        if mod in (Modality.MUST, Modality.MAY, Modality.MUST_NOT):
            label = {
                "must": "mandatory (must)",
                "may": "optional (may)",
                "must_not": "prohibited (must not)",
            }[mod.value]
            out.append(ParsedValue(kind, f"MODALITY:{mod.value}", label, None, None, 0, len(text)))
    return out


def detect_modality(text: str) -> Modality:
    if MUST_NOT_RE.search(text):
        return Modality.MUST_NOT
    if MUST_RE.search(text):
        return Modality.MUST
    if MAY_RE.search(text):
        return Modality.MAY
    return Modality.STATES


def parse_value_norm(value_norm: str) -> tuple[str, float | None]:
    prefix, _, raw = value_norm.partition(":")
    try:
        return prefix, float(raw)
    except ValueError:
        return prefix, None


def display_for_norm(value_norm: str, unit_label: str | None = None) -> str:
    prefix, num = parse_value_norm(value_norm)
    kind = {
        "INR": ValueKind.MONEY,
        "PCT": ValueKind.PERCENT,
        "DAYS": ValueKind.DAYS,
        "MONTHS": ValueKind.MONTHS,
        "YEARS": ValueKind.YEARS,
        "COUNT": ValueKind.COUNT,
    }.get(prefix)
    if prefix == "MODALITY":
        return {"must": "mandatory (must)", "may": "optional (may)", "must_not": "prohibited (must not)"}.get(
            value_norm.partition(":")[2], value_norm
        )
    if kind is None:
        return value_norm
    return display(kind, num, unit_label, value_norm)


def values_in_text(text: str) -> set[str]:
    """Every normalized value mentioned anywhere in free text (used by the blend detector)."""
    found: set[str] = set()
    for kind in (ValueKind.MONEY, ValueKind.PERCENT, ValueKind.DAYS, ValueKind.MONTHS, ValueKind.YEARS):
        for v in find_values(text, kind):
            found.add(v.value_norm)
    for v in find_values(
        text, ValueKind.COUNT, r"(?:reward )?points?|(?:complimentary )?(?:lounge )?visits?"
    ):
        found.add(v.value_norm)
    return found
