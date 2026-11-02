"""Prompts and reply parsing shared by every debugger."""
from __future__ import annotations

import json
import re

from .compress import LEVELS
from .models import Diagnosis

SYSTEM_BASE = """You are a senior engineer debugging a Python repository. You receive a bug report and code excerpts.
Find the root cause: the single function that contains the defect. Reply with ONE JSON object and nothing else.
Diagnose format:
{"action":"diagnose","file":"<path>","function":"<name>","cause":"<one sentence citing evidence from the excerpts>","fix":"<corrected single line of code, or empty>","confidence":"high|low"}
Only cite code that appears in the excerpts. If evidence is missing, set confidence to low."""

SYSTEM_ADAPTIVE = SYSTEM_BASE + """
If the excerpts lack the evidence and ALLOWED_ACTIONS includes request, ask for more context instead of guessing:
{"action":"request","symbols":["<function names to fetch>"],"queries":["<short search text>"]}
Excerpts may be trimmed (see the bracket tag on each header). Request a symbol to see its full body.
Prefer symbols over queries when you know the name."""


def render_context(items: list) -> str:
    return "\n".join(
        f"### {p.chunk.path}::{p.chunk.name} (L{p.chunk.start}-{p.chunk.end}) [{LEVELS[p.level]}]\n```python\n{p.text}\n```"
        for p in items)


def build_user_prompt(bug_report: str, context: str, allowed: str, round_no: int, max_rounds: int, note: str = "") -> str:
    return (f"BUG REPORT:\n{bug_report}\n\nROUND: {round_no}/{max_rounds}\nALLOWED_ACTIONS: {allowed}\n"
            f"{note}\nCONTEXT:\n{context}\n")


def parse_reply(text: str):
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("action") in ("diagnose", "request"):
            return obj
    return None


def to_diagnosis(obj: dict) -> Diagnosis:
    return Diagnosis(file=str(obj.get("file", "")), function=str(obj.get("function", "")).split(".")[-1],
                     cause=str(obj.get("cause", "")), fix=str(obj.get("fix", "")),
                     confidence=str(obj.get("confidence", "")), valid=True)
