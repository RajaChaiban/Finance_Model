"""Prompt loader — single source of truth for agent + RAG prompts.

Each prompt lives as a Markdown file under the top-level ``prompts/`` folder.
This module reads those files at import time, strips the YAML frontmatter,
and returns the prompt body. Editors can tweak prompts in the .md files
without touching Python source.

Files have the shape::

    ---
    agent: IntakeAgent
    role: system
    ...
    ---

    <prompt body, possibly with {placeholder} tokens for str.format(...)>

The body is returned verbatim (whitespace-trimmed). Placeholders are NOT
substituted here — that's the caller's job (e.g. ``str.format(**kwargs)``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# src/agents/prompts.py → repo_root/prompts
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=64)
def load_prompt(relative_path: str) -> str:
    """Load ``prompts/<relative_path>``, strip YAML frontmatter, return body.

    Cached for the lifetime of the process — first call hits disk, subsequent
    calls return the cached string. In tests that rewrite prompt files, call
    ``load_prompt.cache_clear()`` to force a re-read.

    Raises FileNotFoundError if the prompt is missing — fail-loud is correct
    here, since a missing prompt would silently degrade an agent.
    """
    full_path = _PROMPTS_DIR / relative_path
    text = full_path.read_text(encoding="utf-8")

    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + len("\n---\n") :]

    return text.strip()
