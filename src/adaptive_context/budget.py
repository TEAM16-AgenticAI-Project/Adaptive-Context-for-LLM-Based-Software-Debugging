"""Token-budget packing: compress / drop the lowest-priority chunks until the context fits.

Priority is the score (higher first). Pinned chunks (symbols the model asked for) always rank above unpinned ones,
are never compressed below level 1 ("trimmed"), and are dropped only after every unpinned chunk is gone. Among
pinned chunks with equal scores, the later entry in `scored` ranks higher (treated as the newer request), so the
oldest is dropped first. If even the single best chunk does not fit, it is kept and `over_budget` is set.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .compress import compress
from .models import Chunk
from .tokens import count_tokens

HEADER_TOKENS = 20
PINNED_MAX_LEVEL = 1


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
    over_budget: bool = False
    dropped_pinned: list = field(default_factory=list)   # pinned ids whose code was evicted


def code_budget(total: int, *prompt_parts: str, floor: int = 200) -> int:
    """Tokens left for code once the rest of the prompt (system prompt, bug report, scaffold) is paid for.
    Never below `floor`, so the model always sees some code; the caller can detect an overrun from the result."""
    return max(floor, total - sum(count_tokens(p) for p in prompt_parts if p))


def pack(scored: list, max_tokens: int, focus=(), allow_compress: bool = True, pinned=frozenset()) -> PackResult:
    pinned = frozenset(pinned)
    n = len(scored)
    # pinned first; then score; ties: pinned later-in-list first (newer), unpinned earlier first (as before)
    order = sorted(range(n), key=lambda i: (scored[i][0].id not in pinned, -scored[i][1],
                                            -i if scored[i][0].id in pinned else i))
    items = [scored[i] for i in order]
    is_pin = [c.id in pinned for c, _ in items]
    levels = [0] * n
    cache = {}

    def text(i):
        key = (i, levels[i])
        if key not in cache:
            cache[key] = compress(items[i][0], levels[i], focus)
        return cache[key]

    def cost(i):
        return count_tokens(text(i)) + HEADER_TOKENS

    total = sum(cost(i) for i in range(n))

    def lower(i, level):
        nonlocal total
        old = cost(i)
        levels[i] = level
        total += cost(i) - old

    if allow_compress:
        for target in (1, 2, 3):                           # gentle first, harsh last
            for i in reversed(range(n)):                   # lowest priority first
                if total <= max_tokens:
                    break
                if not is_pin[i] and levels[i] < target:
                    lower(i, target)
        for i in reversed(range(n)):                       # then trim pinned code, oldest first
            if total <= max_tokens:
                break
            if is_pin[i] and levels[i] < PINNED_MAX_LEVEL:
                lower(i, PINNED_MAX_LEVEL)
    keep, dropped, dropped_pinned = list(range(n)), [], []
    while total > max_tokens and len(keep) > 1:            # unpinned (lowest first), then pinned (oldest first)
        i = keep.pop()
        total -= cost(i)
        dropped.append(items[i][0].id)
        if is_pin[i]:
            dropped_pinned.append(items[i][0].id)
    packed = [Packed(items[i][0], items[i][1], levels[i], text(i)) for i in keep]
    return PackResult(packed, dropped, total, total > max_tokens, dropped_pinned)
