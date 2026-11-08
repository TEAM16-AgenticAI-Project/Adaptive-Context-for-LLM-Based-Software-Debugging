"""Record reproduction metadata from builder manifests, without running a model.

The builder's fail-before/pass-after records are retained, including unsuccessful
attempts. This summarizes those records; it does not claim to rerun their tests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from .audit import revision


def summarize_manifest(manifest: str, *, write_filtered=False) -> dict:
    path = Path(manifest)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("case IDs must be unique")
    states = [r.get("source", {}).get("reproduced") for r in rows]
    if any(state is not None and type(state) is not bool for state in states):
        raise ValueError("reproduced must be a boolean or absent")
    result = {"manifest": manifest, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "attempted": len(rows), "reproduced": sum(v is True for v in states),
              "not_reproduced": sum(v is False for v in states), "unknown": sum(v is None for v in states),
              "case_ids": [r["id"] for r in rows],
              "unsuccessful": [{"case": r["id"], "buggy_result": r["source"].get("buggy_result"),
                                "fixed_result": r["source"].get("fixed_result")}
                               for r in rows if r.get("source", {}).get("reproduced") is False]}
    if write_filtered:
        target = path.with_name(path.stem + ".reproduced.jsonl")
        target.write_text("".join(json.dumps(r) + "\n" for r in rows if r.get("source", {}).get("reproduced") is True))
        result["filtered_manifest"] = str(target)
    return result


def environment(python: str) -> dict:
    code = ("import importlib.metadata as m,json,sys; "
            "print(json.dumps({'python':sys.version,'packages':sorted("
            "d.metadata['Name']+'=='+d.version for d in m.distributions())}))")
    return json.loads(subprocess.check_output([python, "-c", code], text=True, timeout=30))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True)
    parser.add_argument("--out", default="results/deep/datasets.json")
    parser.add_argument("--write-filtered", action="store_true")
    parser.add_argument("--env", action="append", default=[], help="NAME=PYTHON (record package versions)")
    parser.add_argument("--pin", action="append", default=[], help="NAME=GIT_CHECKOUT (record source revision)")
    args = parser.parse_args(argv)
    result = {"revision": revision(), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "evidence": "Summary of the existing builder's recorded fail-before/pass-after checks; not a new test run",
              "manifests": [summarize_manifest(p, write_filtered=args.write_filtered) for p in args.manifest],
              "environments": {}, "source_pins": {}}
    for value in args.env:
        name, python = value.split("=", 1)
        result["environments"][name] = environment(python)
    for value in args.pin:
        name, checkout = value.split("=", 1)
        result["source_pins"][name] = subprocess.check_output(
            ["git", "-C", checkout, "rev-parse", "HEAD"], text=True, timeout=30).strip()
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"manifests": result["manifests"], "revision": result["revision"]}, indent=2))


if __name__ == "__main__":
    main()
