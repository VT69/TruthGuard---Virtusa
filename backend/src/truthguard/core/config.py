"""Typed settings loaded from the environment (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_DIR.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # storage
    database_url: str = "sqlite+aiosqlite:///./truthguard.db"
    vector_backend: Literal["numpy", "pgvector"] = "numpy"

    # api
    api_key: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    max_upload_bytes: int = 5 * 1024 * 1024
    allowed_upload_types: list[str] = Field(
        default_factory=lambda: [".md", ".txt", ".html", ".htm", ".docx", ".pdf"]
    )

    # LLM. offline = deterministic rule-based agents (no keys needed),
    # live = real providers with offline fallback, replay = cached responses only.
    llm_mode: Literal["offline", "live", "replay"] = "offline"
    llm_providers: list[str] = Field(default_factory=lambda: ["groq", "gemini", "ollama"])
    planner_model: str = "groq/llama-3.1-8b-instant"
    analyst_model: str = "groq/llama-3.3-70b-versatile"
    synth_model: str = "gemini/gemini-2.0-flash"
    critic_model: str = "groq/llama-3.3-70b-versatile"
    extract_model: str = "groq/llama-3.1-8b-instant"
    judge_model: str = "ollama/qwen2.5:7b"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2
    llm_cache_dir: Path = Path("./.cache/llm")
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # models
    embed_backend: Literal["hashing", "sentence-transformers", "openai"] = "hashing"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    rerank_backend: Literal["lexical", "cross-encoder"] = "lexical"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    nli_backend: Literal["heuristic", "cross-encoder"] = "heuristic"
    nli_model: str = "cross-encoder/nli-deberta-v3-xsmall"

    # retrieval
    dense_top_k: int = 40
    lexical_top_k: int = 40
    rrf_k: int = 60
    rerank_top_k: int = 12
    lineage_expansion: bool = True
    counter_retrieval: bool = True
    max_revision_rounds: int = 2

    # resolution / confidence
    precedence_path: Path = PACKAGE_DIR / "resolution" / "precedence.yaml"
    lexicon_path: Path = PACKAGE_DIR / "ingestion" / "lexicon.yaml"
    abstain_threshold: float = 0.35

    # demo
    demo_mode: bool = False
    seed_corpus_dir: Path = REPO_ROOT / "data" / "sample_corpus"
    today_override: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
