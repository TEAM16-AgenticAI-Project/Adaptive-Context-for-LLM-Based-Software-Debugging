# Web UI

A browser front end and HTTP API for the debugger in `src/adaptive_context`. You give it a
Python repository and a bug report. It runs one or more systems on the same index and shows,
for each one, the diagnosed file and function, the suggested fix, the code the model saw
(with its compression level), the round-by-round trace and the token and call counts.

The UI imports the engine from `src/`, so it always runs the same code as the CLI and the
evaluation harness.

## Run it

```bash
# from the repository root
pip install -r ui/backend/requirements.txt
cd ui/backend && uvicorn main:app --port 8000        # open http://localhost:8000
```

Or with Docker:

```bash
docker compose up --build                            # open http://localhost:8000
```

It starts with the offline simulator, so no API key is needed. To use the real model, put
`GROQ_API_KEY=...` in `.env` (or the host's environment variables) and restart.

## What you can do

| Repository source | Notes |
|---|---|
| **Examples** | `payment_bug`, `cart_app` and the eight controlled cases from `data/controlled`. Each has a known answer, so every run is scored afterwards. Each example can be downloaded as a .zip. |
| **Generated case** | A benchmark case from `bench/generate.py`, by seed and size. The simulator solves these, so this is the free way to see the adaptive systems beat the static baseline. |
| **Upload .zip** | Any Python project, up to `MAX_UPLOAD_MB`. |
| **Git URL** | A public repository on an allowed host. Browser URLs work, with or without `.git`; a `/tree/<branch>` link selects that branch. |
| **Server path** | A directory on the server. Hidden unless `ALLOW_LOCAL_PATH=1`. |

| System | What it is |
|---|---|
| Static RAG, Top-3 | Milestone 1 baseline: one retrieval, three chunks, one call |
| Static RAG, Top-10 | Wider static baseline |
| Adaptive loop | Requests symbols and queries each round under the token budget |
| Adaptive + call graph | Adaptive loop with callee prefetch |
| LangGraph agent | Calls the six evidence tools as functions (needs `langgraph`) |

Pick several systems to compare them side by side on the same repository. The default
compares Static RAG Top-3 with Adaptive + call graph.

**Scoring.** For examples and generated cases the known answer is used only after the run,
to mark each result as correct or not and grounded or not. It is never put in the prompt.
Generated cases also run the benchmark's fix check, which only accepts `return <name> <op> <int>`
lines.

**Fix check on real code.** Running a repository's tests executes its code, so this is off
unless `ALLOW_RUN_TESTS=1`. When it is on, the UI shows a checkbox. Bundled examples
use their own test; for uploads and git repos you give a test path.

**Simulator vs Groq.** The simulator only recognises the generated bug pattern. On other code
it returns no diagnosis, and the UI says so, but the evidence lists still show what each
system retrieved. Use Groq for real diagnoses.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | status, version, whether a Groq key is set |
| `GET /api/config` | available LLMs, systems, sources, limits and defaults (the UI builds itself from this) |
| `GET /api/examples` | bundled and controlled examples |
| `GET /api/examples/{id}/download` | an example as a .zip |
| `GET /api/generated?seed=&size=` | preview a generated case |
| `POST /api/debug` | run (multipart form, fields below) |

`POST /api/debug` fields: `bug_report`, `systems` (comma-separated ids, default
`adaptive_graph`), `llm` (`sim` or `groq`), `model`, `budget`, `rounds`, `top_k`, `prefetch`,
and exactly one source: `example`, `gen_seed` (+ `gen_size`), `upload`, `git_url` or
`local_path`. Optional: `verify=1` and `test_path`. The older `mode=baseline|adaptive` field
still works. The response has `repo`, `answer` (or null), `runs` (one per system) and
`elapsed_s`.

```bash
curl -s -F example=payment_bug -F systems=static_k3,adaptive_graph -F llm=sim \
     http://localhost:8000/api/debug | python -m json.tool
```

## Configuration

All settings are environment variables. See `.env.example` at the repository root.

| Variable | Default | Meaning |
|---|---|---|
| `DEFAULT_LLM` | `sim` | `groq` makes Groq the default when a key is set |
| `GROQ_API_KEY` / `GROQ_MODEL` | none / `openai/gpt-oss-20b` | real model |
| `ALLOW_GIT_CLONE` | `1` | allow the Git URL source |
| `GIT_ALLOWED_HOSTS` | `github.com,gitlab.com,bitbucket.org` | hosts the server may clone from |
| `ALLOW_LOCAL_PATH` | `0` | allow server paths |
| `ALLOW_RUN_TESTS` | `0` | allow running repository tests for the fix check |
| `MAX_UPLOAD_MB` / `MAX_UNZIPPED_MB` / `MAX_REPO_FILES` | `25` / `100` / `4000` | input limits |
| `CLONE_TIMEOUT_S` / `TEST_TIMEOUT_S` | `60` / `120` | time limits |
| `RATE_LIMIT_PER_MIN` | `30` | runs per minute per client IP (`0` turns it off) |
| `MAX_CONCURRENT_RUNS` / `QUEUE_WAIT_S` | `2` / `30` | concurrent runs and how long a request waits for a slot |
| `CORS_ORIGINS` | empty | comma-separated origins if another site calls the API |

## Security

- The container runs as a non-root user. `docker-compose.yml` also makes the filesystem
  read-only (with a tmpfs `/tmp`) and caps CPU and memory.
- Uploads: size and expanded-size limits; paths outside the archive and symlinks are rejected.
- Git: https only, allowed hosts only, no credentials in the URL, shallow clone with a timeout,
  no interactive prompts.
- Server paths and repository test runs are off by default.
- Responses carry a strict Content-Security-Policy; the frontend never inserts server text as HTML.
- Keep `GROQ_API_KEY` in the host's environment settings, never in the repository.

## Deploy for free

### Render (no credit card)

1. Sign in at <https://render.com> with GitHub.
2. **New > Blueprint**, choose this repository and the `UI-changes` branch. Render reads
   `render.yaml` at the repository root.
3. When asked for `GROQ_API_KEY`, paste a free key from <https://console.groq.com/keys>, or
   leave it empty to run the simulator only. Click **Apply**.

If you already created the service by hand, use these settings: Root Directory empty,
Dockerfile Path `./Dockerfile`, Docker Build Context `.`, Health Check Path `/api/health`.

The free plan sleeps after about 15 minutes without traffic. The first request after that
takes 30 to 60 seconds.

### Hugging Face Spaces (no credit card)

Create a Space with the **Docker** SDK, push this repository to it, and add `PORT=7860` (plus
`GROQ_API_KEY` if you have one) under Settings > Variables.

## Tests

```bash
pytest ui/tests -q          # offline; uses the simulator
```

## Files

```
Dockerfile, docker-compose.yml, render.yaml, .dockerignore, Makefile   (repository root)
ui/backend/main.py            FastAPI service
ui/backend/requirements.txt
ui/frontend/                  index.html, app.js, style.css (no build step)
ui/examples/cart_app/         bundled example; cart_app.zip is the same files zipped
ui/tests/test_api.py
```
