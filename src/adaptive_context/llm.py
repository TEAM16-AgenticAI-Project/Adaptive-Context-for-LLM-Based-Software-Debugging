"""LLM clients. GroqLLM talks to the OpenAI-compatible Groq endpoint with the standard library only.

Two call styles:
  * `complete(system, user)` -> plain text (used by the static baseline and the plain-Python adaptive loop)
  * `chat(messages, tools, tool_choice)` -> text and/or native function calls (used by the LangGraph agent)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .logs import short

log = logging.getLogger(__name__)
MAX_WAIT = 60            # never sleep longer than this for one retry (Groq may ask for minutes on a daily limit)
MAX_CALL_SECONDS = 300   # give up on one LLM call (all retries included) after this long


class LLMError(RuntimeError):
    pass


class ToolCallRejected(LLMError):
    """The provider refused the model's tool call (e.g. Groq `tool_use_failed`: a tool that was not offered, or
    arguments that do not match the schema). Recoverable: the agent treats it as an invalid reply."""

    def __init__(self, message: str, generation: str = ""):
        super().__init__(message)
        self.generation = generation


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class LLMReply:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    tool_calls: list = field(default_factory=list)   # [ToolCall]
    rejected: str = ""                                 # provider's reason when it refused the tool call


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_tool_calls(message: dict) -> list:
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            args = {"__invalid_json__": str(raw)[:500]}
        out.append(ToolCall(str(fn.get("name", "")), args if isinstance(args, dict) else {}, str(tc.get("id", ""))))
    return out


def _error_body(raw: bytes) -> dict:
    try:
        err = json.loads(raw).get("error", {})
        return err if isinstance(err, dict) else {}
    except (ValueError, AttributeError):
        return {}


class GroqLLM:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None, api_key: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 2048, max_retries: int = 6, timeout: float = 60.0,
                 reasoning_effort: str | None = None):
        load_dotenv()
        self.model = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise LLMError("GROQ_API_KEY missing (set it in .env)")
        self.temperature, self.max_tokens = temperature, max_tokens
        self.max_retries, self.timeout = max_retries, timeout
        # gpt-oss models spend part of max_tokens on hidden reasoning; "low" keeps answers from being cut off.
        self.reasoning_effort = reasoning_effort or os.environ.get("GROQ_REASONING_EFFORT", "low")

    def _post(self, payload: dict) -> tuple:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                   "User-Agent": "adaptive-context/0.2"}
        sent_effort = "gpt-oss" in self.model and bool(self.reasoning_effort)
        start = time.time()
        for attempt in range(self.max_retries):
            body = dict(payload)
            if sent_effort:
                body["reasoning_effort"] = self.reasoning_effort
            t0 = time.time()
            try:
                req = urllib.request.Request(self.URL, data=json.dumps(body).encode(), headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                return data, time.time() - t0
            except urllib.error.HTTPError as e:
                raw = e.read()
                detail = raw[:300]
                if e.code == 400 and (b"tool_use_failed" in raw or b"Tool call validation failed" in raw):
                    err = _error_body(raw)
                    raise ToolCallRejected(str(err.get("message", detail))[:300],
                                           str(err.get("failed_generation", ""))[:2000]) from e
                if e.code == 400 and sent_effort and b"reasoning" in detail:
                    sent_effort = False              # model/endpoint does not accept the parameter: retry without it
                    continue
                reason = short(_error_body(raw).get("message", detail), 200)
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    asked = float(e.headers.get("Retry-After") or 0) or min(2 ** attempt, 30)
                    wait = min(asked, MAX_WAIT)
                    if time.time() - start + wait > MAX_CALL_SECONDS:
                        raise LLMError(f"HTTP {e.code} after {time.time() - start:.0f}s of retries: {reason}") from e
                    log.warning("LLM HTTP %s (attempt %d/%d): %s -> waiting %.0fs%s", e.code, attempt + 1,
                                self.max_retries, reason, wait, f" (server asked {asked:.0f}s)" if asked > wait else "")
                    time.sleep(wait)
                    continue
                log.error("LLM HTTP %s: %s", e.code, reason)
                raise LLMError(f"HTTP {e.code}: {reason}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < self.max_retries - 1 and time.time() - start < MAX_CALL_SECONDS:
                    wait = min(2 ** attempt, 30)
                    log.warning("LLM network error (attempt %d/%d): %s -> retrying in %ds", attempt + 1,
                                self.max_retries, short(e, 160), wait)
                    time.sleep(wait)
                    continue
                raise LLMError(str(e)) from e
        raise LLMError("retries exhausted")

    def complete(self, system: str, user: str) -> LLMReply:
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def chat(self, messages: list, tools: list | None = None, tool_choice=None) -> LLMReply:
        payload = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                   "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        t0 = time.time()
        try:
            data, latency = self._post(payload)
        except ToolCallRejected as e:
            # The refused generation often still holds a usable answer (e.g. a diagnose call in text form).
            return LLMReply(e.generation, 0, 0, time.time() - t0, [], rejected=str(e))
        usage = data.get("usage", {})
        message = (data.get("choices") or [{}])[0].get("message") or {}
        return LLMReply(message.get("content") or "", usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0), latency, _parse_tool_calls(message))


def make_llm(kind: str = "groq", *, model: str | None = None, reasoning_effort: str | None = None,
             max_tokens: int = 2048, tools: bool = False):
    """The LLM for a run: "groq" (live, needs GROQ_API_KEY) or "sim" (offline simulator). tools=True wraps the
    simulator for the agent's native tool calls (Groq supports them directly)."""
    if kind == "sim":
        from .sim import SimulatedLLM
        sim = SimulatedLLM()
        if tools:
            from .agent_graph import SimToolLLM
            return SimToolLLM(sim)
        return sim
    return GroqLLM(model=model, reasoning_effort=reasoning_effort, max_tokens=max_tokens)
