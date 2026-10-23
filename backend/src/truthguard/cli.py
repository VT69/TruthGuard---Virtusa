"""Command line: `truthguard seed`, `truthguard ask "..."`, `truthguard audit`."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from truthguard.core.config import get_settings
from truthguard.core.logging import configure_logging
from truthguard.services.container import AppContainer
from truthguard.storage.repositories import CorpusRepository


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    c = AppContainer(settings)
    await c.startup()
    try:
        if args.cmd == "seed":
            results = await c.seed(Path(args.dir) if args.dir else None)
            new = sum(1 for r in results if not r.skipped)
            print(
                f"ingested {new} new document(s), {len(results) - new} unchanged; corpus: {c.corpus.stats()}"
            )
        elif args.cmd == "ask":
            out = await c.ask(
                args.question, date.fromisoformat(args.as_of) if args.as_of else None, mode=args.mode
            )
            final = out.final or out.baseline
            assert final is not None
            if args.json:
                print(json.dumps(final.model_dump(mode="json"), indent=2, ensure_ascii=False))
            else:
                print(f"[{final.status.value}] {final.answer_text}")
        elif args.cmd == "audit":
            async with c.db.session() as s:
                rows = await CorpusRepository(s).conflicts()
            for conflict, res, severity in sorted(rows, key=lambda r: r[2]):
                rule = res.deciding_rule.value if res.deciding_rule else "-"
                print(
                    f"{severity:8s} {res.status.value:12s} {rule:3s} {conflict.slot_key}  {conflict.distinct_values}"
                )
    finally:
        await c.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(prog="truthguard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    seed = sub.add_parser("seed", help="ingest the sample corpus (idempotent)")
    seed.add_argument("--dir", default=None)
    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("question")
    ask.add_argument("--as-of", default=None)
    ask.add_argument("--mode", default="truthguard", choices=["truthguard", "B0", "B1", "B2"])
    ask.add_argument("--json", action="store_true")
    sub.add_parser("audit", help="print the corpus conflict audit")
    args = parser.parse_args()
    configure_logging("WARNING")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
