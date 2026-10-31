"""Retrievers. BM25 (pure Python, offline) is the default; Chroma is optional."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .models import Chunk

_WORD = re.compile(r"[A-Za-z0-9]+")
_PART = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def tokenize(text: str) -> list:
    out = []
    for m in _WORD.finditer(text):
        out.extend(p.lower() for p in _PART.findall(m.group()))
    return out


class BM25Retriever:
    def __init__(self, chunks: list, k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.by_id = {c.id: c for c in chunks}
        self.by_qual, self.by_short = defaultdict(list), defaultdict(list)
        self.inv = defaultdict(list)
        self.dl = []
        for i, c in enumerate(chunks):
            self.by_qual[c.name].append(c)
            self.by_short[c.short_name].append(c)
            toks = tokenize(c.path) + tokenize(c.name) * 2 + tokenize(c.text)   # name counts double
            self.dl.append(len(toks))
            for term, tf in Counter(toks).items():
                self.inv[term].append((i, tf))
        self.avgdl = (sum(self.dl) / len(self.dl)) if self.dl else 1.0

    def search(self, query: str, k: int, exclude=frozenset()) -> list:
        n = len(self.chunks)
        scores = defaultdict(float)
        for term in set(tokenize(query)):
            post = self.inv.get(term)
            if not post:
                continue
            idf = math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for i, tf in post:
                norm = tf + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl)
                scores[i] += idf * tf * (self.k1 + 1) / norm
        ranked = sorted(scores.items(), key=lambda t: (-t[1], t[0]))
        out = []
        for i, s in ranked:
            c = self.chunks[i]
            if c.id in exclude:
                continue
            out.append((c, s))
            if len(out) == k:
                break
        return out

    def lookup(self, name: str) -> list:
        """Exact symbol lookup: 'path::qual', 'Class.method' or short name."""
        name = name.strip().strip("`()")
        if name in self.by_id:
            return [self.by_id[name]]
        if name in self.by_qual:
            return list(self.by_qual[name])
        return list(self.by_short.get(name.split(".")[-1], []))

    def callees(self, chunk: Chunk) -> list:
        out, seen = [], set()
        for name in chunk.calls:
            for c in self.by_short.get(name, []):
                if c.id != chunk.id and c.id not in seen and c.kind in ("function", "method"):
                    seen.add(c.id)
                    out.append(c)
        return out


class ChromaRetriever(BM25Retriever):
    """Semantic retrieval (ChromaDB + all-MiniLM-L6-v2). Needs `pip install .[chroma]` and a model download."""

    def __init__(self, chunks: list, model: str = "all-MiniLM-L6-v2"):
        super().__init__(chunks)
        import uuid
        import chromadb
        from chromadb.utils import embedding_functions
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model)
        self._col = chromadb.EphemeralClient().create_collection(f"c{uuid.uuid4().hex}", embedding_function=ef)
        self._col.add(ids=[c.id for c in chunks], documents=[c.text for c in chunks])

    def search(self, query: str, k: int, exclude=frozenset()) -> list:
        n = min(len(self.chunks), k + len(exclude))
        r = self._col.query(query_texts=[query], n_results=n)
        out = [(self.by_id[i], 1.0 / (1.0 + d)) for i, d in zip(r["ids"][0], r["distances"][0]) if i not in exclude]
        return out[:k]
