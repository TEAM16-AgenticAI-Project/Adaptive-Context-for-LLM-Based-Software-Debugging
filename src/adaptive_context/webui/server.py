"""Web UI: run any system on a bug and see the whole multi-file fix.

Pick one of the stakeholder's test cases (examples/), a bug from the benchmark manifest (data/real/thefuck.jsonl),
or bring a repository (zip, public git URL, local path). The page shows the live log while the debugger runs, then
every place the fix changes (one diff per file), the test check, the evidence the model read and each round's tool
calls. The UI design comes from Yadnesh's UI-changes branch; this version runs on the shared package (no copy of
the engine) and builds systems the same way as `adaptive-context debug` (cli.build_debugger).

    pip install -e ".[agent,ui]"
    adaptive-context ui --env py38-pytest3=$W/venv38old/bin/python --env py38-pytest6=$W/venv38/bin/python
    # then open http://127.0.0.1:8000

Running a repository's tests runs its code. Bundled examples, benchmark cases and local paths may run tests;
uploads and git URLs only with --allow-untrusted-tests. This is not a sandbox: use it on repositories you trust.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..cases import load_cases
from ..cli import MODES, build_debugger
from ..llm import LLMError, load_dotenv
from ..logs import setup_logging
from ..multifix import preview

try:                                       # optional: pip install -e ".[ui]" (module level, so FastAPI can read the types)
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover
    FastAPI = None

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parents[3]            # the project checkout (editable install)
MAX_UPLOAD_MB = 25
MAX_FILES = 4000
SETTINGS = {"examples": ROOT / "examples", "manifest": ROOT / "data" / "real" / "thefuck.jsonl", "envs": {},
            "python": None, "untrusted_tests": False}


# ---------------------------------------------------------------- cases the page can pick from
def _test_cmd_for(folder: Path) -> str:
    tests = sorted(p.name for p in folder.glob("test_*.py"))
    return f"pytest -q {' '.join(tests)}" if tests else ""


def examples() -> list:
    """Stakeholder test cases (examples/<name>/bug_report.txt) and benchmark bugs whose repos are built."""
    out = []
    ex = Path(SETTINGS["examples"])
    for folder in sorted(p for p in ex.glob("*") if (p / "bug_report.txt").is_file()):
        files = sorted(p.name for p in folder.glob("*.py"))
        out.append({"id": f"example:{folder.name}", "group": "Stakeholder test cases", "title": folder.name,
                    "bug_report": (folder / "bug_report.txt").read_text(encoding="utf-8").strip(),
                    "test_cmd": _test_cmd_for(folder), "files": len(files), "repo": str(folder),
                    "note": f"{len(files)} files: {', '.join(files)}", "ready": True})
    manifest = Path(SETTINGS["manifest"])
    if manifest.is_file():
        for c in load_cases(str(manifest)):
            built = os.path.isdir(c.repo)
            python = SETTINGS["envs"].get(c.env)
            spans = len(set(c.gold_files))
            note = (f"maintainer's fix spans {spans} files" if spans > 1 else
                    f"maintainer's fix changes {len(c.gold_functions)} functions" if len(c.gold_functions) > 1 else "")
            ready = built and bool(python)
            out.append({"id": f"case:{c.id}", "group": f"Benchmark: {manifest.stem}", "title": c.id,
                        "bug_report": c.bug_report, "test_cmd": c.test_cmd, "repo": c.repo, "multi": spans > 1,
                        "note": note if ready else ("repo not built (scripts/setup_env.sh)" if not built else
                                                     f"start the UI with --env {c.env}=<python>"),
                        "ready": ready, "python": python})
    return out


def _example(eid: str) -> dict:
    for e in examples():
        if e["id"] == eid:
            if not e["ready"]:
                raise ValueError(f"{eid}: {e['note']}")
            return e
    raise ValueError(f"unknown example {eid!r}")


# ---------------------------------------------------------------- repositories from the request
def _unzip(data: bytes, tmp: Path) -> Path:
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"upload exceeds {MAX_UPLOAD_MB} MB")
    dest = tmp / "repo"
    dest.mkdir()
    zpath = tmp / "upload.zip"
    zpath.write_bytes(data)
    try:
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():                     # zip-slip guard: nothing may land outside dest
                if not str((dest / member).resolve()).startswith(str(dest.resolve()) + os.sep):
                    raise ValueError("unsafe path in zip")
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        raise ValueError("upload is not a valid zip") from e
    entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest


def _clone(url: str, tmp: Path) -> Path:
    if not (url.startswith("https://") and url.endswith(".git")):
        raise ValueError("git URL must be https://... ending in .git")
    dest = tmp / "repo"
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", url, str(dest)], check=True,
                       capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise ValueError("git clone timed out") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"git clone failed: {e.stderr.decode(errors='replace')[:300]}") from e
    return dest


# ---------------------------------------------------------------- jobs (one runs at a time: one model, one rate limit)
@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | error
    log: list = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    started: float = field(default_factory=time.time)


JOBS: dict = {}
_RUN = threading.Lock()
_CURRENT: list = [None]


class _JobLog(logging.Handler):
    """Copies the package's log lines (rounds, tool calls, test runs, fix checks) into the running job."""

    def emit(self, record):
        job = _CURRENT[0]
        if job is not None:
            job.log.append(self.format(record))


