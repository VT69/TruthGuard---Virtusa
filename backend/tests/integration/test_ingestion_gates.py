"""Phase-2 gates, measured against the generator's ground-truth manifest:
lineage-edge recall >= 0.9 and the corpus audit finds >= 85% of planted conflicts."""

from __future__ import annotations

from typing import Any

from tests.conftest import key_to_doc_id
from truthguard.services.container import AppContainer


async def test_every_document_ingested(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    assert len(seeded.corpus.documents) == len(manifest["documents"]) == 42


async def test_extracted_facts_match_manifest(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    ids = key_to_doc_id(manifest, seeded)
    expected = {
        (ids[d["key"]], f["subject"], f["attribute"], f["scope"], f["value"])
        for d in manifest["documents"]
        for f in d["facts"]
    }
    got = {
        (a.document_id, a.subject_id, a.attribute, a.scope, a.value_norm)
        for a in seeded.corpus.assertions.values()
    }
    # the deterministic extractor must reproduce the generator's ground truth exactly
    assert sorted(expected - got) == []
    assert sorted(got - expected) == []


async def test_lineage_edge_recall_gate(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    ids = key_to_doc_id(manifest, seeded)
    expected = {(ids[e["from"]], ids[e["to"]], e["relation"]) for e in manifest["edges"]}
    got = {(e.from_doc, e.to_doc, e.relation.value) for e in seeded.corpus.edges}
    recall = len(expected & got) / len(expected)
    assert recall >= 0.9, sorted(expected - got)
    scoped = {(e.from_doc, e.scope_section) for e in seeded.corpus.edges if e.scope_section}
    assert (ids["card_fees_amendment_2025_03"], "4.2") in scoped
    inferred = {e.from_doc: e.confidence for e in seeded.corpus.edges}
    assert inferred[ids["account_closure_procedure_revised"]] < inferred[ids["card_fees_policy_v2"]]


async def test_audit_finds_planted_conflicts_gate(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    async with seeded.db.session() as s:
        from truthguard.storage.repositories import CorpusRepository

        audit = await CorpusRepository(s).conflicts()
    slots = {c.slot_key for c, _, _ in audit}
    planted = [pc for pc in manifest["planted_conflicts"] if pc["case"] != "prompt_injection"]
    found = sum(1 for pc in planted if pc["slot"] in slots)
    assert found / len(planted) >= 0.85
    # numeric-drift controls must NOT appear as conflicts
    for ctl in manifest["numeric_drift_controls"]:
        assert ctl["slot"] not in slots


async def test_injection_is_quarantined(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    ids = key_to_doc_id(manifest, seeded)
    doc = seeded.corpus.documents[ids["platinum_offers_update_2025"]]
    assert doc.injection_flag
    zero = [a for a in seeded.corpus.assertions.values() if a.value_norm == "INR:0"]
    assert zero and all(a.quarantined for a in zero)


async def test_metadata_provenance(seeded: AppContainer, manifest: dict[str, Any]) -> None:
    ids = key_to_doc_id(manifest, seeded)
    explicit = seeded.corpus.documents[ids["card_fees_policy_v1"]]
    assert explicit.field_provenance["effective_from"].provenance.value == "explicit"
    header = seeded.corpus.documents[ids["platinum_card_terms_2024"]]
    assert header.field_provenance["effective_from"].provenance.value == "inferred"
    undated = seeded.corpus.documents[ids["account_closure_procedure_revised"]]
    assert undated.effective_from is None and undated.published_at is None
    assert undated.doc_type == "procedure" and undated.authority_tier == 2


async def test_reingest_is_idempotent(seeded: AppContainer) -> None:
    from tests.conftest import CORPUS

    before = seeded.corpus.stats()
    results = await seeded.ingest(
        [(p.name, p.read_bytes()) for p in sorted(CORPUS.glob("*.md"))[:3]], origin="seed"
    )
    assert all(r.skipped for r in results)
    assert seeded.corpus.stats() == before
