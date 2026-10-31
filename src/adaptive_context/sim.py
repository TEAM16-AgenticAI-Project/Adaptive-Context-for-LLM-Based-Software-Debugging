"""SimulatedLLM: a deterministic, offline stand-in for an LLM, used to benchmark *context management*.

It behaves like a careful engineer reading the prompt (it never sees ground truth):
  * finds the failing entry function named in the bug report,
  * follows calls down the chain, reading each function's docstring contract vs its `return` line,
  * if a function it needs is missing it asks for it (only if ALLOWED_ACTIONS allows) or else guesses (low confidence),
  * if a function is only shown as a signature it cannot verify it and asks to see it in full.
`miss_rate` makes it overlook a real mismatch with that probability (deterministic per prompt).
Numbers from this simulator measure retrieval/compression effects, NOT real model quality.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import random
import re

from .llm import LLMReply
from .tokens import count_tokens

_HDR = re.compile(r"^### (?P<path>\S+?)::(?P<name>\S+) \(L(?P<s>\d+)-(?P<e>\d+)\) \[(?P<level>\w+)\]$", re.M)
_ENTRY = re.compile(r"`(\w+)\(")
_DOC = re.compile(r"\b(Add|Subtract|Multiply)\b[^\n\"']*")
_RET = re.compile(r"return\s+(\w+)\s*([+\-*])\s*(\d+)")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\(")
_BUILTIN = set(dir(builtins))
_OP = {"Add": "+", "Subtract": "-", "Multiply": "*"}


def _parse_context(user: str) -> dict:
    ctx = user.split("CONTEXT:", 1)[1] if "CONTEXT:" in user else user
    ms = list(_HDR.finditer(ctx))
    blocks = {}
    for i, m in enumerate(ms):
        body = ctx[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(ctx)]
        code = body.split("```python\n", 1)[-1].rsplit("```", 1)[0]
        blocks[m["name"].split(".")[-1]] = (m["path"], code)
    return blocks


def _check(code: str):
    """True = docstring contract violated, False = consistent, None = cannot verify."""
    doc, ret = _DOC.search(code), _RET.search(code)
    if not doc or not ret:
        return None
    nums = re.findall(r"\d+", doc.group(0))
    if not nums:
        return None
    return not (_OP[doc.group(1)] == ret.group(2) and nums[0] == ret.group(3))


def _first_callee(code: str, own: str):
    body = code.split("\n", 1)[1] if "\n" in code else ""
    for name in _CALL.findall(body):
        if name != own and name not in _BUILTIN:
            return name
    return None


class SimulatedLLM:
    def __init__(self, miss_rate: float = 0.0):
        self.miss_rate = miss_rate

    def complete(self, system: str, user: str) -> LLMReply:
        m = re.search(r"ALLOWED_ACTIONS: (.*)", user)
        can_request = bool(m and "request" in m.group(1))
        report = user.split("CONTEXT:", 1)[0]
        entry = (_ENTRY.search(report) or [None, None])[1]
        blocks = _parse_context(user)
        rng = random.Random(int(hashlib.md5(user.encode()).hexdigest()[:8], 16))

        chain, cur, seen = [], entry, set()
        culprit, need, unverifiable = None, None, []
        while cur and cur not in seen:
            seen.add(cur)
            if cur not in blocks:
                need = cur
                break
            path, code = blocks[cur]
            verdict = _check(code)
            if verdict is None:
                if can_request:
                    need = cur
                    break
                unverifiable.append(cur)
            elif verdict:
                culprit = (cur, path, code)
                break
            chain.append((cur, path))
            cur = _first_callee(code, cur)

        if culprit and rng.random() < self.miss_rate:      # overlooked the mismatch
            culprit, need = None, None
        if culprit:
            name, path, code = culprit
            doc, ret = _DOC.search(code), _RET.search(code)
            fix = f"return {ret.group(1)} {_OP[doc.group(1)]} {re.findall(r'[0-9]+', doc.group(0))[0]}"
            out = {"action": "diagnose", "file": path, "function": name, "confidence": "high", "fix": fix,
                   "cause": f"{name} docstring says '{doc.group(0).strip()}' but code does '{ret.group(0)}'"}
        elif need and can_request:
            out = {"action": "request", "symbols": [need], "queries": []}
        else:
            guess = (chain[-1] if chain else (entry or "", ""))
            out = {"action": "diagnose", "file": guess[1], "function": guess[0], "confidence": "low", "fix": "",
                   "cause": "Could not verify the full call chain; best guess from partial evidence."}
        text = json.dumps(out)
        tin, tout = count_tokens(system + user), count_tokens(text)
        return LLMReply(text, tin, tout, 0.4 + tin / 6000 + tout / 200)   # modelled latency (seconds)
