"""FastAPI application: REST + SSE streaming, API-key stub, strict CORS, Prometheus metrics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from truthguard import __version__
from truthguard.api.schemas import JobAccepted, QueryRequest, QuickDocumentRequest, ReResolveRequest
from truthguard.core.config import REPO_ROOT, Settings, get_settings
from truthguard.core.errors import IngestError, NotFoundError, TruthGuardError, UploadRejectedError
from truthguard.core.ids import new_trace_id, slugify
from truthguard.core.logging import configure_logging, get_logger
from truthguard.domain.models import TraceEvent
from truthguard.ingestion.pipeline import Overrides
from truthguard.observability import metrics as m
from truthguard.resolution.policy import PrecedencePolicy
from truthguard.services.container import AppContainer
from truthguard.services.health import health_summary, lineage_view, report_markdown
from truthguard.storage.repositories import CorpusRepository, JobRepository

log = get_logger(__name__)


DEMO_QUESTIONS = [
    "What is the late payment fee on the Platinum Credit Card?",
    "How often must high-risk customers update their KYC?",
    "How many days do customers have to report a disputed transaction?",
    "What is the interest rate on a 1-year fixed deposit?",
]


def get_container(request: Request) -> AppContainer:
    c: AppContainer = request.app.state.container
    return c


C = Annotated[AppContainer, Depends(get_container)]


def create_app(settings: Settings | None = None, container: AppContainer | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or AppContainer(settings)
        await c.startup()
        if not c.corpus.documents and settings.seed_corpus_dir.exists() and settings.env != "test":
            log.info("startup.seeding", directory=str(settings.seed_corpus_dir))
            await c.seed()
        if settings.demo_mode:
            # warm caches (and, in live mode, the LLM response cache) for the scripted questions
            for q in DEMO_QUESTIONS:
                try:
                    await c.ask(q, persist=False)
                except Exception as exc:  # demo must still start
                    log.warning("demo.warmup_failed", question=q, error=str(exc)[:200])
        app.state.container = c
        yield
        await c.shutdown()

    app = FastAPI(title="TruthGuard API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    async def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if settings.api_key and x_api_key != settings.api_key:
            raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")

    auth = [Depends(require_key)]

    @app.exception_handler(NotFoundError)
    async def _nf(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(TruthGuardError)
    async def _tg(_: Request, exc: TruthGuardError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # ------------------------------------------------------------------ health

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz(c: C) -> dict[str, Any]:
        await c.db.ping()
        return {"status": "ready", "corpus": c.corpus.stats(), "llm_mode": c.gateway.mode}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(m.REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/meta", dependencies=auth)
    async def meta(c: C) -> dict[str, Any]:
        s = c.settings
        return {
            "version": __version__,
            "today": c.today().isoformat(),
            "llm_mode": s.llm_mode,
            "demo_mode": s.demo_mode,
            "models": {
                "planner": s.planner_model,
                "synthesizer": s.synth_model,
                "critic": s.critic_model,
                "extractor": s.extract_model,
            },
            "backends": {
                "embed": s.embed_backend,
                "rerank": s.rerank_backend,
                "nli": s.nli_backend,
                "vector": s.vector_backend,
                "database": c.db.url.split(":")[0],
            },
            "corpus": c.corpus.stats(),
        }

    # ------------------------------------------------------------------ documents

    @app.post("/v1/documents", dependencies=auth, response_model=JobAccepted, status_code=202)
    async def upload(c: C, files: Annotated[list[UploadFile], File()]) -> JobAccepted:
        payload: list[tuple[str, bytes]] = []
        for f in files:
            name = Path(f.filename or "upload").name
            if Path(name).suffix.lower() not in settings.allowed_upload_types:
                raise HTTPException(415, f"unsupported file type: {name}")
            data = await f.read(settings.max_upload_bytes + 1)
            if len(data) > settings.max_upload_bytes:
                raise HTTPException(413, f"{name} exceeds {settings.max_upload_bytes} bytes")
            payload.append((name, data))
        job_id = f"job_{new_trace_id()[3:]}"
        async with c.db.session() as s:
            await JobRepository(s).create(job_id)

        async def run() -> None:
            try:
                results = await c.ingest(payload, origin="user", job_id=job_id)
                m.INGESTED.labels("user").inc(sum(1 for r in results if not r.skipped))
            except Exception as exc:  # recorded on the job row
                log.error("ingest.failed", job=job_id, error=str(exc))

        task = asyncio.create_task(run())
        c.background.add(task)
        task.add_done_callback(c.background.discard)
        return JobAccepted(job_id=job_id, files=[n for n, _ in payload])

    @app.post("/v1/documents/quick", dependencies=auth)
    async def quick_document(c: C, body: QuickDocumentRequest) -> dict[str, Any]:
        """Challenge Mode fast path: one short document, ingested synchronously."""
        tier_types = set(c.policy.tiers)
        if body.doc_type not in tier_types:
            raise HTTPException(422, f"doc_type must be one of {sorted(tier_types)}")
        name = f"{slugify(body.title)[:40]}_{new_trace_id()[3:9]}.md"
        text = f"# {body.title}\n\n{body.text.strip()}\n"
        try:
            results = await c.ingest(
                [(name, text.encode("utf-8"))],
                origin="user",
                overrides=Overrides(
                    title=body.title,
                    doc_type=body.doc_type,
                    effective_from=body.effective_from,
                    published_at=body.published_at or body.effective_from or c.today(),
                ),
            )
        except IngestError as exc:
            raise UploadRejectedError(str(exc)) from exc
        r = results[0]
        m.INGESTED.labels("user").inc()
        doc = c.corpus.documents.get(r.doc_id)
        return {
            "document_id": r.doc_id,
            "skipped": r.skipped,
            "chunks": r.chunks,
            "assertions": r.assertions,
            "injection_flag": r.injection_flag,
            "timings_ms": r.timings_ms,
            "assertions_detail": [
                a.model_dump(mode="json") for a in c.corpus.assertions.values() if a.document_id == r.doc_id
            ],
            "authority_tier": doc.authority_tier if doc else None,
        }

    @app.get("/v1/jobs/{job_id}", dependencies=auth)
    async def job(c: C, job_id: str) -> dict[str, Any]:
        async with c.db.session() as s:
            row = await JobRepository(s).get(job_id)
        if row is None:
            raise NotFoundError(f"job {job_id} not found")
        return row

    @app.get("/v1/documents", dependencies=auth)
    async def documents(c: C) -> list[dict[str, Any]]:
        today = c.today()
        counts: dict[str, int] = {}
        for a in c.corpus.assertions.values():
            counts[a.document_id] = counts.get(a.document_id, 0) + 1
        return [
            {
                "id": d.id,
                "title": d.title,
                "doc_type": d.doc_type,
                "tier": d.authority_tier,
                "version": d.version_label,
                "series_id": d.series_id,
                "topic": d.topic,
                "fmt": d.fmt,
                "origin": d.origin,
                "effective_from": d.effective_from,
                "published_at": d.published_at,
                "status": c.corpus.temporal_status(d.id, today).value,
                "injection_flag": d.injection_flag,
                "assertions": counts.get(d.id, 0),
                "field_provenance": {k: v.model_dump(mode="json") for k, v in d.field_provenance.items()},
            }
            for d in sorted(c.corpus.documents.values(), key=lambda d: (d.topic or "", d.title, d.id))
        ]

    @app.get("/v1/documents/{doc_id}", dependencies=auth)
    async def document(c: C, doc_id: str) -> dict[str, Any]:
        d = c.corpus.documents.get(doc_id)
        if d is None:
            raise NotFoundError(f"document {doc_id} not found")
        edges = [e.model_dump(mode="json") for e in c.corpus.edges if doc_id in (e.from_doc, e.to_doc)]
        return {
            "document": d.model_dump(mode="json"),
            "raw_text": c.corpus.raw_text.get(doc_id, ""),
            "status": c.corpus.temporal_status(doc_id, c.today()).value,
            "chunks": [ch.model_dump(mode="json") for ch in c.corpus.by_doc_chunks.get(doc_id, [])],
            "assertions": [
                a.model_dump(mode="json") for a in c.corpus.assertions.values() if a.document_id == doc_id
            ],
            "edges": edges,
        }

    @app.get("/v1/documents/{doc_id}/lineage", dependencies=auth)
    async def document_lineage(c: C, doc_id: str) -> dict[str, Any]:
        d = c.corpus.documents.get(doc_id)
        if d is None:
            raise NotFoundError(f"document {doc_id} not found")
        views = lineage_view(c.corpus, c.today(), d.series_id)
        return views[0] if views else {"series_id": d.series_id, "documents": [], "edges": []}

    @app.get("/v1/lineage", dependencies=auth)
    async def lineage(c: C) -> list[dict[str, Any]]:
        return lineage_view(c.corpus, c.today())

    # ------------------------------------------------------------------ query

    def _observe(final: dict[str, Any], mode: str) -> None:
        m.QUERIES.labels(mode, final.get("status", "?")).inc()
        usage = final.get("usage") or {}
        m.QUERY_LATENCY.labels(mode).observe(float(usage.get("latency_ms", 0)) / 1000)
        m.LLM_COST.inc(float(usage.get("cost_usd", 0)))
        m.LLM_TOKENS.inc(int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0)))
        for o in final.get("residual_objections", []):
            m.OBJECTIONS.labels(o["type"], o["severity"]).inc()

    @app.post("/v1/query", dependencies=auth)
    async def query(c: C, body: QueryRequest) -> StreamingResponse:
        queue: asyncio.Queue[TraceEvent | None] = asyncio.Queue()

        async def sink(ev: TraceEvent) -> None:
            m.NODE_LATENCY.labels(ev.node).observe(ev.latency_ms / 1000)
            await queue.put(ev)

        async def worker() -> None:
            try:
                out = await c.ask(body.question, body.as_of, body.mode, sink)
                final = out.final or out.baseline
                if final is not None:
                    _observe(final.model_dump(mode="json"), body.mode)
            except Exception as exc:
                log.error("query.failed", error=str(exc))
                from truthguard.core.clock import now

                await queue.put(
                    TraceEvent(
                        trace_id="error",
                        seq=-1,
                        event="error",
                        node="api",
                        ts=now(),
                        payload={"message": str(exc)[:300]},
                    )
                )
            finally:
                await queue.put(None)

        async def stream() -> AsyncIterator[str]:
            task = asyncio.create_task(worker())
            yield ": stream-open\n\n"
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if ev is None:
                        break
                    yield f"event: {ev.event}\ndata: {json.dumps(ev.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/query/sync", dependencies=auth)
    async def query_sync(c: C, body: QueryRequest) -> dict[str, Any]:
        out = await c.ask(body.question, body.as_of, body.mode)
        final = out.final or out.baseline
        assert final is not None
        data = final.model_dump(mode="json")
        _observe(data, body.mode)
        return data

    @app.get("/v1/traces", dependencies=auth)
    async def traces(c: C) -> list[dict[str, Any]]:
        return await c.recent_traces()

    @app.get("/v1/traces/{trace_id}", dependencies=auth)
    async def trace(c: C, trace_id: str) -> dict[str, Any]:
        return await c.trace(trace_id)

    @app.post("/v1/traces/{trace_id}/re-resolve", dependencies=auth)
    async def re_resolve(c: C, trace_id: str, body: ReResolveRequest) -> dict[str, Any]:
        final = await c.re_resolve(trace_id, body.as_of, body.policy)
        return final.model_dump(mode="json")

    # ------------------------------------------------------------------ conflicts / policy

    @app.get("/v1/conflicts", dependencies=auth)
    async def conflicts(c: C, topic: str | None = None, severity: str | None = None) -> dict[str, Any]:
        async with c.db.session() as s:
            items = await CorpusRepository(s).conflicts()
        summary = health_summary(items, c.corpus, c.lexicon, c.today())
        if topic:
            summary["conflicts"] = [r for r in summary["conflicts"] if r["topic"] == topic]
        if severity:
            summary["conflicts"] = [r for r in summary["conflicts"] if r["severity"] == severity]
        return summary

    @app.get("/v1/conflicts/report.md", dependencies=auth)
    async def conflicts_report(c: C) -> PlainTextResponse:
        async with c.db.session() as s:
            items = await CorpusRepository(s).conflicts()
        md = report_markdown(health_summary(items, c.corpus, c.lexicon, c.today()))
        return PlainTextResponse(
            md,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="corpus-health.md"'},
        )

    @app.get("/v1/policy/precedence", dependencies=auth)
    async def get_policy(c: C) -> dict[str, Any]:
        return {
            "policy": c.policy.model_dump(mode="json"),
            "default": c.default_policy.model_dump(mode="json"),
        }

    @app.put("/v1/policy/precedence", dependencies=auth)
    async def put_policy(c: C, policy: PrecedencePolicy) -> dict[str, Any]:
        await c.set_policy(policy)
        return {"policy": c.policy.model_dump(mode="json")}

    @app.post("/v1/policy/precedence/reset", dependencies=auth)
    async def reset_policy(c: C) -> dict[str, Any]:
        await c.set_policy(c.default_policy)
        return {"policy": c.policy.model_dump(mode="json")}

    @app.post("/v1/admin/reset", dependencies=auth)
    async def admin_reset(c: C) -> dict[str, Any]:
        return await c.reset()

    @app.get("/v1/eval/results", dependencies=auth)
    async def eval_results() -> dict[str, Any]:
        path = REPO_ROOT / "eval" / "reports" / "results.json"
        if not path.exists():
            raise NotFoundError("no eval results yet: run `make eval`")
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    return app


def app_factory() -> FastAPI:
    return create_app()
