"""Logging for long runs: every round, LLM call, tool call, test run and fix check is logged with a timestamp.

Modules log through `logging.getLogger(__name__)` (children of "adaptive_context"). The CLI and the evaluation
scripts call `setup_logging` once; logs go to stderr (so JSON on stdout stays clean) and optionally to a file.
    adaptive-context debug ... --log-file results/agent_live/run.log   # then: tail -f that file
"""
from __future__ import annotations

import logging
import os
import sys

ROOT = "adaptive_context"
FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", file: str | None = None) -> logging.Logger:
    log = logging.getLogger(ROOT)
    log.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.propagate = False
    fmt = logging.Formatter(FORMAT, "%H:%M:%S")
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(fmt)
    log.addHandler(err)
    if file:
        os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
        fh = logging.FileHandler(file, encoding="utf-8")       # flushed after every record
        fh.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S"))
        log.addHandler(fh)
    return log


def short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."
