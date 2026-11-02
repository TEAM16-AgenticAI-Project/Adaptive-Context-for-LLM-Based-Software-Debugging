"""LangGraph agent: native function calling over the Tools class, with the context manager in the loop.

Graph (one round = context -> plan -> act):

    START -> seed -> context -> plan --(evidence tool calls)--> act -> context ...
                                 |--(diagnose)--> verify? --(fix fails, rounds left)--> context
                                 |                        \\-> END
                                 \\--(stop: llm error / invalid replies exhausted)--> END

* seed     one BM25 search with the bug report (same first step as the static baseline)
* context  the context manager: packs the evidence pool under the token budget (requested code pinned and
           shown in full), orders it callers-before-callees, and renders a FRESH prompt with a compact action
           history. Tool output never accumulates in a growing chat log; this node decides what the model sees.
* plan     one LLM call with tool schemas. On the last round (or after a round with no new evidence) only
           `diagnose` is offered and forced via tool_choice. Text replies with the legacy JSON protocol are
           accepted as a fallback; malformed calls are recorded and retried (up to MAX_INVALID).
* act      executes up to MAX_CALLS_PER_ROUND tool calls, adds results to the pool and history.
* verify   optional: a caller-supplied verifier (e.g. fixcheck.check_fix) runs the suggested fix against the
           failing test. A failing fix is fed back once so the model can revise it.

Requires `pip install -e ".[agent]"` (langgraph).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional, TypedDict

from . import budget as _budget
from .llm import LLMError, ToolCall
from .models import DebugResult, Diagnosis, RunStats
from .protocol import build_user_prompt, parse_reply, render_context, to_diagnosis
from .sequencing import order
from .tokens import count_tokens
from .tools import DIAGNOSE_SCHEMA, Tools

PIN = 1e6
NEIGHBOUR = 1e3
_TEST_ID = re.compile(r"([\w./-]+\.py::[\w:\[\]-]+)")
_CALLED = re.compile(r"`([A-Za-z_]\w*)\(")              # a call written in backticks in the bug report
MAX_INVALID = 2
MAX_CALLS_PER_ROUND = 3
HISTORY_LINES = 12

SYSTEM_AGENT = """You debug a Python repository from a bug report: find the single function with the defect,
explain the cause from code you have seen, and fix it.
Each round shows the bug report, your recent tool calls (HISTORY) and CONTEXT: code chosen to fit a token
budget; each header's tag says if it is full, trimmed, outline or signature.
Call tools to gather evidence (at most 3 per round); read_function shows a function in full, so use it before
blaming one. Many functions share names: pass the module path the test imports, e.g. "pkg.module.func".
Call diagnose when the evidence supports an answer; fix = the corrected function (def ...) or line, or "".
If ALLOWED_ACTIONS is only diagnose, call diagnose now (confidence "low" if evidence is incomplete).
Only cite code shown in CONTEXT. Without tool support, reply with ONE JSON object:
{"action":"request","symbols":["<names>"],"queries":["<text>"]} or
{"action":"diagnose","file":"...","function":"...","cause":"...","fix":"...","confidence":"high|low"}"""


try:                                                    # shape check from the action-validation PR (kunduru)
    from .protocol import _valid_diagnosis
except ImportError:
    def _valid_diagnosis(obj) -> bool:
        """A diagnosis needs non-empty file, function and cause, a string fix and confidence high|low."""
        if not isinstance(obj, dict) or obj.get("action") != "diagnose":
            return False
        if not all(isinstance(obj.get(k), str) and obj[k].strip() for k in ("file", "function", "cause")):
            return False
        return (bool(obj["function"].rsplit(".", 1)[-1].strip()) and isinstance(obj.get("fix"), str)
                and obj.get("confidence") in ("high", "low"))


def _code_budget(total: int, *prompt_parts: str) -> int:
    """Tokens left for code after the prompt overhead. Uses budget.code_budget once the context-manager PR
    (Yadnesh) lands; until then the same rule is applied here."""
    fn = getattr(_budget, "code_budget", None)
    if fn is not None:
        return fn(total, *prompt_parts)
    return max(200, total - sum(count_tokens(p) for p in prompt_parts if p))


def _pack(scored: list, max_tokens: int, compress: bool, pinned: frozenset):
    """budget.pack with pinned evidence when supported (context-manager PR); plain packing otherwise.
    Pinned chunks always carry the highest scores, so they are compressed and dropped last either way."""
    try:
        return _budget.pack(scored, max_tokens, allow_compress=compress, pinned=pinned)
    except TypeError:
        return _budget.pack(scored, max_tokens, allow_compress=compress)


class AgentState(TypedDict, total=False):
    bug_report: str
    pool: dict
    pinned: frozenset
    history: list
    test_output: str
    round: int
    forced: bool
    stalls: int
    invalid: int
    note: str
    calls: list
    diagnosis: Diagnosis
    stop: str
    stats: RunStats
    context_items: list
    trace: list
    allowed: str
    prompt: str
    verify_attempts: int
    verification: dict


_SALVAGE_NAME = re.compile(r'"name"\s*:\s*"(\w+)"')
_SALVAGE_ARG = re.compile(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _salvage(text: str, schemas: list) -> list:
    """Recover one tool call from a generation the API refused as malformed JSON, e.g.
    '{"name": "search_code", "arguments": {"query":"class Bash",""}"}'. Only a tool offered this round, only
    arguments from its schema, and only if every required argument is present. At temperature 0 the model repeats
    the same broken call, so a plain retry would not help."""
    m = _SALVAGE_NAME.search(text or "")
    spec = {s["function"]["name"]: s["function"]["parameters"] for s in schemas}
    if not m or m.group(1) not in spec:
        return []
    props, required = spec[m.group(1)].get("properties", {}), spec[m.group(1)].get("required", [])
    args = {}
    for k, v in _SALVAGE_ARG.findall(text[m.end():]):
        if k in props and k not in args:
            try:
                args[k] = json.loads(f'"{v}"')
            except ValueError:
                args[k] = v
    return [ToolCall(m.group(1), args, "repaired")] if all(k in args for k in required) else []


def _calls_from_text(text: str) -> list:
    """Fallback for models that answer in text: map the legacy JSON protocol onto tool calls."""
    obj = parse_reply(text)
    if obj is None:
        return []
    if obj["action"] == "diagnose":
        return [ToolCall("diagnose", {k: obj[k] for k in ("file", "function", "cause", "fix", "confidence") if k in obj})]
    calls = [ToolCall("read_function", {"name": s}) for s in obj.get("symbols") or []]
    calls += [ToolCall("search_code", {"query": q}) for q in obj.get("queries") or []]
    return calls[:MAX_CALLS_PER_ROUND]


class LangGraphAgent:
    """Same interface as StaticRAG / AdaptiveDebugger: `run(bug_report) -> DebugResult`."""

    def __init__(self, retriever, llm, *, tools: Optional[Tools] = None, initial_k: int = 3, max_rounds: int = 6,
                 token_budget: int = 3000, sequencing: str = "dependency", compress: bool = True,
                 verifier: Optional[Callable[[Diagnosis], dict]] = None, max_verify: int = 1):
        if initial_k < 1 or max_rounds < 1 or token_budget < 1:
            raise ValueError("initial_k, max_rounds and token_budget must be positive")
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as e:                          # pragma: no cover - depends on the environment
            raise ImportError('LangGraphAgent needs langgraph: pip install -e ".[agent]"') from e
        self.retriever, self.llm = retriever, llm
        self.tools = tools or Tools(retriever)
        self.initial_k, self.max_rounds, self.token_budget = initial_k, max_rounds, token_budget
        self.sequencing, self.compress = sequencing, compress
        self.verifier, self.max_verify = verifier, max_verify

        g = StateGraph(AgentState)
        g.add_node("seed", self._seed)
        g.add_node("context", self._context)
        g.add_node("plan", self._plan)
        g.add_node("act", self._act)
        g.add_node("verify", self._verify)
        g.add_edge(START, "seed")
        g.add_edge("seed", "context")
        g.add_edge("context", "plan")
        g.add_conditional_edges("plan", self._after_plan, {"act": "act", "verify": "verify", "end": END,
                                                           "context": "context"})
        g.add_edge("act", "context")
        g.add_conditional_edges("verify", self._after_verify, {"context": "context", "end": END})
        self.graph = g.compile()

    # ------------------------------------------------------------------ nodes
    def _seed(self, s: AgentState) -> dict:
        stats = RunStats()
        hits = self.retriever.search(s["bug_report"], self.initial_k)
        stats.retrieval_calls += 1
        pool = {c.id: [c, sc] for c, sc in hits}
        pinned = set()
        hist = [f"seed: search(bug report) -> {', '.join(c.id for c, _ in hits) or 'nothing'}"]
        # Failing test first: if the report names a pytest id, pin that test and add its module's imports,
        # which tell the model the module path of the code under test.
        for test_id in dict.fromkeys(_TEST_ID.findall(s["bug_report"])):
            found = self.retriever.lookup(test_id)
            if len(found) == 1:
                test = found[0]
                pool[test.id] = [test, PIN]
                pinned.add(test.id)
                imports = self.retriever.by_id.get(f"{test.path}::<module>")
                if imports is not None:
                    pool[imports.id] = [imports, NEIGHBOUR]
                hist.append(f"seed: failing test {test.id} pinned" + (", its imports added" if imports else ""))
        # Functions the report names as calls (e.g. `checkout(100, 0.2)`) are the entry point of the failure:
        # pin them so compression never reduces them to a signature. Unique names only, at most 2.
        named = 0
        for fn in dict.fromkeys(_CALLED.findall(s["bug_report"])):
            found = self.retriever.lookup(fn)
            if named < 2 and len(found) == 1 and found[0].kind in ("function", "method"):
                pool[found[0].id] = [found[0], PIN]
                pinned.add(found[0].id)
                named += 1
                hist.append(f"seed: {found[0].id} named in the report, pinned")
        return {"pool": pool, "pinned": frozenset(pinned), "history": hist, "test_output": "", "round": 0,
                "forced": False, "stalls": 0, "invalid": 0, "note": "", "calls": [], "diagnosis": Diagnosis(), "stop": "",
                "stats": stats, "context_items": [], "trace": [], "verify_attempts": 0, "verification": {}}

    def _context(self, s: AgentState) -> dict:
        rnd = s["round"] + 1
        allowed = "diagnose" if (rnd >= self.max_rounds or s["forced"]) else "request, diagnose"
        history = "\n".join(s["history"][-HISTORY_LINES:])
        extra = f"HISTORY:\n{history}\n"
        if s["test_output"]:
            extra += f"\nFAILING TEST OUTPUT:\n{s['test_output']}\n"
        if s["note"]:
            extra += f"\nNOTE: {s['note']}\n"
        header = build_user_prompt(s["bug_report"], "", allowed, rnd, self.max_rounds, extra)
        res = _pack([(c, sc) for c, sc in s["pool"].values()], _code_budget(self.token_budget, SYSTEM_AGENT, header),
                    self.compress, s["pinned"])
        over = bool(getattr(res, "over_budget", False))
        ordered = order(res.items, self.retriever, self.sequencing)
        prompt = build_user_prompt(s["bug_report"], render_context(ordered), allowed, rnd, self.max_rounds, extra)
        stats = s["stats"]
        stats.rounds = rnd
        stats.over_budget_rounds += int(over)
        entry = {"round": rnd, "allowed": allowed, "chunks": len(ordered), "dropped": len(res.dropped),
                 "levels": {p.chunk.id: p.level for p in ordered}, "context_tokens": count_tokens(prompt),
                 "over_budget": over}
        return {"round": rnd, "allowed": allowed, "prompt": prompt, "note": "", "stats": stats,
                "context_items": [(p.chunk.id, p.level) for p in ordered], "trace": s["trace"] + [entry]}

    def _plan(self, s: AgentState) -> dict:
        stats, trace = s["stats"], list(s["trace"])
        only_diag = s["allowed"] == "diagnose"
        schemas = [DIAGNOSE_SCHEMA] if only_diag else self.tools.schemas() + [DIAGNOSE_SCHEMA]
        choice = {"type": "function", "function": {"name": "diagnose"}} if only_diag else "auto"
        messages = [{"role": "system", "content": SYSTEM_AGENT}, {"role": "user", "content": s["prompt"]}]
        try:
            reply = self.llm.chat(messages, tools=schemas, tool_choice=choice)
        except LLMError as e:
            stats.failures += 1
            trace[-1] = {**trace[-1], "error": str(e)[:200]}
            return {"stats": stats, "trace": trace, "stop": "llm_error", "calls": []}
        stats.llm_calls += 1
        stats.input_tokens += reply.input_tokens
        stats.output_tokens += reply.output_tokens
        stats.latency_s += reply.latency_s
        calls = list(reply.tool_calls) or _calls_from_text(reply.text)
        rejected = getattr(reply, "rejected", "")
        if not calls and rejected:
            calls = _salvage(reply.text, schemas)
            if calls:                                    # repaired: counted as a failure, but the round goes on
                stats.failures += 1
                trace[-1] = {**trace[-1], "repaired": rejected[:200]}
                rejected = ""
        diag_calls = [c for c in calls if c.name == "diagnose"]
        if diag_calls:
            calls = diag_calls[:1]
        elif only_diag:
            calls = []
        calls = calls[:MAX_CALLS_PER_ROUND]
        if not calls or (diag_calls and not _valid_diagnosis({"action": "diagnose", **calls[0].arguments})):
            stats.failures += 1
            invalid = s["invalid"] + 1
            trace[-1] = {**trace[-1], "invalid": True, "reply": (reply.text or "")[:200],
                         **({"rejected": rejected[:200]} if rejected else {})}
            if rejected:                                 # provider refused the call (e.g. a tool not offered)
                offered = "diagnose" if only_diag else "one of the listed tools or diagnose"
                note = (f"The API rejected your last tool call ({rejected[:160]}). Call only {offered}, with "
                        "arguments as valid JSON.")
            else:
                note = ("Your last reply had no valid tool call. Call one of the listed tools, or diagnose with all "
                        "fields (file, function, cause, fix, confidence=high|low).")
            hist = s["history"] + [f"round {s['round']}: invalid reply ({invalid}/{MAX_INVALID + 1})"]
            if invalid > MAX_INVALID:
                return {"stats": stats, "trace": trace, "invalid": invalid, "stop": "invalid_exhausted", "calls": []}
            if s["round"] >= self.max_rounds:
                return {"stats": stats, "trace": trace, "invalid": invalid, "stop": "round_limit", "calls": []}
            return {"stats": stats, "trace": trace, "invalid": invalid, "note": note, "history": hist, "calls": []}
        trace[-1] = {**trace[-1], "calls": [{"name": c.name, "args": c.arguments} for c in calls]}
        out = {"stats": stats, "trace": trace, "calls": calls}
        if diag_calls:
            out["diagnosis"] = to_diagnosis({"action": "diagnose", **calls[0].arguments})
        return out

    def _act(self, s: AgentState) -> dict:
        pool = dict(s["pool"])
        pinned, stats, trace = set(s["pinned"]), s["stats"], list(s["trace"])
        hist, test_output, progress, results, missed = list(s["history"]), s["test_output"], 0, [], []
        for call in s["calls"]:
            stats.tool_calls += 1
            if call.name in ("search_code", "grep_symbol", "read_function", "get_callers", "get_callees"):
                stats.retrieval_calls += 1
            r = self.tools.execute(call.name, call.arguments, exclude=frozenset(pool))
            if not r.ok:
                stats.failures += 1
            for chunk, score in r.chunks:
                if r.pin:
                    # newer requests rank higher, so if pinned evidence must be dropped the oldest request goes
                    # first, never the function the model just asked for
                    progress += int(chunk.id not in pinned)
                    pinned.add(chunk.id)
                    pool[chunk.id] = [chunk, PIN + stats.tool_calls]
                    continue
                prio = NEIGHBOUR if call.name in ("get_callers", "get_callees") else score
                if chunk.id not in pool:
                    pool[chunk.id] = [chunk, prio]
                    progress += 1
                elif prio > pool[chunk.id][1]:
                    pool[chunk.id] = [chunk, prio]
                    progress += 1
            if r.text and r.text != test_output:
                test_output = r.text
                progress += 1
            args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call.arguments.items())
            hist.append(f"round {s['round']}: {call.name}({args}) -> {r.summary}")
            results.append({"name": call.name, "ok": r.ok, "summary": r.summary})
            if not r.ok or (call.name != "search_code" and not r.chunks and not r.text):
                missed.append(f"{call.name}: {r.summary}")    # wrong name form or ambiguous; not a repeat
        stalls = 0 if progress else s.get("stalls", 0) + 1
        trace[-1] = {**trace[-1], "action": "tools", "results": results, "progress": progress}
        # No new evidence: if the calls only repeated what the model already has, it is done searching -> force a
        # diagnosis. If a lookup simply missed (wrong name form), give it one more round with the reason first.
        forced = s["forced"] or (progress == 0 and (not missed or stalls >= 2))
        if progress:
            note = ""
        elif forced:
            note = "Your last tool calls added no new evidence. Diagnose now with what you have."
        else:
            note = ("Your last lookup found nothing (" + "; ".join(missed)[:300] + "). Use a name shown in CONTEXT "
                    "or HISTORY, as 'pkg.module.func' or 'path.py::func', or diagnose.")
        return {"pool": pool, "pinned": frozenset(pinned), "stats": stats, "trace": trace, "history": hist,
                "test_output": test_output, "forced": forced, "stalls": stalls, "note": note, "calls": []}

    def _verify(self, s: AgentState) -> dict:
        d, stats, trace = s["diagnosis"], s["stats"], list(s["trace"])
        res = dict(self.verifier(d)) if self.verifier else {}
        attempts = s["verify_attempts"] + 1
        trace[-1] = {**trace[-1], "verification": {k: v for k, v in res.items() if k != "output"}}
        out = {"verification": res, "verify_attempts": attempts, "trace": trace}
        if res.get("passed"):
            return {**out, "stop": "verified"}
        if attempts <= self.max_verify and s["round"] < self.max_rounds:
            tail = "\n".join(str(res.get("output", "")).splitlines()[-8:])
            note = (f"Your fix for {d.function} did not pass the failing test ({res.get('reason', 'failed')}). "
                    "Re-check the evidence and diagnose again.")
            hist = s["history"] + [f"round {s['round']}: diagnose({d.file}::{d.function}) -> fix check "
                                   f"{res.get('reason', 'failed')}"]
            return {**out, "note": note, "history": hist, "test_output": tail or s["test_output"],
                    "diagnosis": d, "stats": stats}
        return {**out, "stop": "verify_failed"}

    # ------------------------------------------------------------------ routing
    def _after_plan(self, s: AgentState) -> str:
        if s.get("stop"):
            return "end"
        calls = s.get("calls") or []
        if not calls:
            return "context"                              # invalid reply: retry with a note
        if calls[0].name == "diagnose":
            if self.verifier and s["diagnosis"].valid and s["diagnosis"].fix.strip():
                return "verify"
            return "end"
        return "act"

    def _after_verify(self, s: AgentState) -> str:
        return "end" if s.get("stop") else "context"

    # ------------------------------------------------------------------ public
    def run(self, bug_report: str) -> DebugResult:
        t0 = time.perf_counter()
        final: dict[str, Any] = self.graph.invoke({"bug_report": bug_report},
                                                  {"recursion_limit": 8 * self.max_rounds + 20})
        stats: RunStats = final["stats"]
        stats.wall_s = time.perf_counter() - t0
        d: Diagnosis = final.get("diagnosis") or Diagnosis()
        stop = final.get("stop") or ""
        if not stop:
            stop = "diagnosed" if d.valid else "round_limit"
            if d.valid and final.get("forced"):
                stop = "forced_diagnosis"
        return DebugResult(d, stats, final.get("context_items", []), final.get("trace", []),
                           stop_reason=stop, verification=final.get("verification", {}))


class SimToolLLM:
    """Offline adapter: exposes SimulatedLLM through `chat()` with native-style tool calls.

    The simulator only reads the rendered prompt (it never sees ground truth); its JSON request/diagnose
    replies are mapped onto read_function / search_code / diagnose calls. Numbers measure context
    management under the simulator's assumptions, not a real model's reasoning.
    """

    def __init__(self, sim):
        self.sim = sim

    def complete(self, system: str, user: str):
        return self.sim.complete(system, user)

    def chat(self, messages: list, tools=None, tool_choice=None):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = self.sim.complete(system, user)
        reply.tool_calls = _calls_from_text(reply.text)
        return reply
