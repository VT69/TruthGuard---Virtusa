"""HTTP API: health, SSE stream, documents, quick ingest (Challenge Mode), time travel,
policy edit, reset, upload validation and the API-key stub."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CORPUS, make_settings
from truthguard.api.app import create_app
from truthguard.services.container import AppContainer


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    tmp = tmp_path_factory.mktemp("api")
    settings = make_settings(tmp, seed_corpus_dir=CORPUS)
    app = create_app(settings, AppContainer(settings))
    with TestClient(app) as c:
        assert c.portal is not None
        c.portal.call(app.state.container.seed)
        yield c


def parse_sse(text: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    events = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if not ln.startswith(":")]
        name = next((ln[7:] for ln in lines if ln.startswith("event: ")), None)
        data = next((ln[6:] for ln in lines if ln.startswith("data: ")), None)
        if name and data:
            events.append((name, json.loads(data)))
    return events


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz").json()
    assert ready["corpus"]["documents"] == 42
    assert "truthguard_queries_total" in client.get("/metrics").text


def test_query_stream(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "What is the late fee on the Platinum card?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "plan.created" and names[-1] == "answer.final"
    final = events[-1][1]["payload"]
    assert final["status"] == "RESOLVED_CONFLICT"
    trace = client.get(f"/v1/traces/{final['trace_id']}").json()
    assert len(trace["events"]) == len(events)
    assert all("latency_ms" in e for e in trace["events"])


def test_baseline_mode(client: TestClient) -> None:
    r = client.post(
        "/v1/query/sync", json={"question": "What is the late fee on the Platinum card?", "mode": "B0"}
    )
    body = r.json()
    assert body["mode"] == "B0" and body["status"] == "VERIFIED"
    assert body["value_flags"][0]["verdict"] in ("stale", "winner", "blended")


def test_documents_and_lineage(client: TestClient) -> None:
    docs = client.get("/v1/documents").json()
    assert len(docs) == 42
    d = client.get("/v1/documents/card_fees_policy_v2").json()
    assert "4.2 Late Payment Fee" in d["raw_text"] and d["status"] == "active"  # only clause 4.2 is amended
    assert client.get("/v1/documents/card_fees_policy_v1").json()["status"] == "superseded"
    lin = client.get("/v1/documents/card_fees_policy_v2/lineage").json()
    assert {x["id"] for x in lin["documents"]} >= {
        "card_fees_policy_v1",
        "card_fees_policy_v2",
        "card_fees_amendment_2025_03",
    }
    assert any(s["series_id"] == "fd_rates_circular" for s in client.get("/v1/lineage").json())


def test_conflicts_and_report(client: TestClient) -> None:
    health = client.get("/v1/conflicts").json()
    assert health["totals"]["conflicts"] >= 15
    assert health["heatmap"]["cells"]
    assert any(s["action"] == "retire_or_update" for s in health["suggestions"])
    only_kyc = client.get("/v1/conflicts", params={"topic": "kyc"}).json()
    assert all(r["topic"] == "kyc" for r in only_kyc["conflicts"])
    md = client.get("/v1/conflicts/report.md")
    assert md.text.startswith("# TruthGuard Corpus Health Report")


def test_time_travel_and_policy_edit(client: TestClient) -> None:
    final = client.post(
        "/v1/query/sync", json={"question": "What is the window for raising a card dispute?"}
    ).json()
    assert final["status"] == "UNRESOLVED_CONFLICT"
    policy = client.get("/v1/policy/precedence").json()["policy"]
    policy["order"] = ["R0", "R1", "R4", "R2", "R3", "R5"]
    policy["rules"]["R4"]["cross_series"] = True
    flipped = client.post(f"/v1/traces/{final['trace_id']}/re-resolve", json={"policy": policy}).json()
    assert flipped["status"] == "RESOLVED_CONFLICT"
    kyc = client.post(
        "/v1/query/sync", json={"question": "How often must high-risk customers update their KYC?"}
    ).json()
    later = client.post(f"/v1/traces/{kyc['trace_id']}/re-resolve", json={"as_of": "2027-02-01"}).json()
    assert any(c["value_norm"] == "YEARS:1" and c["verdict"] == "winner" for c in later["claims"])
    bad = dict(policy, order=["R5", "R0"])
    assert client.put("/v1/policy/precedence", json=bad).status_code == 422


def test_challenge_mode_quick_document_and_reset(client: TestClient) -> None:
    r = client.post(
        "/v1/documents/quick",
        json={
            "title": "Judge memo",
            "doc_type": "memo",
            "effective_from": "2026-10-23",
            "text": "Effective today, the late payment fee on the Platinum Credit Card is ₹1,200.",
        },
    )
    body = r.json()
    assert r.status_code == 200, body
    assert body["assertions"] == 1 and set(body["timings_ms"]) >= {"parse", "assertions"}
    final = client.post("/v1/query/sync", json={"question": "What is the Platinum card late fee?"}).json()
    assert final["status"] == "UNRESOLVED_CONFLICT"
    assert client.post("/v1/admin/reset").json()["removed_documents"] == 1
    final = client.post("/v1/query/sync", json={"question": "What is the Platinum card late fee?"}).json()
    assert final["status"] == "RESOLVED_CONFLICT"


def test_upload_job_and_validation(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/v1/documents", files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 415
    content = b"# Branch note\n\nThe annual fee for the Gold Credit Card is \xe2\x82\xb92,000.\n"
    r = client.post("/v1/documents", files=[("files", ("gold_note.md", content, "text/markdown"))])
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert client.get("/v1/jobs/nope").status_code == 404
    client.post("/v1/admin/reset")


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, api_key="secret")
    with TestClient(create_app(settings, AppContainer(settings))) as c:
        assert c.get("/v1/documents").status_code == 401
        assert c.get("/v1/documents", headers={"X-API-Key": "secret"}).status_code == 200
        assert c.get("/healthz").status_code == 200
