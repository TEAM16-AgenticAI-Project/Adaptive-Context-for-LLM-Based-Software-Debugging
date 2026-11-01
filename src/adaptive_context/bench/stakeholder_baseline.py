"""Adapter for the original stakeholder CLI; never sends gold/source to it.

The upstream Rich UI discards the raw 'Relevant file' section. A temporary
sitecustomize hook captures the original model response and selected windows,
without changing retrieval, prompt construction or provider generation. Only
terminal animation is bypassed. This remains a separate, unmatched reference.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import time

from ..cases import load_cases, norm_path, score_diagnosis
from ..chunking import chunk_repo, load_repo_dir
from ..models import Diagnosis
from ..tools import IGNORE
from .audit import revision

DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_MAX_TOKENS = 4096
REVIEWED_REVISION = "c545608474b62e5635d240c49a790703e687848e"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HEADINGS = re.compile(
    r"^[ \t]*(Evidence considered|Conclusion|Relevant file|Function|Suggested change|Explanation):[ \t]*([^\n]*)$",
    re.MULTILINE | re.IGNORECASE)

# Executed by the child interpreter before the original src/baseline.py. No
# case labels, source metadata, expected paths or patches are present here.
CAPTURE_HOOK = '''
import hashlib
import json
import os
import time
from pathlib import Path
from context_builder import DebuggingContextBuilder
from terminal_ui import TerminalUI

_capture = {}
_original_build = DebuggingContextBuilder.build_prompt

def _build(self, bug_report, chunks):
    prompt = _original_build(self, bug_report, chunks)
    _capture["selected_chunks"] = [{"path": c.source_path, "start": c.start_line, "end": c.end_line} for c in chunks]
    _capture["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    return prompt

def _generate(self, provider, prompt, *, selected_chunks=()):
    started = time.perf_counter()
    result = provider.generate(prompt)
    elapsed = time.perf_counter() - started
    _capture["raw_response"] = result.text
    _capture["usage"] = {k: getattr(result, k, None) for k in ("input_tokens", "output_tokens", "total_tokens")}
    _capture["model_s"] = elapsed
    Path(os.environ["ADAPTIVE_BASELINE_CAPTURE"]).write_text(json.dumps(_capture))
    return result, elapsed, elapsed

DebuggingContextBuilder.build_prompt = _build
TerminalUI.generate_response = _generate
'''


def read_key_file(path: str) -> str:
    """Read only GROQ_API_KEY as data; never source or execute an env file."""
    file = Path(path)
    if not file.exists():
        return ""
    for line in file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and name.strip() == "GROQ_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            return value
    return ""


def parse_output(output: str) -> Diagnosis:
    text = _ANSI.sub("", output).replace("**", "")
    text = "\n".join(re.sub(r"^\s*│ ?", "", line).removesuffix("│").rstrip() for line in text.splitlines())
    matches = list(_HEADINGS.finditer(text))
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).lower()] = (match.group(2) + "\n" + text[match.end():end]).strip()
    paths = re.findall(r"[\w./\\-]+\.py\b", sections.get("relevant file", ""))
    file = norm_path(paths[-1]) if paths else ""
    fix = sections.get("suggested change", "").strip()
    if fix.startswith("```"):
        lines = fix.splitlines()
        fix = "\n".join(lines[1:-1]) if len(lines) >= 2 and lines[-1].strip() == "```" else ""
    function = sections.get("function", "").strip("` ")
    try:
        tree = ast.parse(textwrap.dedent(fix))
        definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if definitions:
            function = definitions[0].name
        elif tree.body and not function:
            function = "<module>"
    except (SyntaxError, ValueError):
        return Diagnosis(file=file, cause=sections.get("conclusion", ""), fix=fix)
    cause = sections.get("conclusion", "") or sections.get("explanation", "")
    return Diagnosis(file, function, cause, fix, "", bool(file and function and cause and fix))


def context_items(repo: str, selected: list) -> list:
    """Count evidence only when a complete AST chunk fits inside a selected window."""
    chunks = chunk_repo(load_repo_dir(repo))
    return [(chunk.id, 0) for chunk in chunks if any(
        norm_path(window["path"]) == chunk.path and window["start"] <= chunk.start and window["end"] >= chunk.end
        for window in selected)]


def _upstream_revision(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_case(case, baseline_root: str, *, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS,
             timeout=600, skip_live=False, execute=subprocess.run) -> dict:
    if max_tokens < 1 or timeout < 1:
        raise ValueError("max_tokens and timeout must be positive")
    root = Path(baseline_root).resolve()
    record = {"case": case.id, "dataset": case.dataset, "system": "stakeholder_original", "model": model,
              "max_tokens": max_tokens, "top_k": 3, "status": "not_run", "metrics": None,
              "upstream_revision": _upstream_revision(root)}
    if skip_live:
        return {**record, "reason": "Live calls explicitly skipped; no model result is claimed."}
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key == "your_key_here":
        return {**record, "reason": "GROQ_API_KEY is not configured."}
    if not (root / "src/baseline.py").is_file():
        raise FileNotFoundError("baseline_root must contain the stakeholder's src/baseline.py")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="stakeholder-case-") as temporary:
        base = Path(temporary)
        repo = base / "repo"
        shutil.copytree(case.repo, repo, ignore=IGNORE)
        (repo / "bug_report.txt").write_text(case.bug_report, encoding="utf-8")
        capture_dir = base / "capture"
        capture_dir.mkdir()
        (capture_dir / "sitecustomize.py").write_text(CAPTURE_HOOK)
        capture_file = base / "response.json"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(capture_dir), str(root / "src")]),
               "ADAPTIVE_BASELINE_CAPTURE": str(capture_file), "NO_COLOR": "1", "COLUMNS": "160"}
        for secret in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(secret, None)
        command = ["uv", "run", "python", "src/baseline.py", str(repo), "--provider", "groq", "--model", model,
                   "--top-k", "3", "--max-tokens", str(max_tokens)]
        try:
            result = execute(command, cwd=root, env=env, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
            captured = json.loads(capture_file.read_text()) if capture_file.exists() else {}
            raw = captured.get("raw_response", result.stdout).replace(key, "<redacted>")
            diagnosis = parse_output(raw)
            items = context_items(str(repo), captured.get("selected_chunks", []))
            status = "ok" if result.returncode == 0 and diagnosis.valid else "error"
            return {**record, "status": status, "reason": "" if status == "ok" else "process_or_parse_failure",
                    "returncode": result.returncode, "wall_s": time.perf_counter() - started,
                    "raw_response": raw, "stdout": result.stdout.replace(key, "<redacted>"),
                    "stderr": result.stderr.replace(key, "<redacted>"), "diagnosis": asdict(diagnosis),
                    "selected_chunks": captured.get("selected_chunks", []), "context_items": items,
                    "usage": captured.get("usage"), "prompt_sha256": captured.get("prompt_sha256"),
                    "metrics": score_diagnosis(case, diagnosis, items) if status == "ok" else None}
        except (subprocess.TimeoutExpired, OSError, ValueError) as error:
            return {**record, "status": "error", "reason": type(error).__name__,
                    "wall_s": time.perf_counter() - started, "metrics": None}


def run(manifest: str, baseline_root: str, out: str, *, limit=None, sleep=0, repo_root=None, **kwargs) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if sleep < 0:
        raise ValueError("sleep must be nonnegative")
    cases = load_cases(manifest, root=repo_root)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    # Persist after each case so a timeout or interrupted live batch retains evidence.
    with (directory / "results.jsonl").open("w", encoding="utf-8") as output:
        for index, case in enumerate(cases[:limit]):
            if index and sleep and not kwargs.get("skip_live"):
                time.sleep(sleep)
            try:
                row = run_case(case, baseline_root, **kwargs)
            except (OSError, ValueError) as error:
                row = {"case": case.id, "status": "error", "metrics": None, "reason": type(error).__name__}
            rows.append(row)
            output.write(json.dumps(row) + "\n")
            output.flush()
            print(f"{case.id}: {row['status']}", flush=True)
    measured = [r for r in rows if r["status"] != "not_run"]
    summary = {"revision": revision(), "manifest": manifest, "repo_root": repo_root,
               "planned": len(rows), "measured": len(measured),
               "parsed_successful": sum(r["status"] == "ok" for r in rows),
               "not_run": sum(r["status"] == "not_run" for r in rows),
               "errors": sum(r["status"] == "error" for r in rows),
               "metrics": {key: sum(bool(r.get("metrics") and r["metrics"][key]) for r in measured) / len(measured)
                           for key in ("file_hit", "func_hit", "evidence", "grounded")} if measured else None,
               "conditions": {"reference": "Original Chroma + 80-line windows + Top-3 + one model call",
                   "max_tokens": kwargs.get("max_tokens", DEFAULT_MAX_TOKENS), "default_before_override": 512,
                   "capture": "Original response and selected windows; presentation animation bypassed",
                   "grounding": "Complete AST function must fit in a selected window",
                   "denominator": "All attempted executions, including process/parse failures; not_run rows excluded",
                   "reviewed_upstream_revision": REVIEWED_REVISION,
                   "fairness": "Separate reference; not matched to team chunking/prompts/retriever. No fabricated usage."}}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/real/thefuck.jsonl")
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--out", default="results/deep/stakeholder")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repo-root", help="override the base directory of the manifest's repo paths")
    parser.add_argument("--sleep", type=float, default=0)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--env-file", default=".env", help="read GROQ_API_KEY if not already in the environment")
    args = vars(parser.parse_args(argv))
    env_file = args.pop("env_file")
    if not args["skip_live"] and not os.environ.get("GROQ_API_KEY"):
        key = read_key_file(env_file)
        if key:
            os.environ["GROQ_API_KEY"] = key
    summary = run(**args)
    print(json.dumps(summary, indent=2))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
