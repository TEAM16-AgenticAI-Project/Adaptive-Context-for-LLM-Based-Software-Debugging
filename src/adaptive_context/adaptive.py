"""Adaptive debugger: iterative retrieval + compression + sequencing under a token budget.

Per round:
  1. pack the pool of chunks into the token budget (compress/drop lowest priority first)
  2. order the chunks (callers before callees by default)
  3. ask the LLM: diagnose, or request symbols/queries for missing evidence
  4. requested symbols are pinned (top priority, shown in full); optionally their callees are prefetched
The loop stops on a diagnosis, when a request finds nothing new, or at max_rounds (last round forces a diagnosis).
"""
from __future__ import annotations

from .budget import pack
from .llm import LLMError
from .models import DebugResult, Diagnosis, RunStats
from .protocol import SYSTEM_ADAPTIVE, build_user_prompt, parse_reply, render_context, to_diagnosis
from .sequencing import order
from .tokens import count_tokens

PIN = 1e6
PREFETCH = 1e3


class AdaptiveDebugger:
    def __init__(self, retriever, llm, *, initial_k: int = 3, max_rounds: int = 6, token_budget: int = 3000,
                 per_round_k: int = 2, sequencing: str = "dependency", compress: bool = True,
                 prefetch_depth: int = 0):
        self.retriever, self.llm = retriever, llm
        self.initial_k, self.max_rounds, self.token_budget = initial_k, max_rounds, token_budget
        self.per_round_k, self.sequencing, self.compress = per_round_k, sequencing, compress
        self.prefetch_depth = prefetch_depth

    def _prefetch(self, chunk, depth, add):
        frontier = [chunk]
        for hop in range(depth):
            nxt = []
            for c in frontier:
                for callee in self.retriever.callees(c):
                    if add(callee, PREFETCH - hop):
                        nxt.append(callee)
            frontier = nxt

    def run(self, bug_report: str) -> DebugResult:
        stats, trace, pool = RunStats(), [], {}

        def add(chunk, score):
            if chunk.id in pool:
                if score > pool[chunk.id][1]:
                    pool[chunk.id][1] = score
                    return True          # promoted
                return False
            pool[chunk.id] = [chunk, score]
            return True

        hits = self.retriever.search(bug_report, self.initial_k)
        stats.retrieval_calls += 1
        for c, s in hits:
            add(c, s)
        if hits and self.prefetch_depth:
            self._prefetch(hits[0][0], self.prefetch_depth, add)

        note, forced, diag, last_items = "", False, Diagnosis(), []
        for rnd in range(1, self.max_rounds + 1):
            stats.rounds = rnd
            res = pack([(c, s) for c, s in pool.values()], self.token_budget, allow_compress=self.compress)
            ordered = order(res.items, self.retriever, self.sequencing)
            last_items = [(p.chunk.id, p.level) for p in ordered]
            allowed = "diagnose" if (rnd == self.max_rounds or forced) else "request, diagnose"
            user = build_user_prompt(bug_report, render_context(ordered), allowed, rnd, self.max_rounds, note)
            entry = {"round": rnd, "allowed": allowed, "chunks": len(ordered), "dropped": len(res.dropped),
                     "context_tokens": count_tokens(user)}
            note = ""
            try:
                reply = self.llm.complete(SYSTEM_ADAPTIVE, user)
            except LLMError:
                stats.failures += 1
                trace.append({**entry, "error": True})
                break
            stats.llm_calls += 1
            stats.input_tokens += reply.input_tokens
            stats.output_tokens += reply.output_tokens
            stats.latency_s += reply.latency_s
            obj = parse_reply(reply.text)
            if obj is None:
                stats.failures += 1
                note = "Your last reply was not valid JSON. Reply with exactly one JSON object."
                trace.append({**entry, "invalid": True})
                continue
            if obj["action"] == "diagnose" or allowed == "diagnose":
                if obj["action"] == "diagnose":
                    diag = to_diagnosis(obj)
                else:
                    stats.failures += 1
                trace.append({**entry, "action": "diagnose"})
                break
            progress = 0
            for sym in list(obj.get("symbols") or [])[:5]:
                stats.retrieval_calls += 1
                for c in self.retriever.lookup(str(sym)):
                    if add(c, PIN):
                        progress += 1
                    self._prefetch(c, self.prefetch_depth, add)
            for q in list(obj.get("queries") or [])[:2]:
                stats.retrieval_calls += 1
                for c, s in self.retriever.search(str(q), self.per_round_k, exclude=set(pool)):
                    if add(c, s):
                        progress += 1
            trace.append({**entry, "action": "request", "progress": progress})
            if progress == 0:
                forced = True
                note = "Your request returned no new context. You must diagnose now with what you have."
        return DebugResult(diag, stats, last_items, trace)
