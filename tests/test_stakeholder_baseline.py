import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from adaptive_context.bench import stakeholder_baseline as baseline
from adaptive_context.cases import RealCase

SAMPLE = (Path(__file__).parent / "fixtures/stakeholder_summary.txt").read_text()


def fixture_case(tmp_path):
    repo = tmp_path / "buggy"
    repo.mkdir()
    (repo / "payment.py").write_text("def checkout(price):\n    return price\n")
    (repo / ".env").write_text("private file must not be copied")
    return RealCase("payment", "fixture", str(repo), "Checkout returns the full price.",
                    ["payment.py"], ["payment.py::checkout"], source={"secret_gold": "LABEL_SENTINEL"})


def test_saved_summary_fixture_parses_without_gold():
    # This is a saved parser fixture, not a live-model observation.
    result = baseline.parse_output(SAMPLE)
    assert result.valid and result.file == "payment.py" and result.function == "checkout"
    assert "discards" in result.cause
    assert baseline.parse_output("\x1b[32m" + SAMPLE.replace("Relevant file:", "**Relevant file:**") + "\x1b[0m").valid


def test_parser_rejects_missing_file_and_truncated_function():
    assert not baseline.parse_output("diagnose: checkout").valid
    assert not baseline.parse_output(SAMPLE.replace("def checkout(price):", "def checkout(")).valid
    assert not baseline.parse_output(SAMPLE.replace("payment.py", "unknown")).valid


def test_actual_command_contains_only_buggy_repo_and_report(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    upstream = tmp_path / "upstream"
    (upstream / "src").mkdir(parents=True)
    (upstream / "src/baseline.py").write_text("# fixture only")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        copied = Path(command[4])
        assert not (copied / ".env").exists()
        assert (copied / "bug_report.txt").read_text() == case.bug_report
        assert "LABEL_SENTINEL" not in " ".join(command)
        assert "LABEL_SENTINEL" not in (copied / "bug_report.txt").read_text()
        return SimpleNamespace(stdout=SAMPLE, stderr="", returncode=0)

    result = baseline.run_case(case, str(upstream), execute=execute)
    assert commands[0][:4] == ["uv", "run", "python", "src/baseline.py"]
    assert commands[0][-1] == "4096"
    assert result["status"] == "ok" and result["metrics"]["func_hit"]
    assert not result["metrics"]["evidence"]  # no captured windows => no fabricated grounding
    assert (Path(case.repo) / "payment.py").read_text().endswith("return price\n")


def test_keyless_and_explicit_skip_never_launch_a_process(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("No process should run")

    assert baseline.run_case(case, str(tmp_path), execute=forbidden)["status"] == "not_run"
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert baseline.run_case(case, str(tmp_path), skip_live=True, execute=forbidden)["status"] == "not_run"


def test_error_output_redacts_key_and_never_scores_as_success(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    upstream = tmp_path / "upstream"
    (upstream / "src").mkdir(parents=True)
    (upstream / "src/baseline.py").write_text("# fixture")
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    result = baseline.run_case(case, str(upstream), execute=lambda *a, **kw:
                               SimpleNamespace(stdout="", stderr="test-secret", returncode=1))
    assert result["metrics"] is None and result["status"] == "error"
    assert "test-secret" not in json.dumps(result)
    timed = baseline.run_case(case, str(upstream), execute=lambda *a, **kw:
                              (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 1)))
    assert timed["reason"] == "TimeoutExpired"


def test_skip_summary_keeps_unmeasured_metrics_null(tmp_path):
    case = fixture_case(tmp_path)
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps({"id": case.id, "repo": case.repo, "bug_report": case.bug_report}) + "\n")
    result = baseline.run(str(manifest), str(tmp_path), str(tmp_path / "output"), skip_live=True)
    assert result["planned"] == result["not_run"] == 1
    assert result["measured"] == 0 and result["metrics"] is None


def test_context_requires_complete_function_window(tmp_path):
    case = fixture_case(tmp_path)
    assert baseline.context_items(case.repo, [{"path": "payment.py", "start": 1, "end": 1}]) == []
    assert baseline.context_items(case.repo, [{"path": "payment.py", "start": 1, "end": 2}]) == [("payment.py::checkout", 0)]


def test_capture_hook_preserves_prompt_and_captures_model_output(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    builder = ModuleType("context_builder")
    terminal = ModuleType("terminal_ui")

    class Builder:
        def build_prompt(self, bug_report, chunks):
            return f"original-prompt:{bug_report}"

    class UI:
        pass

    builder.DebuggingContextBuilder = Builder
    terminal.TerminalUI = UI
    monkeypatch.setitem(sys.modules, "context_builder", builder)
    monkeypatch.setitem(sys.modules, "terminal_ui", terminal)
    target = tmp_path / "captured.json"
    monkeypatch.setenv("ADAPTIVE_BASELINE_CAPTURE", str(target))
    namespace = {}
    exec(baseline.CAPTURE_HOOK, namespace)
    chunk = SimpleNamespace(source_path="payment.py", start_line=1, end_line=2)
    prompt = Builder().build_prompt("user-report", [chunk])
    assert prompt == "original-prompt:user-report"
    calls = []

    def generate(value):
        calls.append(value)
        return SimpleNamespace(text=SAMPLE, input_tokens=40, output_tokens=20, total_tokens=60)

    UI().generate_response(SimpleNamespace(generate=generate), prompt)
    captured = json.loads(target.read_text())
    assert calls == [prompt] and captured["raw_response"] == SAMPLE
    assert captured["usage"]["total_tokens"] == 60
    assert captured["selected_chunks"] == [{"path": "payment.py", "start": 1, "end": 2}]
