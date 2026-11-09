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
* verify   optional test-driven repair: a verifier (multifix.TestOracle, or fixcheck.check_fix) applies the whole
           candidate, i.e. `fix` plus every `other_changes` location in any file, to a copy and runs the tests.
           The feedback names a failing test, broken tests, names other files still use, and related tests that
           still fail (often the same defect in a sibling implementation). Each failed check grants two more
           rounds (look, then re-diagnose), up to max_verify times; the best checked candidate is returned.

Multi-file support: `diagnose.other_changes` lists further locations; `get_related` and the automatic related
context (base / sibling implementations of a method the model reads) surface code in other files.

Requires `pip install -e ".[agent]"` (langgraph).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from typing import Any, Callable, Optional, TypedDict

from . import budget as _budget
from .llm import LLMError, ToolCall
from .logs import short
from .models import DebugResult, Diagnosis, RunStats
from .protocol import build_user_prompt, parse_reply, render_context, to_diagnosis
from .sequencing import order
from .tokens import count_tokens
from .tools import DIAGNOSE_SCHEMA, Tools

log = logging.getLogger(__name__)

PIN = 1e6
NEIGHBOUR = 1e3
MAX_LOCATIONS = 8
_TEST_ID = re.compile(r"([\w./-]+\.py::[\w:\[\]-]+)")
_UNITTEST_ID = re.compile(r"`((?:\w+\.)+test\w*)`")      # e.g. `test.test_utils.TestUtil.test_dfxp2srt`
# traceback frames: unittest/CPython `File "a/b.py", line 12, in f` and pytest `a/b.py:12: in f` / `a/b.py:12: Error`
_FRAME = re.compile(r'File "([^"]+?\.py)", line (\d+)|^\s*([\w./-]+\.py):(\d+):(?!\s*\w*Warning)')
_EXC_LINE = re.compile(r"^\s*(E\s|\w+(Error|Exception|Exit|Interrupt)\b|\w+\.\w+Error\b)")
MAX_FRAMES = 6
_CALLED = re.compile(r"`([A-Za-z_]\w*)\(")              # a call written in backticks in the bug report
MAX_INVALID = 2
MAX_CALLS_PER_ROUND = 3
MAX_REASKS = 2              # a fix that only re-sends the kept place goes back unchecked at most this often
HISTORY_LINES = 12

