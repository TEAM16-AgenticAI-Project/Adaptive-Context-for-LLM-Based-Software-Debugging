"""Multi-file debugging: multi-location fixes, the test oracle, related implementations, and the repair loop."""
import os
import sys
import textwrap

import pytest

from adaptive_context.chunking import chunk_repo
from adaptive_context.llm import LLMReply, ToolCall
from adaptive_context.models import Diagnosis
from adaptive_context.multifix import Edit, TestOracle, apply_edits, clean_code, dangling_names, edits_from_diagnosis
from adaptive_context.retrieval import BM25Retriever
from adaptive_context.tools import Tools

# A small two-shell package with the same defect in sibling classes (like thefuck-16), one test file per shell.
SHELLS = {
    "pkg/__init__.py": "",
    "pkg/generic.py": "class Generic:\n    def app_alias(self, name):\n        return 'alias ' + name\n",
    "pkg/bash.py": "from pkg.generic import Generic\n\n\nclass Bash(Generic):\n"
                   "    def app_alias(self, name):\n        return 'alias ' + name + '=fk'\n",
    "pkg/zsh.py": "from pkg.generic import Generic\n\n\nclass Zsh(Generic):\n"
                  "    def app_alias(self, name):\n        return 'alias ' + name + '=fk'\n",
    "pkg/util.py": "def old_name(s):\n    return s.strip()\n",
    "pkg/user.py": "from pkg.util import old_name\n\n\ndef use(s):\n    return old_name(s)\n",
    "tests/__init__.py": "",
    "tests/test_bash.py": "from pkg.bash import Bash\n\n\ndef test_app_alias():\n"
                          "    assert Bash().app_alias('fuck') == \"alias fuck='fk'\"\n",
    "tests/test_zsh.py": "from pkg.zsh import Zsh\n\n\ndef test_app_alias():\n"
                         "    assert Zsh().app_alias('fuck') == \"alias fuck='fk'\"\n",
}
FIXED = "def app_alias(self, name):\n    return 'alias ' + name + \"='fk'\"\n"


@pytest.fixture()
def shells(tmp_path):
    for rel, text in SHELLS.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return str(tmp_path)


def _src(root, rel):
    return open(os.path.join(root, rel)).read()


# ---------------------------------------------------------------- applying edits
def test_apply_edits_replaces_adds_and_inserts(shells):
    res = apply_edits(shells, [
        Edit("pkg/zsh.py", "Zsh.app_alias", FIXED),
        Edit("bash.py", "app_alias", "```python\n" + FIXED + "```"),        # suffix path, bare method, fenced
        Edit("pkg/zsh.py", "Zsh.helper", "def helper(self):\n        return 1\n"),   # new method, odd indent
        Edit("pkg/util.py", "new_name", "def new_name(s):\n    return s.strip()\n"),
        Edit("pkg/util.py", "<module>", "import os\n"),
        Edit("pkg/util.py", "<module>", "old = new_name\n"),
    ])
    assert res.problems == []
    zsh = _src(shells, "pkg/zsh.py")
    assert "\"='fk'\"" in zsh and "    def helper(self):" in zsh
    ns = {}
    exec(compile(zsh.replace("from pkg.generic import Generic", "Generic = object"), "zsh", "exec"), ns)
    assert ns["Zsh"]().helper() == 1 and ns["Zsh"]().app_alias("fuck") == "alias fuck='fk'"
    assert "\"='fk'\"" in _src(shells, "pkg/bash.py")
    util = _src(shells, "pkg/util.py")
    assert util.startswith("import os\n") and util.rstrip().endswith("old = new_name")       # alias after the def


