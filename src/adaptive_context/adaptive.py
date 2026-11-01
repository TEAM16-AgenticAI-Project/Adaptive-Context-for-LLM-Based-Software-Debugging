"""Adaptive debugger: iterative retrieval + compression + sequencing under a token budget.

Per round:
  1. pack the pool of chunks into the token budget (compress/drop lowest priority first)
  2. order the chunks (callers before callees by default)
  3. ask the LLM: diagnose, or request symbols/queries for missing evidence
  4. requested symbols are pinned (top priority, kept at 'trimmed' or better); optionally their callees are prefetched
The loop stops on a diagnosis, when a request finds nothing new, or at max_rounds (last round forces a diagnosis).

Budget (`budget_scope`):
  "prompt" (default)  token_budget is the whole prompt; code gets what the system prompt and bug report leave
  "code"              token_budget is for code only (the behaviour before Milestone 2)
Pinning (`pin_policy`):
  "pinned" (default)  requested chunks are pinned; newer requests rank higher, so the oldest is dropped first
  "notes"             as "pinned", and evicted pinned code is kept as a two-line note
  "legacy"            the behaviour before Milestone 2: requests share one score and may be compressed to a signature
"""
from __future__ import annotations

import time

from .budget import code_budget, pack
from .llm import LLMError
from .models import DebugResult, Diagnosis, RunStats
from .protocol import SYSTEM_ADAPTIVE, build_user_prompt, parse_reply, render_context, to_diagnosis
from .sequencing import order
from .tokens import count_tokens

PIN = 1e6
PREFETCH = 1e3
PIN_POLICIES = ("legacy", "pinned", "notes")
BUDGET_SCOPES = ("prompt", "code")
CODE_FLOOR = 200
_REPACK_TRIES = 3


