"""Static RAG baseline: retrieve once with a fixed Top-K, one LLM call, no compression."""
from __future__ import annotations

import time

from .budget import Packed
from .llm import LLMError
from .models import DebugResult, Diagnosis, RunStats
from .protocol import SYSTEM_BASE, build_user_prompt, parse_reply, render_context, to_diagnosis


class StaticRAG:
    def __init__(self, retriever, llm, top_k: int = 3):
        self.retriever, self.llm, self.top_k = retriever, llm, top_k

    def run(self, bug_report: str) -> DebugResult:
        stats = RunStats(retrieval_calls=1, rounds=1)
        hits = self.retriever.search(bug_report, self.top_k)
        items = [Packed(c, s, 0, c.text) for c, s in hits]
        user = build_user_prompt(bug_report, render_context(items), "diagnose", 1, 1)
        diag = Diagnosis()
        t0 = time.time()
        try:
            reply = self.llm.complete(SYSTEM_BASE, user)
            stats.llm_calls = 1
            stats.input_tokens, stats.output_tokens = reply.input_tokens, reply.output_tokens
            stats.latency_s = reply.latency_s
            obj = parse_reply(reply.text)
            if obj and obj["action"] == "diagnose":
                diag = to_diagnosis(obj)
            else:
                stats.failures += 1
        except LLMError:
            stats.failures += 1
            stats.latency_s = time.time() - t0
        return DebugResult(diag, stats, [(p.chunk.id, 0) for p in items],
                           [{"round": 1, "context_tokens": stats.input_tokens, "chunks": len(items)}])
