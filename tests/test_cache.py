"""Tests for the CachingLLM wrapper (adaptive_context.bench.cache)."""
from __future__ import annotations

from adaptive_context.bench.cache import CachingLLM
from adaptive_context.llm import LLMReply, ToolCall


class CountingLLM:
    """A fake LLM that records how many real calls it received."""
    def __init__(self, model="fake-model"):
        self.model = model
        self.complete_calls = 0
        self.chat_calls = 0

    def complete(self, system, user):
        self.complete_calls += 1
        return LLMReply(text=f"reply:{user}", input_tokens=10, output_tokens=5, latency_s=0.1)

    def chat(self, messages, tools=None, tool_choice=None):
        self.chat_calls += 1
        return LLMReply(text="chat-reply", input_tokens=20, output_tokens=8, latency_s=0.2,
                        tool_calls=[ToolCall("search", {"q": "x"}, "id1")])


def test_identical_complete_calls_hit_cache():
    inner = CountingLLM()
    llm = CachingLLM(inner)
    r1 = llm.complete("sys", "find the bug")
    r2 = llm.complete("sys", "find the bug")
    assert inner.complete_calls == 1          # second call served from cache
    assert r1.text == r2.text
    assert llm.hits == 1 and llm.misses == 1


def test_different_prompts_miss():
    inner = CountingLLM()
    llm = CachingLLM(inner)
    llm.complete("sys", "a")
    llm.complete("sys", "b")
    assert inner.complete_calls == 2
    assert llm.misses == 2 and llm.hits == 0


def test_reply_shape_preserved_including_tool_calls():
    inner = CountingLLM()
    llm = CachingLLM(inner)
    llm.chat([{"role": "user", "content": "hi"}], tools=[{"t": 1}])
    r2 = llm.chat([{"role": "user", "content": "hi"}], tools=[{"t": 1}])
    assert inner.chat_calls == 1
    # the cached reply reconstructs tool calls identically
    assert r2.tool_calls[0].name == "search"
    assert r2.tool_calls[0].arguments == {"q": "x"}
    assert r2.input_tokens == 20 and r2.output_tokens == 8


def test_model_name_separates_caches():
    # The cache key includes the model name, so a shared on-disk cache would not
    # collide across models. Verify keys differ for the same prompt on two models.
    from adaptive_context.bench.cache import _key
    ka = _key("m1", "complete", {"system": "s", "user": "u"})
    kb = _key("m2", "complete", {"system": "s", "user": "u"})
    assert ka != kb


def test_persists_and_reloads_from_disk(tmp_path):
    path = str(tmp_path / "cache.jsonl")
    inner1 = CountingLLM()
    llm1 = CachingLLM(inner1, path=path)
    llm1.complete("sys", "persist me")
    assert inner1.complete_calls == 1

    # a fresh wrapper over a fresh inner loads the cache from disk and does not call
    inner2 = CountingLLM()
    llm2 = CachingLLM(inner2, path=path)
    r = llm2.complete("sys", "persist me")
    assert inner2.complete_calls == 0          # served from the on-disk cache
    assert r.text == "reply:persist me"
    assert llm2.hits == 1


def test_stats_report():
    llm = CachingLLM(CountingLLM())
    llm.complete("s", "x")
    llm.complete("s", "x")
    st = llm.stats()
    assert st["hits"] == 1 and st["misses"] == 1
    assert 0.0 <= st["hit_rate"] <= 1.0
    assert st["entries"] == 1
