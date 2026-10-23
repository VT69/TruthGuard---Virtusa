"""Application container: builds and wires every component from Settings.

One instance per process (FastAPI lifespan, eval runner, CLI). The corpus snapshot and the
components that read it are rebuilt atomically after each ingest.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from truthguard.agents.analyst import AnalystAgent
from truthguard.agents.baselines import BaselineRunner
from truthguard.agents.critic import CriticAgent
from truthguard.agents.finalizer import FinalizerAgent
from truthguard.agents.planner import PlannerAgent
from truthguard.agents.synthesizer import SynthesizerAgent
from truthguard.core.clock import today
from truthguard.core.config import Settings
from truthguard.core.errors import NotFoundError
from truthguard.core.ids import new_trace_id
from truthguard.core.logging import get_logger
from truthguard.domain.enums import JobStage
from truthguard.domain.models import (
    BaselineAnswer,
    EvidenceSet,
    FinalAnswer,
    QueryPlan,
    SlotAnalysis,
    TraceEvent,
)
from truthguard.ingestion.lexicon import Lexicon, load_lexicon
from truthguard.ingestion.parse import EXTENSIONS
from truthguard.ingestion.pipeline import IngestPipeline, IngestResult, Overrides
from truthguard.llm.cache import ResponseCache
from truthguard.llm.gateway import LLMGateway
from truthguard.llm.providers import Provider, build_providers
from truthguard.orchestration.graph import EventSink, GraphOptions, GraphState, TruthGuardGraph
from truthguard.resolution.engine import Resolver
from truthguard.resolution.policy import PrecedencePolicy, load_policy
from truthguard.retrieval.corpus import CorpusSnapshot
from truthguard.retrieval.dense import DenseIndex, NumpyDenseIndex, PgVectorDenseIndex
from truthguard.retrieval.embed import Embedder, build_embedder
from truthguard.retrieval.rerank import CrossEncoderReranker, LexicalReranker, Reranker
from truthguard.retrieval.retriever import RetrievalConfig, Retriever
from truthguard.storage.db import Database
from truthguard.storage.repositories import CorpusRepository, JobRepository, PolicyRepository, TraceRepository
from truthguard.verification.nli import CrossEncoderNLI, HeuristicNLI, NLIScorer

log = get_logger(__name__)


@dataclass
class QueryOutcome:
    final: FinalAnswer | None
    baseline: BaselineAnswer | None
    state: GraphState | None


class AppContainer:
    def __init__(self, settings: Settings, providers: dict[str, Provider] | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.lexicon: Lexicon = load_lexicon(settings.lexicon_path)
        self.default_policy = load_policy(settings.precedence_path)
        self.policy = self.default_policy
        self.embedder: Embedder = build_embedder(
            settings.embed_backend, settings.embed_model, settings.embed_dim, api_key=settings.openai_api_key
        )
        self.reranker: Reranker = (
            CrossEncoderReranker(settings.rerank_model)
            if settings.rerank_backend == "cross-encoder"
            else LexicalReranker(self.lexicon)
        )
        self.nli: NLIScorer = (
            CrossEncoderNLI(settings.nli_model)
            if settings.nli_backend == "cross-encoder"
            else HeuristicNLI(self.lexicon)
        )
        if providers is None:
            providers = build_providers(
                groq_api_key=settings.groq_api_key,
                gemini_api_key=settings.gemini_api_key,
                openai_api_key=settings.openai_api_key,
                anthropic_api_key=settings.anthropic_api_key,
                ollama_base_url=settings.ollama_base_url,
            )
        self.gateway = LLMGateway(
            providers,
            ResponseCache(settings.llm_cache_dir),
            mode=settings.llm_mode,
            fallback_order=settings.llm_providers,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
        self.pipeline = IngestPipeline(
            self.db, self.lexicon, self.embedder, self.gateway, settings.extract_model, lambda: self.policy
        )
        self.corpus = CorpusSnapshot()
        self._lock = asyncio.Lock()
        self.background: set[asyncio.Task[Any]] = set()
        self._wire()

    # ------------------------------------------------------------------ lifecycle

    def _wire(self) -> None:
        s = self.settings
        dense: DenseIndex = (
            PgVectorDenseIndex(self.db) if s.vector_backend == "pgvector" else NumpyDenseIndex(self.corpus)
        )
        self.retriever = Retriever(
            self.corpus,
            self.embedder,
            self.reranker,
            self.lexicon,
            RetrievalConfig(
                dense_top_k=s.dense_top_k,
                lexical_top_k=s.lexical_top_k,
                rrf_k=s.rrf_k,
                top_k=s.rerank_top_k,
                lineage_expansion=s.lineage_expansion,
            ),
            dense,
        )
        topic_of = {sid: subj.topic for sid, subj in self.lexicon.subjects.items()}
        self.planner = PlannerAgent(self.lexicon, self.gateway, s.planner_model)
        self.analyst = AnalystAgent(topic_of)
        self.synthesizer = SynthesizerAgent(self.lexicon, self.gateway, s.synth_model)
        self.critic = CriticAgent(
            self.lexicon,
            self.corpus,
            self.retriever,
            self.nli,
            self.gateway,
            s.critic_model,
            counter_retrieval=s.counter_retrieval,
        )
        self.finalizer = FinalizerAgent(self.lexicon, self.gateway, s.planner_model, s.abstain_threshold)
        self.graph = TruthGuardGraph(
            self.planner, self.retriever, self.analyst, self.synthesizer, self.critic, self.finalizer
        )
        self.baselines = BaselineRunner(
            self.lexicon, self.corpus, self.retriever, self.gateway, s.synth_model
        )

    async def startup(self, create_schema: bool = True) -> None:
        if create_schema:
            await self.db.create_all()
        async with self.db.session() as s:
            stored = await PolicyRepository(s).get()
        if stored:
            self.policy = PrecedencePolicy.model_validate(stored)
        await self.refresh()

    async def shutdown(self) -> None:
        for t in list(self.background):
            t.cancel()
        await self.db.dispose()

    async def refresh(self) -> None:
        async with self.db.session() as s:
            data = await CorpusRepository(s).load_all()
        snapshot = CorpusSnapshot.build(data, self.embedder)
        async with self._lock:
            snapshot.version = self.corpus.version + 1
            self.corpus = snapshot
            self._wire()

    # ------------------------------------------------------------------ ingest

    async def ingest(
        self,
        files: list[tuple[str, bytes]],
        origin: str = "user",
        job_id: str | None = None,
        overrides: Overrides | None = None,
    ) -> list[IngestResult]:
        async def progress(stage: JobStage, info: dict[str, object]) -> None:
            if job_id is None:
                return
            async with self.db.session() as s:
                await JobRepository(s).update(job_id, stage=stage.value, status="running", progress=info)

        results = []
        try:
            for name, data in files:
                results.append(await self.pipeline.ingest_file(name, data, origin, overrides, progress))
            if job_id:
                async with self.db.session() as s:
                    await JobRepository(s).update(job_id, stage=JobStage.AUDIT.value)
            await self.pipeline.finalize(self.today())
            await self.refresh()
        except Exception as exc:
            if job_id:
                async with self.db.session() as s:
                    await JobRepository(s).update(
                        job_id, status="failed", stage=JobStage.FAILED.value, error=str(exc)
                    )
            raise
        if job_id:
            async with self.db.session() as s:
                await JobRepository(s).update(
                    job_id,
                    status="done",
                    stage=JobStage.DONE.value,
                    document_ids=[r.doc_id for r in results],
                    progress={
                        "results": [
                            {
                                "doc_id": r.doc_id,
                                "filename": r.filename,
                                "skipped": r.skipped,
                                "chunks": r.chunks,
                                "assertions": r.assertions,
                                "injection_flag": r.injection_flag,
                                "timings_ms": r.timings_ms,
                            }
                            for r in results
                        ]
                    },
                )
        return results

    async def seed(self, directory: Path | None = None) -> list[IngestResult]:
        directory = directory or self.settings.seed_corpus_dir
        files = [
            (p.name, p.read_bytes())
            for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix.lower() in EXTENSIONS
        ]
        return await self.ingest(files, origin="seed")

    async def reset(self) -> dict[str, int]:
        """Demo reset: drop judge-added documents and restore the default precedence policy."""
        async with self.db.session() as s:
            repo = CorpusRepository(s)
            ids = await repo.user_document_ids()
            await repo.delete_documents(ids)
            await PolicyRepository(s).clear()
        self.policy = self.default_policy
        await self.pipeline.finalize(self.today())
        await self.refresh()
        return {"removed_documents": len(ids)}

    # ------------------------------------------------------------------ policy

    async def set_policy(self, policy: PrecedencePolicy) -> None:
        async with self.db.session() as s:
            await PolicyRepository(s).put(policy.model_dump(mode="json"))
        self.policy = policy
        await self.pipeline.finalize(self.today())
        await self.refresh()

    # ------------------------------------------------------------------ queries

    def today(self) -> date:
        if self.settings.today_override:
            from truthguard.ingestion.metadata import parse_date

            d = parse_date(self.settings.today_override)
            if d:
                return d
        return today()

    async def ask(
        self,
        question: str,
        as_of: date | None = None,
        mode: str = "truthguard",
        sink: EventSink | None = None,
        options: GraphOptions | None = None,
        persist: bool = True,
    ) -> QueryOutcome:
        trace_id = new_trace_id()
        default_as_of = as_of or self.today()
        if mode.upper() in ("B0", "B1", "B2", "BASELINE"):
            bmode = "B0" if mode.upper() == "BASELINE" else mode.upper()
            plan = self.planner.rule_plan(question, default_as_of, explicit_as_of=as_of is not None)
            ans = await self.baselines.run(bmode, trace_id, plan, self.policy)
            if sink is not None:
                from truthguard.core.clock import now

                await sink(
                    TraceEvent(
                        trace_id=trace_id,
                        seq=0,
                        event="answer.final",
                        node="baseline",
                        ts=now(),
                        payload=ans.model_dump(mode="json"),
                    )
                )
            return QueryOutcome(final=None, baseline=ans, state=None)
        opts = options or GraphOptions(
            lineage_expansion=self.settings.lineage_expansion,
            counter_retrieval=self.settings.counter_retrieval,
            max_rounds=self.settings.max_revision_rounds,
        )
        state = GraphState(trace_id=trace_id, question=question, as_of=default_as_of)
        await self.graph.run(state, Resolver(self.policy), sink, opts, explicit_as_of=as_of is not None)
        if persist and state.final is not None:
            await self._persist(state)
        return QueryOutcome(final=state.final, baseline=None, state=state)

    async def _persist(self, state: GraphState) -> None:
        assert state.final is not None
        async with self.db.session() as s:
            await TraceRepository(s).save(
                trace_id=state.trace_id,
                question=state.question,
                as_of=state.final.as_of,
                mode="truthguard",
                status=state.final.status.value,
                final=state.final.model_dump(mode="json"),
                state=state.snapshot(),
                events=[e.model_dump(mode="json") for e in state.events],
                latency_ms=state.final.usage.latency_ms,
            )

    async def re_resolve(
        self, trace_id: str, as_of: date | None, policy: PrecedencePolicy | None
    ) -> FinalAnswer:
        async with self.db.session() as s:
            row = await TraceRepository(s).get(trace_id)
        if row is None:
            raise NotFoundError(f"trace {trace_id} not found")
        plan = QueryPlan.model_validate(row.state["plan"])
        evidence = EvidenceSet.model_validate(row.state["evidence"])
        analyses = [SlotAnalysis.model_validate(a) for a in row.state["analyses"]]
        return await self.graph.re_resolve(
            trace_id, plan, evidence, analyses, as_of or plan.as_of, Resolver(policy or self.policy)
        )

    async def trace(self, trace_id: str) -> dict[str, Any]:
        async with self.db.session() as s:
            row = await TraceRepository(s).get(trace_id)
        if row is None:
            raise NotFoundError(f"trace {trace_id} not found")
        return {
            "id": row.id,
            "question": row.question,
            "as_of": row.as_of.isoformat(),
            "status": row.status,
            "final": row.final,
            "events": row.events,
            "latency_ms": row.latency_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def recent_traces(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            rows = await TraceRepository(s).recent(limit)
        return [
            {
                "id": r.id,
                "question": r.question,
                "status": r.status,
                "as_of": r.as_of.isoformat(),
                "latency_ms": r.latency_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