SYSTEM_AGENT = """You debug a Python repository from a bug report: find the defect, explain the cause from code
you have seen, and fix it.
Each round shows the bug report, your recent tool calls (HISTORY) and CONTEXT: code chosen to fit a token
budget; each header's tag says if it is full, trimmed, outline or signature.
Call tools to gather evidence (at most 3 per round); read_function shows a function in full, so use it before
blaming one. Many functions share names: pass the module path the test imports, e.g. "pkg.module.func".
Bugs can span files: a wrong result may come from a function the suspect calls (get_callees); the same defect
may repeat in sibling implementations of a method, or a subclass may depend on its base class (get_related);
renaming or changing a function means its callers must change too, or keep the old name (get_callers).
Call diagnose when the evidence supports an answer. fix = the complete corrected function (def ...), or set
replace to the exact current lines and fix to their new lines (best for long functions). List every other
function that must change, in any file, in other_changes, in the same form (a function that does not exist yet
is added; function "<module>" adds module-level code such as an import or alias). Use fix "" only if you have none.
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
    extra_rounds: int          # rounds granted after a failed fix check
    best: dict                 # best fix-check result so far: {"diagnosis", "verification"}
    seen_calls: dict           # "tool:args" -> round it was first run (repeats are not executed again)
    last_retry: bool           # the one extra round after an invalid reply on the last round was used
    unforced: bool             # a diagnose-only round was relaxed once because the model asked for a tool
    checked: dict              # fix signature -> fix-check result (the same fix is not tested twice)
    kept: list                 # places of a fix that made the failing test pass; merged into later fixes
    kept_left: int             # related tests still failing with the kept fix
    asked: str                 # the place the agent asked for next (file::function)
    reasks: int                # times a fix that only repeated the kept place was sent back unchecked


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


def _locations(changes) -> list:
    """Validated other_changes: [{file, function, fix}] with non-empty strings, at most MAX_LOCATIONS."""
    out = []
    for ch in changes if isinstance(changes, list) else []:
        if isinstance(ch, dict) and all(isinstance(ch.get(k), str) and ch[k].strip() for k in ("file", "function", "fix")):
            loc = {k: ch[k] for k in ("file", "function", "fix")}
            if isinstance(ch.get("replace"), str) and ch["replace"].strip():
                loc["replace"] = ch["replace"]
            out.append(loc)
    return out[:MAX_LOCATIONS]


def _describe(call) -> str:
    if call.name == "diagnose":
        a = call.arguments
        more = len(a.get("other_changes") or []) if isinstance(a.get("other_changes"), list) else 0
        return f"diagnose({a.get('file')}::{a.get('function')}{f', +{more} more places' if more else ''})"
    args = ", ".join(f"{k}={json.dumps(v)[:80]}" for k, v in call.arguments.items())
    return f"{call.name}({args})"


def _signature(d) -> str:
    """Identity of a proposed fix (all locations), to recognise the same fix proposed twice."""
    from .multifix import edits_from_diagnosis
    return json.dumps([[e.file, e.function, " ".join(e.code.split()), " ".join(e.old.split())]
                       for e in edits_from_diagnosis(d)])


def _places(d) -> list:
    """Every place a diagnosis changes, as other_changes-style dicts (the primary location first)."""
    first = {"file": d.file, "function": d.function, "fix": d.fix}
    if d.replace:
        first["replace"] = d.replace
    return [first] + [dict(loc) for loc in d.locations]


def _same_place(a: dict, b: dict) -> bool:
    fa, fb = a["file"].lstrip("./"), b["file"].lstrip("./")
    return (fa.endswith(fb) or fb.endswith(fa)) and a["function"].split(".")[-1] == b["function"].split(".")[-1]


def _with_kept(d, kept: list):
    """Add the kept places (from a fix whose failing test passed) that the new diagnosis does not change itself, so
    a model can fix a multi-file bug one place at a time and the agent tests the combination."""
    mine = _places(d)
    extra = [k for k in kept if not any(_same_place(k, m) for m in mine)]
    return dataclasses.replace(d, locations=(list(d.locations) + extra)[:MAX_LOCATIONS]) if extra else d


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_TEST_FILE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$|_test\.py$")


def _is_test_path(path: str) -> bool:
    return bool(_TEST_FILE.search(path))


def _rank(res: dict) -> tuple:
    """Order fix-check results from worst to best (for keeping the best candidate)."""
    return (bool(res.get("passed")), bool(res.get("target_passed")), bool(res.get("applied")),
            -len(res.get("regressions", [])) - len(res.get("dangling", [])) - len(res.get("related_failing", [])))


def _lenient_reply(text: str):
    """parse_reply for JSON with raw newlines inside strings (models write code that way), else field salvage."""
    dec = json.JSONDecoder(strict=False)
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("action") in ("diagnose", "request"):
            return obj
    if re.search(r'"action"\s*:\s*"diagnose"', text or ""):
        obj = {"action": "diagnose"}
        for k, v in _SALVAGE_ARG.findall(text):
            if k in ("file", "function", "cause", "fix", "confidence", "replace") and k not in obj:
                try:
                    obj[k] = json.loads(f'"{v}"', strict=False)
                except ValueError:
                    obj[k] = v
        if all(obj.get(k) for k in ("file", "function", "fix")):
            return obj
    return None


def _calls_from_text(text: str) -> list:
    """Fallback for models that answer in text: map the legacy JSON protocol onto tool calls."""
    obj = parse_reply(text) or _lenient_reply(text)
    if obj is None:
        return []
    if obj["action"] == "diagnose":
        keys = ("file", "function", "cause", "fix", "confidence", "replace", "other_changes")
        return [ToolCall("diagnose", {k: obj[k] for k in keys if k in obj})]
    calls = [ToolCall("read_function", {"name": s}) for s in obj.get("symbols") or []]
    calls += [ToolCall("search_code", {"query": q}) for q in obj.get("queries") or []]
    return calls[:MAX_CALLS_PER_ROUND]


class LangGraphAgent:
    """Same interface as StaticRAG / AdaptiveDebugger: `run(bug_report) -> DebugResult`."""

    def __init__(self, retriever, llm, *, tools: Optional[Tools] = None, initial_k: int = 3, max_rounds: int = 6,
                 token_budget: int = 3000, sequencing: str = "dependency", compress: bool = True,
                 verifier: Optional[Callable[[Diagnosis], dict]] = None, max_verify: int = 1,
                 auto_related: bool = True, time_limit: Optional[float] = None):
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
        self.verifier, self.max_verify, self.auto_related = verifier, max_verify, auto_related
        self.time_limit = time_limit                  # seconds for one run(); checked between steps
        self._t0 = 0.0

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
        log.info("seed: %d chunks indexed; first search -> %s", len(self.retriever.chunks),
                 short(", ".join(c.id for c, _ in hits) or "nothing", 200))
        # Failing test first: if the report names a pytest id, pin that test and add its module's imports,
        # which tell the model the module path of the code under test.
        test_ids = _TEST_ID.findall(s["bug_report"]) + _UNITTEST_ID.findall(s["bug_report"])
        for test_id in dict.fromkeys(test_ids):
            found = self.tools.resolve(test_id)
            if len(found) == 1 and found[0].id not in pinned:
                test = found[0]
                pool[test.id] = [test, PIN]
                pinned.add(test.id)
                imports = self.retriever.by_id.get(f"{test.path}::<module>")
                if imports is not None:
                    pool[imports.id] = [imports, NEIGHBOUR]
                hist.append(f"seed: failing test {test.id} pinned" + (", its imports added" if imports else ""))
                log.info("seed: failing test %s pinned", test.id)
        # Traceback frames in the report: the functions the failure ran through. The deepest two project frames
        # (where it crashed) are pinned in full; the others enter as neighbours. Test files are skipped (the
        # failing test is already pinned) and so are frames outside the repository (site-packages, stdlib).
        frames, raised = self._frame_chunks(s["bug_report"])
        for c in frames:
            if c.id in pinned:
                continue
            crash = c.id in raised
            pool[c.id] = [c, PIN if crash else NEIGHBOUR]
            if crash:
                pinned.add(c.id)
        if frames:
            hist.append("seed: traceback frames added: " + ", ".join(c.id + (" (raised here)" if c.id in raised else "")
                                                                     for c in frames))
            log.info("seed: traceback frames added: %s", short(", ".join(c.id for c in frames), 240))
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
                "stats": stats, "context_items": [], "trace": [], "verify_attempts": 0, "verification": {},
                "extra_rounds": 0, "best": {}, "seen_calls": {}, "checked": {}}

    def _context(self, s: AgentState) -> dict:
        rnd = s["round"] + 1
        allowed = "diagnose" if (rnd >= self._limit(s) or s["forced"]) else "request, diagnose"
        history = "\n".join(s["history"][-HISTORY_LINES:])
        extra = f"HISTORY:\n{history}\n"
        if s["test_output"]:
            extra += f"\nFAILING TEST OUTPUT:\n{s['test_output']}\n"
        if s["note"]:
            extra += f"\nNOTE: {s['note']}\n"
        header = build_user_prompt(s["bug_report"], "", allowed, rnd, self._limit(s), extra)
        res = _budget.pack([(c, sc) for c, sc in s["pool"].values()],
                           _budget.code_budget(self.token_budget, SYSTEM_AGENT, header),
                           allow_compress=self.compress, pinned=s["pinned"])
        over = bool(getattr(res, "over_budget", False))
        ordered = order(res.items, self.retriever, self.sequencing)
        prompt = build_user_prompt(s["bug_report"], render_context(ordered), allowed, rnd, self._limit(s), extra)
        stats = s["stats"]
        stats.rounds = rnd
        stats.over_budget_rounds += int(over)
        entry = {"round": rnd, "allowed": allowed, "chunks": len(ordered), "dropped": len(res.dropped),
                 "levels": {p.chunk.id: p.level for p in ordered}, "context_tokens": count_tokens(prompt),
                 "over_budget": over}
        log.info("round %d/%d (%s) | prompt %d tokens, %d code chunks, %d dropped | %.0fs elapsed", rnd,
                 self._limit(s), "diagnose only" if allowed == "diagnose" else "tools + diagnose",
                 entry["context_tokens"], len(ordered), len(res.dropped), self._elapsed())
        return {"round": rnd, "allowed": allowed, "prompt": prompt, "note": "", "stats": stats,
                "context_items": [(p.chunk.id, p.level) for p in ordered], "trace": s["trace"] + [entry]}

    def _plan(self, s: AgentState) -> dict:
        stats, trace = s["stats"], list(s["trace"])
        if self._expired():
            log.warning("time limit of %.0fs reached before round %d: stopping", self.time_limit, s["round"])
            trace[-1] = {**trace[-1], "error": "time limit reached"}
            return {"stats": stats, "trace": trace, "stop": "timeout", "calls": []}
        only_diag = s["allowed"] == "diagnose"
        schemas = [DIAGNOSE_SCHEMA] if only_diag else self.tools.schemas() + [DIAGNOSE_SCHEMA]
        choice = {"type": "function", "function": {"name": "diagnose"}} if only_diag else "auto"
        messages = [{"role": "system", "content": SYSTEM_AGENT}, {"role": "user", "content": s["prompt"]}]
        t_call = time.perf_counter()
        try:
            reply = self.llm.chat(messages, tools=schemas, tool_choice=choice)
        except LLMError as e:
            stats.failures += 1
            trace[-1] = {**trace[-1], "error": str(e)[:200]}
            log.error("LLM call failed after %.0fs: %s", time.perf_counter() - t_call, short(e, 300))
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
            log.warning("LLM reply %d invalid (%s) after %.1fs: %s", invalid, "rejected by API" if rejected else
                        "no valid call", time.perf_counter() - t_call, short(rejected or reply.text or "(empty)", 200))
            if invalid > MAX_INVALID:
                return {"stats": stats, "trace": trace, "invalid": invalid, "stop": "invalid_exhausted", "calls": []}
            if rejected and only_diag and s.get("forced") and not s.get("unforced") and s["round"] < self._limit(s):
                # diagnose-only, but the model insists on a tool: give it that one tool round instead of
                # burning its remaining replies on rejected calls
                log.info("  diagnose-only round, but the model asked for a tool: allowing one more tool round")
                note = ("You may call one more tool now (it was not offered in the last round), then diagnose.")
                return {"stats": stats, "trace": trace, "invalid": invalid, "note": note, "history": hist,
                        "calls": [], "forced": False, "unforced": True}
            if s["round"] >= self._limit(s):
                if not s.get("last_retry"):            # the final answer was rejected: one more diagnose-only try
                    log.info("  last round's reply was invalid: one more diagnose-only round")
                    return {"stats": stats, "trace": trace, "invalid": invalid, "note": note, "history": hist,
                            "calls": [], "extra_rounds": s.get("extra_rounds", 0) + 1, "last_retry": True,
                            "forced": True}
                return {"stats": stats, "trace": trace, "invalid": invalid, "stop": "round_limit", "calls": []}
            return {"stats": stats, "trace": trace, "invalid": invalid, "note": note, "history": hist, "calls": []}
        trace[-1] = {**trace[-1], "calls": [{"name": c.name, "args": c.arguments} for c in calls]}
        log.info("LLM replied in %.1fs (%d in / %d out tokens): %s", time.perf_counter() - t_call,
                 reply.input_tokens, reply.output_tokens, short("; ".join(_describe(c) for c in calls), 300))
        out = {"stats": stats, "trace": trace, "calls": calls}
        if diag_calls:
            args = calls[0].arguments
            d = to_diagnosis({"action": "diagnose", **args})
            qual = str(args.get("function", "")).split("::")[-1].strip()
            if "." in qual:                       # keep 'Cls.method': the fix must replace that exact method
                d.function = qual
            d.locations = _locations(args.get("other_changes"))
            d.replace = args["replace"] if isinstance(args.get("replace"), str) else ""
            out["diagnosis"] = d
        return out

    def _frame_chunks(self, report: str) -> tuple:
        """Project functions on the traceback(s) in `report`: (frames, ids of frames where an exception was raised).

        The raise site is the deepest project frame before the exception line (`TypeError: ...`, pytest's `E   ...`
        or `path.py:12: ValueError`). Frames outside the repository (stdlib, site-packages) are skipped but keep
        the raise site, since the project called them; a test-file frame resets it, so an assertion raised in the
        test does not blame the framework frame before it. Warning lines are not frames."""
        spans: dict = {}
        for c in self.retriever.chunks:
            if c.kind in ("function", "method"):
                spans.setdefault(c.path, []).append(c)
        frames, raised, last = [], [], None
        for line in (report or "").splitlines():
            m = _FRAME.search(line)
            if m:
                path, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                rel = self.tools._repo_path(path.replace("\\", "/").lstrip("./"))
                if rel is None:                                   # stdlib / site-packages: keep the raise site
                    continue
                last = None
                if _is_test_path(rel):
                    continue
                hit = [c for c in spans.get(rel, []) if c.start <= int(num) <= c.end]
                if hit:
                    last = min(hit, key=lambda ch: ch.end - ch.start)   # innermost definition containing the line
                    if last.id not in {c.id for c in frames}:
                        frames.append(last)
                    if m.group(3) and re.match(r"\s*\w+(Error|Exception)\b", line[m.end():]):
                        raised.append(last.id)                    # pytest: `path.py:12: ValueError`
                        last = None
            elif last is not None and _EXC_LINE.match(line):
                raised.append(last.id)
                last = None
        raised = list(dict.fromkeys(raised))[-2:]
        keep = [c for c in frames if c.id in raised] + [c for c in frames if c.id not in raised]
        keep = keep[:MAX_FRAMES]
        return [c for c in frames if c in keep], set(raised)

    def _limit(self, s: AgentState) -> int:
        return self.max_rounds + s.get("extra_rounds", 0)

    def _act(self, s: AgentState) -> dict:
        pool = dict(s["pool"])
        pinned, stats, trace = set(s["pinned"]), s["stats"], list(s["trace"])
        hist, test_output, progress, results, missed = list(s["history"]), s["test_output"], 0, [], []
        seen = dict(s.get("seen_calls") or {})
        for call in s["calls"]:
            key = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
            if key in seen:                              # identical call: the answer cannot change
                summary = (f"already called in round {seen[key]}; the result would not change. Use a different "
                           "tool or name (read_function, get_callees, get_related), or diagnose")
                hist.append(f"round {s['round']}: {_describe(call)} -> {summary}")
                results.append({"name": call.name, "ok": False, "summary": summary, "repeat": True})
                missed.append(f"{call.name}: repeated call")
                log.info("  tool %s: repeated call, not run again", _describe(call))
                continue
            seen[key] = s["round"]
            stats.tool_calls += 1
            if call.name in ("search_code", "grep_symbol", "read_function", "get_callers", "get_callees",
                             "get_related"):
                stats.retrieval_calls += 1
            t_tool = time.perf_counter()
            r = self.tools.execute(call.name, call.arguments, exclude=frozenset(pool))
            log.info("  tool %s -> %s (%.1fs)", _describe(call), short(r.summary, 220), time.perf_counter() - t_tool)
            if not r.ok:
                stats.failures += 1
            if self.auto_related and call.name == "read_function":
                # multi-file awareness at low cost: the same method in the base class and in sibling classes
                # enters the pool as low-priority neighbours (compressed or dropped first under the budget)
                cands = list({c.id: c for ch, _ in r.chunks for _, c in self.tools.related(ch)
                              if c.id not in pool and c.id not in pinned}.values())
                focus = self._focus(s, pinned) if len(cands) > 3 else set()
                auto = sorted(cands, key=lambda c: -len(focus & set(_WORD.findall(c.text))))[:3]
                for c in auto:
                    pool[c.id] = [c, NEIGHBOUR - 1]
                if auto:
                    hist.append(f"round {s['round']}: related implementations added: {', '.join(c.id for c in auto)}")
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
                missed.append(f"{call.name}: {r.summary}")    # wrong name form or ambiguous
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
        if forced and not s["forced"]:
            log.info("  no new evidence: next round is diagnose-only")
        return {"pool": pool, "pinned": frozenset(pinned), "stats": stats, "trace": trace, "history": hist,
                "test_output": test_output, "forced": forced, "stalls": stalls, "note": note, "calls": [],
                "seen_calls": seen}

    def _focus(self, s: AgentState, pinned) -> set:
        """Words of the bug report and of the failing tests' files: related implementations sharing more of them
        (e.g. the sibling that already reads the environment variable the test sets) are shown first."""
        tests = {c.path for c in self.retriever.chunks if c.id in pinned and _is_test_path(c.path)}
        text = s["bug_report"] + " " + " ".join(c.text for c in self.retriever.chunks if c.path in tests)
        return set(_WORD.findall(text))

    def _verify(self, s: AgentState) -> dict:
        """Test-driven repair: apply the candidate (all its locations) in a copy, run the tests, feed back."""
        d, stats, trace = s["diagnosis"], s["stats"], list(s["trace"])
        kept = s.get("kept") or []
        asked, reasks = s.get("asked") or "", s.get("reasks", 0)
        if kept and asked and reasks < MAX_REASKS and all(any(_same_place(p, k) for k in kept) for p in _places(d)):
            # the model rewrote the part that already works instead of the missing place: no fix check spent
            done = ", ".join(f"{k['file']}::{k['function']}" for k in kept)
            log.info("  the fix only changes the kept place again; asking for %s (no fix check spent)", asked)
            if trace:
                trace[-1] = {**trace[-1], "note": f"only repeated the kept change; asked for {asked}"}
            note = (f"{done} is already fixed and KEPT (the failing test passes with it): do not change it again. "
                    f"The remaining place is {asked} (in CONTEXT). Diagnose it now: set file and function to it and "
                    "give its corrected code in fix.")
            best = s.get("best") or {}
            return {"note": note, "trace": trace, "reasks": reasks + 1, "forced": False, "stalls": 0,
                    "extra_rounds": s.get("extra_rounds", 0) + 1,
                    "history": s["history"] + [f"round {s['round']}: diagnose({d.file}::{d.function}) repeated "
                                               "the kept change; not checked"],
                    "diagnosis": best.get("diagnosis", d)}
        if kept:
            merged = _with_kept(d, kept)
            if merged is not d:
                log.info("  adding the kept change(s) %s to this fix",
                         ", ".join(f"{k['file']}::{k['function']}" for k in kept if k not in _places(d)))
            d = merged
        attempts = s["verify_attempts"] + 1
        if self._expired():
            log.warning("time limit of %.0fs reached: keeping the diagnosis without checking it", self.time_limit)
            return {"stop": "timeout", "trace": trace}
        sig = _signature(d)
        checked = dict(s.get("checked") or {})
        t_check = time.perf_counter()
        if sig in checked:                                 # the same fix again: do not rerun the tests
            res = {**checked[sig], "repeat": True}
            res["output"] = ("You proposed exactly the same fix as before, and it fails the same way. Change it: "
                             "read the failing assertion below and fix what it expects.\n" + str(res.get("output", "")))
            log.info("fix check %d: same fix as an earlier attempt -> %s (tests not rerun)", attempts,
                     res.get("reason", "failed"))
        else:
            n = 1 + len(d.locations)
            log.info("fix check %d: applying %d change(s) in %s and running tests...", attempts, n,
                     ", ".join(dict.fromkeys([d.file] + [loc["file"] for loc in d.locations])))
            res = dict(self.verifier(d)) if self.verifier else {}
            checked[sig] = res
            log.info("fix check %d: %s in %.0fs | target test %s | broken %d | related failing %d | dangling %d",
                     attempts, res.get("reason", "?"), time.perf_counter() - t_check,
                     "passes" if res.get("target_passed") else "fails", len(res.get("regressions", [])),
                     len(res.get("related_failing", [])), len(res.get("dangling", [])))
            if not res.get("applied", True):
                log.info("  not applied: %s", short(res.get("output", ""), 300))
        trace[-1] = {**trace[-1], "verification": {k: v for k, v in res.items() if k != "output"}}
        best = s.get("best") or {}
        if not best or _rank(res) > _rank(best["verification"]):
            best = {"diagnosis": d, "verification": res}
        out = {"verification": res, "verify_attempts": attempts, "trace": trace, "best": best, "checked": checked,
               "diagnosis": d}
        if res.get("passed"):
            return {**out, "stop": "verified"}
        left = len(res.get("related_failing", [])) + len(res.get("dangling", []))
        if (res.get("target_passed") and not res.get("regressions") and res.get("applied", True)
                and left < s.get("kept_left", 10 ** 6)):
            kept = _places(d)                              # this part works: keep it, look for the rest
            out["kept_left"] = left                        # a later fix replaces it only if fewer tests still fail
        if attempts <= self.max_verify:
            pool, pinned = dict(s["pool"]), set(s["pinned"])
            added = []
            for loc in (res.get("suggest") or []) if res.get("target_passed") else []:   # where else the defect is
                for c in self.tools.resolve(loc)[:1]:
                    if c.id not in pinned:
                        pinned.add(c.id)
                        pool[c.id] = [c, PIN + stats.tool_calls + 1]
                        added.append(c.id)
            if added:
                log.info("  likely the same defect in: %s (pinned)", ", ".join(added))
            out.update(pool=pool, pinned=frozenset(pinned))
            where = ", ".join(dict.fromkeys([d.file] + [loc["file"] for loc in d.locations]))
            todo = [loc for loc in res.get("suggest") or [] if not any(
                _same_place({"file": loc.split("::")[0], "function": loc.split("::")[-1]}, k) for k in kept)]
            tried = [p for p in _places(d) if not any(_same_place(p, k) for k in kept)]   # beyond the kept fix
            if res.get("target_passed") and kept and not todo and tried and res.get("related_failing"):
                again = f"{tried[0]['file']}::{tried[0]['function']}"
                note = (f"Your change to {', '.join(k['file'] + '::' + k['function'] for k in kept)} still makes the "
                        f"failing test pass and stays KEPT, but your change to {again} did not fix its tests (still "
                        f"failing: {', '.join(res['related_failing'][:3])}; the test is in CONTEXT). Diagnose {again} "
                        "again: set file and function to it and give its corrected code in fix.")
                log.info("  kept change still passes; asking for %s again", again)
                out["asked"] = again
            elif res.get("target_passed") and kept and todo:
                # one place at a time: small models rewrite the part that works instead of adding the missing one
                note = (f"Your change to {', '.join(k['file'] + '::' + k['function'] for k in kept)} makes the "
                        f"failing test pass and is KEPT (it is added to your next fix automatically). Related tests "
                        f"still fail because the same defect is also in {todo[0]} (in CONTEXT). Now diagnose "
                        f"{todo[0]}: set file and function to it and give its corrected code in fix.")
                log.info("  keeping the passing change; asking for %s next", todo[0])
                out["asked"] = todo[0]
            elif not res.get("applied", True):
                note = (f"Your fix could not be applied ({short(str(res.get('output', '')), 200)}). Diagnose again "
                        "with the whole corrected function in fix: valid Python, the complete def, and no replace.")
            elif not res.get("target_passed"):
                # target first: small models chase the sibling places before the main fix works
                note = (f"With your fix in {where}, the failing test still fails. FAILING TEST OUTPUT shows the "
                        "assertion that failed and the value your code produced; the test's other assertions are in "
                        "CONTEXT. Correct the fix so that every assertion holds (compare the expected and the actual "
                        "text character by character), then diagnose again. Make this test pass first.")
            else:
                note = (f"Your fix (in {where}) did not pass the checks: {res.get('reason', 'failed')}. The results "
                        "are under FAILING TEST OUTPUT. Gather evidence if needed (read_function, get_related, "
                        "get_callers), then diagnose again with ALL the changes: fix plus other_changes.")
            hist = s["history"] + [f"round {s['round']}: diagnose({d.file}::{d.function}"
                                   f"{' +' + str(len(d.locations)) if d.locations else ''}) -> fix check "
                                   f"{res.get('reason', 'failed')}"]
            feedback = str(res.get("output", ""))[-1500:]
            # two more rounds per failed check: one to look, one to re-diagnose; tools allowed again
            return {**out, "note": note, "history": hist, "test_output": feedback or s["test_output"],
                    "stats": stats, "extra_rounds": s.get("extra_rounds", 0) + 2,
                    "forced": False, "stalls": 0, "kept": kept}
        return {**out, "stop": "verify_failed"}

    # ------------------------------------------------------------------ routing
    def _after_plan(self, s: AgentState) -> str:
        if s.get("stop"):
            return "end"
        calls = s.get("calls") or []
        if not calls:
            return "context"                              # invalid reply: retry with a note
        if calls[0].name == "diagnose":
            if self.verifier and s["diagnosis"].valid:
                return "verify"                         # a diagnosis without fix code gets "no_fix" feedback
            return "end"
        return "act"

    def _after_verify(self, s: AgentState) -> str:
        return "end" if s.get("stop") else "context"

    # ------------------------------------------------------------------ public
    def _elapsed(self) -> float:
        return time.perf_counter() - self._t0

    def _expired(self) -> bool:
        return bool(self.time_limit) and self._elapsed() > self.time_limit

    def run(self, bug_report: str) -> DebugResult:
        t0 = self._t0 = time.perf_counter()
        limit = 8 * (self.max_rounds + 2 * self.max_verify + MAX_REASKS) + 20
        final: dict[str, Any] = self.graph.invoke({"bug_report": bug_report}, {"recursion_limit": limit})
        stats: RunStats = final["stats"]
        stats.wall_s = time.perf_counter() - t0
        d: Diagnosis = final.get("diagnosis") or Diagnosis()
        verification = final.get("verification", {})
        stop = final.get("stop") or ""
        best = final.get("best") or {}
        if stop != "verified" and best and (not verification or _rank(best["verification"]) > _rank(verification)):
            d, verification = best["diagnosis"], best["verification"]      # keep the best checked candidate
        if not stop:
            stop = "diagnosed" if d.valid else "round_limit"
            if d.valid and final.get("forced"):
                stop = "forced_diagnosis"
        log.info("done in %.0fs: %s | %s::%s%s | %d LLM calls, %d tool calls, %d tokens", stats.wall_s, stop,
                 d.file or "-", d.function or "-", f" (+{len(d.locations)} more places)" if d.locations else "",
                 stats.llm_calls, stats.tool_calls, stats.total_tokens)
        return DebugResult(d, stats, final.get("context_items", []), final.get("trace", []),
                           stop_reason=stop, verification=verification)


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
