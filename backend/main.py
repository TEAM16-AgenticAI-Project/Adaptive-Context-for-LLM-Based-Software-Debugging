"""Adaptive-Context Debugger — production web API.

Wraps the `adaptive_context` engine (adaptive retrieval, compression, sequencing, token
budget) behind a small FastAPI service. A user submits a bug report plus a repository
(an uploaded zip, a public git URL, or a server-local path) and gets back the diagnosed
file + function, the cause, a suggested fix, the *evidence* the model actually read, and a
round-by-round trace.

Two LLM backends:
  * sim  -- the offline deterministic SimulatedLLM (no API key, free; measures context delivery)
  * groq -- the real openai/gpt-oss-20b via Groq (needs GROQ_API_KEY in the environment)

Safety: executing a repository's test suite runs its code. That is OFF by default in this
service (RUN_TESTS=0). Only enable it for repositories you trust, ideally inside the provided
container, which is a weak isolation boundary -- not a security sandbox.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from adaptive_context.adaptive import AdaptiveDebugger
from adaptive_context.baseline import StaticRAG
from adaptive_context.chunking import chunk_repo, load_repo_dir
from adaptive_context.llm import GroqLLM, LLMError
from adaptive_context.retrieval import BM25Retriever
from adaptive_context.sim import SimulatedLLM

# ---------------------------------------------------------------------------
# configuration (all via environment, 12-factor style)
# ---------------------------------------------------------------------------
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
MAX_REPO_FILES = int(os.environ.get("MAX_REPO_FILES", "4000"))
CLONE_TIMEOUT_S = int(os.environ.get("CLONE_TIMEOUT_S", "60"))
ALLOW_LOCAL_PATH = os.environ.get("ALLOW_LOCAL_PATH", "1") == "1"
ALLOW_GIT_CLONE = os.environ.get("ALLOW_GIT_CLONE", "1") == "1"
DEFAULT_LLM = os.environ.get("DEFAULT_LLM", "sim")
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

app = FastAPI(title="Adaptive-Context Debugger", version="1.0.0",
              description="LLM-based Python debugging with adaptive context management.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
class DebugResponse(BaseModel):
    diagnosis: dict
    stop_reason: str
    stats: dict
    context: list
    trace: list
    repo_stats: dict
    config: dict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _build_llm(llm_name: str, model: str | None):
    if llm_name == "groq":
        if not os.environ.get("GROQ_API_KEY"):
            raise HTTPException(503, "groq mode needs GROQ_API_KEY set on the server; use llm=sim otherwise")
        try:
            return GroqLLM(model=model or None)
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
    return SimulatedLLM()


def _materialize_repo(tmp: Path, *, upload, git_url, local_path) -> Path:
    """Return a local directory holding the repo to analyse, honouring the enabled sources."""
    sources = [bool(upload), bool(git_url), bool(local_path)]
    if sum(sources) != 1:
        raise HTTPException(400, "provide exactly one of: upload, git_url, local_path")

    if upload:
        dest = tmp / "repo"
        dest.mkdir()
        zpath = tmp / "upload.zip"
        data = upload.file.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_MB} MB")
        zpath.write_bytes(data)
        try:
            with zipfile.ZipFile(zpath) as zf:
                for member in zf.namelist():
                    # zip-slip guard: never let a member escape dest
                    target = (dest / member).resolve()
                    if not str(target).startswith(str(dest.resolve())):
                        raise HTTPException(400, "unsafe path in zip")
                zf.extractall(dest)
        except zipfile.BadZipFile as e:
            raise HTTPException(400, "upload is not a valid zip") from e
        # if the zip had a single top folder, descend into it
        entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
        return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest

    if git_url:
        if not ALLOW_GIT_CLONE:
            raise HTTPException(403, "git clone is disabled on this server")
        if not (git_url.startswith("https://") and git_url.endswith(".git")):
            raise HTTPException(400, "git_url must be an https URL ending in .git")
        dest = tmp / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", git_url, str(dest)],
                check=True, capture_output=True, timeout=CLONE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise HTTPException(504, "git clone timed out") from e
        except subprocess.CalledProcessError as e:
            raise HTTPException(400, f"git clone failed: {e.stderr.decode(errors='replace')[:300]}") from e
        return dest

    # local_path
    if not ALLOW_LOCAL_PATH:
        raise HTTPException(403, "local path access is disabled on this server")
    p = Path(local_path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {local_path}")
    return p


def _run_debug(repo_dir: Path, bug_report: str, *, mode: str, llm_name: str, model: str | None,
               budget: int, rounds: int, top_k: int, prefetch: int) -> dict:
    files = load_repo_dir(str(repo_dir))
    if not files:
        raise HTTPException(400, "no .py files found in the repository")
    if len(files) > MAX_REPO_FILES:
        raise HTTPException(413, f"repository has {len(files)} files; limit is {MAX_REPO_FILES}")
    chunks = chunk_repo(files)
    retr = BM25Retriever(chunks)
    llm = _build_llm(llm_name, model)

    if mode == "baseline":
        system = StaticRAG(retr, llm, top_k=top_k)
    else:  # adaptive
        system = AdaptiveDebugger(retr, llm, initial_k=top_k, max_rounds=rounds,
                                  token_budget=budget, prefetch_depth=prefetch)
    t0 = time.perf_counter()
    result = system.run(bug_report)
    wall = time.perf_counter() - t0

    diag = asdict(result.diagnosis)
    stats = asdict(result.stats)
    stats.setdefault("wall_s", wall)
    # evidence: the chunk ids (and compression level) that were in the final prompt
    context = [{"chunk_id": cid, "level": lvl} for cid, lvl in (result.context_items or [])]
    return {
        "diagnosis": diag,
        "stop_reason": result.stop_reason,
        "stats": stats,
        "context": context,
        "trace": result.trace or [],
        "repo_stats": {"files": len(files), "chunks": len(chunks)},
        "config": {"mode": mode, "llm": llm_name, "model": model, "budget": budget,
                   "rounds": rounds, "top_k": top_k, "prefetch": prefetch},
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "groq_key_present": bool(os.environ.get("GROQ_API_KEY")),
            "default_llm": DEFAULT_LLM}


@app.post("/api/debug", response_model=DebugResponse)
def debug(
    bug_report: str = Form(...),
    mode: str = Form("adaptive"),
    llm: str = Form(None),
    model: str = Form(None),
    budget: int = Form(3000),
    rounds: int = Form(6),
    top_k: int = Form(3),
    prefetch: int = Form(2),
    git_url: str = Form(None),
    local_path: str = Form(None),
    upload: UploadFile = File(None),
):
    if not bug_report.strip():
        raise HTTPException(400, "bug_report is required")
    if mode not in ("baseline", "adaptive"):
        raise HTTPException(400, "mode must be 'baseline' or 'adaptive'")
    llm_name = (llm or DEFAULT_LLM).lower()
    if llm_name not in ("sim", "groq"):
        raise HTTPException(400, "llm must be 'sim' or 'groq'")
    for name, lo, hi, val in (("budget", 100, 32000, budget), ("rounds", 1, 20, rounds),
                              ("top_k", 1, 50, top_k), ("prefetch", 0, 5, prefetch)):
        if not lo <= val <= hi:
            raise HTTPException(400, f"{name} must be between {lo} and {hi}")

    tmp = Path(tempfile.mkdtemp(prefix="acdbg_"))
    try:
        repo_dir = _materialize_repo(tmp, upload=upload, git_url=git_url, local_path=local_path)
        return JSONResponse(_run_debug(repo_dir, bug_report, mode=mode, llm_name=llm_name, model=model,
                                       budget=budget, rounds=rounds, top_k=top_k, prefetch=prefetch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/example")
def example():
    """Return the bundled payment_bug example so the UI has a one-click demo."""
    report = (EXAMPLES_DIR / "payment_bug" / "bug_report.txt").read_text(encoding="utf-8")
    return {"bug_report": report.strip(), "local_path": str((EXAMPLES_DIR / "payment_bug").resolve())}


# serve the frontend (mounted last so /api/* wins)
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/")
def index():  # pragma: no cover - only used if StaticFiles not mounted
    idx = FRONTEND_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else {"service": "adaptive-context-debugger"}