def test_replace_edits_change_only_the_given_lines(shells):
    res = apply_edits(shells, [
        Edit("pkg/zsh.py", "Zsh.app_alias", "return 'alias ' + name + \"='fk'\"", old="return 'alias ' + name + '=fk'"),
        Edit("pkg/bash.py", "app_alias", "x = 1\nreturn 'alias ' + name + \"='fk'\"",
             old="  return 'alias ' + name + '=fk'  \n"),                    # indentation and spaces ignored
    ])
    assert res.problems == []
    assert "        return 'alias ' + name + \"='fk'\"" in _src(shells, "pkg/zsh.py")
    assert "        x = 1\n        return 'alias ' + name + \"='fk'\"" in _src(shells, "pkg/bash.py")
    miss = apply_edits(shells, [Edit("pkg/zsh.py", "Zsh.app_alias", "pass", old="return nothing_like_this")])
    assert miss.problems and "not found" in miss.problems[0]
    d = Diagnosis("pkg/zsh.py", "Zsh.app_alias", "c", "new", "high", True,
                  [{"file": "pkg/bash.py", "function": "Bash.app_alias", "fix": "b", "replace": "a"}], replace="old")
    assert [(e.old, e.code) for e in edits_from_diagnosis(d)] == [("old", "new"), ("a", "b")]


def test_apply_edits_reports_problems(shells):
    res = apply_edits(shells, [Edit("nope.py", "f", "def f():\n    pass\n"),
                               Edit("pkg/zsh.py", "Zsh.app_alias", "zzz_unrelated_call(1, 2, 3)"),
                               Edit("pkg/zsh.py", "Zsh.new_one", "x = 1"),
                               Edit("pkg/zsh.py", "Missing.m", "def m(self):\n    pass\n")])
    assert any("file not found" in p for p in res.problems)
    assert any("no similar line" in p for p in res.problems)              # fixcheck's single-line rule
    assert any("give the complete new function" in p for p in res.problems)
    assert any("class Missing not found" in p for p in res.problems)
    line = apply_edits(shells, [Edit("pkg/bash.py", "Bash.app_alias", "return 'alias ' + name + \"='fk'\"")])
    assert line.problems == [] and "\"='fk'\"" in _src(shells, "pkg/bash.py")     # a single corrected line
    bad = apply_edits(shells, [Edit("pkg/util.py", "old_name", "def old_name(s):\n    return (\n")])
    assert any("syntax error" in p or "does not parse" in p for p in bad.problems)


def test_clean_code_repairs_an_over_indented_body():
    code = "def f(x):\n        if x:\n            return 1\n        return 2\n"
    assert clean_code(code).splitlines()[1].startswith("    ") and compile(clean_code(code), "x", "exec")


def test_a_rename_leaves_dangling_names(shells):
    res = apply_edits(shells, [Edit("pkg/util.py", "old_name", "def new_name(s):\n    return s.strip()\n")])
    assert res.removed["pkg/util.py"] == {"old_name"}
    assert dangling_names(shells, res.removed) == [("old_name", "pkg/user.py")]


def test_edits_from_diagnosis_includes_other_changes():
    d = Diagnosis("pkg/zsh.py", "Zsh.app_alias", "c", FIXED, "high", True,
                  [{"file": "pkg/bash.py", "function": "Bash.app_alias", "fix": FIXED}])
    assert [(e.file, e.function) for e in edits_from_diagnosis(d)] == [("pkg/zsh.py", "Zsh.app_alias"),
                                                                       ("pkg/bash.py", "Bash.app_alias")]


# ---------------------------------------------------------------- test oracle
def test_oracle_flags_the_sibling_and_accepts_the_full_fix(shells):
    oracle = TestOracle(shells, python=sys.executable, test_cmd="pytest -q tests/test_zsh.py::test_app_alias")
    one = oracle.check([Edit("pkg/zsh.py", "Zsh.app_alias", FIXED)])
    assert one["target_passed"] and not one["passed"] and one["reason"] == "related_failing"
    assert one["related_failing"] == ["tests/test_bash.py::test_app_alias"] and "sibling" in one["output"]
    both = oracle.check([Edit("pkg/zsh.py", "Zsh.app_alias", FIXED), Edit("pkg/bash.py", "Bash.app_alias", FIXED)])
    assert both["passed"] and both["files_changed"] == ["pkg/bash.py", "pkg/zsh.py"]
    assert "app_alias" in _src(shells, "pkg/zsh.py") and "\"='fk'\"" not in _src(shells, "pkg/zsh.py")  # original kept


