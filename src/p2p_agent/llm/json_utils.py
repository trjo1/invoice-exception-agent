"""Shared JSON extraction helper for LLM responses.

Chat APIs often wrap structured output in ```json blocks; sometimes there's
surrounding prose. This module gives the single place that does the parse.
Imported by both the corpus ingester (which parses chat-pasted output) and
the classifier (which parses live API output).
"""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.+?)\n\s*```", re.DOTALL)


def _scan_balanced(text: str, start: int, open_ch: str, close_ch: str) -> str | None:
    """Return the balanced substring starting at `text[start]` (which must be `open_ch`).

    Walks forward counting braces, skipping over string literals. Returns the
    whole `{...}` or `[...]` substring including delimiters, or None if no balanced
    close is found.
    """
    depth = 0
    i = start
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def extract_json_from_response(text: str) -> Any:
    """Pull the first JSON value out of `text`.

    Tolerant: tries a fenced ```json block first, then bare text starting with
    `{`/`[`, then falls back to scanning the text for the first balanced `{...}`
    or `[...]` (lets the model precede the JSON with prose). Raises `ValueError`
    only when no JSON-like structure can be located at all.
    """
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        return json.loads(fence_match.group(1))
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        return json.loads(stripped)

    # Fallback: scan for the first balanced JSON object or array anywhere in the
    # text. Handles "Here is the classification: {...}" without forcing a retry.
    for i, ch in enumerate(text):
        if ch == "{":
            candidate = _scan_balanced(text, i, "{", "}")
        elif ch == "[":
            candidate = _scan_balanced(text, i, "[", "]")
        else:
            continue
        if candidate is not None:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue  # this brace wasn't real JSON; try the next one
    raise ValueError("No JSON found in response text")
