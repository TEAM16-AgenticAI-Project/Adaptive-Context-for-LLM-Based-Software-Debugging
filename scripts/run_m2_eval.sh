#!/usr/bin/env bash
# One command to produce every Milestone 2 evaluation table under results/m2/.
#
# Offline (default): simulator runs plus the dataset audits and validations that need
# no network and no API key. Safe to run anywhere after scripts/setup_env.sh.
#   scripts/run_m2_eval.sh
#
# Live: adds real gpt-oss-20b runs on Groq for the controlled and real-bug manifests.
# Needs GROQ_API_KEY in the environment (or .env). Groq's free tier is rate limited, so
# this uses a small case limit by default; raise LIMIT when you have quota.
#   OFFLINE=0 scripts/run_m2_eval.sh
#
# Knobs:
#   OFFLINE   1 (default) simulator + audits only; 0 also runs live Groq
#   N         synthetic bench case count (default 200)
#   LIMIT     live runs: cases per manifest (default 5, to respect rate limits)
#   SYSTEMS   comma-separated system names for run_cases (default the four core systems)
#   OUT       output root (default results/m2)
#   CONTROLLED  controlled manifest (default data/controlled/cases.jsonl)
#   REAL        real-bug manifest (default data/real/thefuck.jsonl)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OFFLINE="${OFFLINE:-1}"
N="${N:-200}"
LIMIT="${LIMIT:-5}"
SYSTEMS="${SYSTEMS:-static_k3,static_k10,adaptive,adaptive_graph}"
OUT="${OUT:-results/m2}"
CONTROLLED="${CONTROLLED:-data/controlled/cases.jsonl}"
REAL="${REAL:-data/real/thefuck.jsonl}"
PY="${PYTHON:-python}"

run() { echo "== $*"; "$@"; }

mkdir -p "$OUT"

echo "=== Milestone 2 evaluation (OFFLINE=$OFFLINE) ==="

# 1. Synthetic benchmark on generated call-chain bugs (simulator, deterministic).
run "$PY" -m adaptive_context.bench.run --n "$N" --llm sim --out "$OUT/synthetic_sim"

# 2. Render the synthetic summary into report tables.
run "$PY" -m adaptive_context.bench.report --summary "$OUT/synthetic_sim/summary.json" --out "$OUT/synthetic_sim"

# 3. Controlled multi-file cases, systems scored vs gold (simulator).
if [ -f "$CONTROLLED" ]; then
  run "$PY" -m adaptive_context.bench.run_cases --manifest "$CONTROLLED" \
    --systems "$SYSTEMS" --llm sim --out "$OUT/controlled_sim"
else
  echo "   (skip) controlled manifest not found at $CONTROLLED; build it with controlled.py --build"
fi

# 4. Aggregate and validate every results file (blocks mixing sim and live in one table).
run "$PY" -m adaptive_context.bench.aggregate --results results --out "$OUT"

# 5. Section 7 contributions table from the git history.
run "$PY" -m adaptive_context.bench.contributions --out "$OUT/contributions.md" \
  --repo-url "https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging"

if [ "$OFFLINE" = "1" ]; then
  echo "=== offline tables written under $OUT/ ==="
  exit 0
fi

echo "=== live runs (Groq gpt-oss-20b, LIMIT=$LIMIT per manifest) ==="
if [ -z "${GROQ_API_KEY:-}" ]; then
  echo "   GROQ_API_KEY is not set; cannot run live. Set it or add it to .env." >&2
  exit 1
fi

# 6. Controlled cases, live model.
if [ -f "$CONTROLLED" ]; then
  run "$PY" -m adaptive_context.bench.run_cases --manifest "$CONTROLLED" \
    --systems "$SYSTEMS" --llm groq --limit "$LIMIT" --out "$OUT/controlled_live"
fi

# 7. Real BugsInPy/thefuck bugs, live model.
if [ -f "$REAL" ]; then
  run "$PY" -m adaptive_context.bench.run_cases --manifest "$REAL" \
    --systems "$SYSTEMS" --llm groq --limit "$LIMIT" --out "$OUT/real_live"
else
  echo "   (skip) real manifest not found at $REAL; build dataset B with scripts/setup_env.sh"
fi

# 8. Re-aggregate so the live tables are indexed (still kept separate from sim tables).
run "$PY" -m adaptive_context.bench.aggregate --results results --out "$OUT"

echo "=== all tables written under $OUT/ ==="