def test_oracle_reports_dangling_names_and_broken_tests(shells):
    oracle = TestOracle(shells, python=sys.executable, test_cmd="pytest -q tests/test_zsh.py::test_app_alias")
    res = oracle.check([Edit("pkg/zsh.py", "Zsh.app_alias", FIXED), Edit("pkg/bash.py", "Bash.app_alias", FIXED),
                        Edit("pkg/util.py", "old_name", "def new_name(s):\n    return s\n")])
    assert not res["passed"] and res["dangling"] == [("old_name", "pkg/user.py")] and "keep the old name" in res["output"]
    broken = oracle.check([Edit("pkg/zsh.py", "Zsh.app_alias", FIXED),
                           Edit("pkg/bash.py", "Bash.app_alias", "def app_alias(self, name):\n    raise ValueError\n")])
    assert not broken["passed"] and broken["related_failing"] == ["tests/test_bash.py::test_app_alias"]
    assert oracle.check(Diagnosis())["reason"] == "no_fix"


# ---------------------------------------------------------------- related implementations
def test_get_related_finds_base_and_siblings_across_files():
    t = Tools(BM25Retriever(chunk_repo(SHELLS)))
    res = t.execute("get_related", {"name": "pkg/zsh.py::Zsh.app_alias"})
    assert res.pin and sorted(c.id for c, _ in res.chunks) == ["pkg/bash.py::Bash.app_alias",
                                                               "pkg/generic.py::Generic.app_alias"]
    assert "(base)" in res.summary and "(sibling)" in res.summary
    base = t.execute("get_related", {"name": "pkg/generic.py::Generic.app_alias"})
    assert sorted(c.id for c, _ in base.chunks) == ["pkg/bash.py::Bash.app_alias", "pkg/zsh.py::Zsh.app_alias"]


def test_get_related_names_subclasses_that_inherit():
    files = {"web.py": "class Handler:\n    def finish(self):\n        self.ui = None\n",
             "ws.py": "from web import Handler\n\n\nclass WSHandler(Handler):\n    def open(self):\n        self.finish()\n"}
    res = Tools(BM25Retriever(chunk_repo(files))).execute("get_related", {"name": "web.py::Handler.finish"})
    assert "Inherited without override by: WSHandler (ws.py)" in res.summary


# ---------------------------------------------------------------- repair loop in the agent
class Script:
    def __init__(self, steps):
        self.steps, self.seen = list(steps), []

    def chat(self, messages, tools=None, tool_choice=None):
        self.seen.append({"tools": [t["function"]["name"] for t in tools or []], "prompt": messages[-1]["content"]})
        step = self.steps.pop(0) if self.steps else ToolCall("diagnose", {})
        return LLMReply("", 10, 5, 0.0, [step])


def _diag(*others, fix=FIXED):
    return ToolCall("diagnose", {"file": "pkg/zsh.py", "function": "Zsh.app_alias", "cause": "quotes missing",
                                 "fix": fix, "confidence": "high",
                                 "other_changes": [{"file": f, "function": fn, "fix": FIXED} for f, fn in others]})


def _agent(shells, script, **kw):
    pytest.importorskip("langgraph")
    from adaptive_context.agent_graph import LangGraphAgent
    from adaptive_context.chunking import load_repo_dir
    retr = BM25Retriever(chunk_repo(load_repo_dir(shells)))
    cmd = "pytest -q tests/test_zsh.py::test_app_alias"
    oracle = TestOracle(shells, python=sys.executable, test_cmd=cmd)
    tools = Tools(retr, shells, python=sys.executable, test_cmd=cmd)
    return LangGraphAgent(retr, script, tools=tools, verifier=oracle.check, **kw)


def test_repair_loop_adds_the_sibling_after_feedback(shells):
    script = Script([_diag(), ToolCall("get_related", {"name": "pkg/zsh.py::Zsh.app_alias"}),
                     _diag(("pkg/bash.py", "Bash.app_alias"))])
    res = _agent(shells, script, max_verify=3).run(
        "The regression test `tests/test_zsh.py::test_app_alias` fails on the current code.")
    assert res.stop_reason == "verified" and res.verification["files_changed"] == ["pkg/bash.py", "pkg/zsh.py"]
    assert res.diagnosis.locations == [{"file": "pkg/bash.py", "function": "Bash.app_alias", "fix": FIXED}]
    assert "test_bash.py::test_app_alias" in script.seen[1]["prompt"]          # feedback reached the model
    assert "get_related" in script.seen[1]["tools"]                            # tools allowed again after a check


