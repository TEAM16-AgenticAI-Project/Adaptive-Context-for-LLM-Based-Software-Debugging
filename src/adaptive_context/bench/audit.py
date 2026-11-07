"""LLM-free evidence audit. Gold labels are used for scoring only, never retrieval.

Deep's --audit and Tanay's --validate-fixcheck share one CLI.
Both actions retain their existing Python helpers and result formats.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import tempfile
import textwrap
from pathlib import Path
import platform
import re
import shlex
import statistics
import subprocess
import time

from ..cases import load_cases, norm_path
from ..chunking import chunk_repo, load_repo_dir, parse_source
from ..retrieval import BM25Retriever
from ..fixcheck import FixError, apply_fix_to_source, check_fix, find_function
from ..tools import run_failing_test
from .bugsinpy import git_apply


def revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def test_paths(command: str) -> list:
    """Extract explicit pytest file targets without executing the command."""
    return list(dict.fromkeys(token.split("::", 1)[0] for token in shlex.split(command)
                             if token.split("::", 1)[0].endswith(".py") and not token.startswith("-")))


def imported_modules(source: str, test_path: str) -> set:
    tree = parse_source(source)
    package = list(Path(test_path).with_suffix("").parts[:-1])
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            if base:
                modules.add(base)
                modules.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return modules


def _module(path: str) -> str:
    value = norm_path(path).removesuffix(".py").replace("/", ".")
    return value.removesuffix(".__init__")


def rank_metrics(hits: list, gold_ids: list) -> dict:
    ranks = {}
    for rank, (chunk, _) in enumerate(hits, 1):
        ranks.setdefault(chunk.id, rank)
    gold_ranks = {gold: ranks.get(gold) for gold in gold_ids}
    known = [r for r in gold_ranks.values() if r is not None]
    return {"gold_ranks": gold_ranks, "best_rank": min(known) if known else None,
            "hit_at_3": any(r <= 3 for r in known), "hit_at_10": any(r <= 10 for r in known),
            "all_at_3": bool(gold_ranks) and all(r is not None and r <= 3 for r in gold_ranks.values()),
            "all_at_10": bool(gold_ranks) and all(r is not None and r <= 10 for r in gold_ranks.values()),
            "top10": [{"id": c.id, "score": float(s)} for c, s in hits[:10]]}


def audit_case(case, *, chroma=False, chroma_factory=None, deduplicate=False) -> dict:
    repo = Path(case.repo).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"Missing case repository: {case.id}; rebuild the dataset first")
    files = load_repo_dir(str(repo))
    chunks = chunk_repo(files)
    input_count = len(chunks)
    last = {chunk.id: index for index, chunk in enumerate(chunks)}
    duplicate_count = len(chunks) - len(last)
    if deduplicate:
        # Duplicate Python definitions in older test files share the AST chunk
        # ID. Keep the last definition, for BOTH rankers, as an explicit variant.
        chunks = [chunk for index, chunk in enumerate(chunks) if last[chunk.id] == index]
    started = time.perf_counter()
    retriever = BM25Retriever(chunks)
    indexed_s = time.perf_counter() - started
    started = time.perf_counter()
    # The query is exactly the bug report. No gold/source metadata enters ranking.
    hits = retriever.search(case.bug_report, max(1, len(chunks)))
    bm25 = {**rank_metrics(hits, case.gold_functions), "index_s": indexed_s,
            "query_s": time.perf_counter() - started}
    imports, inspected, import_errors = set(), [], []
    for relative in test_paths(case.test_cmd):
        target = (repo / relative).resolve()
        if not target.is_relative_to(repo) or relative not in files:
            import_errors.append({"path": relative, "error": "missing_or_outside_repo"})
            continue
        try:
            imports.update(imported_modules(files[relative], relative))
            inspected.append(relative)
        except (SyntaxError, ValueError):
            import_errors.append({"path": relative, "error": "syntax_error"})
    output = case.bug_report.partition("Failing test output")[2] if "Failing test output" in case.bug_report else ""
    normalized = output.replace("\\", "/")
    named = [f for f in case.gold_files if re.search(re.escape(norm_path(f)) + r"(?=[:\s\"')]|$)", normalized)]
    ambiguity = {name: sum(c.kind in ("function", "method") and c.short_name == name for c in chunks)
                 for name in sorted(case.gold_short_names)}
    row = {"case": case.id, "dataset": case.dataset, "status": "ok", "chunks": len(chunks),
           "input_chunks": input_count, "duplicate_chunks": duplicate_count, "deduplicated": deduplicate,
           "source_files": len(files), "gold_functions": case.gold_functions,
           "gold_indexed": {g: g in retriever.by_id for g in case.gold_functions},
           "retrievers": {"bm25": bm25}, "test_output_available": bool(output.strip()),
           "test_output_names_gold_file": bool(named) if output.strip() else None,
           "named_gold_files": named, "test_modules_inspected": inspected, "test_import_errors": import_errors,
           "test_imports_gold_module": any(_module(f) in imports for f in case.gold_files) if inspected else None,
           "gold_short_name_counts": ambiguity, "test_evidence_origin": "manifest_bug_report",
           "bug_report_sha256": hashlib.sha256(case.bug_report.encode()).hexdigest()}
    if chroma:
        if chroma_factory is None:
            from ..retrieval import ChromaRetriever
            chroma_factory = ChromaRetriever
        semantic = None
        try:
            started = time.perf_counter()
            semantic = chroma_factory(chunks)
            indexed_s = time.perf_counter() - started
            started = time.perf_counter()
            semantic_hits = semantic.search(case.bug_report, min(10, len(chunks))) if chunks else []
            row["retrievers"]["chroma"] = {**rank_metrics(semantic_hits, case.gold_functions),
                "rank_scope": "top10_only", "index_s": indexed_s, "query_s": time.perf_counter() - started}
        finally:
            # ChromaRetriever currently has no public lifecycle method. A supplied
            # factory can own cleanup; release the standard collection after scoring.
            if semantic is not None and chroma_factory.__module__ == "adaptive_context.retrieval":
                import chromadb
                chromadb.EphemeralClient().delete_collection(semantic._col.name)
    return row


def summarize(rows: list, revision: dict | None = None) -> dict:
    """Keep both published helper signatures: audit(rows), fixcheck(rows, revision)."""
    if revision is not None:
        return summarize_fixcheck(rows, revision)
    ok = [r for r in rows if r["status"] == "ok"]
    summary = {"attempted": len(rows), "processed": len(ok), "errors": len(rows) - len(ok), "retrievers": {}}
    for name in sorted({n for r in ok for n in r["retrievers"]}):
        values = [r["retrievers"][name] for r in ok if name in r["retrievers"]]
        summary["retrievers"][name] = {"n": len(values), **{
            metric: sum(v[metric] for v in values) / len(values) if values else None
            for metric in ("hit_at_3", "hit_at_10", "all_at_3", "all_at_10")}}
    for key in ("test_output_names_gold_file", "test_imports_gold_module"):
        values = [r[key] for r in ok if r[key] is not None]
        summary[key] = {"observed": len(values), "yes": sum(values), "rate": sum(values) / len(values) if values else None}
    counts = [n for r in ok for n in r["gold_short_name_counts"].values()]
    summary["name_ambiguity"] = {"labels": len(counts), "median": statistics.median(counts) if counts else None,
                                 "max": max(counts) if counts else None}
    return summary


def summarize_audit(rows: list) -> dict:
    """Named evidence-summary entry point published by the main-branch merger."""
    return summarize(rows)


def run_audit(manifest: str, out: str, *, chroma=False, limit=None, deduplicate=False, repo_root=None) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cases = load_cases(manifest, root=repo_root)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    rows = []
    for case in cases[:limit]:
        try:
            row = audit_case(case, chroma=chroma, deduplicate=deduplicate)
        except Exception as error:
            row = {"case": case.id, "dataset": case.dataset, "status": "error",
                   "error": f"{type(error).__name__}: {error}"}
        rows.append(row)
        print(f"{case.id}: {row['status']}", flush=True)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "audit.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    summary = {"revision": revision(), "manifest": manifest, "repo_root": repo_root,
               "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
               "python": platform.python_version(), "llm_calls": 0,
               "deduplicated": deduplicate,
               "deduplication_rule": "Keep last AST definition per ID for both rankers" if deduplicate else "None",
               "definitions": {"hit_at_k": "at least one evaluator gold function in first k retrieved chunks",
                   "all_at_k": "all evaluator gold functions in first k chunks",
                   "imports": "direct syntactic imports only; conditional imports counted, transitive imports not inferred",
                   "traceback": "gold relative path explicitly mentioned in manifest failing-test output, not a fresh test run"},
               **summarize(rows)}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary



ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_MANIFEST = os.path.join(ROOT, "data", "real", "thefuck.jsonl")
_TEST_FILE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$")


def split_patch(patch: str) -> dict:
    """{path: section text} for every non-test file in a unified git diff."""
    out, cur, buf = {}, None, []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if cur:
                out[cur] = "".join(buf)
            cur, buf = line.split(" b/")[-1].strip(), [line]
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "".join(buf)
    return {p: s for p, s in out.items() if not _TEST_FILE.search(p)}


def _segment(source: str, node) -> str:
    """Source of a function including its decorators, dedented."""
    lines = source.splitlines()
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return textwrap.dedent("\n".join(lines[start - 1: node.end_lineno])) + "\n"


def _same_module(a: str, b: str) -> bool:
    """Same code modulo where top-level imports sit: identical non-import statements and the same imported names.

    A model writing "imports + function" puts new imports after the last import (fixcheck's rule), while the
    maintainer may extend an existing import line or insert one mid-block; both bind the same names."""
    def norm(src):
        tree = ast.parse(src)
        names, rest = set(), []
        for n in tree.body:
            if isinstance(n, ast.Import):
                names |= {(None, al.name, al.asname, 0) for al in n.names}
            elif isinstance(n, ast.ImportFrom):
                names |= {(n.module, al.name, al.asname, n.level) for al in n.names}
            else:
                rest.append(ast.dump(n))
        return names, rest
    return norm(a) == norm(b)


def fixed_sources(repo: str, sections: dict) -> dict:
    """Apply the maintainer's patch to copies of the touched files; return {path: (buggy, fixed)}."""
    tmp = tempfile.mkdtemp(prefix="gold_fix_")
    try:
        work = os.path.join(tmp, "repo")
        for path in sections:
            os.makedirs(os.path.dirname(os.path.join(work, path)), exist_ok=True)
            shutil.copy(os.path.join(repo, path), os.path.join(work, path))
        patch_file = os.path.join(tmp, "gold.patch")
        with open(patch_file, "w", encoding="utf-8") as fh:
            fh.write("".join(sections.values()))
        git_apply(work, patch_file, reverse=False)
        out = {}
        for path in sections:
            with open(os.path.join(repo, path), encoding="utf-8") as a, \
                    open(os.path.join(work, path), encoding="utf-8") as b:
                out[path] = (a.read(), b.read())
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gold_fix(case, patch: str) -> dict:
    """The maintainer's fix written the way a model would submit it.

    Returns {"form": "function"|"diff", "why", "fix", "file", "function", "negative"}; ``negative`` is the buggy
    function resubmitted unchanged (the negative control)."""
    sections = split_patch(patch)
    srcs = fixed_sources(case.repo, sections)
    first = case.gold_functions[0] if case.gold_functions else ""
    file, qual = (first.split("::", 1) + [""])[:2] if first else (sorted(sections)[0], "")
    negative = ""
    if qual and qual != "<module>" and file in srcs:
        try:
            _, node = find_function(ast.parse(srcs[file][0]), qual)
            negative = _segment(srcs[file][0], node)
        except FixError:
            pass
    diff = {"form": "diff", "fix": "".join(sections.values()), "file": file, "function": qual, "negative": negative}
    if len(sections) > 1:
        return {**diff, "why": "multi_file"}
    if len(case.gold_functions) != 1 or qual in ("", "<module>"):
        return {**diff, "why": "multi_function" if len(case.gold_functions) > 1 else "module_only"}
    buggy, fixed = srcs[file]
    fixed_tree = ast.parse(fixed)
    try:
        _, node = find_function(fixed_tree, qual)
    except FixError:
        return {**diff, "why": "function_renamed"}
    have = {ast.dump(n) for n in ast.parse(buggy).body if isinstance(n, (ast.Import, ast.ImportFrom))}
    new_imports = [ast.get_source_segment(fixed, n).strip() for n in fixed_tree.body
                   if isinstance(n, (ast.Import, ast.ImportFrom)) and ast.dump(n) not in have]
    candidate = "".join(i + "\n" for i in new_imports) + ("\n" if new_imports else "") + _segment(fixed, node)
    try:
        applied, _ = apply_fix_to_source(buggy, qual, candidate)
        if _same_module(applied, fixed):
            return {**diff, "form": "function", "why": "imports+function" if new_imports else "single_function",
                    "fix": candidate}
    except FixError:
        pass
    return {**diff, "why": "module_level"}


def _revision() -> dict:
    def git(*a):
        p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    dirty = git("status", "--porcelain", "--", "src")
    return {"commit": git("rev-parse", "HEAD"), "src_dirty": bool(dirty)}


def _brief(r: dict) -> dict:
    return {k: r[k] for k in ("applied", "mode", "passed", "reason")}


def validate_case(case, patch: str, python: str | None, timeout: int = 120) -> dict:
    g = gold_fix(case, patch)
    t0 = time.perf_counter()
    gold = check_fix(case.repo, g["file"], g["function"], g["fix"], test_cmd=case.test_cmd, python=python,
                     timeout=timeout)
    gold_s = round(time.perf_counter() - t0, 2)
    if g["negative"]:
        neg = check_fix(case.repo, g["file"], g["function"], g["negative"], test_cmd=case.test_cmd, python=python,
                        timeout=timeout)
    else:
        neg = {"applied": False, "mode": "", "passed": False, "reason": "no_negative", "output": ""}
    base = run_failing_test(case.repo, None, timeout, 40, python, case.test_cmd)
    rejected = not neg["passed"] and neg["reason"] != "no_negative"
    rec = {"id": case.id, "env": case.env, "files": len(case.gold_files), "functions": len(case.gold_functions),
           "module_level_change": case.module_level_change, "form": g["form"], "why": g["why"],
           "target": f"{g['file']}::{g['function']}", "gold": {**_brief(gold), "secs": gold_s},
           "negative": _brief(neg), "baseline_failed": not base["passed"],
           "ok": bool(gold["passed"] and rejected and not base["passed"])}
    if not rec["ok"]:
        rec["gold_output"] = gold["output"][-2000:]
        rec["negative_output"] = neg["output"][-2000:]
    return rec


def summarize_fixcheck(records: list, revision: dict) -> dict:
    n = len(records)

    def count(pred):
        return sum(1 for r in records if pred(r))
    forms = {}
    for r in records:
        f = forms.setdefault(r["form"], {"cases": 0, "gold_passed": 0})
        f["cases"] += 1
        f["gold_passed"] += int(r["gold"]["passed"])
    return {"revision": revision, "cases": n,
            "gold_passed": count(lambda r: r["gold"]["passed"]),
            "negative_rejected": count(lambda r: not r["negative"]["passed"] and r["negative"]["reason"] != "no_negative"),
            "negative_reasons": sorted({r["negative"]["reason"] for r in records}),
            "baseline_failed": count(lambda r: r["baseline_failed"]),
            "all_ok": count(lambda r: r["ok"]),
            "by_form": forms,
            "by_why": {w: count(lambda r, w=w: r["why"] == w) for w in sorted({r["why"] for r in records})},
            "not_ok": [r["id"] for r in records if not r["ok"]]}


def markdown(records: list, summary: dict) -> str:
    rev = summary["revision"]
    lines = ["# Fix-check validation on dataset B (BugsInPy/thefuck, real tests, no LLM)", "",
             f"Commit `{rev['commit'][:10]}`{' (src dirty)' if rev['src_dirty'] else ''}. "
             f"Gold fix passes: **{summary['gold_passed']}/{summary['cases']}**; "
             f"negative control rejected: **{summary['negative_rejected']}/{summary['cases']}**; "
             f"unchanged repo fails: **{summary['baseline_failed']}/{summary['cases']}**.", "",
             "| Case | Gold form | Why | Gold check | Negative control | Unchanged repo |",
             "|---|---|---|---|---|---|"]
    for r in records:
        g, ng = r["gold"], r["negative"]
        lines.append(f"| {r['id']} | {r['form']} | {r['why']} | {g['reason']} ({g['mode'] or '-'}) | "
                     f"{'rejected' if not ng['passed'] else 'ACCEPTED'}: {ng['reason']} | "
                     f"{'fails' if r['baseline_failed'] else 'PASSES'} |")
    return "\n".join(lines) + "\n"


def markdown_fixcheck(records: list, summary: dict) -> str:
    """Preserve the named main-branch formatter as well as markdown()."""
    return markdown(records, summary)


def validate_fixcheck(manifest: str, bugsinpy: str, envs: dict, out_dir: str, timeout: int = 120,
                      only: set | None = None, project: str = "thefuck", repo_root=None) -> dict:
    cases = [c for c in load_cases(manifest, root=repo_root) if not only or c.id in only]
    records = []
    for case in cases:
        bug = case.source.get("bugsinpy", "").split("/")[-1]
        with open(os.path.join(bugsinpy, "projects", project, "bugs", bug, "bug_patch.txt"), encoding="utf-8") as fh:
            patch = fh.read()
        rec = validate_case(case, patch, envs.get(case.env), timeout)
        records.append(rec)
        print(f"{rec['id']:>11}  {rec['form']:<8} gold={rec['gold']['reason']:<10} neg={rec['negative']['reason']:<12} "
              f"baseline_failed={rec['baseline_failed']}  {'OK' if rec['ok'] else 'CHECK'}", flush=True)
    summary = summarize(records, _revision())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "fixcheck_validation.jsonl"), "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(out_dir, "fixcheck_validation.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(out_dir, "fixcheck_validation.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown(records, summary))
    return summary



# Both owners share this dispatcher; each action preserves its own result schema.
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="evidence audit (Deep)")
    mode.add_argument("--validate-fixcheck", action="store_true", help="maintainer-fix validation (Tanay)")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", help="override the base directory of manifest checkout paths")
    parser.add_argument("--out", help="defaults to results/deep/audit for evidence, or results for fixcheck")
    parser.add_argument("--chroma", action="store_true", help="also measure Chroma (--audit only)")
    parser.add_argument("--limit", type=int, help="maximum evidence-audit cases")
    parser.add_argument("--deduplicate-chunks", dest="deduplicate", action="store_true",
                        help="last-definition input variant for both rankers (--audit only)")
    parser.add_argument("--bugsinpy", help="BugsInPy checkout for maintainer patches")
    parser.add_argument("--project", default="thefuck")
    parser.add_argument("--env", action="append", default=[], help="NAME=PYTHON (repeatable; fixcheck)")
    parser.add_argument("--cases", default="", help="comma-separated fixcheck case IDs")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.timeout < 1 or (args.limit is not None and args.limit < 1):
        parser.error("timeout and limit must be positive")
    if args.audit:
        if args.cases:
            parser.error("--cases is for --validate-fixcheck; --audit supports --limit")
        summary = run_audit(args.manifest, args.out or "results/deep/audit", chroma=args.chroma,
                            limit=args.limit, deduplicate=args.deduplicate, repo_root=args.repo_root)
        print(json.dumps(summary, indent=2))
        return 1 if summary["errors"] else 0
    if not args.bugsinpy:
        parser.error("--validate-fixcheck needs --bugsinpy")
    if args.chroma or args.deduplicate or args.limit is not None:
        parser.error("--chroma, --deduplicate-chunks and --limit require --audit")
    envs = {}
    for item in args.env:
        name, separator, python = item.partition("=")
        if not separator or not name or not python:
            parser.error("--env must be NAME=PYTHON")
        # Keep a virtualenv's interpreter symlink: resolving it selects the base
        # Python and loses that environment's installed test dependencies.
        envs[name] = os.path.abspath(os.path.expanduser(python))
    only = {value.strip() for value in args.cases.split(",") if value.strip()} or None
    summary = validate_fixcheck(args.manifest, args.bugsinpy, envs, args.out or os.path.join(ROOT, "results"),
                                args.timeout, only, args.project, repo_root=args.repo_root)
    print(json.dumps({key: value for key, value in summary.items() if key != "revision"}, indent=2))
    return 0 if summary["all_ok"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
