"""A caching wrapper for any LLM, to make evaluation runs fast and repeatable.

Model calls dominate evaluation wall time, and at temperature 0 the same prompt
returns the same answer, so caching them is free accuracy-wise and large on speed.
`CachingLLM` wraps any object with the project's `complete(system, user)` and
`chat(messages, tools, tool_choice)` methods and transparently memoizes replies to a
JSON-lines file on disk. A second run of the same evaluation, or a repeated sub-prompt
within one run, hits the cache instead of the network.

Design: this is a pure wrapper. It does not modify GroqLLM, SimulatedLLM, or any core
module; it only sits in front of them, so it is safe to drop into the evaluation path
without changing behavior. Cache keys include the model name, so results from different
models never collide.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading

from ..llm import LLMReply, ToolCall


def _key(model: str, kind: str, payload: dict) -> str:
    """Stable cache key from the model, call kind, and the call payload."""
    blob = json.dumps({"model": model, "kind": kind, "payload": payload},
                      sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _reply_to_dict(reply: LLMReply) -> dict:
    return {
        "text": reply.text,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "latency_s": reply.latency_s,
        "tool_calls": [{"name": t.name, "arguments": t.arguments, "id": t.id}
                       for t in (reply.tool_calls or [])],
    }


def _dict_to_reply(d: dict) -> LLMReply:
    return LLMReply(
        text=d["text"],
        input_tokens=d.get("input_tokens", 0),
        output_tokens=d.get("output_tokens", 0),
        latency_s=d.get("latency_s", 0.0),
        tool_calls=[ToolCall(t["name"], t.get("arguments", {}), t.get("id", ""))
                    for t in d.get("tool_calls", [])],
    )


class CachingLLM:
    """Wrap an LLM so identical calls are served from a JSON-lines cache on disk.

    The wrapped object must expose `complete` and/or `chat` and (optionally) a `model`
    attribute. Cached replies are tagged `cached=True` only in the in-memory hit counter;
    the returned LLMReply is identical in shape to a live one, so callers cannot tell the
    difference. A cached reply reports its original token counts but is not re-billed.
    """

    def __init__(self, inner, path: str | None = None):
        self.inner = inner
        self.model = getattr(inner, "model", "unknown")
        self.path = path
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        if path and os.path.exists(path):
            self._load(path)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if "key" in rec and "reply" in rec:
                    self._store[rec["key"]] = rec["reply"]

    def _append(self, key: str, reply: dict) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "reply": reply}) + "\n")

    def _lookup_or_call(self, kind: str, payload: dict, call):
        key = _key(self.model, kind, payload)
        with self._lock:
            cached = self._store.get(key)
        if cached is not None:
            self.hits += 1
            return _dict_to_reply(cached)
        reply = call()
        rec = _reply_to_dict(reply)
        with self._lock:
            self._store[key] = rec
            self.misses += 1
            self._append(key, rec)
        return reply

    def complete(self, system: str, user: str) -> LLMReply:
        payload = {"system": system, "user": user}
        return self._lookup_or_call("complete", payload, lambda: self.inner.complete(system, user))

    def chat(self, messages: list, tools: list | None = None, tool_choice=None) -> LLMReply:
        payload = {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        return self._lookup_or_call("chat", payload, lambda: self.inner.chat(messages, tools, tool_choice))

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
                "entries": len(self._store)}
