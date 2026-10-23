"""PostgreSQL + pgvector path. Runs when TEST_POSTGRES_URL points at an empty database with
the `vector` extension available, e.g.
    TEST_POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/truthguard_test
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import CORPUS, make_settings
from truthguard.domain.enums import AnswerStatus
from truthguard.retrieval.dense import NumpyDenseIndex
from truthguard.services.container import AppContainer

PG_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL not set")]


async def test_postgres_pgvector_end_to_end(tmp_path: Path) -> None:
    c = AppContainer(make_settings(tmp_path, database_url=PG_URL, vector_backend="pgvector"))
    await c.db.drop_all()
    await c.startup()
    try:
        await c.seed(CORPUS)
        assert c.corpus.stats()["documents"] == 42
        f = (await c.ask("What is the late payment fee on the Platinum Credit Card?")).final
        assert f is not None and f.status == AnswerStatus.RESOLVED_CONFLICT
        # pgvector's exact-ish HNSW ranking agrees with the in-memory scan on the top hits
        vec = c.embedder.embed(["Platinum card late payment fee"])[0]
        pg = [cid for cid, _ in await c.retriever.dense.search(vec, 5)]
        local = [cid for cid, _ in await NumpyDenseIndex(c.corpus).search(vec, 5)]
        assert pg[:3] == local[:3]
    finally:
        await c.db.drop_all()
        await c.shutdown()
