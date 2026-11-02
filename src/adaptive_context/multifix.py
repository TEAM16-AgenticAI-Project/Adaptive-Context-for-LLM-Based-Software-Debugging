"""Apply a fix that spans several functions and files, and check it with the repository's own tests.

The LangGraph agent uses this for its test-driven repair loop (`--mode agent_verify`), and
`bench/multifile_eval.py` uses it to score multi-file bugs. It overlaps with the single-function fix check
(`fixcheck.check_fix`, verification PR): this module adds what multi-file bugs need:

* several edits in one patch: replace a function or method, add a new one, or insert module-level code
  (`<module>`: imports, aliases, constants) after the imports;
* a dangling-name check: a function the patch removed or renamed that other files still import or call;
* related tests: test files that belong to the changed modules, run before and after the patch, so the model
  learns which tests it broke and which tests about the same functions still fail (the same defect in a
  sibling implementation, for example).

Everything runs in a temporary copy with a timeout. That protects the original files; it is not a sandbox.
"""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import tempfile
import textwrap
from dataclasses import dataclass, field

from .logs import short
from .tools import IGNORE, failure_excerpt, run_pytest_in

log = logging.getLogger(__name__)
_NOT_TESTS = re.compile(r"(^|/)(maint|demos?|examples?|docs?|scripts|benchmarks?|\.tox|build|dist)/")

