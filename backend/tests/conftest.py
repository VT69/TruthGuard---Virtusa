from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from truthguard.core.config import REPO_ROOT, Settings
from truthguard.core.logging import configure_logging
from truthguard.services.container import AppContainer

CORPUS = REPO_ROOT / "data" / "sample_corpus"
EVAL_TODAY = "2026-10-23"

configure_logging("WARNING")


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "database_url": f"sqlite+aiosqlite:///{tmp / 'tg.db'}",
        "llm_cache_dir": tmp / "llm",
        "today_override": EVAL_TODAY,
        "llm_mode": "offline",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
async def seeded(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[AppContainer]:
    """One seeded corpus shared by read-only integration tests."""
    tmp = tmp_path_factory.mktemp("seeded")
    c = AppContainer(make_settings(tmp))
    await c.startup()
    await c.seed(CORPUS)
    yield c
    await c.shutdown()


def key_to_doc_id(manifest: dict[str, Any], container: AppContainer) -> dict[str, str]:
    by_file = {d.source_uri: d.id for d in container.corpus.documents.values()}
    return {m["key"]: by_file[m["filename"]] for m in manifest["documents"]}
