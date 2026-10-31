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


@dataclass
class RunStats:
    llm_calls: int = 0
    retrieval_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    rounds: int = 0
    failures: int = 0          # unparseable replies / LLM errors

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class DebugResult:
    diagnosis: Diagnosis
    stats: RunStats
    context_items: list = field(default_factory=list)   # [(chunk_id, level)] in the last prompt
    trace: list = field(default_factory=list)           # one dict per round
