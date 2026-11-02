# Adaptive-Context Debugger — Web UI

A production-ready web application that wraps the
[Adaptive-Context](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging)
engine (adaptive retrieval, compression, sequencing, token-budget management) behind a clean
browser UI and a FastAPI service.

Give it a **Python repository** and a **bug report**; it returns the diagnosed file + function,
the cause, a suggested fix, and — the point of the project — the **evidence the model actually
read**, plus a round-by-round trace.

> This is a deployable wrapper around a course research engine. "Production-ready" here means:
> containerized, configurable via environment, input-validated, resource-bounded, non-root,
> health-checked, and documented — runnable with one command. It is not a managed hosted
> service; you supply the host and (optionally) a Groq API key.

## Run it (one command)

```bash
# with Docker (recommended) — then open http://localhost:8000
docker compose up --build
```

Or without Docker:

```bash
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --port 8000      # open http://localhost:8000
```

It runs in **free offline `sim` mode** out of the box (no API key, no network). The bundled
`payment_bug` example gives you a one-click demo.

## Use a real LLM (Groq)

```bash
cp .env.example .env
# edit .env:  DEFAULT_LLM=groq  and  GROQ_API_KEY=<your key from https://groq.com/>
docker compose up --build
```

Then pick **llm = groq** in the UI. Without a key it stays in `sim` mode.

## What you can analyse

The UI offers four repository sources:

| Source | Notes |
|---|---|
| Bundled example | the `payment_bug` demo, one click |
| Upload `.zip` | a zipped Python repo (≤ `MAX_UPLOAD_MB`, default 25 MB) |
| Public git URL | an `https://…​.git` URL, shallow-cloned on the server |
| Server path | a directory on the server host — **off by default in the container** |

## How it works

```
 Browser (static HTML/CSS/JS, no build step)
        │  POST /api/debug  (bug report + repo source + settings)
        ▼
 FastAPI backend (backend/main.py)
        │  load_repo_dir → chunk_repo → BM25Retriever
        │  AdaptiveDebugger / StaticRAG  +  SimulatedLLM | GroqLLM
        ▼
 adaptive_context engine (vendored in backend/adaptive_context/)
        │  adaptive retrieval · compression · sequencing · token budget · fix check
        ▼
 JSON: diagnosis, cause, fix, evidence chunks, per-round trace, stats
```

The engine is the real one from the research repo, copied in so this app is self-contained.

## API

`GET /api/health` → service status, whether a Groq key is present.
`GET /api/example` → the bundled example's bug report + path.
`POST /api/debug` (multipart form):

| field | default | meaning |
|---|---|---|
| `bug_report` | — | required; the failure description |
| `mode` | `adaptive` | `adaptive` (iterative) or `baseline` (static Top-K) |
| `llm` | `sim` | `sim` (offline) or `groq` (real model) |
| `budget` | 3000 | total token budget for the prompt |
| `rounds` | 6 | max adaptive rounds |
| `top_k` | 3 | initial retrieval size |
| `prefetch` | 2 | call-graph prefetch depth |
| one of: `upload` / `git_url` / `local_path` | — | the repository to analyse |

## Configuration (environment)

See `.env.example`. Key flags: `DEFAULT_LLM`, `GROQ_API_KEY`, `GROQ_MODEL`,
`ALLOW_LOCAL_PATH`, `ALLOW_GIT_CLONE`, `MAX_UPLOAD_MB`, `MAX_REPO_FILES`, `CLONE_TIMEOUT_S`,
`CORS_ORIGINS`.

## Security notes (read before exposing publicly)

- **Test execution is NOT enabled in this web service.** The research engine can run a repo's
  pytest suite (`run_failing_test`), which executes the repo's code. That path is intentionally
  not wired into the API. If you add it, run it only on trusted repos and inside strong
  isolation — the engine's temp-copy + timeout is *not* a security sandbox.
- The container runs as a **non-root** user and caps CPU/memory in `docker-compose.yml`.
- Zip uploads are size-capped and **zip-slip–guarded**; git URLs must be `https … .git` and are
  shallow-cloned with a timeout.
- `ALLOW_LOCAL_PATH` is **off** in the container so a web user cannot read arbitrary server
  paths. Enable it only for local/trusted use.
- Put the service behind a reverse proxy with TLS and rate limiting for any real deployment;
  hold `GROQ_API_KEY` server-side only.

## Tests

```bash
pip install -r backend/requirements.txt pytest httpx
cd backend && python -m pytest ../tests -q      # offline, no key needed
```

## Deploy anywhere that runs a container

The image is a standard Python slim container. It runs as-is on Fly.io, Render, Railway, Google
Cloud Run, AWS App Runner/ECS, or any Docker host. Set the environment variables from
`.env.example`, expose port 8000, and point a TLS-terminating proxy at it.

## Layout

```
app/
  backend/
    main.py                  # FastAPI service
    requirements.txt
    adaptive_context/        # vendored research engine (pure stdlib)
  frontend/
    index.html  style.css  app.js   # no-build single-page UI
  examples/payment_bug/      # bundled one-click demo
  tests/test_api.py          # offline smoke tests
  Dockerfile  docker-compose.yml  Makefile  .env.example
```
