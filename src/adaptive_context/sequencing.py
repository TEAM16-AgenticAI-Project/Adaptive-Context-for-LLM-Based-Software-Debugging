"""Order chunks inside the prompt."""
from __future__ import annotations

import heapq


def order(packed: list, retriever, strategy: str = "dependency") -> list:
    if strategy == "score":
        return sorted(packed, key=lambda p: -p.score)
    if strategy == "reverse":          # most relevant last (closest to the question)
        return sorted(packed, key=lambda p: p.score)
    # dependency: callers before callees (reading order of the call chain); ties by score
    by_id = {p.chunk.id: p for p in packed}
    out_edges = {pid: [] for pid in by_id}
    indeg = {pid: 0 for pid in by_id}
    for pid, p in by_id.items():
        for callee in retriever.callees(p.chunk):
            if callee.id in by_id and callee.id != pid:
                out_edges[pid].append(callee.id)
                indeg[callee.id] += 1
    heap = [(-by_id[pid].score, pid) for pid, d in indeg.items() if d == 0]
    heapq.heapify(heap)
    result, done = [], set()
    while heap:
        _, pid = heapq.heappop(heap)
        result.append(by_id[pid])
        done.add(pid)
        for nxt in out_edges[pid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (-by_id[nxt].score, nxt))
    result.extend(sorted((p for pid, p in by_id.items() if pid not in done), key=lambda p: -p.score))  # cycles
    return result
