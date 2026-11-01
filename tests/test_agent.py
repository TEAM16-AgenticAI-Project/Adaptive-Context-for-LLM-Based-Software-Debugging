"""LangGraph agent, tools as native function calls, and the Groq tool-call parsing."""
import json
import os

import pytest

from adaptive_context.chunking import chunk_repo, load_repo_dir
from adaptive_context.llm import LLMReply, ToolCall, _parse_tool_calls
from adaptive_context.retrieval import BM25Retriever
from adaptive_context.tools import DIAGNOSE_SCHEMA, TOOL_SCHEMAS, Tools

PAYMENT = os.path.join(os.path.dirname(__file__), "..", "examples", "payment_bug")

FIXED_CHECKOUT = '''def checkout(price, discount):
    """Return the amount the customer pays."""
    return apply_discount(price, discount)'''
WRONG_CHECKOUT = FIXED_CHECKOUT.replace("apply_discount(price, discount)", "apply_discount(price, 0)")
DIAG = {"file": "payment.py", "function": "checkout", "cause": "the discounted value is discarded",
        "fix": FIXED_CHECKOUT, "confidence": "high"}


class ToolScript:
    """Scripted LLM speaking native tool calls (or text), recording what it was offered each round."""

    def __init__(self, steps):
        self.steps, self.seen = list(steps), []

    def chat(self, messages, tools=None, tool_choice=None):
        self.seen.append({"tools": [t["function"]["name"] for t in tools or []], "choice": tool_choice,
                          "prompt": messages[-1]["content"]})
        step = self.steps.pop(0) if self.steps else ToolCall("diagnose", {})
        if isinstance(step, str):
            return LLMReply(step, 10, 5, 0.0)
        return LLMReply("", 10, 5, 0.0, step if isinstance(step, list) else [step])


def _retriever(files=None):
    return BM25Retriever(chunk_repo(files if files is not None else load_repo_dir(PAYMENT)))


def _agent(script, **kw):
    pytest.importorskip("langgraph")
    from adaptive_context import agent_graph as lg
    retr = _retriever()
    return lg.LangGraphAgent(retr, script, tools=Tools(retr, PAYMENT, test_path="test_payment.py"), **kw)


# ---------------------------------------------------------------- llm
def test_groq_tool_calls_are_parsed_safely():
    msg = {"tool_calls": [{"id": "c1", "function": {"name": "read_function", "arguments": '{"name": "checkout"}'}},
                          {"id": "c2", "function": {"name": "diagnose", "arguments": "{not json"}}]}
    calls = _parse_tool_calls(msg)
    assert calls[0] == ToolCall("read_function", {"name": "checkout"}, "c1")
    assert calls[1].name == "diagnose" and "__invalid_json__" in calls[1].arguments
    assert _parse_tool_calls({"content": "hi"}) == []


# ---------------------------------------------------------------- tools
def test_schemas_cover_the_six_tools_and_diagnose():
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert names == ["search_code", "grep_symbol", "read_function", "get_callers", "get_callees", "run_failing_test"]
    assert DIAGNOSE_SCHEMA["function"]["parameters"]["required"] == ["file", "function", "cause", "fix", "confidence"]
    assert "run_failing_test" not in [s["function"]["name"] for s in Tools(_retriever()).schemas()]


def test_tool_execute_validates_and_pins():
    files = {"pkg/core.py": "def run(c):\n    return helper(c)\n\n\ndef helper(c):\n    return c\n"}
    t = Tools(_retriever(files))
    assert not t.execute("delete_repo", {}).ok
    assert not t.execute("read_function", {"name": ""}).ok
    assert not t.execute("run_failing_test", {}).ok                   # no test configured
    res = t.execute("read_function", {"name": "helper"})
    assert res.ok and res.pin and [c.name for c, _ in res.chunks] == ["helper"]
    assert [c.name for c, _ in t.execute("get_callees", {"name": "run"}).chunks] == ["helper"]
    assert [c.name for c, _ in t.execute("get_callers", {"name": "helper"}).chunks] == ["run"]


def test_ambiguous_name_adds_nothing_and_asks_for_module_path():
    files = {f"pkg/r{i}.py": f"def match(c):\n    return c == {i}\n" for i in range(9)}
    res = Tools(_retriever(files)).execute("read_function", {"name": "match"})
    assert res.ok and res.chunks == [] and "ambiguous" in res.summary and "module path" in res.summary


