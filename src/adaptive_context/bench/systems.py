"""System factory: build any named debugging system from shared components.

Every system exposes the same `run(bug_report: str) -> DebugResult` contract, so the
evaluation runner can score them the same way. The names are fixed by the team plan:

    static_k3       Static RAG, one retrieval, Top-3, one LLM call (the Milestone 1 baseline)
    static_k10      Static RAG with a wider Top-10 (a second, wider baseline)
    adaptive        Adaptive loop, bounded rounds, compression, ordering, token budget
    adaptive_graph  Adaptive loop plus call-graph prefetch (callees of requested symbols)
    agent           LangGraph agent with tools (imported lazily; needs the agent module)
    agent_verify    The agent plus fix verification after it proposes a change

The static and adaptive systems are always available. The agent systems are imported
lazily so this module, and every other system, work before the agent module has landed.
"""
from __future__ import annotations

from ..adaptive import AdaptiveDebugger
from ..baseline import StaticRAG

SYSTEM_NAMES = ("static_k3", "static_k10", "adaptive", "adaptive_graph", "agent", "agent_verify")

# The two static configurations are baselines; the proposed system is the adaptive family.
BASELINE_NAMES = ("static_k3", "static_k10")


def _default_cfg() -> dict:
    """The default knobs a system reads from cfg; callers may override any of these."""
    return {
        "top_k": 3,
        "wide_k": 10,
        "budget": 3000,
        "rounds": 6,
        "prefetch_depth": 2,
        "budget_scope": "prompt",
    }


def make_system(name: str, retriever, llm, cfg: dict | None = None, tools=None, verifier=None):
    """Construct the system called `name` from the shared retriever, llm, and config.

    tools and verifier are only used by the agent systems. Passing them to a static or
    adaptive system is harmless and ignored, so one call site can build every system.
    """
    if name not in SYSTEM_NAMES:
        raise ValueError(f"unknown system '{name}'; valid names: {', '.join(SYSTEM_NAMES)}")
    c = _default_cfg()
    if cfg:
        c.update(cfg)

    if name == "static_k3":
        return StaticRAG(retriever, llm, top_k=c["top_k"])
    if name == "static_k10":
        return StaticRAG(retriever, llm, top_k=c["wide_k"])
    if name == "adaptive":
        return AdaptiveDebugger(retriever, llm, initial_k=c["top_k"], max_rounds=c["rounds"],
                                token_budget=c["budget"], prefetch_depth=0,
                                budget_scope=c["budget_scope"])
    if name == "adaptive_graph":
        return AdaptiveDebugger(retriever, llm, initial_k=c["top_k"], max_rounds=c["rounds"],
                                token_budget=c["budget"], prefetch_depth=c["prefetch_depth"],
                                budget_scope=c["budget_scope"])

    # Agent systems: import lazily so the rest of this module works without the agent.
    if name in ("agent", "agent_verify"):
        try:
            from ..agent import AgentDebugger
        except ImportError as exc:  # the agent module (or langgraph) is not available yet
            raise RuntimeError(
                f"system '{name}' needs the agent module, which is not available: {exc}") from exc
        use_verifier = verifier if name == "agent_verify" else None
        return AgentDebugger(retriever, llm, tools=tools, verifier=use_verifier,
                             max_rounds=c["rounds"], token_budget=c["budget"])
    raise AssertionError("unreachable")  # pragma: no cover


def available_systems(include_agent: bool = True) -> list:
    """Return the system names that can be built in this environment.

    The agent systems are included only if the agent module imports, so a caller can
    evaluate every buildable system without hitting a missing-dependency error.
    """
    names = [n for n in SYSTEM_NAMES if n not in ("agent", "agent_verify")]
    if include_agent:
        try:
            from .. import agent  # noqa: F401  (probe only)
            names += ["agent", "agent_verify"]
        except ImportError:
            pass
    return names


def result_row(name: str, res) -> dict:
    """Flatten a DebugResult into one metrics row for a results table.

    This reads only the shared DebugResult / RunStats fields, so it works for every
    system. Scoring against gold labels is done separately by cases.score_diagnosis.
    """
    s = res.stats
    d = res.diagnosis
    return {
        "system": name,
        "role": "baseline" if name in BASELINE_NAMES else "proposed",
        "valid": bool(d.valid),
        "file": d.file,
        "function": d.function,
        "confidence": d.confidence,
        "stop_reason": res.stop_reason,
        "llm_calls": s.llm_calls,
        "tool_calls": s.tool_calls,
        "retrieval_calls": s.retrieval_calls,
        "rounds": s.rounds,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "total_tokens": s.total_tokens,
        "wall_s": s.wall_s,
        "latency_s": s.latency_s,
        "failures": s.failures,
    }