def test_best_candidate_is_kept_when_no_fix_passes(shells):
    broken = "def app_alias(self, name):\n    raise ValueError\n"
    script = Script([_diag(), _diag(fix=broken), _diag(fix=broken)])
    res = _agent(shells, script, max_verify=2).run("`tests/test_zsh.py::test_app_alias` fails")
    assert res.stop_reason == "verify_failed"
    assert res.verification["target_passed"] and res.diagnosis.fix == FIXED       # the better first candidate


def test_unittest_style_test_id_is_pinned(shells):
    res = _agent(shells, Script([_diag(("pkg/bash.py", "Bash.app_alias"))])).run(
        "The regression test `tests.test_zsh.test_app_alias` fails on the current code.")
    assert ("tests/test_zsh.py::test_app_alias", 0) in res.context_items


def test_reading_a_method_adds_its_siblings_as_neighbours(shells):
    script = Script([ToolCall("read_function", {"name": "pkg.zsh.Zsh.app_alias"}), _diag()])
    res = _agent(shells, script, max_verify=1).run("zsh alias is wrong")
    assert any("related implementations added" in line for line in textwrap.dedent(script.seen[1]["prompt"]).splitlines())
    assert {"pkg/bash.py::Bash.app_alias", "pkg/generic.py::Generic.app_alias"} <= {c for c, _ in res.context_items}


# ---------------------------------------------------------------- robustness: timeouts and repeats
def test_a_hanging_test_and_its_subprocesses_are_killed(tmp_path):
    import time

    from adaptive_context.tools import run_pytest_in
    (tmp_path / "test_hang.py").write_text(
        "import subprocess, sys, time\n\n\ndef test_hang():\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n    time.sleep(120)\n")
    t0 = time.time()
    res = run_pytest_in(str(tmp_path), "test_hang.py", timeout=3, python=sys.executable)
    assert res["timed_out"] and not res["passed"] and time.time() - t0 < 20


def test_time_limit_stops_the_agent(shells):
    res = _agent(shells, Script([_diag()] * 5), max_verify=3, time_limit=1e-9).run("zsh alias is wrong")
    assert res.stop_reason == "timeout" and res.stats.llm_calls == 0


def test_a_repeated_tool_call_is_not_run_again(shells):
    search = ToolCall("search_code", {"query": "alias"})
    script = Script([search, search, _diag(("pkg/bash.py", "Bash.app_alias"))])
    res = _agent(shells, script, max_verify=1).run("zsh alias is wrong")
    assert res.stats.tool_calls == 1 and res.trace[1]["results"][0]["repeat"]
    assert "already called in round 1" in script.seen[2]["prompt"]


def test_the_same_fix_is_not_tested_twice(shells):
    calls = []
    oracle = TestOracle(shells, python=sys.executable, test_cmd="pytest -q tests/test_zsh.py::test_app_alias")

    def counting(d):
        calls.append(d)
        return oracle.check(d)
    pytest.importorskip("langgraph")
    from adaptive_context.agent_graph import LangGraphAgent
    from adaptive_context.chunking import load_repo_dir
    retr = BM25Retriever(chunk_repo(load_repo_dir(shells)))
    script = Script([_diag(), _diag(), _diag(("pkg/bash.py", "Bash.app_alias"))])
    res = LangGraphAgent(retr, script, tools=Tools(retr, shells), verifier=counting, max_verify=3).run("zsh alias")
    assert len(calls) == 2 and res.stop_reason == "verified"
    assert "exactly the same fix as before" in script.seen[2]["prompt"]


# ---------------------------------------------------------------- evidence: definitions, tracebacks, hints
def test_search_for_a_name_puts_its_definition_first():
    files = {"pkg/std.py": "class Bar:\n    @staticmethod\n    def format_meter(n):\n        return str(n)\n",
             **{f"tests/test_{i}.py": f"def test_{i}():\n    assert format_meter({i}) == format_meter({i})\n"
                for i in range(5)}}
    t = Tools(BM25Retriever(chunk_repo(files)))
    res = t.execute("search_code", {"query": "def format_meter"})
    assert res.chunks[0][0].id == "pkg/std.py::Bar.format_meter" and "definition first" in res.summary
    assert t.definitions_for("format_meter looks wrong") == []          # only a bare name triggers it


