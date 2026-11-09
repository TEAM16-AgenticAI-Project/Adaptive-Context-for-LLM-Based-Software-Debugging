from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    id: str            # "<path>::<qualified name>"
    path: str
    name: str          # qualified, e.g. "Cart.total" or "checkout"
    start: int
    end: int
    text: str
    kind: str = "function"   # function | method | module | window
    calls: tuple = ()        # names called inside this chunk

    @property
    def short_name(self) -> str:
        return self.name.split(".")[-1]


@dataclass
class Diagnosis:
    file: str = ""
    function: str = ""
    cause: str = ""
    fix: str = ""
    confidence: str = ""
    valid: bool = False
    # further places the fix needs, for bugs that span several functions or files: [{file, function, fix[, replace]}]
    locations: list = field(default_factory=list)
    replace: str = ""          # optional: the exact current lines that `fix` replaces (instead of a whole function)


@dataclass
class RunStats:
    llm_calls: int = 0
    retrieval_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0     # time spent inside LLM calls (as reported by the client)
    rounds: int = 0
    failures: int = 0          # unparseable replies / LLM errors / invalid tool calls
    tool_calls: int = 0        # tool executions requested by the model (agent mode)
    wall_s: float = 0.0        # measured end-to-end time of `run()` (perf_counter)
    over_budget_rounds: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Why a run stopped. Every debugger sets exactly one of these.
STOP_REASONS = ("diagnosed", "forced_diagnosis", "round_limit", "invalid_exhausted", "llm_error", "timeout",
                "verified", "verify_failed")


@dataclass
class DebugResult:
    diagnosis: Diagnosis
    stats: RunStats
    context_items: list = field(default_factory=list)   # [(chunk_id, level)] in the last prompt
    trace: list = field(default_factory=list)           # one dict per round
    stop_reason: str = ""
    verification: dict = field(default_factory=dict)    # fix check result, when one was run
