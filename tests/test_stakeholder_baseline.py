import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from adaptive_context.bench import stakeholder_baseline as baseline
from adaptive_context.cases import RealCase

SAMPLE = (Path(__file__).parent / "fixtures/stakeholder_summary.txt").read_text()


def test_env_file_is_read_as_data_not_executed(tmp_path):
    file = tmp_path / ".env"
    file.write_text('# comment\nexport GROQ_API_KEY="test-key"\nOTHER=ignored\n')
    assert baseline.read_key_file(str(file)) == "test-key"
    assert baseline.read_key_file(str(tmp_path / "absent")) == ""
    file.write_text("GROQ_API_KEY=$(touch never-execute)\n")
    assert baseline.read_key_file(str(file)) == "$(touch never-execute)"
    assert not (tmp_path / "never-execute").exists()


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


def test_parser_preserves_python_exponentiation_and_bold_headings():
    text = SAMPLE.replace("return price * 0.8", "return price ** 2").replace("Relevant file:", "**Relevant file:**")
    result = baseline.parse_output(text)
    assert result.valid and "return price ** 2" in result.fix


@pytest.mark.parametrize("function", ["checkout", "Checkout.checkout"])
def test_parser_preserves_explicit_function_when_fix_includes_helper(function):
    text = SAMPLE.replace("Relevant file:", f"Function: `{function}`\n\nRelevant file:")
    text = text.replace("def checkout(price):", "def normalize(price):\n    return price\n\ndef checkout(price):")
    result = baseline.parse_output(text)
    assert result.valid and result.function == function
    assert "def normalize" in result.fix and "def checkout" in result.fix


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


@pytest.mark.parametrize("payload", [
    None,
    {"raw_response": 17},
    {"raw_response": SAMPLE, "selected_chunks": [{"path": "payment.py"}]},
    {"raw_response": SAMPLE, "selected_chunks": [{"path": "payment.py", "start": 2, "end": 1}]},
])
def test_malformed_capture_is_an_execution_failure(tmp_path, monkeypatch, payload):
    case = fixture_case(tmp_path)
    upstream = tmp_path / "upstream"
    (upstream / "src").mkdir(parents=True)
    (upstream / "src/baseline.py").write_text("# fixture")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")

    def execute(command, **kwargs):
        Path(kwargs["env"]["ADAPTIVE_BASELINE_CAPTURE"]).write_text(json.dumps(payload))
        return SimpleNamespace(stdout=SAMPLE, stderr="", returncode=0)

    result = baseline.run_case(case, str(upstream), execute=execute)
    assert result["status"] == "error" and result["metrics"] is None


def test_bad_capture_does_not_abort_the_remaining_cases(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    upstream = tmp_path / "upstream"
    (upstream / "src").mkdir(parents=True)
    (upstream / "src/baseline.py").write_text("# fixture")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text("".join(json.dumps({"id": name, "repo": case.repo, "bug_report": case.bug_report}) + "\n"
                                for name in ("bad-capture", "good-capture")))
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        payload = None if len(calls) == 1 else {"raw_response": SAMPLE, "selected_chunks": []}
        Path(kwargs["env"]["ADAPTIVE_BASELINE_CAPTURE"]).write_text(json.dumps(payload))
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    result = baseline.run(str(manifest), str(upstream), str(tmp_path / "output"), execute=execute)
    rows = [json.loads(line) for line in (tmp_path / "output/results.jsonl").read_text().splitlines()]
    assert [row["status"] for row in rows] == ["error", "ok"]
    assert result["measured"] == 2 and result["errors"] == result["parsed_successful"] == 1


def test_skip_summary_keeps_unmeasured_metrics_null(tmp_path):
    case = fixture_case(tmp_path)
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps({"id": case.id, "repo": case.repo, "bug_report": case.bug_report}) + "\n")
    result = baseline.run(str(manifest), str(tmp_path), str(tmp_path / "output"), skip_live=True)
    assert result["planned"] == result["not_run"] == 1
    assert result["measured"] == 0 and result["metrics"] is None


def test_input_fingerprints_track_manifest_and_report_changes(tmp_path):
    case = fixture_case(tmp_path)
    manifest = tmp_path / "cases.jsonl"
    hashes = []
    for index, report in enumerate((case.bug_report, case.bug_report + " Discounts should apply.")):
        manifest.write_text(json.dumps({"id": case.id, "repo": case.repo, "bug_report": report}) + "\n")
        output = tmp_path / f"output-{index}"
        summary = baseline.run(str(manifest), str(tmp_path), str(output), skip_live=True)
        row = json.loads((output / "results.jsonl").read_text())
        assert summary["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert row["bug_report_sha256"] == hashlib.sha256(report.encode("utf-8")).hexdigest()
        assert row["status"] == "not_run" and summary["metrics"] is None
        hashes.append((summary["manifest_sha256"], row["bug_report_sha256"]))
    assert hashes[0][0] != hashes[1][0] and hashes[0][1] != hashes[1][1]


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


@pytest.mark.skipif(shutil.which("uv") is None, reason="the upstream CLI uses uv")
def test_capture_works_in_a_real_child_process_without_a_model(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    upstream = tmp_path / "upstream"
    source = upstream / "src"
    source.mkdir(parents=True)
    (source / "context_builder.py").write_text(
        'class DebuggingContextBuilder:\n'
        '    def build_prompt(self, bug_report, chunks):\n'
        '        return "original-prompt:" + bug_report\n')
    (source / "terminal_ui.py").write_text('class TerminalUI:\n    pass\n')
    (source / "baseline.py").write_text(
        'from types import SimpleNamespace as S\n'
        'from context_builder import DebuggingContextBuilder\n'
        'from terminal_ui import TerminalUI\n'
        'from pathlib import Path\nimport sys\n'
        'report = (Path(sys.argv[1]) / "bug_report.txt").read_text()\n'
        'prompt = DebuggingContextBuilder().build_prompt(report, [S(source_path="payment.py", start_line=1, end_line=2)])\n'
        f'provider = S(generate=lambda p: S(text={SAMPLE!r}, input_tokens=40, output_tokens=20, total_tokens=60))\n'
        'TerminalUI().generate_response(provider, prompt)\n')
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setenv("UV_OFFLINE", "1")
    result = baseline.run_case(case, str(upstream), timeout=30)
    assert result["status"] == "ok", result
    assert result["metrics"]["grounded"] and result["usage"]["total_tokens"] == 60
    assert result["prompt_sha256"]