def test_not_found_points_to_the_class_in_another_file():
    files = {"web.py": "class Handler:\n    def finish(self):\n        pass\n",
             "tests/test_web.py": "class RenderHandler(object):\n    def on_message(self, m):\n        pass\n"}
    res = Tools(BM25Retriever(chunk_repo(files))).execute("read_function", {"name": "web.py::RenderHandler"})
    assert "'RenderHandler' is a class defined in tests/test_web.py" in res.summary


def test_traceback_frames_seed_the_context(shells):
    report = ("The regression test `tests/test_zsh.py::test_app_alias` fails.\n"
              '  File "tests/test_zsh.py", line 5, in test_app_alias\n'
              '  File "pkg/zsh.py", line 6, in app_alias\n'
              '  File "/usr/lib/python3.8/unittest/case.py", line 60, in testPartExecutor\n')
    script = Script([_diag(("pkg/bash.py", "Bash.app_alias"))])
    res = _agent(shells, script).run(report)
    assert ("pkg/zsh.py::Zsh.app_alias", 0) in res.context_items            # the crashing frame, in full
    assert "seed: traceback frames added: pkg/zsh.py::Zsh.app_alias" in script.seen[0]["prompt"]
    assert "case.py" not in script.seen[0]["prompt"].split("HISTORY:")[1].split("CONTEXT:")[0]   # stdlib skipped


def test_a_rejected_last_round_gets_one_more_try(shells):
    class LastRejected(Script):
        def chat(self, messages, tools=None, tool_choice=None):
            self.seen.append({"tools": [t["function"]["name"] for t in tools or []], "prompt": messages[-1]["content"]})
            if len(self.seen) == 1:
                return LLMReply("", 10, 5, 0.0, [], rejected="attempted to call tool 'read_function' which was not "
                                                            "in request.tools")
            return LLMReply("", 10, 5, 0.0, [_diag(("pkg/bash.py", "Bash.app_alias"))])
    script = LastRejected([])
    res = _agent(shells, script, max_rounds=1).run("zsh alias is wrong")
    assert res.stop_reason == "verified" and script.seen[1]["tools"] == ["diagnose"]


def test_oracle_ignores_listed_test_files(shells):
    oracle = TestOracle(shells, python=sys.executable, test_cmd="pytest -q tests/test_zsh.py::test_app_alias",
                        ignore=["tests/test_bash.py"])
    assert "tests/test_bash.py" not in oracle.related_test_files(["pkg/bash.py", "pkg/zsh.py"], ["app_alias"])


def test_a_forced_round_is_relaxed_once_when_the_model_insists_on_a_tool(shells):
    class Insists(Script):
        def chat(self, messages, tools=None, tool_choice=None):
            names = [t["function"]["name"] for t in tools or []]
            self.seen.append({"tools": names, "prompt": messages[-1]["content"]})
            n = len(self.seen)
            if n <= 3:                                    # the same search 3 times -> next round is diagnose-only
                return LLMReply("", 10, 5, 0.0, [ToolCall("search_code", {"query": "alias"})])
            if n == 4:                                    # forced round: asks for a tool anyway -> rejected
                return LLMReply("", 10, 5, 0.0, [], rejected="attempted to call tool 'search_code' which was not "
                                                            "in request.tools")
            if n == 5:
                return LLMReply("", 10, 5, 0.0, [ToolCall("get_related", {"name": "pkg/zsh.py::Zsh.app_alias"})])
            return LLMReply("", 10, 5, 0.0, [_diag(("pkg/bash.py", "Bash.app_alias"))])
    script = Insists([])
    res = _agent(shells, script, max_verify=1, max_rounds=8).run("zsh alias is wrong")
    assert script.seen[3]["tools"] == ["diagnose"] and "get_related" in script.seen[4]["tools"]
    assert "You may call one more tool now" in script.seen[4]["prompt"] and res.stop_reason == "verified"
