"""Section-aware chunking: one chunk per section, long sections split at line boundaries
into 300-600 token windows with ~15% overlap. Chunk text is a verbatim slice of the
document's raw text, so quote offsets stay valid."""

from __future__ import annotations

from truthguard.core.ids import stable_id
from truthguard.domain.models import Chunk, ParsedDocument
from truthguard.ingestion.injection import is_injection

MAX_TOKENS = 600
OVERLAP = 0.15


def approx_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def chunk_document(doc_id: str, parsed: ParsedDocument) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in parsed.sections:
        body = section.text
        heading = section.heading_path[-1] if section.heading_path else ""
        if body.strip() == heading.strip() and len(parsed.sections) > 1:
            continue  # a bare heading carries no content of its own
        windows = _windows(body) if approx_tokens(body) > MAX_TOKENS else [(0, len(body))]
        for start, end in windows:
            text = body[start:end]
            ord_ = len(chunks)
            abs_start = section.char_start + start
            chunks.append(
                Chunk(
                    id=stable_id("ch", doc_id, ord_, abs_start),
                    document_id=doc_id,
                    ord=ord_,
                    section_path=section.heading_path,
                    text=text,
                    char_start=abs_start,
                    char_end=abs_start + len(text),
                    injection_flag=is_injection(text),
                )
            )
    return chunks


def _windows(body: str) -> list[tuple[int, int]]:
    lines: list[tuple[int, int]] = []
    pos = 0
    for line in body.split("\n"):
        lines.append((pos, pos + len(line)))
        pos += len(line) + 1
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        j = i
        tokens = 0
        while j < len(lines) and (tokens < MAX_TOKENS * 0.75 or j == i):
            tokens += approx_tokens(body[lines[j][0] : lines[j][1]])
            j += 1
        out.append((lines[i][0], lines[j - 1][1]))
        if j >= len(lines):
            break
        back = max(1, int((j - i) * OVERLAP))
        i = max(i + 1, j - back)
    return out


def index_text(title: str, section_path: list[str], text: str) -> str:
    return f"{title} | {' > '.join(section_path)}\n{text}"