def test_module_paths_and_pytest_ids_resolve_one_function():
    files = {f"pkg/r{i}.py": f"def match(c):\n    return c == {i}\n" for i in range(9)}
    files["pkg/sub/__init__.py"] = "class Rule:\n    def match(self, c):\n        return False\n"
    files["tests/test_r.py"] = "class TestR:\n    def test_match(self):\n        assert True\n"
    t = Tools(_retriever(files))
    for name, want in (("pkg.r3.match", "pkg/r3.py::match"), ("pkg/r5.py::match", "pkg/r5.py::match"),
                       ("pkg.sub.Rule.match", "pkg/sub/__init__.py::Rule.match"),
                       ("tests/test_r.py::TestR::test_match", "tests/test_r.py::TestR.test_match")):
        res = t.execute("read_function", {"name": name})
        assert [c.id for c, _ in res.chunks] == [want], name


# ---------------------------------------------------------------- agent graph
def test_agent_calls_tools_then_diagnoses():
    script = ToolScript([ToolCall("run_failing_test", {}), ToolCall("read_function", {"name": "apply_discount"}),
                         ToolCall("diagnose", DIAG)])
    res = _agent(script).run("checkout(100, 0.20) returns 100 instead of 80")
    assert res.stop_reason == "diagnosed" and res.diagnosis.function == "checkout"
    assert res.stats.tool_calls == 2 and res.stats.llm_calls == 3 and res.stats.wall_s > 0
    assert "run_failing_test" in script.seen[0]["tools"] and "diagnose" in script.seen[0]["tools"]
    assert "assert 100 == 80" in script.seen[1]["prompt"]               # the traceback reached the next prompt
    assert any(cid == "discount.py::apply_discount" for cid, _ in res.context_items)
    assert [t.get("calls", [{}])[0].get("name") for t in res.trace][:2] == ["run_failing_test", "read_function"]


def test_agent_forces_diagnosis_on_last_round():
    script = ToolScript([ToolCall("search_code", {"query": "discount"}), ToolCall("diagnose", DIAG)])
    res = _agent(script, max_rounds=2).run("checkout returns the wrong amount")
    assert script.seen[1]["tools"] == ["diagnose"]
    assert script.seen[1]["choice"] == {"type": "function", "function": {"name": "diagnose"}}
    assert res.diagnosis.valid and res.stats.rounds == 2


def test_agent_recovers_from_invalid_replies_then_gives_up():
    script = ToolScript(["not json", ToolCall("diagnose", {"file": "payment.py"}), ToolCall("diagnose", DIAG)])
    res = _agent(script).run("checkout returns the wrong amount")
    assert res.stop_reason == "diagnosed" and res.stats.failures == 2
    res2 = _agent(ToolScript(["?", '{"action":"diagnose"}', "?", "?"])).run("checkout returns the wrong amount")
    assert res2.stop_reason == "invalid_exhausted" and not res2.diagnosis.valid     # empty diagnosis never counts


def test_agent_accepts_legacy_json_text():
    script = ToolScript(['{"action":"request","symbols":["apply_discount"],"queries":[]}',
                         json.dumps({"action": "diagnose", **DIAG})])
    res = _agent(script).run("checkout returns the wrong amount")
    assert res.diagnosis.valid and res.stats.tool_calls == 1


def test_agent_verifier_feeds_back_a_failing_fix_once():
    seen = []

    def verifier(d):                                                  # stands in for fixcheck.check_fix
        seen.append(d.fix)
        ok = d.fix == FIXED_CHECKOUT
        return {"applied": True, "mode": "function", "passed": ok, "reason": "tests_pass" if ok else "tests_fail",
                "output": "" if ok else "E   assert 100 == 80.0"}

    script = ToolScript([ToolCall("diagnose", {**DIAG, "fix": WRONG_CHECKOUT}), ToolCall("diagnose", DIAG)])
    res = _agent(script, verifier=verifier).run("checkout returns the wrong amount")
    assert len(seen) == 2 and res.stop_reason == "verified" and res.verification["passed"]
    assert "did not pass" in script.seen[1]["prompt"]

    always_fails = ToolScript([ToolCall("diagnose", {**DIAG, "fix": WRONG_CHECKOUT})] * 3)
    res2 = _agent(always_fails, verifier=verifier).run("checkout returns the wrong amount")
    assert res2.stop_reason == "verify_failed"


def test_agent_pins_the_failing_test_named_in_the_report():
    res = _agent(ToolScript([ToolCall("diagnose", DIAG)])).run(
        "The test `test_payment.py::test_checkout_with_discount` fails.")
    assert ("test_payment.py::test_checkout_with_discount", 0) in res.context_items


def test_simulator_drives_the_graph_offline():
    pytest.importorskip("langgraph")
    from adaptive_context import agent_graph as lg
    from adaptive_context.bench.generate import generate_case
    from adaptive_context.sim import SimulatedLLM
    solved = 0
    for seed in range(20):
        case = generate_case(seed)
        res = lg.LangGraphAgent(_retriever(case.files), lg.SimToolLLM(SimulatedLLM())).run(case.bug_report)
        solved += res.diagnosis.function == case.truth.function
    assert solved >= 18
