# ADR 0004: Light format parsers; Docling as the upgrade path

**Status:** accepted · **Date:** 2026-10-23

## Context
We must parse PDF, DOCX, HTML, Markdown and text into sections with heading paths and offsets that let quotes be verified verbatim.

## Options
1. Docling — layout-aware, but downloads models and adds minutes to install; risky on a demo laptop.
2. Unstructured — similar weight.
3. **Per-format light parsers** (pdfplumber with font-size heading detection, python-docx styles, stdlib HTMLParser, Markdown/text rules) that all render to one canonical `raw_text`.

## Decision
Option 3 (`ingestion/parse.py`). Section and chunk offsets index `raw_text`, so citations are checkable character by character.

## Consequences
Scanned PDFs (OCR) and complex multi-column layouts are out of scope, as the spec allows. Swapping in Docling only has to produce the same `ParsedDocument`.
