"""Versioned prompt templates. Loaded as text — never inline a prompt in Python.

Each `.md` file in this directory is a system or user prompt. Helpers below
load and (optionally) format them.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Read a prompt file by stem (e.g. 'exception_classification' → exception_classification.md)."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text()


def render_prompt(name: str, **substitutions: object) -> str:
    """Read a prompt file and substitute $-style placeholders. Use `safe_substitute`
    so any literal $ in the template that isn't a placeholder doesn't error.
    """
    return Template(load_prompt(name)).safe_substitute(**substitutions)
