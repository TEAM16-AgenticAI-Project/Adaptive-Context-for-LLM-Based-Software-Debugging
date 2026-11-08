"""Web service for the adaptive-context debugger.

The service imports the engine from ``src/adaptive_context`` in this repository, so the UI
always runs the same code as the CLI and the evaluation harness.

A request names a repository (a bundled example, a generated benchmark case, an uploaded
zip, a public git URL, or a server path when enabled), a bug report, and one or more
systems. Each system runs on the same retriever and the response holds, per system, the
diagnosis, the evidence the model saw (with its compression level), the round-by-round
trace and the run statistics. When the repository has a known answer (examples and
generated cases), each run is scored against it after the run. The answer is never part
of the prompt.

LLM backends:
  sim   deterministic offline simulator (no key, no network). It measures how well the
        context manager delivers evidence; it only recognises the generated bug pattern.
  groq  openai/gpt-oss-20b on Groq. Needs GROQ_API_KEY in the server environment.

Running a repository's tests executes its code, so fix verification for examples, uploads
and git repos is off unless ALLOW_RUN_TESTS=1. Generated cases are always verified with
the benchmark's own checker, which only accepts ``return <name> <op> <int>`` lines.
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]          # repository root (ui/backend/main.py)
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from adaptive_context.bench.generate import GenParams, generate_case, verify_fix  # noqa: E402
from adaptive_context.bench.systems import make_system  # noqa: E402
from adaptive_context.cases import RealCase, load_cases, score_diagnosis  # noqa: E402
from adaptive_context.chunking import chunk_repo, load_repo_dir  # noqa: E402
from adaptive_context.compress import LEVELS, compress  # noqa: E402
from adaptive_context.llm import GroqLLM, LLMError  # noqa: E402
from adaptive_context.retrieval import BM25Retriever  # noqa: E402
from adaptive_context.sim import SimulatedLLM  # noqa: E402
from adaptive_context.tools import Tools  # noqa: E402

VERSION = "2.0.0"
log = logging.getLogger("acdbg")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# configuration (environment only)
# ---------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DEFAULT_LLM = os.environ.get("DEFAULT_LLM", "sim").strip().lower()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
ALLOW_LOCAL_PATH = _env_bool("ALLOW_LOCAL_PATH", False)
ALLOW_GIT_CLONE = _env_bool("ALLOW_GIT_CLONE", True)
ALLOW_RUN_TESTS = _env_bool("ALLOW_RUN_TESTS", False)
GIT_ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get(
    "GIT_ALLOWED_HOSTS", "github.com,gitlab.com,bitbucket.org").split(",") if h.strip()]
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 25)
MAX_UNZIPPED_MB = _env_int("MAX_UNZIPPED_MB", 100)
MAX_REPO_FILES = _env_int("MAX_REPO_FILES", 4000)
CLONE_TIMEOUT_S = _env_int("CLONE_TIMEOUT_S", 60)
TEST_TIMEOUT_S = _env_int("TEST_TIMEOUT_S", 120)
RATE_LIMIT_PER_MIN = _env_int("RATE_LIMIT_PER_MIN", 30)
MAX_CONCURRENT_RUNS = max(1, _env_int("MAX_CONCURRENT_RUNS", 2))
QUEUE_WAIT_S = _env_int("QUEUE_WAIT_S", 30)
MAX_SYSTEMS_PER_RUN = 5
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
EVIDENCE_TEXT_LIMIT = 4000

SYSTEMS = {
    "static_k3": ("Static RAG, Top-3", "Milestone 1 baseline: one retrieval, three chunks, one LLM call."),
    "static_k10": ("Static RAG, Top-10", "Wider static baseline: does retrieving more help?"),
    "adaptive": ("Adaptive loop", "Requests symbols and queries each round under a token budget."),
    "adaptive_graph": ("Adaptive + call graph", "Adaptive loop that also prefetches callees of what it reads."),
    "agent": ("LangGraph agent", "Agent that calls the six evidence tools as functions."),
}
MODE_ALIASES = {"baseline": "static_k3"}
GEN_SIZES = {"small": GenParams(12, 8), "medium": GenParams(40, 30), "large": GenParams(120, 100)}


def _agent_available() -> bool:
    return importlib.util.find_spec("langgraph") is not None


def _groq_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# examples with a known answer (used only for scoring after a run)
# ---------------------------------------------------------------------------
def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _load_examples() -> dict:
    out = {}
    pay = ROOT / "examples" / "payment_bug"
    if pay.is_dir():
        out["payment_bug"] = {
            "id": "payment_bug", "group": "Bundled", "title": "Payment: the discount is never applied",
            "dir": pay, "bug_report": _read(pay / "bug_report.txt"), "test_cmd": "pytest -q test_payment.py",
            "gold": {"files": ["payment.py"], "functions": ["payment.py::checkout"]}}
    cart = ROOT / "ui" / "examples" / "cart_app"
    if cart.is_dir():
        out["cart_app"] = {
            "id": "cart_app", "group": "Bundled", "title": "Cart: tax charged on the full price",
            "dir": cart, "bug_report": _read(cart / "BUG_REPORT.txt"), "test_cmd": "pytest -q test_cart.py",
            "gold": {"files": ["tax.py"], "functions": ["tax.py::apply_tax"]}}
    manifest = ROOT / "data" / "controlled" / "cases.jsonl"
    if manifest.is_file():
        for c in load_cases(str(manifest)):
            if Path(c.repo).is_dir():
                out[c.id] = {"id": c.id, "group": "Controlled (lexical mismatch)", "title": c.bug_report,
                             "dir": Path(c.repo), "bug_report": c.bug_report, "test_cmd": c.test_cmd or None,
                             "gold": {"files": list(c.gold_files), "functions": list(c.gold_functions)}}
    return out


EXAMPLES = _load_examples()


# ---------------------------------------------------------------------------
# app, middleware, rate limiting
# ---------------------------------------------------------------------------
app = FastAPI(title="Adaptive-Context Debugger", version=VERSION,
              description="LLM-based Python debugging with adaptive context management.")
if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return resp


_rate_lock = threading.Lock()
_rate_hits: dict = defaultdict(deque)
_run_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate(request: Request) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return
    now, ip = time.monotonic(), _client_ip(request)
    with _rate_lock:
        q = _rate_hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(429, f"rate limit: at most {RATE_LIMIT_PER_MIN} runs per minute")
        q.append(now)


# ---------------------------------------------------------------------------
# repository sources
# ---------------------------------------------------------------------------
def normalize_git_url(url: str) -> tuple:
    """Return (clone_url, branch or None) for a public https repository URL.

    Accepts what people paste from a browser: with or without `.git`, a trailing slash,
    or a GitHub/GitLab `/tree/<branch>` page. Only https and the allowed hosts are accepted.
    """
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme != "https" or not p.netloc or p.username or p.password:
        raise HTTPException(400, "git_url must be a public https URL, e.g. https://github.com/owner/repo")
    host = p.hostname.lower() if p.hostname else ""
    if GIT_ALLOWED_HOSTS and "*" not in GIT_ALLOWED_HOSTS and host not in GIT_ALLOWED_HOSTS:
        raise HTTPException(400, f"git host '{host}' is not allowed; allowed: {', '.join(GIT_ALLOWED_HOSTS)}")
    path, branch = p.path.rstrip("/"), None
    m = re.match(r"^(/[^/]+/[^/]+?)(?:\.git)?(?:/-)?/tree/([^/]+)", path)
    if m:
        path, branch = m.group(1), m.group(2)
    m = re.match(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$", path)
    if not m:
        raise HTTPException(400, "git_url must point at a repository: https://<host>/<owner>/<repo>")
    if branch is not None and not re.match(r"^[A-Za-z0-9_./-]{1,100}$", branch):
        raise HTTPException(400, "invalid branch name in git_url")
    return f"https://{host}/{m.group(1)}/{m.group(2)}.git", branch


def _extract_zip(data: bytes, dest: Path) -> Path:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, "upload is not a valid .zip file") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_REPO_FILES * 5:
            raise HTTPException(413, "zip has too many entries")
        if sum(i.file_size for i in infos) > MAX_UNZIPPED_MB * 1024 * 1024:
            raise HTTPException(413, f"zip expands to more than {MAX_UNZIPPED_MB} MB")
        root = dest.resolve()
        for info in infos:
            target = (dest / info.filename).resolve()
            if target != root and root not in target.parents:
                raise HTTPException(400, "zip contains a path outside the archive")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(400, "zip contains a symbolic link")
        zf.extractall(dest)
    entries = [p for p in dest.iterdir() if p.name not in ("__MACOSX", ".DS_Store")]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest


def _clone(url: str, dest: Path) -> Path:
    if not ALLOW_GIT_CLONE:
        raise HTTPException(403, "cloning git repositories is disabled on this server")
    clone_url, branch = normalize_git_url(url)
    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, str(dest)]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=CLONE_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(504, f"git clone timed out after {CLONE_TIMEOUT_S}s") from e
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
        raise HTTPException(400, f"git clone failed (is the repository public?): {msg[0][:200]}") from e
    return dest


def _repo_from_source(tmp: Path, *, example, gen_seed, gen_size, upload_bytes, upload_name, git_url,
                      local_path) -> dict:
    chosen = [s for s, v in (("example", example), ("generated", gen_seed), ("upload", upload_bytes),
                             ("git", git_url), ("local", local_path)) if v not in (None, "", b"")]
    if len(chosen) != 1:
        raise HTTPException(400, "choose exactly one repository source: example, generated, upload, git_url, local_path")
    src = chosen[0]
    if src == "example":
        ex = EXAMPLES.get(example)
        if ex is None:
            raise HTTPException(404, f"unknown example '{example}'")
        return {"source": "example", "label": ex["title"], "dir": ex["dir"], "files": load_repo_dir(str(ex["dir"])),
                "gold": ex["gold"], "test_cmd": ex["test_cmd"], "default_report": ex["bug_report"], "case": None}
    if src == "generated":
        try:
            seed = int(gen_seed)
        except ValueError as e:
            raise HTTPException(400, "gen_seed must be an integer") from e
        if not 0 <= seed <= 10_000_000:
            raise HTTPException(400, "gen_seed must be between 0 and 10000000")
        params = GEN_SIZES.get(gen_size or "medium")
        if params is None:
            raise HTTPException(400, f"gen_size must be one of {', '.join(GEN_SIZES)}")
        case = generate_case(seed, params)
        gold = {"files": [case.truth.file], "functions": [case.truth.chunk_id]}
        return {"source": "generated", "label": f"Generated case seed={seed} ({gen_size or 'medium'})", "dir": None,
                "files": case.files, "gold": gold, "test_cmd": None, "default_report": case.bug_report, "case": case}
    if src == "upload":
        if len(upload_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_MB} MB")
        d = tmp / "upload"
        d.mkdir()
        repo = _extract_zip(upload_bytes, d)
        return {"source": "upload", "label": upload_name or "uploaded.zip", "dir": repo,
                "files": load_repo_dir(str(repo)), "gold": None, "test_cmd": None, "default_report": "", "case": None}
    if src == "git":
        repo = _clone(git_url, tmp / "git")
        return {"source": "git", "label": normalize_git_url(git_url)[0], "dir": repo,
                "files": load_repo_dir(str(repo)), "gold": None, "test_cmd": None, "default_report": "", "case": None}
    if not ALLOW_LOCAL_PATH:
        raise HTTPException(403, "server paths are disabled on this server; use an example, upload or git URL")
    p = Path(local_path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, f"not a directory on the server: {local_path}")
    return {"source": "local", "label": str(p), "dir": p, "files": load_repo_dir(str(p)), "gold": None,
            "test_cmd": None, "default_report": "", "case": None}


# ---------------------------------------------------------------------------
# running systems
# ---------------------------------------------------------------------------
def _parse_systems(systems: str | None, mode: str | None) -> list:
    raw = systems if systems else (mode or "adaptive_graph")
    names = []
    for n in str(raw).split(","):
        n = MODE_ALIASES.get(n.strip(), n.strip())
        if not n:
            continue
        if n not in SYSTEMS:
            raise HTTPException(400, f"unknown system '{n}'; choose from {', '.join(SYSTEMS)}")
        if n == "agent" and not _agent_available():
            raise HTTPException(400, "the agent system needs langgraph, which is not installed on this server")
        if n not in names:
            names.append(n)
    if not names:
        raise HTTPException(400, "choose at least one system")
    if len(names) > MAX_SYSTEMS_PER_RUN:
        raise HTTPException(400, f"at most {MAX_SYSTEMS_PER_RUN} systems per run")
    return names


def _make_llm(name: str, model: str | None):
    if name == "groq":
        if not _groq_available():
            raise HTTPException(503, "groq needs GROQ_API_KEY on the server; choose the sim LLM instead")
        try:
            return GroqLLM(model=model or GROQ_MODEL)
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
    return SimulatedLLM()


def _build(name: str, retr, llm, llm_name: str, cfg: dict):
    if name == "agent":
        from adaptive_context.agent_graph import LangGraphAgent, SimToolLLM
        agent_llm = SimToolLLM(llm) if llm_name == "sim" else llm
        # no repo_root: the agent cannot run the repository's tests from the web service
        return LangGraphAgent(retr, agent_llm, tools=Tools(retr), initial_k=cfg["top_k"],
                              max_rounds=cfg["rounds"], token_budget=cfg["budget"])
    return make_system(name, retr, llm, cfg)


def _jsonable(x, limit: int = 600):
    if isinstance(x, str):
        return x if len(x) <= limit else x[:limit] + " ..."
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): _jsonable(v, limit) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_jsonable(v, limit) for v in x]
    return _jsonable(str(x), limit)


def _evidence(retr, items, gold_ids: set) -> list:
    out = []
    for cid, lvl in items or []:
        ch = retr.by_id.get(cid)
        text = compress(ch, lvl) if ch is not None else ""
        if len(text) > EVIDENCE_TEXT_LIMIT:
            text = text[:EVIDENCE_TEXT_LIMIT] + "\n# ... cut for display"
        out.append({"chunk_id": cid, "path": ch.path if ch else "", "name": ch.name if ch else cid,
                    "start": ch.start if ch else None, "end": ch.end if ch else None, "level": lvl,
                    "level_name": LEVELS[lvl] if 0 <= lvl < len(LEVELS) else str(lvl), "text": text,
                    "is_answer": cid in gold_ids})
    return out


def _score(repo: dict, report: str, diag, items) -> dict | None:
    gold = repo["gold"]
    if not gold:
        return None
    if repo["case"] is not None:                          # generated case: benchmark scoring
        t = repo["case"].truth
        file_hit = bool(diag.valid and diag.file == t.file)
        func_hit = bool(file_hit and diag.function.split(".")[-1] == t.function)
        evidence = any(cid == t.chunk_id and lvl <= 2 for cid, lvl in items)
        fix_ok = bool(func_hit and verify_fix(repo["case"], diag.file, diag.function, diag.fix))
        return {"file_hit": file_hit, "func_hit": func_hit, "evidence": evidence,
                "grounded": func_hit and evidence, "fix_ok": fix_ok}
    case = RealCase("ui", "ui", str(repo["dir"]), report, gold["files"], gold["functions"])
    return score_diagnosis(case, diag, items)


def _verify(repo: dict, diag, test_path: str | None, want: bool) -> dict | None:
    if not want or repo["case"] is not None:
        return None
    if not ALLOW_RUN_TESTS:
        return {"skipped": True, "reason": "running repository tests is disabled on this server (ALLOW_RUN_TESTS=0)"}
    if not (diag.valid and diag.file and diag.function and diag.fix.strip()):
        return {"skipped": True, "reason": "no file, function and fix to check"}
    test_cmd = repo["test_cmd"]
    if not test_cmd and not test_path:
        return {"skipped": True, "reason": "no test to run; give a test path"}
    from adaptive_context.fixcheck import check_fix
    res = check_fix(str(repo["dir"]), diag.file, diag.function, diag.fix, test_path=test_path or None,
                    test_cmd=None if test_path else test_cmd, python=sys.executable, timeout=TEST_TIMEOUT_S)
    return _jsonable(res, 3000)


def _run_one(name: str, retr, repo: dict, report: str, llm, llm_name: str, cfg: dict, test_path, want_verify,
             gold_ids: set) -> dict:
    label, _ = SYSTEMS[name]
    t0 = time.perf_counter()
    try:
        res = _build(name, retr, llm, llm_name, cfg).run(report)
    except LLMError as e:
        return {"system": name, "label": label, "error": f"LLM error: {e}"}
    except Exception as e:  # noqa: BLE001 - one failing system must not hide the others
        log.exception("system %s failed", name)
        return {"system": name, "label": label, "error": f"{type(e).__name__}: {e}"}
    wall = time.perf_counter() - t0
    stats = asdict(res.stats)
    stats["total_tokens"] = res.stats.total_tokens
    stats["wall_s"] = stats.get("wall_s") or wall
    items = list(res.context_items or [])
    return {"system": name, "label": label, "error": None, "diagnosis": asdict(res.diagnosis),
            "stop_reason": res.stop_reason, "stats": stats, "evidence": _evidence(retr, items, gold_ids),
            "trace": _jsonable(res.trace or []), "score": _score(repo, report, res.diagnosis, items),
            "verification": _verify(repo, res.diagnosis, test_path, want_verify)}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "version": VERSION, "groq_key_present": _groq_available(),
            "default_llm": _effective_default_llm()}


def _effective_default_llm() -> str:
    return "groq" if DEFAULT_LLM == "groq" and _groq_available() else "sim"


@app.get("/api/config")
def config():
    agent_ok = _agent_available()
    return {
        "version": VERSION,
        "llms": [
            {"id": "sim", "label": "Simulator (offline, free)", "available": True,
             "note": "Measures evidence delivery. Solves generated cases only."},
            {"id": "groq", "label": f"Groq: {GROQ_MODEL}", "available": _groq_available(),
             "note": "Real model." if _groq_available() else "Disabled: GROQ_API_KEY is not set on this server."},
        ],
        "default_llm": _effective_default_llm(),
        "systems": [{"id": k, "label": v[0], "description": v[1],
                     "available": agent_ok if k == "agent" else True,
                     "note": "" if (k != "agent" or agent_ok) else "langgraph is not installed on this server"}
                    for k, v in SYSTEMS.items()],
        "default_systems": ["static_k3", "adaptive_graph"],
        "sources": {"example": bool(EXAMPLES), "generated": True, "upload": True, "git": ALLOW_GIT_CLONE,
                    "local": ALLOW_LOCAL_PATH},
        "git_hosts": GIT_ALLOWED_HOSTS,
        "verify_tests": ALLOW_RUN_TESTS,
        "generated_sizes": list(GEN_SIZES),
        "limits": {"max_upload_mb": MAX_UPLOAD_MB, "max_repo_files": MAX_REPO_FILES,
                   "max_systems": MAX_SYSTEMS_PER_RUN, "rate_limit_per_min": RATE_LIMIT_PER_MIN},
        "defaults": {"budget": 3000, "rounds": 6, "top_k": 3, "prefetch": 2},
    }


@app.get("/api/examples")
def examples():
    return [{"id": e["id"], "group": e["group"], "title": e["title"], "bug_report": e["bug_report"],
             "has_answer": bool(e["gold"]), "has_test": bool(e["test_cmd"])} for e in EXAMPLES.values()]


@app.get("/api/examples/{example_id}/download")
def download_example(example_id: str):
    ex = EXAMPLES.get(example_id)
    if ex is None:
        raise HTTPException(404, "unknown example")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ex["dir"].rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                zf.write(p, f"{example_id}/{p.relative_to(ex['dir']).as_posix()}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{example_id}.zip"'})


@app.get("/api/generated")
def generated(seed: int = 0, size: str = "medium"):
    if size not in GEN_SIZES or not 0 <= seed <= 10_000_000:
        raise HTTPException(400, "bad seed or size")
    case = generate_case(seed, GEN_SIZES[size])
    return {"seed": seed, "size": size, "bug_report": case.bug_report, "files": len(case.files),
            "chunks": len(chunk_repo(case.files)), "depth": case.truth.depth}


@app.post("/api/debug")
def debug(
    request: Request,
    bug_report: str = Form(""),
    systems: str | None = Form(None),
    mode: str | None = Form(None),
    llm: str | None = Form(None),
    model: str | None = Form(None),
    budget: int = Form(3000),
    rounds: int = Form(6),
    top_k: int = Form(3),
    prefetch: int = Form(2),
    example: str | None = Form(None),
    gen_seed: str | None = Form(None),
    gen_size: str | None = Form(None),
    git_url: str | None = Form(None),
    local_path: str | None = Form(None),
    test_path: str | None = Form(None),
    verify: str | None = Form(None),
    upload: UploadFile | None = File(None),
):
    _check_rate(request)
    names = _parse_systems(systems, mode)
    llm_name = (llm or _effective_default_llm()).strip().lower()
    if llm_name not in ("sim", "groq"):
        raise HTTPException(400, "llm must be 'sim' or 'groq'")
    if model and not re.match(r"^[\w./:-]{1,80}$", model):
        raise HTTPException(400, "invalid model name")
    for name, lo, hi, val in (("budget", 300, 32000, budget), ("rounds", 1, 12, rounds),
                              ("top_k", 1, 30, top_k), ("prefetch", 0, 4, prefetch)):
        if not lo <= val <= hi:
            raise HTTPException(400, f"{name} must be between {lo} and {hi}")
    if test_path and (test_path.startswith(("/", "-")) or ".." in Path(test_path).parts):
        raise HTTPException(400, "test_path must be a relative path inside the repository")
    upload_bytes = None
    if upload is not None and upload.filename:
        upload_bytes = upload.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    want_verify = (verify or "").lower() in ("1", "true", "on", "yes")

    if not _run_slots.acquire(timeout=QUEUE_WAIT_S):
        raise HTTPException(503, "the server is busy; try again in a moment")
    tmp = Path(tempfile.mkdtemp(prefix="acdbg_"))
    t0 = time.perf_counter()
    try:
        repo = _repo_from_source(tmp, example=example, gen_seed=gen_seed, gen_size=gen_size,
                                 upload_bytes=upload_bytes, upload_name=upload.filename if upload else None,
                                 git_url=git_url, local_path=local_path)
        report = bug_report.strip() or repo["default_report"]
        if not report:
            raise HTTPException(400, "describe the bug: the bug report is empty")
        if len(report) > 20000:
            raise HTTPException(400, "bug report is too long (max 20000 characters)")
        files = repo["files"]
        if not files:
            raise HTTPException(400, "no Python (.py) files found in the repository")
        if len(files) > MAX_REPO_FILES:
            raise HTTPException(413, f"repository has {len(files)} Python files; the limit is {MAX_REPO_FILES}")
        chunks = chunk_repo(files)
        retr = BM25Retriever(chunks)
        llm_obj = _make_llm(llm_name, model)
        cfg = {"top_k": top_k, "budget": budget, "rounds": rounds, "prefetch_depth": prefetch}
        gold_ids = set(repo["gold"]["functions"]) if repo["gold"] else set()
        runs = [_run_one(n, retr, repo, report, llm_obj, llm_name, cfg, test_path, want_verify, gold_ids)
                for n in names]
        elapsed = time.perf_counter() - t0
        log.info("run source=%s systems=%s llm=%s files=%d elapsed=%.2fs", repo["source"], ",".join(names),
                 llm_name, len(files), elapsed)
        return JSONResponse({
            "repo": {"source": repo["source"], "label": repo["label"], "files": len(files), "chunks": len(chunks)},
            "bug_report": report,
            "llm": {"id": llm_name, "model": (model or GROQ_MODEL) if llm_name == "groq" else "simulator"},
            "settings": {"budget": budget, "rounds": rounds, "top_k": top_k, "prefetch": prefetch},
            "answer": repo["gold"],
            "runs": runs,
            "elapsed_s": round(elapsed, 3),
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _run_slots.release()


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def _dump_config() -> str:  # pragma: no cover - debugging helper: python main.py
    return json.dumps(config(), indent=2)


if __name__ == "__main__":  # pragma: no cover
    print(_dump_config())
