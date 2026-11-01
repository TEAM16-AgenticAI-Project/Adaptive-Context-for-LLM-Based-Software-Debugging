#!/usr/bin/env bash
# One-time setup for every team member (Milestone 2).
#   1. installs the package with dev + agent extras
#   2. creates two Python 3.8 environments that the old thefuck commits need to run their tests
#   3. rebuilds dataset B: 32 real bugs from BugsInPy/thefuck into data/real/repos (git-ignored),
#      checking that each bug FAILS on the buggy source and PASSES with the maintainer's patch
#
#   scripts/setup_env.sh                 # WORK defaults to ../m2-work (outside the repo)
#   PYTHON=python3.12 scripts/setup_env.sh
# Needs: git, uv (https://docs.astral.sh/uv/), Python >= 3.10. Takes ~5 minutes the first time.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$ROOT/../m2-work}"
PY="${PYTHON:-python}"
mkdir -p "$WORK"
cd "$ROOT"

echo "== 1. package (dev + agent extras)"
"$PY" -m pip install -q -e ".[dev,agent]"

echo "== 2. Python 3.8 test environments for thefuck (bugs 3-21 need pytest < 4)"
uv python install 3.8
if [ ! -x "$WORK/venv38/bin/python" ]; then
  uv venv -q -p 3.8 "$WORK/venv38"
  VIRTUAL_ENV="$WORK/venv38" uv pip install -q "pytest==6.2.5" "mock==4.0.2" "pytest-mock==3.1.0" psutil \
    "colorama==0.4.3" "six==1.15.0" "decorator==4.4.2" "pyte==0.8.0" "pexpect==4.8.0" "pytest-benchmark==3.2.3" \
    importlib-metadata "setuptools<70"
fi
if [ ! -x "$WORK/venv38old/bin/python" ]; then
  uv venv -q -p 3.8 "$WORK/venv38old"
  VIRTUAL_ENV="$WORK/venv38old" uv pip install -q "pytest==3.10.1" "mock==4.0.2" "pytest-mock==1.10.4" psutil \
    "colorama==0.4.3" "six==1.15.0" "decorator==4.4.2" "pyte==0.8.0" "pexpect==4.8.0" "pytest-benchmark==3.2.3" \
    "setuptools<70" "attrs<20" "more-itertools<9" "pluggy<1"
fi

echo "== 3. dataset B (BugsInPy/thefuck, 32 bugs)"
[ -d "$WORK/BugsInPy" ] || git clone -q --depth 1 https://github.com/soarsmu/BugsInPy.git "$WORK/BugsInPy"
[ -d "$WORK/thefuck" ] || git clone -q https://github.com/nvbn/thefuck.git "$WORK/thefuck"
"$PY" -W ignore -m adaptive_context.bench.bugsinpy --bugsinpy "$WORK/BugsInPy" --project thefuck \
  --clone "$WORK/thefuck" --out data/real --bugs 1-32 \
  --env "py38-pytest6=$WORK/venv38/bin/python" --env "py38-pytest3=$WORK/venv38old/bin/python" \
  --env-for 3-21=py38-pytest3 --default-env py38-pytest6

cat <<MSG

Setup done. Pass these to any command that runs the real cases' tests:
  --env py38-pytest6=$WORK/venv38/bin/python --env py38-pytest3=$WORK/venv38old/bin/python
For live runs: cp .env.example .env and add GROQ_API_KEY (never commit it).
MSG
