# MCP server: Adaptive Context Debugger as a tool for other agents

The debugger is exposed as an MCP server so any MCP-aware agent (Claude Desktop, Cursor,
Kiro, and so on) can call it as a tool. The server is a thin, additive wrapper over the
existing debugger; it does not change any core behavior.

## Tools

- `diagnose_bug(repo_path, bug_report, mode="adaptive", backend="sim", model=None,
  top_k=3, rounds=6, token_budget=3000, prefetch_depth=2)`
  Returns the diagnosis (file, function, cause, suggested_fix, confidence), the evidence
  the model actually read, the round-by-round trace, and run stats.
- `localize_fault(repo_path, bug_report, ...)`
  The lighter localization-only path: returns just the file and function plus the
  evidence read, for an agent that wants to find the place and run its own repair.

Backend: `sim` (offline simulator, default, no API key) or `groq` (needs `GROQ_API_KEY`).

## Requirements (verified)

Run on Python 3.10 to 3.12 with the MCP 1.x SDK. The current MCP 2.x renamed the server
class, so pin `mcp<2`:

    pip install "mcp<2"

The server import is version-resilient (it tries the 1.x and 2.x layouts), but it was
verified end to end with `mcp<2` on Python 3.12: an MCP client connected over stdio,
listed the two tools, and called `diagnose_bug` successfully.

## Run it

    python -m adaptive_context.mcp_server

## Register it with an MCP host

Example MCP client config (adjust the path and Python to your environment):

```json
{
  "mcpServers": {
    "adaptive-context-debugger": {
      "command": "python",
      "args": ["-m", "adaptive_context.mcp_server"],
      "env": {
        "GROQ_API_KEY": "set-this-only-if-you-want-the-live-model"
      }
    }
  }
}
```

With no `GROQ_API_KEY`, the server runs the offline simulator, which is enough to
demonstrate the tool contract and the adaptive-versus-static behavior. Set the key to run
the live model.
