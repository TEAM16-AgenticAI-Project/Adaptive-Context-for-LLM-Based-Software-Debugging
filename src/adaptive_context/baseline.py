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
        stop_reason = ""
        run_started = time.perf_counter()
        try:
            reply = self.llm.complete(SYSTEM_BASE, user)
            stats.llm_calls = 1
            stats.input_tokens, stats.output_tokens = reply.input_tokens, reply.output_tokens
            stats.latency_s = reply.latency_s
            obj = parse_reply(reply.text)
            if obj and obj["action"] == "diagnose":
                candidate = to_diagnosis(obj)
                # A diagnosis without a file (and function) is not a real diagnosis:
                # count it as a failure instead of letting an empty answer pass as valid.
                if candidate.file.strip() and candidate.function.strip():
                    diag = candidate
                    stop_reason = "diagnosed"
                else:
                    diag = Diagnosis(file=candidate.file, function=candidate.function,
                                     cause=candidate.cause, fix=candidate.fix,
                                     confidence=candidate.confidence, valid=False)
                    stats.failures += 1
                    stop_reason = "invalid_exhausted"
            else:
                stats.failures += 1
                stop_reason = "invalid_exhausted"
        except LLMError:
            stats.failures += 1
            stop_reason = "llm_error"
        stats.wall_s = time.perf_counter() - run_started
        return DebugResult(diag, stats, [(p.chunk.id, 0) for p in items],
                           [{"round": 1, "context_tokens": stats.input_tokens, "chunks": len(items)}],
                           stop_reason=stop_reason)