MODULE = "<module>"
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n|\n?\s*```\s*$")
_FAIL_LINE = re.compile(r"^(FAILED|ERROR|FAIL) (\S+)")


@dataclass
class Edit:
    file: str
    function: str          # 'func', 'Cls.method', or '<module>'
    code: str              # the complete new function, or (with `old`) the new lines
    old: str = ""          # optional: exact current lines inside that function (or file) that `code` replaces


@dataclass
class PatchResult:
    files: dict = field(default_factory=dict)     # path -> new source
    problems: list = field(default_factory=list)
    removed: dict = field(default_factory=dict)   # path -> names the patch removed from that file


def edits_from_diagnosis(d) -> list:
    """The primary fix plus every entry of `d.locations` ({file, function, fix})."""
    out = []
    if getattr(d, "fix", "").strip():
        out.append(Edit(d.file, d.function, d.fix, getattr(d, "replace", "") or ""))
    for loc in getattr(d, "locations", []) or []:
        if isinstance(loc, dict) and str(loc.get("fix", "")).strip():
            out.append(Edit(str(loc.get("file", "")), str(loc.get("function", "")), str(loc["fix"]),
                            str(loc.get("replace", "") or "")))
    return out


def _replace_lines(lines: list, lo: int, hi: int, old: str, new: str) -> list | None:
    """Replace the block `old` (compared line by line without indentation) found in lines[lo:hi] by `new`,
    re-indented to the first matched line. None if the block is not there."""
    want = [ln.strip() for ln in old.strip("\n").splitlines() if ln.strip()]
    if not want:
        return None
    for i in range(lo, hi):
        j, k = i, 0
        while j < hi and k < len(want):
            if not lines[j].strip():                   # blank lines inside the block are allowed
                j += 1
                continue
            if lines[j].strip() != want[k]:
                break
            j, k = j + 1, k + 1
        if k == len(want):
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            block = textwrap.indent(textwrap.dedent(_FENCE.sub("", new.strip("\n"))) + "\n", indent)
            return lines[:i] + block.splitlines(keepends=True) + lines[j:]
    return None


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


def clean_code(code: str) -> str:
    """Strip markdown fences and indentation. Repairs the common model output of a flush-left `def` whose body is
    indented inconsistently (e.g. copied from inside a class)."""
    cand = textwrap.dedent(_FENCE.sub("", code.strip("\n")).rstrip() + "\n")
    if _parses(cand):
        return cand
    lines = cand.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.rstrip().endswith(":") and not ln.startswith((" ", "\t")):
            fixed = "".join(lines[:i + 1]) + textwrap.indent(textwrap.dedent("".join(lines[i + 1:])), "    ")
            if _parses(fixed):
                return fixed
            break
    return cand


def _repo_file(root: str, path: str) -> str | None:
    path = path.strip().lstrip("./").replace("\\", "/")
    if os.path.isfile(os.path.join(root, path)):
        return path
    hits = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("__pycache__", "node_modules")]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
            if rel.endswith("/" + path):
                hits.append(rel)
    return hits[0] if len(hits) == 1 else None


def _defs(tree) -> dict:
    """'func' / 'Cls' / 'Cls.method' -> node (top level and one class level)."""
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out[f"{n.name}.{m.name}"] = m
    return out


def _top_names(tree) -> set:
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))} | {
        t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}


def _apply_one(src: str, e: Edit) -> tuple:
    """Return (new source, problem or '')."""
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        return src, f"{e.file} does not parse before the edit: {ex.msg}"
    lines = src.splitlines(keepends=True)
    code = clean_code(e.code)
    name = e.function.strip().split("::")[-1].strip("`() ")
    if name in (MODULE, "<imports>", ""):
        try:
            only_imports = all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.parse(code).body)
        except SyntaxError:
            return src, f"the module-level code for {e.file} does not parse"
        if not only_imports:      # aliases / constants may use functions defined in the module: append at the end
            return src.rstrip("\n") + "\n\n\n" + code, ""
        last_import = max((n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
        return "".join(lines[:last_import]) + code + "".join(lines[last_import:]), ""
    defs = _defs(tree)
    if e.old.strip():                                     # replace a few lines inside the function (or file)
        node = defs.get(name) or next((v for k, v in defs.items() if k.endswith("." + name)), None)
        lo, hi = ((node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1, node.end_lineno) \
            if node is not None else (0, len(lines))
        out = _replace_lines(lines, lo, hi, e.old, e.code)
        if out is None:
            return src, f"the lines to replace were not found in {e.file}::{e.function}"
        return "".join(out), ""
    if name not in defs and "." not in name:              # bare method name: unique method of that name
        cands = [k for k in defs if k.endswith("." + name)]
        if len(cands) == 1:
            name = cands[0]
        elif cands:
            return src, (f"'{name}' is ambiguous in {e.file} ({', '.join(cands[:4])}): give the function as "
                         "'Class.method'")
    node = defs.get(name)
    if not code.lstrip().startswith(("def ", "async def ", "@", "class ")):
        return src, f"the fix for {e.file}::{e.function} must be the complete new function (def ...)"
    if node is not None:
        start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
        body = textwrap.indent(code, " " * node.col_offset)
        return "".join(lines[:start]) + body + "".join(lines[node.end_lineno:]), ""
    cls = name.rsplit(".", 1)[0] if "." in name else ""
    if cls and isinstance(defs.get(cls), ast.ClassDef):  # new method in an existing class
        c = defs[cls]
        indent = " " * (c.body[0].col_offset if c.body else c.col_offset + 4)
        return "".join(lines[:c.end_lineno]) + "\n" + textwrap.indent(code, indent) + "".join(
            lines[c.end_lineno:]), ""
    if cls:
        return src, f"class {cls} not found in {e.file}"
    return src.rstrip("\n") + "\n\n\n" + code, ""            # new top-level function


def apply_edits(root: str, edits: list) -> PatchResult:
    """Apply edits to the files under `root` in place (call it on a copy). Edits to one file apply in order."""
    res = PatchResult()
    for e in edits:
        path = _repo_file(root, e.file)
        if path is None:
            res.problems.append(f"file not found: {e.file}")
            continue
        if path not in res.files:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                res.files[path] = fh.read()
            res.removed[path] = set()
        new, problem = _apply_one(res.files[path], Edit(path, e.function, e.code, e.old))
        if problem:
            res.problems.append(problem)
            continue
        res.files[path] = new
    for path, src in res.files.items():
        full = os.path.join(root, path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            before = fh.read()
        try:
            after_tree = ast.parse(src)
        except SyntaxError as ex:
            res.problems.append(f"syntax error in {path} after the edit, line {ex.lineno}: {ex.msg}")
            continue
        try:
            res.removed[path] = _top_names(ast.parse(before)) - _top_names(after_tree)
        except SyntaxError:
            pass
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(src)
    return res


def dangling_names(root: str, removed: dict) -> list:
    """[(name, file)] for names a patch removed from a module that other files still use."""
    out = []
    for path, names in removed.items():
        for name in sorted(names):
            pat = re.compile(rf"\b{re.escape(name)}\b")
            for d, dirs, files in os.walk(root):
                dirs[:] = [x for x in dirs if not x.startswith(".") and x != "__pycache__"]
                for f in files:
                    rel = os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
                    if not f.endswith(".py") or rel == path:
                        continue
                    with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                        if pat.search(fh.read()):
                            out.append((name, rel))
    return out


def failing_tests(output: str) -> set:
    return {m.group(2) for m in map(_FAIL_LINE.match, output.splitlines()) if m}


def _target_file(test_cmd: str | None, test_path: str | None) -> str | None:
    for tok in reversed((test_cmd or "").split() + [test_path or ""]):
        if "::" in tok or tok.endswith(".py"):
            return tok.split("::")[0]
        if tok.count(".") >= 2 and not tok.startswith("-"):                 # unittest id test.test_x.Cls.test
            parts = tok.split(".")
            for i in range(len(parts), 0, -1):
                cand = "/".join(parts[:i]) + ".py"
                if cand.startswith(("test", "tests")) or "/test" in cand:
                    return cand
    return None


class TestOracle:
    """Checks a candidate patch: target test, dangling names, and related tests before vs after."""

    __test__ = False                                   # not a pytest test class

    def __init__(self, repo: str, python: str | None = None, test_cmd: str | None = None,
                 test_path: str | None = None, timeout: int = 120, max_related: int = 6):
        self.repo, self.python, self.test_cmd, self.test_path = repo, python, test_cmd, test_path
        self.timeout, self.max_related = timeout, max_related
        self._baseline: dict = {}

    @property
    def available(self) -> bool:
        return bool(self.repo and (self.test_cmd or self.test_path))

    # ------------------------------------------------------------------ helpers
    def _copy(self) -> tuple:
        tmp = tempfile.mkdtemp(prefix="adaptive_fix_")
        work = os.path.join(tmp, "repo")
        shutil.copytree(self.repo, work, ignore=IGNORE, symlinks=True)
        return tmp, work

    def related_test_files(self, changed: list, names=()) -> list:
        """Test files a developer would run for this change, most specific first: the failing test's file, test
        files named after the changed modules, test files next to the failing test that mention a changed function
        (where a sibling implementation's test lives), then test files importing the changed modules."""
        target = _target_file(self.test_cmd, self.test_path)
        out = [target] if target and os.path.isfile(os.path.join(self.repo, target)) else []
        texts: dict = {}

        def text(rel: str) -> str:
            if rel not in texts:
                with open(os.path.join(self.repo, rel), encoding="utf-8", errors="replace") as fh:
                    texts[rel] = fh.read()
            return texts[rel]
        mods = {os.path.splitext(os.path.basename(p))[0] for p in changed} - {"__init__"}
        dotted = {os.path.splitext(p)[0].replace("/", ".") for p in changed}
        tests = []
        for d, dirs, files in os.walk(self.repo):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "__pycache__")
            tests += [rel for rel in (os.path.relpath(os.path.join(d, f), self.repo).replace("\\", "/")
                                      for f in sorted(files) if f.endswith(".py") and re.match(r"tests?_|.*_test\.py$", f))
                      if not _NOT_TESTS.search(rel)]
        stem = lambda rel: re.sub(r"^tests?_|_test$", "", os.path.splitext(os.path.basename(rel))[0])  # noqa: E731
        here = os.path.dirname(target) if target else None
        words = [re.compile(rf"\b{re.escape(n)}\b") for n in names if n and len(n) > 3]
        groups = [
            [t for t in tests if stem(t) in mods],                                       # test_bash.py for bash.py
            [t for t in tests if here is not None and os.path.dirname(t) == here
             and any(w.search(text(t)) for w in words)],                                 # siblings' tests
            [t for t in tests if any(m in text(t) for m in dotted)],                     # importers
        ]
        for group in groups:
            for t in group:
                if len(out) >= self.max_related:
                    return out
                if t not in out:
                    out.append(t)
        return out

    def _run_files(self, work: str, files: list, label: str) -> set:
        if not files:
            return set()
        cmd = f"pytest -q -rf -p no:cacheprovider --continue-on-collection-errors {' '.join(files)}"
        res = run_pytest_in(work, None, self.timeout, 400, self.python, cmd)
        fails = failing_tests(res["output"])
        log.info("    related tests %s: %d file(s), %d failing, %.0fs%s", label, len(files), len(fails),
                 res.get("seconds", 0), " (TIMED OUT)" if res["timed_out"] else "")
        return fails

    def baseline(self, files: list) -> set:
        key = tuple(files)
        if key not in self._baseline:
            tmp, work = self._copy()
            try:
                self._baseline[key] = self._run_files(work, files, "before the fix")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return self._baseline[key]

    # ------------------------------------------------------------------ main entry
    def check(self, d) -> dict:
        edits = d if isinstance(d, list) else edits_from_diagnosis(d)
        if not edits:
            return {"applied": False, "mode": "multi", "passed": False, "reason": "no_fix",
                    "output": "The diagnosis has no fix code to test."}
        tmp, work = self._copy()
        try:
            patch = apply_edits(work, edits)
            changed = sorted(patch.files)
            if patch.problems:
                return {"applied": False, "mode": "multi", "passed": False, "reason": "apply_failed",
                        "files_changed": changed, "output": "Could not apply the fix: " + "; ".join(patch.problems)}
            dangling = dangling_names(work, patch.removed)
            log.info("    applied %d change(s) to %s; dangling names: %d", len(edits), ", ".join(changed), len(dangling))
            target = run_pytest_in(work, self.test_path, self.timeout, 200, self.python, self.test_cmd)
            log.info("    failing test %s (%.0fs)%s", "PASSES" if target["passed"] else "still fails",
                     target.get("seconds", 0), " (TIMED OUT)" if target["timed_out"] else "")
            keys = {e.function.split(".")[-1] for e in edits if e.function not in (MODULE, "")}
            files = self.related_test_files(changed, sorted(keys))
            log.info("    related test files: %s", short(", ".join(files) or "none", 240))
            before, after = self.baseline(files), self._run_files(work, files, "after the fix")
            regressions = sorted(after - before)
            target_name = re.split(r"[.:]", (self.test_cmd or self.test_path or "").split()[-1])[-1]
            target_name = target_name.split("[")[0]
            keys |= {target_name} if target_name else set()
            tfile = _target_file(self.test_cmd, self.test_path)
            is_target = (lambda t: bool(tfile) and t.startswith(tfile) and bool(target_name)  # noqa: E731
                         and target_name in t)
            still = sorted(t for t in after & before if any(k and k in t for k in keys) and not is_target(t))
            passed = target["passed"] and not regressions and not dangling and not still
            reason = ("tests_pass" if passed else "tests_fail" if not target["passed"] else
                      "regressions" if regressions else "dangling_names" if dangling else "related_failing")
            return {"applied": True, "mode": "multi", "passed": passed, "reason": reason, "files_changed": changed,
                    "target_passed": target["passed"], "regressions": regressions, "related_failing": still,
                    "dangling": dangling, "related_files": files,
                    "output": self.feedback(edits, changed, target, regressions, still, dangling)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def feedback(edits, changed, target, regressions, still, dangling) -> str:
        lines = [f"Your fix changed {len(edits)} place(s) in {len(changed)} file(s): {', '.join(changed)}."]
        if not target["passed"]:
            lines.append(f"The failing test still fails:\n{failure_excerpt(target['output'])}")
        else:
            lines.append("The failing test now passes.")
        if regressions:
            lines.append("Your change broke tests that passed before: " + ", ".join(regressions[:6]))
        if dangling:
            lines.append("These names no longer exist but other files still use them: " +
                         ", ".join(f"{n} (in {f})" for n, f in dangling[:6]) +
                         ". Update those files too, or keep the old name.")
        if still:
            lines.append("Related tests about the same functions still fail, so the same defect may exist in other "
                         "places (e.g. sibling implementations): " + ", ".join(still[:6]))
        return "\n".join(lines)
