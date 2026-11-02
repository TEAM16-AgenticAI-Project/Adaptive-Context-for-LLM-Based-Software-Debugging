"""Exercise the actual retriever adapter without downloads in the default suite.

The real model/database comparison is run separately via bench.audit --chroma.
"""
import sys
from types import ModuleType, SimpleNamespace

from adaptive_context.models import Chunk
from adaptive_context.retrieval import ChromaRetriever


def test_chroma_ranking_exclusions_and_symbol_lookup(monkeypatch):
    records = {}

    class Collection:
        def add(self, **kwargs):
            records["added"] = kwargs

        def query(self, **kwargs):
            records["query"] = kwargs
            return {"ids": [["b", "a", "c"]], "distances": [[0.1, 0.3, 0.9]]}

    class Embedding:
        def __init__(self, **kwargs):
            records["embedding"] = kwargs

    chroma = ModuleType("chromadb")
    utils = ModuleType("chromadb.utils")
    utils.embedding_functions = SimpleNamespace(SentenceTransformerEmbeddingFunction=Embedding)
    chroma.EphemeralClient = lambda: SimpleNamespace(create_collection=lambda *a, **kw: Collection())
    monkeypatch.setitem(sys.modules, "chromadb", chroma)
    monkeypatch.setitem(sys.modules, "chromadb.utils", utils)
    chunks = [Chunk(name, f"{name}.py", name, 1, 2, f"def {name}():\n    return 1") for name in "abc"]
    retriever = ChromaRetriever(chunks)
    hits = retriever.search("reported symptom", 2, exclude={"b"})
    assert [c.id for c, _ in hits] == ["a", "c"]
    assert hits[0][1] > hits[1][1]
    assert records["query"] == {"query_texts": ["reported symptom"], "n_results": 3}
    assert records["added"]["documents"] == [c.text for c in chunks]
    assert records["embedding"]["model_name"] == "all-MiniLM-L6-v2"
    assert retriever.lookup("a") == [chunks[0]]
