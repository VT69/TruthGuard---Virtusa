"""`make preflight`: run 10 minutes before presenting. Prints GREEN or RED per check.

Checks the API, database, corpus, configured LLM mode and the web UI, then runs three
canary queries whose expected statuses are known from the demo script.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
WEB = os.environ.get("WEB_URL", "http://localhost:3000").rstrip("/")
HEADERS = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}

CANARIES = [
    ("What is the late payment fee on the Platinum Credit Card?", "RESOLVED_CONFLICT", "₹950"),
    ("How many days do customers have to report a disputed transaction?", "UNRESOLVED_CONFLICT", "60 days"),
    ("What is the annual fee for the Titanium Credit Card?", "INSUFFICIENT_EVIDENCE", ""),
]

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
failures = 0


def report(ok: bool, name: str, detail: str = "") -> None:
    global failures  # noqa: PLW0603
    failures += 0 if ok else 1
    tag = f"{GREEN}GREEN{RESET}" if ok else f"{RED} RED {RESET}"
    print(f"[{tag}] {name}{' — ' + detail if detail else ''}")


def main() -> int:
    with httpx.Client(timeout=30, headers=HEADERS) as client:
        try:
            ready = client.get(f"{API}/readyz").json()
            report(ready.get("status") == "ready", "API + database ready", str(ready.get("corpus")))
            corpus = ready.get("corpus", {})
            report(corpus.get("documents", 0) >= 40, "corpus seeded", f"{corpus.get('documents')} documents")
            meta = client.get(f"{API}/v1/meta").json()
            report(True, "LLM mode", f"{meta['llm_mode']} (models: {meta['models']})")
            report(meta["today"] is not None, "as-of date", meta["today"])
        except Exception as exc:
            report(False, "API reachable", str(exc))
            return 1
        for question, status, needle in CANARIES:
            t0 = time.perf_counter()
            try:
                body = client.post(f"{API}/v1/query/sync", json={"question": question}).json()
                ms = (time.perf_counter() - t0) * 1000
                ok = body["status"] == status and needle in body["answer_text"] and ms < 8000
                report(ok, f"canary: {question[:48]}…", f"{body['status']} in {ms:.0f} ms")
            except Exception as exc:
                report(False, f"canary: {question[:48]}…", str(exc))
        try:
            web = client.get(WEB)
            report(web.status_code == 200, "web UI", WEB)
        except Exception as exc:
            report(False, "web UI", str(exc))
    print(
        f"\n{'ALL GREEN — go present.' if not failures else f'{failures} check(s) RED — fix before presenting.'}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
