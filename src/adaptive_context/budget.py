"""Token-budget packing: compress / drop the lowest-priority chunks until the context fits."""
from __future__ import annotations

from dataclasses import dataclass

from .compress import compress
from .tokens import count_tokens
from .models import Chunk

HEADER_TOKENS = 20


@dataclass
class Packed:
    chunk: Chunk
    score: float
    level: int
    text: str


@dataclass
class PackResult:
    items: list
    dropped: list
    tokens: int


def pack(scored: list, max_tokens: int, focus=(), allow_compress: bool = True) -> PackResult:
    items = sorted(scored, key=lambda t: -t[1])          # best first = highest priority
    levels = [0] * len(items)
    cache = {}

    def text(i):
        key = (i, levels[i])
        if key not in cache:
            cache[key] = compress(items[i][0], levels[i], focus)
        return cache[key]

    def cost(i):
        return count_tokens(text(i)) + HEADER_TOKENS

    total = sum(cost(i) for i in range(len(items)))
    if allow_compress:
        for target in (1, 2, 3):                         # gentle first, harsh last
            for i in reversed(range(len(items))):        # lowest priority first
                if total <= max_tokens:
                    break
                if levels[i] < target:
                    old = cost(i)
                    levels[i] = target
                    total += cost(i) - old
    keep, dropped = list(range(len(items))), []
    while total > max_tokens and len(keep) > 1:
        i = keep.pop()
        total -= cost(i)
        dropped.append(items[i][0].id)
    packed = [Packed(items[i][0], items[i][1], levels[i], text(i)) for i in keep]
    return PackResult(packed, dropped, total)
