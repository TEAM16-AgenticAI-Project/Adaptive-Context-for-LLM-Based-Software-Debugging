"""MCP server exposing the Adaptive Context debugger as tools for other agents.

This is a thin, additive wrapper over the existing debugger (`AdaptiveDebugger`,
`StaticRAG`) and the same build steps the CLI uses. It does not change any core
behavior; it only lets an MCP-aware agent (Claude Desktop, Cursor, Kiro, and so on)
call the debugger as a tool.

Tools:
  diagnose_bug   given a repository and a bug report, return the root cause, the
                 relevant file and function, the evidence the model read, a suggested
                 fix, and the round-by-round trace.
  localize_fault the lighter localization-only path: just the file and function, for
                 agents that want to find the place and do their own repair.

Backend: defaults to the offline simulator so it runs with no API key. If GROQ_API_KEY
is set (and backend="groq" is requested), it uses the real model.

Run (stdio transport, the usual MCP setup):
    python -m adaptive_context.mcp_server
"""
from __future__ import annotations

import os

from .adaptive import AdaptiveDebugger
from .baseline import StaticRAG
from .chunking import chunk_repo, load_repo_dir
from .retrieval import BM25Retriever
from .sim import SimulatedLLM

def _load_fastmcp():
    """Import FastMCP, tolerating the 1.x and 2.x package layouts.

    FastMCP lives at `mcp.server.fastmcp.FastMCP` in the 1.x SDK and in the separate
    `fastmcp` distribution for 2.x. Try both so the server runs wherever MCP is
    correctly installed, without pinning the caller to one layout.
    """
    try:
        from mcp.server.fastmcp import FastMCP
        return FastMCP
    except ImportError:
        pass
    try:
        from fastmcp import FastMCP
        return FastMCP
    except ImportError as exc:
        raise ImportError(
            "FastMCP is required to run the MCP server. Install a compatible MCP SDK, "
            "for example `pip install 'mcp[cli]'` (1.x) or `pip install fastmcp` (2.x), "
            "on Python 3.10-3.12.") from exc


# The server object is created lazily in main() so importing this module (for example
# to test the tool functions) does not require the transport library to be installed.
mcp = None


def _get_server():
    global mcp
    if mcp is None:
        FastMCP = _load_fastmcp()
        server = FastMCP("adaptive-context-debugger")
        server.tool()(diagnose_bug)
        server.tool()(localize_fault)
        mcp = server
    return mcp


def _build_llm(backend: str, model: str | None):
    """Return an LLM backend. 'sim' needs no key; 'groq' needs GROQ_API_KEY."""
    if backend == "groq":
        if not os.environ.get("GROQ_API_KEY"):
            raise ValueError("backend='groq' requires GROQ_API_KEY in the environment")
        from .llm import GroqLLM
        return GroqLLM(model=model)
    return SimulatedLLM()


def _build_system(mode: str, repo_path: str, backend: str, model: str | None,
                  top_k: int, rounds: int, budget: int, prefetch_depth: int):
    """Chunk the repo, build a retriever and llm, and return the requested debugger.

    This mirrors the CLI's wiring so the MCP path behaves like every other entry point.
    """
    files = load_repo_dir(repo_path)
    chunks = chunk_repo(files)
    if not chunks:
        raise ValueError(f"no Python source found under {repo_path}")
    retriever = BM25Retriever(chunks)
    llm = _build_llm(backend, model)
    if mode == "static":
        return StaticRAG(retriever, llm, top_k=top_k)
    if mode == "adaptive":
        return AdaptiveDebugger(retriever, llm, initial_k=top_k, max_rounds=rounds,
                                token_budget=budget, prefetch_depth=prefetch_depth)
    raise ValueError(f"mode must be 'static' or 'adaptive', got {mode!r}")


def _result_to_dict(result) -> dict:
    """Flatten a DebugResult into a JSON-serializable dict for the tool response."""
    d = result.diagnosis
    s = result.stats
    return {
        "diagnosis": {
            "file": d.file,
            "function": d.function,
            "cause": d.cause,
            "suggested_fix": d.fix,
            "confidence": d.confidence,
            "valid": bool(d.valid),
        },
        "stop_reason": result.stop_reason,
        "evidence_read": [{"chunk_id": cid, "level": lvl} for cid, lvl in result.context_items],
        "trace": result.trace,
        "stats": {
            "llm_calls": s.llm_calls,
            "retrieval_calls": s.retrieval_calls,
            "rounds": s.rounds,
            "total_tokens": s.total_tokens,
            "wall_s": s.wall_s,
            "failures": s.failures,
        },
    }


def diagnose_bug(repo_path: str, bug_report: str, mode: str = "adaptive",
                 backend: str = "sim", model: str | None = None,
                 top_k: int = 3, rounds: int = 6, token_budget: int = 3000,
                 prefetch_depth: int = 2) -> dict:
    """Diagnose a bug in a Python repository.

    Args:
        repo_path: path to a local Python repository to analyze.
        bug_report: the bug description, often a failing-test message or stack trace.
        mode: 'adaptive' (multi-round retrieval, default) or 'static' (one-shot baseline).
        backend: 'sim' (offline, default) or 'groq' (needs GROQ_API_KEY).
        model: optional model id for the groq backend.
        top_k: initial number of chunks to retrieve.
        rounds: max adaptive rounds.
        token_budget: token budget for the assembled context.
        prefetch_depth: call-graph prefetch depth (adaptive only); 0 disables it.

    Returns:
        A dict with the diagnosis (file, function, cause, suggested_fix, confidence),
        the evidence the model actually read, the round-by-round trace, and run stats.
    """
    system = _build_system(mode, repo_path, backend, model, top_k, rounds,
                           token_budget, prefetch_depth)
    result = system.run(bug_report)
    return _result_to_dict(result)


def localize_fault(repo_path: str, bug_report: str, backend: str = "sim",
                   model: str | None = None, top_k: int = 3, rounds: int = 6,
                   token_budget: int = 3000, prefetch_depth: int = 2) -> dict:
    """Localize a fault without proposing a fix: return just the file and function.

    This is the lighter path for an agent that wants to find the place and run its own
    repair. It uses the adaptive debugger and returns only the localization fields plus
    the evidence it read.
    """
    system = _build_system("adaptive", repo_path, backend, model, top_k, rounds,
                           token_budget, prefetch_depth)
    result = system.run(bug_report)
    d = result.diagnosis
    return {
        "file": d.file,
        "function": d.function,
        "valid": bool(d.valid),
        "evidence_read": [{"chunk_id": cid, "level": lvl} for cid, lvl in result.context_items],
        "stop_reason": result.stop_reason,
    }


def main():
    """Run the MCP server over stdio (the standard MCP transport)."""
    _get_server().run()


if __name__ == "__main__":
    main()
