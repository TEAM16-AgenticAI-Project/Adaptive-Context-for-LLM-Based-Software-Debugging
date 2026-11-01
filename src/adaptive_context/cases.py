"""Case manifests for real and controlled debugging cases (one JSON object per line).

Model-visible fields:  id, repo (path to the buggy checkout), bug_report
Run-time fields:       test_cmd (how to reproduce), env (name of the Python environment to run tests with)
Evaluator-only fields: gold {"files": [...], "functions": ["path::Qual.name", ...]}, source (provenance)

Gold answers are never passed to a debugger; only `bug_report` and the repository are.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class RealCase:
    id: str
    dataset: str
    repo: str
    bug_report: str
    gold_files: list
    gold_functions: list
    test_cmd: str = ""
    env: str = ""
    source: dict = field(default_factory=dict)
    module_level_change: bool = False

    @property
    def gold_short_names(self) -> set:
        return {g.split("::")[-1].split(".")[-1] for g in self.gold_functions}


def load_cases(manifest: str, root: str | None = None) -> list:
    """Load a JSONL manifest. Relative `repo` paths are resolved against `root` (default: the manifest's folder)."""
    base = root or os.path.dirname(os.path.abspath(manifest))
    out = []
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            repo = d["repo"] if os.path.isabs(d["repo"]) else os.path.join(base, d["repo"])
            gold = d.get("gold", {})
            out.append(RealCase(d["id"], d.get("dataset", ""), repo, d["bug_report"], list(gold.get("files", [])),
                                list(gold.get("functions", [])), d.get("test_cmd", ""), d.get("env", ""),
                                d.get("source", {}), bool(gold.get("module_level_change", False))))
    return out


def norm_path(p: str) -> str:
    return (p or "").strip().replace("\\", "/").lstrip("./")


def score_diagnosis(case: RealCase, diagnosis, context_items) -> dict:
    """Localization + grounding scores against evaluator-only gold metadata."""
    gold_files = {norm_path(f) for f in case.gold_files}
    d_file = norm_path(diagnosis.file)
    file_hit = bool(diagnosis.valid and (d_file in gold_files or any(d_file.endswith("/" + g) or g.endswith("/" + d_file)
                                                                        for g in gold_files if d_file)))
    func_hit = bool(file_hit and diagnosis.function.split(".")[-1] in case.gold_short_names)
    gold_ids = set(case.gold_functions)
    evidence = any(cid in gold_ids and lvl <= 2 for cid, lvl in context_items)
    return {"file_hit": file_hit, "func_hit": func_hit, "evidence": evidence, "grounded": func_hit and evidence}
