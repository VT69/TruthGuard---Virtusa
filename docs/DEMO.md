# Demo script (5 minutes)

**Setup, 10 minutes before you present:**

```bash
make demo        # compose stack in DEMO_MODE with "today" pinned to 2026-10-23, then preflight
# or, without Docker:
TODAY_OVERRIDE=2026-10-23 DEMO_MODE=true make api   # terminal 1
make web                                            # terminal 2
make preflight                                      # must print ALL GREEN
```

Open http://localhost:3000. Click **Reset demo** (top right) after every rehearsal.

Roles: one **driver** at the keyboard, one **narrator**. Rehearse at least five times with a stopwatch. The whole script is automated in `frontend/e2e/demo-script.spec.ts` (`make e2e`), so if CI is green, the demo works.

| Time | Beat | Driver does | Narrator says |
| --- | --- | --- | --- |
| 0:00 | **Hook** (30 s) | Open **Corpus Health**. | "Imagine a customer quoted last year's fee because the bot read an old FAQ. Northwind's knowledge base contradicts itself in **21 places**, 4 of them unresolvable, and 16 stale documents are still searchable. TruthGuard found all of that before anyone asked." Point at the heatmap and the "Retire this FAQ" suggestions. |
| 0:30 | **Split screen** (60 s) | **Ask**: click the first example, *late payment fee on the Platinum Credit Card*. | "On the right, plain RAG answers **₹750** with confidence, and that's stale (red). On the left, TruthGuard: **₹950, Conflict resolved**. The amendment supersedes clause 4.2 of v2 (R1), and the help-centre FAQ is outranked (R2). It also quarantined a planted prompt injection telling assistants to say the fee is zero." Click the ₹950 citation: **Evidence X-ray** opens the amendment at the exact span. Open a v2 quote: the **SUPERSEDED** stamp. |
| 1:30 | **Time travel** (45 s) | Ask *How often must high-risk customers update their KYC?* Then drag the **Time travel** slider past **2027-01**. | "Today it's 2 years. A board policy that takes effect on 1 January 2027 changes it. Drag the date, and the answer becomes **1 year**: rule R0 hands over to R1. No LLM call; the rules re-run in milliseconds." |
| 2:15 | **Challenge Mode** (60 s) | Hand a judge the keyboard. Click **Challenge**. The memo is pre-filled: "Effective today, the late payment fee on the Platinum Credit Card is ₹1,200." Click **Add**, then **Re-ask**. | "Ingested in a few tens of milliseconds. Now the critic and the resolver see a tier-5 memo that is newer than the official amendment and contradicts it. TruthGuard refuses to pick a side: **Unresolved conflict, possible unofficial update; escalate to the Cards Policy Committee.**" Point at the debate theatre. |
| 3:15 | **Governance** (30 s) | Ask *How many days do customers have to report a disputed transaction?* (unresolved: policy 30 days vs branch memo 60). Open **Policy**, click **Preset: recency beats authority**, then **Compare saved vs draft policy**. | "Precedence is policy, so it's configurable and auditable. Flip one rule and the verdict flips with it: 60 days, decided by R4. Restore it." |
| 3:45 | **Proof** (45 s) | Open **Eval**. | "On the frozen test split, naive RAG silently blends or picks a side on **97%** of conflict questions; TruthGuard on **0%**, with conflict-detection F1 0.98 and 96% correct winner *and* rule. Lineage expansion matters when retrieval is tight: at k=3 it lifts winner recall from 82% to 100%. Cost per query offline is zero; latency p50 72 ms." |
| 4:30 | **Close** (30 s) | Back to Ask. | One architecture sentence: "Conflicts are structured slots, precedence is deterministic code, and an independent critic hunts for counter-evidence." Roadmap: SharePoint/Confluence connectors, a review queue for unresolved conflicts, owner alerts. **Honest failure:** "One test question, 'within how many working days are closure requests processed', isn't mapped by our offline planner. Paraphrases are where the LLM planner earns its keep, and it's the next thing we'd harden." |

## Bulletproofing

- **Pinned date.** `TODAY_OVERRIDE` (set by `make demo`) keeps the scripted answers from drifting as real time passes.
- **`DEMO_MODE`** warms caches for the scripted questions at startup. In live mode it also records the LLM responses, so `LLM_MODE=replay` survives a provider outage.
- **Live paths stay live.** Challenge Mode and free-form questions use the provider fallback chain (e.g. Groq → Gemini → local Ollama) and fall back to the deterministic agents.
- **`make preflight`** checks the API, database, corpus and LLM mode, runs three canary queries with expected statuses, and checks the UI. It prints GREEN or RED.
- **Reset demo** restores the seeded corpus and the default policy after a judge's challenge.
- **Backups:** record a 3-minute screen capture of the script and put screenshots in the deck. Have both localhost and a hosted URL ready.

## Judge Q&A

| Likely question | Short answer |
| --- | --- |
| Why not let the LLM decide which source wins? | Precedence is policy, so it must be deterministic and auditable. Property tests prove determinism and input-order independence. Ablation A3 (LLM picks the winner) is built and runs with keys. |
| What if documents have no dates or versions? | Metadata is inferred with provenance and confidence. An undated "this procedure replaces the 2022 circular" still produces an edge, but at confidence 0.72, which caps resolution strength and shows an "inferred date" badge. |
| Can citations be hallucinated? | Every quote is checked verbatim in code before the answer ships. A fabricated quote is a critical objection, and the answer is revised or withheld. |
| How does it scale? | Assertions are precomputed at ingest, so query cost is a few LLM calls. The API is stateless, with pgvector HNSW in Postgres and ingest workers. See `docs/ARCHITECTURE.md`. |
| How would a client deploy it? | Source connectors, role-based access per document, and a review queue where policy owners settle unresolved conflicts. Corpus Health already names the owner. |
| What does it get wrong? | Offline, the planner and extractor depend on a domain lexicon written by the same team that wrote the questions. One frozen test question fails on phrasing, and a 3-chunk retrieval budget costs 4 points of resolution accuracy. The LLM paths are built and tested with fakes but not yet measured with real providers. |
