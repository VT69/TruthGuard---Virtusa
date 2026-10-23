"""Versioned prompt templates loaded from `truthguard/prompts/*.md` (never inline strings).

File format: `<name>.v<N>.md` containing `## system` and `## user` sections. Variables
are `{{name}}`; evidence is always passed inside delimited blocks so the model treats
documents as data, not instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    system: str
    user: str

    def render(self, **variables: Any) -> RenderedPrompt:
        def sub(template: str) -> str:
            def repl(m: re.Match[str]) -> str:
                key = m.group(1)
                if key not in variables:
                    raise KeyError(f"prompt {self.name}.v{self.version} missing variable {key!r}")
                value = variables[key]
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

            return _VAR.sub(repl, template)

        return RenderedPrompt(
            name=f"{self.name}.v{self.version}", system=sub(self.system), user=sub(self.user)
        )


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    system: str
    user: str

    def messages(self, json_schema: dict[str, Any] | None = None) -> list[dict[str, str]]:
        system = self.system
        if json_schema is not None:
            system += (
                "\n\nRespond with a single JSON object that validates against this JSON Schema. "
                "No prose, no code fences.\n" + json.dumps(json_schema, ensure_ascii=False)
            )
        return [{"role": "system", "content": system}, {"role": "user", "content": self.user}]


@lru_cache(maxsize=64)
def load_prompt(name: str, version: int | None = None) -> PromptTemplate:
    candidates = sorted(PROMPT_DIR.glob(f"{name}.v*.md"))
    if not candidates:
        raise FileNotFoundError(f"no prompt named {name!r} in {PROMPT_DIR}")
    by_version = {int(p.stem.rsplit(".v", 1)[1]): p for p in candidates}
    chosen = version if version is not None else max(by_version)
    text = by_version[chosen].read_text(encoding="utf-8")
    parts = re.split(r"^## (system|user)\s*$", text, flags=re.MULTILINE)
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i]] = parts[i + 1].strip()
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"prompt {name}.v{chosen} needs '## system' and '## user' sections")
    return PromptTemplate(name=name, version=chosen, system=sections["system"], user=sections["user"])


def evidence_block(doc_id: str, chunk_id: str, header: str, text: str) -> str:
    """Wrap untrusted document text in an unambiguous delimiter."""
    safe = text.replace("<<<", "‹‹‹").replace(">>>", "›››")
    return f'<<<EVIDENCE doc="{doc_id}" chunk="{chunk_id}" {header}>>>\n{safe}\n<<<END EVIDENCE>>>'
