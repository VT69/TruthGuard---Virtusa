"""Injectable clock so tests, eval and demos are reproducible."""

from __future__ import annotations

from datetime import UTC, date, datetime

_override: date | None = None


def set_today(value: date | None) -> None:
    global _override  # noqa: PLW0603 - module-level test seam
    _override = value


def today() -> date:
    return _override or datetime.now(UTC).date()


def now() -> datetime:
    return datetime.now(UTC)
