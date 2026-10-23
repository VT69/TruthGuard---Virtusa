from __future__ import annotations

import hashlib
import re
import uuid


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 12) -> str:
    """Deterministic id from its parts, so re-ingesting identical content is idempotent."""
    digest = sha256_hex("\x1f".join(str(p) for p in parts))[:length]
    return f"{prefix}_{digest}"


def new_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:16]}"


def slugify(text: str, max_len: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len] or "doc"
