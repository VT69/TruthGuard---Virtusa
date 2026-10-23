# TruthGuard API: FastAPI + agents. CPU-only, no model downloads in the default build.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir uv==0.8.17
WORKDIR /app/backend

COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN uv pip install --no-cache .

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY data/sample_corpus /app/data/sample_corpus

RUN useradd --create-home --uid 10001 truthguard && mkdir -p /app/.cache && chown -R truthguard /app
USER truthguard

ENV SEED_CORPUS_DIR=/app/data/sample_corpus LLM_CACHE_DIR=/app/.cache/llm LOG_JSON=true
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
# migrate, then serve (the app seeds the sample corpus on first start)
CMD ["sh", "-c", "alembic upgrade head && uvicorn truthguard.api.app:app_factory --factory --host 0.0.0.0 --port 8000"]
