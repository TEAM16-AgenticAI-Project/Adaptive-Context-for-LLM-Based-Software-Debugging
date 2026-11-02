#!/usr/bin/env bash
# Multi-file bug set (Milestone 2): real BugsInPy bugs whose maintainer fix spans several source files
# (thefuck 16/17 from setup_env.sh, plus youtube-dl, tornado and tqdm built here).
#   1. clones youtube-dl, tornado and tqdm into $WORK (BugsInPy too, if setup_env.sh has not)
#   2. creates Python 3.8 test environments for tornado and tqdm (youtube-dl reuses venv38)
#   3. rebuilds the bugs into data/real/repos (git-ignored), checking each FAILS before and PASSES after the fix
#   4. writes data/real/multifile.jsonl
#
#   WORK="$HOME/m2-work" scripts/setup_multifile.sh        # run scripts/setup_env.sh first
# Needs git, uv and Python >= 3.10. Takes ~5 minutes the first time (youtube-dl's history is ~80 MB).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$ROOT/../m2-work}"
PY="${PYTHON:-python}"
mkdir -p "$WORK"
cd "$ROOT"

echo "== 1. sources"
[ -d "$WORK/BugsInPy" ] || git clone -q --depth 1 https://github.com/soarsmu/BugsInPy.git "$WORK/BugsInPy"
for spec in youtube-dl=ytdl-org/youtube-dl tornado=tornadoweb/tornado tqdm=tqdm/tqdm; do
  name="${spec%%=*}"
  [ -d "$WORK/$name" ] || git clone -q "https://github.com/${spec#*=}.git" "$WORK/$name"
done

echo "== 2. Python 3.8 test environments"
uv python install 3.8
mkenv() {
  local dir="$WORK/$1"; shift
  if [ ! -x "$dir/bin/python" ]; then
    uv venv -q -p 3.8 "$dir"
    VIRTUAL_ENV="$dir" uv pip install -q "pytest==6.2.5" "$@"
  fi
}
mkenv venv38                 # also created by setup_env.sh (with thefuck's extras)
mkenv venv-tornado
mkenv venv-tqdm nose flake8 coverage pytest-timeout "pytest-asyncio<0.15"

echo "== 3. bugs"
build() {
  "$PY" -W ignore -m adaptive_context.bench.bugsinpy --bugsinpy "$WORK/BugsInPy" --project "$1" \
    --clone "$WORK/$1" --out data/real --bugs "$2" --env "$3=$4" --default-env "$3"
}
build youtube-dl 16,31,32,33,35,38,39,40,42 py38 "$WORK/venv38/bin/python"
build tornado 6,10 py38-tornado "$WORK/venv-tornado/bin/python"
build tqdm 2 py38-tqdm "$WORK/venv-tqdm/bin/python"

echo "== 4. manifest"
"$PY" -m adaptive_context.bench.multifile_eval --make-manifest

cat <<MSG

Multi-file setup done. The committed manifests are the reference: restore them with
  git checkout -- data/real
(the rebuilt repos in data/real/repos stay; they are git-ignored). Then run, from zsh or bash:
  W="$WORK"
  ENVS=(--env py38-pytest3=\$W/venv38old/bin/python --env py38-pytest6=\$W/venv38/bin/python
        --env py38=\$W/venv38/bin/python --env py38-tornado=\$W/venv-tornado/bin/python
        --env py38-tqdm=\$W/venv-tqdm/bin/python)
  adaptive-context multifile --bugsinpy \$W/BugsInPy "\${ENVS[@]}" --several-only --out results/multifile/verify
MSG
