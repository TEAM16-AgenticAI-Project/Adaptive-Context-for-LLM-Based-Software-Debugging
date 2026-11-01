"""LLM-free evidence audit. Gold labels are used for scoring only, never retrieval.

Tanay owns the future --validate-fixcheck action. This module exposes run_audit
separately so that action can share main() without changing the audit schema.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
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
    ranks = {chunk.id: rank for rank, (chunk, _) in enumerate(hits, 1)}
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


def summarize(rows: list) -> dict:
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


def run_audit(manifest: str, out: str, *, chroma=False, limit=None, deduplicate=False) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cases = load_cases(manifest)
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
    summary = {"revision": revision(), "manifest": manifest,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--manifest", default="data/real/thefuck.jsonl")
    parser.add_argument("--out", default="results/deep/audit")
    parser.add_argument("--chroma", action="store_true", help="also measure the actual optional ChromaRetriever")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--deduplicate-chunks", dest="deduplicate", action="store_true",
                        help="explicit comparison variant: keep last definition per ID for BOTH rankers")
    args = parser.parse_args(argv)
    result = run_audit(args.manifest, args.out, chroma=args.chroma, limit=args.limit, deduplicate=args.deduplicate)
    print(json.dumps(result, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