class AdaptiveDebugger:
    def __init__(self, retriever, llm, *, initial_k: int = 3, max_rounds: int = 6, token_budget: int = 3000,
                 per_round_k: int = 2, sequencing: str = "dependency", compress: bool = True,
                 prefetch_depth: int = 0, pin_policy: str = "pinned", budget_scope: str = "prompt"):
        for name, v, low in (("initial_k", initial_k, 1), ("max_rounds", max_rounds, 1), ("token_budget", token_budget, 1),
                             ("per_round_k", per_round_k, 1), ("prefetch_depth", prefetch_depth, 0)):
            if not isinstance(v, int) or isinstance(v, bool) or v < low:
                raise ValueError(f"{name} must be an integer >= {low}, got {v!r}")
        if pin_policy not in PIN_POLICIES:
            raise ValueError(f"pin_policy must be one of {PIN_POLICIES}, got {pin_policy!r}")
        if budget_scope not in BUDGET_SCOPES:
            raise ValueError(f"budget_scope must be one of {BUDGET_SCOPES}, got {budget_scope!r}")
        self.retriever, self.llm = retriever, llm
        self.initial_k, self.max_rounds, self.token_budget = initial_k, max_rounds, token_budget
        self.per_round_k, self.sequencing, self.compress = per_round_k, sequencing, compress
        self.prefetch_depth, self.pin_policy, self.budget_scope = prefetch_depth, pin_policy, budget_scope

    def _prefetch(self, chunk, depth, add):
        frontier = [chunk]
        for hop in range(depth):
            nxt = []
            for c in frontier:
                for callee in self.retriever.callees(c):
                    if add(callee, PREFETCH - hop):
                        nxt.append(callee)
            frontier = nxt

    def _build(self, pool, bug_report, allowed, rnd, note):
        """Pack, order and render one round's prompt. Returns (pack result, ordered items, user prompt, code budget)."""
        legacy = self.pin_policy == "legacy"
        pinned = frozenset() if legacy else frozenset(cid for cid, (_, s) in pool.items() if s >= PIN)
        scored = [(c, s) for c, s in pool.values()]

        def attempt(budget):
            res = pack(scored, budget, allow_compress=self.compress, pinned=pinned, notes=self.pin_policy == "notes")
            ordered = order(res.items, self.retriever, self.sequencing)
            user = build_user_prompt(bug_report, render_context(ordered), allowed, rnd, self.max_rounds, note)
            return res, ordered, user

        if self.budget_scope == "code":
            return (*attempt(self.token_budget), self.token_budget)
        scaffold = build_user_prompt(bug_report, "", allowed, rnd, self.max_rounds, note)
        budget = code_budget(self.token_budget, SYSTEM_ADAPTIVE, scaffold, floor=CODE_FLOOR)
        for _ in range(_REPACK_TRIES):      # per-chunk header cost in pack is an estimate: shrink until the prompt fits
            res, ordered, user = attempt(budget)
            excess = count_tokens(SYSTEM_ADAPTIVE) + count_tokens(user) - self.token_budget
            if excess <= 0 or res.over_budget or budget <= CODE_FLOOR:
                break
            budget = max(CODE_FLOOR, budget - excess)
        return res, ordered, user, budget

    def run(self, bug_report: str) -> DebugResult:
        t_start = time.perf_counter()
        stats, trace, pool = RunStats(), [], {}
        legacy = self.pin_policy == "legacy"
        requests = 0                                     # recency counter for pinned symbols

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
        stop, last_invalid = "", False
        for rnd in range(1, self.max_rounds + 1):
            stats.rounds = rnd
            allowed = "diagnose" if (rnd == self.max_rounds or forced) else "request, diagnose"
            res, ordered, user, budget = self._build(pool, bug_report, allowed, rnd, note)
            last_items = [(p.chunk.id, p.level) for p in ordered]
            prompt_tokens = count_tokens(SYSTEM_ADAPTIVE) + count_tokens(user)
            over = res.over_budget or (self.budget_scope == "prompt" and prompt_tokens > self.token_budget)
            stats.over_budget_rounds += over
            entry = {"round": rnd, "allowed": allowed, "chunks": len(ordered), "dropped": len(res.dropped),
                     "context_tokens": count_tokens(user), "prompt_tokens": prompt_tokens, "code_budget": budget,
                     "over_budget": over, "dropped_ids": list(res.dropped), "dropped_pinned": list(res.dropped_pinned),
                     "items": list(last_items)}
            note = ""
            try:
                reply = self.llm.complete(SYSTEM_ADAPTIVE, user)
            except LLMError:
                stats.failures += 1
                trace.append({**entry, "error": True})
                stop = "llm_error"
                break
            stats.llm_calls += 1
            stats.input_tokens += reply.input_tokens
            stats.output_tokens += reply.output_tokens
            stats.latency_s += reply.latency_s
            obj = parse_reply(reply.text)
            if obj is None:
                stats.failures += 1
                last_invalid = True
                note = "Your last reply was not valid JSON. Reply with exactly one JSON object."
                trace.append({**entry, "invalid": True})
                continue
            last_invalid = False
            if obj["action"] == "diagnose" or allowed == "diagnose":
                if obj["action"] == "diagnose":
                    diag = to_diagnosis(obj)
                    stop = "forced_diagnosis" if allowed == "diagnose" else "diagnosed"
                else:
                    stats.failures += 1      # asked for more context when only a diagnosis was allowed
                    stop = "round_limit"
                trace.append({**entry, "action": "diagnose" if obj["action"] == "diagnose" else "request"})
                break
            shown = dict(last_items)
            progress, symbols = 0, [str(s) for s in list(obj.get("symbols") or [])[:5]]
            for sym in symbols:
                stats.retrieval_calls += 1
                for c in self.retriever.lookup(sym):
                    if legacy:
                        if add(c, PIN):
                            progress += 1
                    else:
                        requests += 1
                        add(c, PIN + requests)           # newest request ranks highest
                        if shown.get(c.id, 99) > 1:      # new, or not readable in the last prompt
                            progress += 1
                    self._prefetch(c, self.prefetch_depth, add)
            for q in list(obj.get("queries") or [])[:2]:
                stats.retrieval_calls += 1
                for c, s in self.retriever.search(str(q), self.per_round_k, exclude=set(pool)):
                    if add(c, s):
                        progress += 1
            trace.append({**entry, "action": "request", "symbols": symbols, "progress": progress})
            if progress == 0:
                forced = True
                note = "Your request returned no new context. You must diagnose now with what you have."
        if not stop:
            stop = "invalid_exhausted" if last_invalid else "round_limit"
        stats.wall_s = time.perf_counter() - t_start
        return DebugResult(diag, stats, last_items, trace, stop_reason=stop)