def _to_json(obj):
    return json.loads(json.dumps(obj, default=str))


def run_job(job: Job, req: dict) -> None:
    with _RUN:
        _CURRENT[0], job.status = job, "running"
        tmp = Path(tempfile.mkdtemp(prefix="adaptive_ui_"))
        try:
            job.result = _debug(req, tmp)
            job.status = "done"
        except (ValueError, LLMError, RuntimeError) as e:
            job.error, job.status = str(e), "error"
            log.error("%s", e)
        except Exception as e:  # noqa: BLE001 - shown on the page instead of a silent 500
            job.error, job.status = f"{type(e).__name__}: {e}", "error"
            log.exception("run failed")
        finally:
            _CURRENT[0] = None
            shutil.rmtree(tmp, ignore_errors=True)


def _debug(req: dict, tmp: Path) -> dict:
    python, trusted, test_cmd = SETTINGS["python"], True, (req.get("test_cmd") or "").strip() or None
    if req.get("example"):
        ex = _example(req["example"])
        repo, python = ex["repo"], ex.get("python") or python
    elif req.get("upload") is not None:
        repo, trusted = str(_unzip(req["upload"], tmp)), SETTINGS["untrusted_tests"]
    elif req.get("git_url"):
        repo, trusted = str(_clone(req["git_url"].strip(), tmp)), SETTINGS["untrusted_tests"]
    elif req.get("local_path"):
        repo = str(Path(req["local_path"]).expanduser().resolve())
        if not os.path.isdir(repo):
            raise ValueError(f"not a directory: {req['local_path']}")
    else:
        raise ValueError("pick an example, upload a zip, or give a git URL or a local path")
    if test_cmd and not trusted:
        log.warning("not running tests from an uploaded or cloned repository (start with --allow-untrusted-tests)")
        test_cmd = None
    mode = req["mode"]
    if mode == "agent_verify" and not test_cmd:
        raise ValueError("agent_verify needs a test command (the failing test it reruns on each fix)")
    n = sum(1 for _ in Path(repo).rglob("*.py"))
    if n > MAX_FILES:
        raise ValueError(f"repository has {n} Python files; the limit is {MAX_FILES}")
    log.info("repository %s | mode %s | llm %s | test: %s", os.path.basename(repo), mode, req["llm"], test_cmd or "none")
    t0 = time.perf_counter()
    system, report, info = build_debugger(repo, req["bug_report"], mode=mode, llm=req["llm"], python=python,
                                          test_cmd=test_cmd, max_tokens=4096 if mode.startswith("agent") else 2048,
                                          top_k=req["top_k"], rounds=req["rounds"], budget=req["budget"],
                                          max_verify=req["max_verify"], time_limit=req["time_limit"] or None)
    res = system.run(report)
    d = res.diagnosis
    changes = preview(repo, d) if d.valid else {"files": [], "problems": []}
    return _to_json({
        "diagnosis": vars(d), "stop_reason": res.stop_reason, "verification": res.verification,
        "stats": {**vars(res.stats), "total_tokens": res.stats.total_tokens, "wall_s": time.perf_counter() - t0},
        "context": [{"chunk_id": cid, "level": lvl} for cid, lvl in res.context_items or []],
        "trace": res.trace or [], "changes": changes, "repo": info,
        "config": {k: req[k] for k in ("mode", "llm", "budget", "rounds", "top_k", "max_verify")},
    })


