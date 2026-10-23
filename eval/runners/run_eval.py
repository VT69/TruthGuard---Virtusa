"""Run TruthGuard, baselines B0-B2 and ablations A1-A4 over the gold set and write a report.

    python -m eval.runners.run_eval --split test            # frozen test split (final numbers)
    python -m eval.runners.run_eval --split dev             # tuning split
    LLM_MODE=live python -m eval.runners.run_eval ...       # real providers (needs API keys)

Outputs eval/reports/results.{md,json} and charts. Offline mode uses deterministic agents and
deterministic baseline PROXIES; the report says so on its first line. A3 (LLM resolver) and
A4 (same-model critic) only differ from TruthGuard when an LLM is configured, so they are
reported as skipped offline rather than invented.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from eval.runners.scoring import (
    QuestionScore,
    aggregate,
    as_dict,
    normalize_baseline,
    normalize_truthguard,
    score_question,
)
from truthguard.core.config import REPO_ROOT, Settings
from truthguard.core.logging import configure_logging
from truthguard.domain.enums import ResolutionStatus, RuleId
from truthguard.domain.models import EvidenceSet, Resolution, SlotAnalysis
from truthguard.llm.prompts import load_prompt
from truthguard.orchestration.graph import GraphOptions
from truthguard.services.container import AppContainer

DATASETS = REPO_ROOT / "eval" / "datasets"
REPORTS = REPO_ROOT / "eval" / "reports"
CORPUS = REPO_ROOT / "data" / "sample_corpus"

RUN_DESCRIPTIONS = {
    "B0": "Naive RAG: dense top-5, one answer, no conflict handling",
    "B1": "RAG + recency: newest documents first",
    "B2": "Self-critique: one agent drafts and critiques itself",
    "TG": "TruthGuard full pipeline",
    "A1": "Ablation: no lineage expansion",
    "A2": "Ablation: no counter-retrieval in the critic",
    "A3": "Ablation: an LLM picks the winner instead of the rules",
    "A4": "Ablation: critic uses the synthesizer's model",
    "TG@k3": "Stress: TruthGuard with a 3-chunk retrieval budget",
    "A1@k3": "Stress: no lineage expansion, 3-chunk budget",
    "A2@k3": "Stress: no counter-retrieval, 3-chunk budget",
}


class _LLMPick(BaseModel):
    winner_document_id: str | None = None
    reason: str = ""


def load_questions(split: str) -> list[dict[str, Any]]:
    name = "gold" if split == "all" else split
    return [
        json.loads(line)
        for line in (DATASETS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def llm_resolver(container: AppContainer):  # type: ignore[no-untyped-def]
    async def resolve(an: SlotAnalysis, evidence: EvidenceSet, as_of: date) -> Resolution:
        by_id = evidence.assertion_by_id()
        docs = evidence.document_by_id()
        items = [by_id[i] for i in an.row.assertion_ids if i in by_id]
        lines = "\n".join(
            f"- document_id={a.document_id} title={docs[a.document_id].title!r} type={docs[a.document_id].doc_type} "
            f"date={docs[a.document_id].sort_date} value={a.value_display} quote={a.quote!r}"
            for a in items
        )
        prompt = load_prompt("resolver_llm").render(as_of=as_of.isoformat(), positions=lines)
        pick = await container.gateway.structured(prompt, _LLMPick, container.settings.analyst_model)
        winners = [a for a in items if a.document_id == pick.winner_document_id]
        if not winners:
            return Resolution(
                conflict_id=an.key,
                slot_key=an.row.slot_key,
                status=ResolutionStatus.UNRESOLVED,
                rationale=pick.reason,
                as_of=as_of,
            )
        win_value = winners[0].value_norm
        return Resolution(
            conflict_id=an.key,
            slot_key=an.row.slot_key,
            status=ResolutionStatus.RESOLVED,
            winner_assertion_ids=[winners[0].id],
            winners_by_scope={winners[0].scope: winners[0].id},
            eliminated_by={a.id: RuleId.R5 for a in items if a.value_norm != win_value},
            rationale=f"LLM choice: {pick.reason}",
            as_of=as_of,
            strength=0.5,
        )

    return resolve


async def run_all(split: str, runs: list[str]) -> dict[str, Any]:
    questions = load_questions(split)
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{tmp}/eval.db",
            today_override=manifest["eval_today"],
            llm_cache_dir=REPO_ROOT / "eval" / ".llm_cache",
            env="test",
        )
        c = AppContainer(settings)
        await c.startup()
        t0 = time.perf_counter()
        await c.seed(CORPUS)
        ingest_s = time.perf_counter() - t0
        file_to_key = {d["filename"]: d["key"] for d in manifest["documents"]}
        doc_key = {d.id: file_to_key.get(d.source_uri, d.id) for d in c.corpus.documents.values()}
        live = c.gateway.live
        results: dict[str, Any] = {}
        per_question: dict[str, list[dict[str, Any]]] = {}
        retrieval: dict[str, dict[str, float]] = {}
        for run in runs:
            if run in ("A3", "A4") and not live:
                results[run] = {"skipped": "requires LLM_MODE=live: offline agents have no LLM to swap"}
                continue
            scores: list[QuestionScore] = []
            cites = [0, 0]
            hit, total = 0, 0
            for q in questions:
                as_of = date.fromisoformat(q["as_of"])
                started = time.perf_counter()
                if run in ("B0", "B1", "B2"):
                    out = await c.ask(q["question"], as_of, mode=run, persist=False)
                    assert out.baseline is not None
                    norm = normalize_baseline(
                        out.baseline.model_dump(mode="json"),
                        doc_key,
                        (time.perf_counter() - started) * 1000,
                    )
                else:
                    base, _, budget = run.partition("@k")
                    opts = GraphOptions(
                        lineage_expansion=base != "A1",
                        counter_retrieval=base != "A2",
                        top_k=int(budget) if budget else None,
                        max_rounds=settings.max_revision_rounds,
                        resolve_override=llm_resolver(c) if base == "A3" else None,
                    )
                    if run == "A4":
                        c.critic.judge_model = settings.synth_model
                    out = await c.ask(q["question"], as_of, options=opts, persist=False)
                    if run == "A4":
                        c.critic.judge_model = settings.critic_model
                    assert out.final is not None and out.state is not None
                    norm = normalize_truthguard(out.final.model_dump(mode="json"), doc_key)
                    cites[0] += norm.citations_total
                    cites[1] += norm.citations_good
                    if q["expected_winners"]:
                        ev_docs = {doc_key.get(d, d) for d in out.state.evidence.document_by_id()}
                        total += 1
                        hit += int(set(q["expected_winners"]) <= ev_docs)
                scores.append(score_question(q, norm))
            agg = aggregate(scores, (cites[0], cites[1]) if run not in ("B0", "B1", "B2") else None)
            results[run] = agg
            per_question[run] = [as_dict(s) for s in scores]
            if total:
                retrieval[run] = {
                    "winner_doc_recall": round(hit / total, 4),
                    "questions": total,
                }
        sweep = await retrieval_sweep(c, questions, doc_key)
        await c.shutdown()

    report = {
        "generated": date.today().isoformat(),
        "split": split,
        "n_questions": len(questions),
        "llm_mode": settings.llm_mode,
        "models": {"synthesizer": settings.synth_model, "critic": settings.critic_model} if live else None,
        "ingest_seconds": round(ingest_s, 2),
        "runs": results,
        "descriptions": RUN_DESCRIPTIONS,
        "retrieval": retrieval,
        "retrieval_sweep": sweep,
        "per_question": per_question,
    }
    return report


def save_report(report: dict[str, Any], questions: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, questions, out_dir)
    write_charts(report, out_dir)


async def retrieval_sweep(
    c: AppContainer, questions: list[dict[str, Any]], doc_key: dict[str, str]
) -> list[dict[str, Any]]:
    """Does the evidence contain every version of the conflict? With vs without lineage expansion."""
    rows = []
    targets = [
        q
        for q in questions
        if len(q["slots"]) == 1 and (q["expected_eliminations"] or q["expected_positions"])
    ]
    for k in (3, 5, 8, 12):
        for expand in (True, False):
            win, full = 0, 0
            for q in targets:
                plan = c.planner.rule_plan(q["question"], date.fromisoformat(q["as_of"]), explicit_as_of=True)
                ev = await c.retriever.retrieve(plan, lineage_expansion=expand, top_k=k)
                docs = {doc_key.get(d, d) for d in ev.document_by_id()}
                need = set(q["expected_winners"]) | set(q["expected_positions"])
                allv = need | set(q["expected_eliminations"])
                win += int(need <= docs)
                full += int(allv <= docs)
            n = max(1, len(targets))
            rows.append(
                {
                    "k": k,
                    "lineage_expansion": expand,
                    "questions": len(targets),
                    "winner_recall": round(win / n, 4),
                    "all_versions_recall": round(full / n, 4),
                }
            )
    return rows


def _fmt(v: Any, pct: bool = True) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and pct:
        return f"{v * 100:.1f}%"
    return str(v)


def write_markdown(report: dict[str, Any], questions: list[dict[str, Any]], out_dir: Path) -> None:
    runs = report["runs"]
    offline = report["llm_mode"] == "offline"
    lines = [
        "# TruthGuard evaluation report",
        "",
        (
            f"> **Mode: {report['llm_mode']}.** Agents run their deterministic paths and baselines B0-B2 are "
            "deterministic *proxies* of naive-RAG behaviour (see `truthguard/agents/baselines.py`). Re-run with "
            "`LLM_MODE=live` and API keys to measure real LLM baselines and ablations A3/A4."
            if offline
            else f"> **Mode: {report['llm_mode']}** with models {report['models']}."
        ),
        "",
        f"Split: **{report['split']}** ({report['n_questions']} questions). Generated {report['generated']}. "
        f"Corpus ingest: {report['ingest_seconds']} s for 42 documents.",
        "",
        "## Headline",
        "",
        "| Run | Silent-blend ↓ | Conflict F1 ↑ | Resolution acc. ↑ | Status acc. ↑ | Value acc. ↑ | Stale cites ↓ |"
        " Citation prec. ↑ | Abstention recall ↑ | p50 / p95 ms | $/query |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run, agg in runs.items():
        if "skipped" in agg:
            lines.append(f"| {run} | skipped: {agg['skipped']} | | | | | | | | | |")
            continue
        lines.append(
            f"| **{run}** | {_fmt(agg['silent_blend_rate'])} | {_fmt(agg['conflict_detection']['f1'], False)} | "
            f"{_fmt(agg['resolution_accuracy'])} | {_fmt(agg['status_accuracy'])} | {_fmt(agg['value_accuracy'])} | "
            f"{_fmt(agg['stale_citation_rate'])} | {_fmt(agg['citation_precision'])} | "
            f"{_fmt(agg['abstention_recall'])} | {agg['latency_ms']['p50']} / {agg['latency_ms']['p95']} | "
            f"{agg['cost_usd_per_query']} |"
        )
    lines += [
        "",
        "Runs: " + "; ".join(f"**{k}** {v}" for k, v in report["descriptions"].items()),
        "",
    ]
    if "TG" in runs and "B0" in runs and "skipped" not in runs["B0"]:
        tg, b0 = (
            runs["TG"]["silent_blend_rate"] or 0.0,
            runs["B0"]["silent_blend_rate"] or 0.0,
        )
        ratio = "∞" if tg == 0 else f"{b0 / tg:.1f}×"
        lines += [
            f"Silent-blend rate: TruthGuard {tg * 100:.1f}% vs naive RAG {b0 * 100:.1f}% ({ratio} lower).",
            "",
        ]
    if report["retrieval"]:
        lines += [
            "## Retrieval: is the winning document in the evidence?",
            "",
            "| Run | Winner-document recall | Questions |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {k} | {_fmt(v['winner_doc_recall'])} | {v['questions']} |"
            for k, v in report["retrieval"].items()
        ]
        lines.append("")
    if report.get("retrieval_sweep"):
        lines += [
            "## Lineage expansion vs retrieval budget",
            "",
            "Share of conflict questions whose evidence contains the winning source / every version of the "
            "conflict, by rerank budget k.",
            "",
            "| k | Expansion | Winner recall | All-versions recall |",
            "| --- | --- | --- | --- |",
        ]
        for r in report["retrieval_sweep"]:
            lines.append(
                f"| {r['k']} | {'on' if r['lineage_expansion'] else 'off'} | {_fmt(r['winner_recall'])} | "
                f"{_fmt(r['all_versions_recall'])} |"
            )
        lines.append("")
    if "TG" in runs and "by_case" in runs["TG"]:
        lines += [
            "## TruthGuard by planted case",
            "",
            "| Case | n | Status acc. | Silent-blend |",
            "| --- | --- | --- | --- |",
        ]
        for case, v in runs["TG"]["by_case"].items():
            lines.append(
                f"| {case} | {v['n']} | {_fmt(v['status_accuracy'])} | {_fmt(v['silent_blend_rate'])} |"
            )
        lines += ["", "## TruthGuard status confusion (expected -> got)", ""]
        lines += [f"- `{k}`: {n}" for k, n in runs["TG"]["confusion"].items()]
        qmap = {q["id"]: q for q in questions}
        wrong = [
            s
            for s in report["per_question"]["TG"]
            if not (
                s["status_correct"]
                and (s["values_correct"] or s["expected_status"] == "INSUFFICIENT_EVIDENCE")
            )
        ]
        lines += ["", f"## Where TruthGuard is wrong ({len(wrong)} questions)", ""]
        if not wrong:
            lines.append("None on this split.")
        for s in wrong:
            q = qmap[s["id"]]
            lines.append(
                f"- `{s['id']}` ({q['case']}) “{q['question']}” expected {s['expected_status']} "
                f"{q['expected_values']}, got {s['status']}."
            )
    (out_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_charts(report: dict[str, Any], out_dir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover
        return
    runs = {k: v for k, v in report["runs"].items() if "skipped" not in v}
    names = list(runs)
    colors = [
        "#2563eb" if n.startswith("TG") else "#9ca3af" if n.startswith("A") else "#dc2626" for n in names
    ]
    panels = [
        ("silent_blend_rate", "Silent-blend rate (%) — lower is better"),
        ("status_accuracy", "Status accuracy (%) — higher is better"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), dpi=150)
    for ax, (key, title) in zip(axes, panels, strict=True):
        vals = [(runs[n][key] or 0) * 100 for n in names]
        bars = ax.bar(names, vals, color=colors)
        ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals], padding=2, fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.set_ylim(0, 115)
        ax.tick_params(axis="x", labelsize=8, rotation=30)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(
        f"TruthGuard vs baselines and ablations ({report['split']} split, {report['llm_mode']} mode)",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out_dir / "silent_blend.png")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--split", default="test", choices=["dev", "test", "all"])
    parser.add_argument("--runs", default="B0,B1,B2,TG,A1,A2,A3,A4,TG@k3,A1@k3,A2@k3")
    parser.add_argument("--out", type=Path, default=REPORTS)
    args = parser.parse_args()
    configure_logging("WARNING")
    report = asyncio.run(run_all(args.split, args.runs.split(",")))
    save_report(report, load_questions(args.split), args.out)
    for run, agg in report["runs"].items():
        if "skipped" in agg:
            print(f"{run:3s} skipped ({agg['skipped']})")
        else:
            print(
                f"{run:3s} silent_blend={agg['silent_blend_rate']} f1={agg['conflict_detection']['f1']} "
                f"resolution={agg['resolution_accuracy']} status={agg['status_accuracy']} "
                f"values={agg['value_accuracy']} p50={agg['latency_ms']['p50']}ms"
            )
    print(f"report: {args.out / 'results.md'}")


if __name__ == "__main__":
    main()
