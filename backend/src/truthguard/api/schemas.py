"""Request/response schemas for the REST API (domain models are reused for responses)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from truthguard.resolution.policy import PrecedencePolicy


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    as_of: date | None = None
    mode: Literal["truthguard", "baseline", "B0", "B1", "B2"] = "truthguard"


class QuickDocumentRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=10, max_length=20_000)
    doc_type: str = "memo"
    effective_from: date | None = None
    published_at: date | None = None


class ReResolveRequest(BaseModel):
    as_of: date | None = None
    policy: PrecedencePolicy | None = None


class JobAccepted(BaseModel):
    job_id: str
    files: list[str]