# ---------------------------------------------------------------- HTTP
def create_app():
    if FastAPI is None:  # pragma: no cover - only without the [ui] extra
        raise RuntimeError('the web UI needs fastapi: pip install -e ".[ui]"')

    load_dotenv()                                    # GROQ_API_KEY from .env, as the CLI does
    app = FastAPI(title="Adaptive-Context Debugger", docs_url="/api/docs")
    handler = _JobLog()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    pkg = logging.getLogger("adaptive_context")
    if pkg.level == logging.NOTSET:
        pkg.setLevel(logging.INFO)
    if not any(isinstance(h, _JobLog) for h in pkg.handlers):
        pkg.addHandler(handler)

    @app.get("/api/health")
    def health():
        try:
            import langgraph  # noqa: F401
            agent = True
        except ImportError:
            agent = False
        return {"groq_key": bool(os.environ.get("GROQ_API_KEY")), "agent": agent,
                "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                "untrusted_tests": SETTINGS["untrusted_tests"]}

    @app.get("/api/examples")
    def list_examples():
        return [{k: v for k, v in e.items() if k not in ("repo", "python")} for e in examples()]

    @app.post("/api/debug")
    def debug(bug_report: str = Form(...), mode: str = Form("agent_verify"), llm: str = Form("groq"),
              example: str = Form(None), git_url: str = Form(None), local_path: str = Form(None),
              test_cmd: str = Form(None), budget: int = Form(4000), rounds: int = Form(8), top_k: int = Form(3),
              max_verify: int = Form(5), time_limit: int = Form(600), upload: UploadFile = File(None)):
        if not bug_report.strip():
            raise HTTPException(400, "describe the bug first")
        if mode not in MODES or llm not in ("groq", "sim"):
            raise HTTPException(400, f"mode must be one of {', '.join(MODES)}; llm groq or sim")
        for name, lo, hi, val in (("budget", 500, 32000, budget), ("rounds", 1, 20, rounds), ("top_k", 1, 20, top_k),
                                  ("max_verify", 1, 10, max_verify), ("time_limit", 0, 3600, time_limit)):
            if not lo <= val <= hi:
                raise HTTPException(400, f"{name} must be between {lo} and {hi}")
        job = Job(uuid.uuid4().hex[:12])
        req = {"bug_report": bug_report, "mode": mode, "llm": llm, "example": example, "git_url": git_url,
               "local_path": local_path, "test_cmd": test_cmd, "budget": budget, "rounds": rounds, "top_k": top_k,
               "max_verify": max_verify, "time_limit": time_limit,
               "upload": upload.file.read() if upload is not None and upload.filename else None}
        JOBS[job.id] = job
        threading.Thread(target=run_job, args=(job, req), daemon=True).start()
        return {"job": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, since: int = 0):
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return {"status": job.status, "log": job.log[since:], "next": len(job.log), "error": job.error,
                "result": job.result if job.status == "done" else None, "elapsed": time.time() - job.started}

    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="adaptive-context ui", description="web UI for the debugger")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--env", action="append", default=[], help="NAME=PYTHON for the benchmark's test environments")
    ap.add_argument("--python", default=None, help="Python that runs the tests of examples and local repos")
    ap.add_argument("--examples", default=str(SETTINGS["examples"]), help="folder of example bugs")
    ap.add_argument("--manifest", default=str(SETTINGS["manifest"]), help="benchmark case manifest (JSONL)")
    ap.add_argument("--allow-untrusted-tests", action="store_true",
                    help="also run the tests of uploaded and cloned repositories (runs their code)")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    a = ap.parse_args(argv)
    SETTINGS.update(examples=a.examples, manifest=a.manifest, envs=dict(e.split("=", 1) for e in a.env),
                    python=a.python, untrusted_tests=a.allow_untrusted_tests)
    setup_logging(a.log_level)
    try:
        import uvicorn
    except ImportError:
        print('the web UI needs fastapi and uvicorn: pip install -e ".[ui]"', file=sys.stderr)
        return 2
    print(f"Adaptive-Context Debugger on http://{a.host}:{a.port}", file=sys.stderr)
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
