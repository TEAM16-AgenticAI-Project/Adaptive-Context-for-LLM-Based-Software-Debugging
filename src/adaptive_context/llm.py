"""LLM clients. GroqLLM talks to the OpenAI-compatible Groq endpoint with the standard library only."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class GroqLLM:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None, api_key: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 800, max_retries: int = 6, timeout: float = 60.0):
        load_dotenv()
        self.model = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise LLMError("GROQ_API_KEY missing (set it in .env)")
        self.temperature, self.max_tokens = temperature, max_tokens
        self.max_retries, self.timeout = max_retries, timeout

    def complete(self, system: str, user: str) -> LLMReply:
        body = json.dumps({"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                   "User-Agent": "adaptive-context/0.1"}
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                req = urllib.request.Request(self.URL, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                usage = data.get("usage", {})
                return LLMReply(data["choices"][0]["message"].get("content") or "",
                                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                time.time() - t0)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    wait = float(e.headers.get("Retry-After") or 0) or min(2 ** attempt, 30)
                    time.sleep(wait)
                    continue
                raise LLMError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise LLMError(str(e)) from e
        raise LLMError("retries exhausted")
