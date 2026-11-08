"""Synthetic, executable bug benchmark. Fully deterministic per seed, scales to any repo size / case count.

Each case is a Python repo with:
  * one entry -> callee -> ... call chain (length 1..max_depth+1) spread over random modules,
    where exactly ONE function violates its own docstring contract (the bug),
  * many distractor chains that share vocabulary with the bug report (hard for similarity search),
  * filler comments / long docstrings (so compression has something to remove).
The bug report only names the entry function, so a bug at depth d needs d call hops to find.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

DOMAINS = ["billing", "shipping", "inventory", "payroll", "loyalty", "tax", "refund", "booking", "ledger", "pricing"]
VERBS = ["compute", "apply", "adjust", "normalize", "validate", "scale", "round", "merge", "resolve", "settle"]
ADJ = ["base", "net", "gross", "final", "adjusted", "partial", "monthly", "weekly", "regional", "legacy", "primary", "bulk"]
NOUNS = ["total", "discount", "fee", "rate", "surcharge", "credit", "balance", "score", "margin", "quota"]
FILLER = ["keep this in sync with the policy notes", "values are always handled in whole units",
          "legacy callers depend on this ordering", "do not change without a review from the owning team",
          "this step runs after the validation stage", "rounding is handled by the caller",
          "the result feeds the next stage of the flow", "see the design notes for the rationale"]
SYM = {"add": "+", "sub": "-", "mul": "*"}


@dataclass
class GenParams:
    n_modules: int = 40
    n_distractor_chains: int = 30
    max_depth: int = 4
    filler_lines: int = 4


@dataclass
class Truth:
    file: str
    function: str
    chunk_id: str
    depth: int
    chain: list
    entry: str


@dataclass
class Case:
    id: str
    files: dict
    bug_report: str
    truth: Truth
    test: dict = field(default_factory=dict)


def _apply(op, v, c):
    return v + c if op == "add" else v - c if op == "sub" else v * c


def _doc(op, c, callee):
    if op == "add":
        return f"Add {c} to the result of {callee}." if callee else f"Add {c} to the input."
    if op == "sub":
        return f"Subtract {c} from the result of {callee}." if callee else f"Subtract {c} from the input."
    return f"Multiply the result of {callee} by {c}." if callee else f"Multiply the input by {c}."


class _Namer:
    def __init__(self, rng):
        self.rng, self.used = rng, set()

    def new(self):
        for _ in range(20):
            n = f"{self.rng.choice(VERBS)}_{self.rng.choice(ADJ)}_{self.rng.choice(NOUNS)}"
            if n not in self.used:
                break
        else:
            n += "_" + "".join(self.rng.choices("abcdefghij", k=3))
            while n in self.used:
                n += self.rng.choice("klmnop")
        self.used.add(n)
        return n


def _func_src(rng, name, domain, doc_op, doc_c, code_op, code_c, callee, filler_lines):
    lines = [f"def {name}(x):", f'    """{_doc(doc_op, doc_c, callee)}', "",
             f"    Part of the {domain} pipeline. Keep results consistent with the ledger.", '    """']
    for _ in range(filler_lines):
        lines.append(f"    # {rng.choice(FILLER)}")
    lines.append(f'    trace = ("{name}", x)')
    if callee:
        lines.append(f"    y = {callee}(x)")
        lines.append(f"    return y {SYM[code_op]} {code_c}")
    else:
        lines.append(f"    return x {SYM[code_op]} {code_c}")
    return "\n".join(lines)


