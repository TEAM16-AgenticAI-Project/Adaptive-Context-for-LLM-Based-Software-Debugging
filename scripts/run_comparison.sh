#!/usr/bin/env bash
# Baseline vs proposed on the same bugs with the same model -> one file: $OUT/comparison.md
#
#   LLM=sim scripts/run_comparison.sh     # dry run, offline, about a minute (numbers are not model quality)
#   scripts/run_comparison.sh             # live: Groq (GROQ_API_KEY from .env), same model for every system
#
# Knobs (environment variables):
#   LLM      groq (default) or sim
#   SYSTEMS  systems to compare (default: the two static baselines, the adaptive loop and agent + fix check)
#   CASES    real thefuck bugs (default: the 6 used in our live runs, 2 of them multi-file)
#   W        folder with the Python 3.8 test environments (default: ../m2-work, as scripts/setup_env.sh makes it)
#   OUT      output folder (default results/m2/compare)
#   EXTRA    optional LABEL=PATH of an `adaptive-context debug` JSON to list separately in the report
set -euo pipefail
cd "$(dirname "$0")/.."

LLM="${LLM:-groq}"
SYSTEMS="${SYSTEMS:-static_k3,static_k10,adaptive,agent_verify}"
CASES="${CASES:-thefuck-2,thefuck-5,thefuck-16,thefuck-17,thefuck-23,thefuck-29}"
W="${W:-$(cd .. && pwd)/m2-work}"
OUT="${OUT:-results/m2/compare}"
[ "$LLM" = "sim" ] && OUT="${OUT}_sim"
COMMON=(--systems "$SYSTEMS" --llm "$LLM" --check-fixes --rounds 8 --budget 4000 --max-verify 6)

for py in "$W/venv38old/bin/python" "$W/venv38/bin/python"; do
  [ -x "$py" ] || { echo "missing $py: set W to the folder made by scripts/setup_env.sh" >&2; exit 1; }
done

echo "== 1/3 stakeholder test cases (examples/payment_bug, examples/auth_bug)"
adaptive-context cases --manifest data/stakeholder/cases.jsonl "${COMMON[@]}" --out "$OUT/stakeholder"

echo "== 2/3 real bugs: $CASES"
adaptive-context cases --manifest data/real/thefuck.jsonl --cases "$CASES" \
  --env "py38-pytest3=$W/venv38old/bin/python" --env "py38-pytest6=$W/venv38/bin/python" \
  "${COMMON[@]}" --out "$OUT/thefuck"

echo "== 3/3 report"
ARGS=("$OUT/stakeholder" "$OUT/thefuck" --audit results/deep/audit/summary.json --out "$OUT/comparison.md")
[ -n "${EXTRA:-}" ] && ARGS+=(--extra "$EXTRA")
python -m adaptive_context.bench.compare "${ARGS[@]}" > /dev/null
echo "wrote $OUT/comparison.md"