def generate_case(seed: int, p: GenParams = GenParams()) -> Case:
    rng = random.Random(seed)
    namer = _Namer(rng)
    domain = rng.choice(DOMAINS)
    depth = rng.randint(0, p.max_depth)
    length = min(p.max_depth + 1, depth + 1 + rng.randint(0, 2))
    modules = [f"pkg/{rng.choice(DOMAINS)}_{i:03d}.py" for i in range(max(2, p.n_modules))]
    funcs = {m: [] for m in modules}
    imports = {m: set() for m in modules}

    def place(chain_names, specs, bug_idx=None, bug=None):
        homes = [rng.choice(modules) for _ in chain_names]
        for i, name in enumerate(chain_names):
            op, c = specs[i]
            code_op, code_c = (bug if i == bug_idx else (op, c))
            callee = chain_names[i + 1] if i + 1 < len(chain_names) else None
            funcs[homes[i]].append(_func_src(rng, name, domain if bug_idx is not None else rng.choice(DOMAINS),
                                             op, c, code_op, code_c, callee, p.filler_lines))
            if callee and homes[i + 1] != homes[i]:
                imports[homes[i]].add(f"from {homes[i + 1][:-3].replace('/', '.')} import {callee}")
        return homes

    # real chain with exactly one contract violation
    chain = [namer.new() for _ in range(length)]
    specs = [(rng.choice(["add", "sub", "mul"]), rng.randint(2, 9)) for _ in chain]
    op, c = specs[depth]
    if rng.random() < 0.5:
        bug = ({"add": "sub", "sub": "add", "mul": "add"}[op], c)
    else:
        bug = (op, c + 1)
    homes = place(chain, specs, depth, bug)

    def evaluate(x, buggy):
        v = x
        for i in reversed(range(length)):
            o, k = bug if (buggy and i == depth) else specs[i]
            v = _apply(o, v, k)
        return v

    for _ in range(50):                      # pick an input where the bug changes the output
        x0 = rng.randint(2, 50)
        expected, got = evaluate(x0, False), evaluate(x0, True)
        if got != expected:
            break

    # distractor chains (contract-consistent)
    for _ in range(p.n_distractor_chains):
        names = [namer.new() for _ in range(rng.randint(1, p.max_depth + 1))]
        place(names, [(rng.choice(["add", "sub", "mul"]), rng.randint(2, 9)) for _ in names])

    files = {}
    for m in modules:
        if funcs[m]:
            rng.shuffle(funcs[m])
            files[m] = "\n".join(sorted(imports[m])) + ("\n\n\n" if imports[m] else "") + "\n\n\n".join(funcs[m]) + "\n"
    noun = chain[0].split("_")[-1]
    report = (f"After the latest release, `{chain[0]}({x0})` returns {got} but the {domain} team expects {expected}. "
              f"The {noun} calculation in the {domain} flow looks wrong somewhere. Find the root cause and suggest a fix.")
    bug_file = homes[depth]
    truth = Truth(bug_file, chain[depth], f"{bug_file}::{chain[depth]}", depth, chain, chain[0])
    return Case(f"case-{seed}", files, report, truth, {"entry": chain[0], "x0": x0, "expected": expected})


_SAFE_FIX = re.compile(r"^return\s+[A-Za-z_]\w*\s*[+\-*]\s*\d+$")


def verify_fix(case: Case, file: str, function: str, fix: str) -> bool:
    """Apply a one-line fix to the named function and run the case's failing test.
    Only `return <name> <op> <int>` lines are accepted, so untrusted model text is never executed."""
    fix = (fix or "").strip()
    if file not in case.files or not _SAFE_FIX.match(fix):
        return False
    patched = dict(case.files)
    lines = patched[file].splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(f"def {function}("))
        ridx = next(i for i in range(start, len(lines)) if lines[i].lstrip().startswith("return"))
    except StopIteration:
        return False
    lines[ridx] = "    " + fix
    patched[file] = "\n".join(lines)
    ns = {}
    try:
        for src in patched.values():
            src = "\n".join(l for l in src.splitlines() if not l.startswith("from pkg"))
            exec(compile(src, "<case>", "exec"), ns)
        return ns[case.test["entry"]](case.test["x0"]) == case.test["expected"]
    except Exception:
        return False
