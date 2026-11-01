"""Retrievers. BM25 (pure Python, offline) is the default; Chroma is optional."""
from __future__ import annotations

import ast
import math
import re
import textwrap
from collections import Counter, defaultdict

from .chunking import parse_source
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
        self._modules = None
        self.by_path = defaultdict(list)
        for c in chunks:
            self.by_path[c.path].append(c)
        self._file_imports = {}

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

    # ---- module paths -------------------------------------------------------------------------------------
    def _module_index(self):
        """Dotted module name -> file paths. Every suffix of a path is indexed ('a.b.c', 'b.c', 'c'), so imports
        resolve whether or not the repo root is the import root; full names win over suffixes."""
        if self._modules is None:
            full, suffix = {}, defaultdict(list)
            for path in sorted({c.path for c in self.chunks}):
                if not path.endswith(".py"):
                    continue
                parts = path[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if not parts:
                    continue
                full.setdefault(".".join(parts), path)
                for i in range(1, len(parts)):
                    suffix[".".join(parts[i:])].append(path)
            self._modules = (full, suffix)
        return self._modules

    def module_paths(self, module: str) -> list:
        full, suffix = self._module_index()
        if module in full:
            return [full[module]]
        return list(suffix.get(module, []))

    def resolve_dotted(self, dotted: str) -> list:
        """'pkg.mod.func' or 'pkg.mod.Class.method' -> chunks. The longest module prefix that names a file wins."""
        parts = [p for p in dotted.split(".") if p]
        for i in range(len(parts) - 1, 0, -1):
            module, qual = ".".join(parts[:i]), ".".join(parts[i:])
            hits = [self.by_id[f"{p}::{qual}"] for p in self.module_paths(module) if f"{p}::{qual}" in self.by_id]
            if hits:
                return hits
        return []

    def _by_path_qual(self, path: str, qual: str) -> list:
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if f"{path}::{qual}" in self.by_id:
            return [self.by_id[f"{path}::{qual}"]]
        return [c for c in self.by_qual.get(qual, []) if c.path.endswith("/" + path)]

    def lookup(self, name: str) -> list:
        """Symbol lookup, most specific form first; returns the first form that matches, never a union:
        1. chunk id / 'path.py::Qual.name' / pytest id 'tests/t.py::TestX::test_y[param]'
        2. dotted module path as written in imports: 'pkg.mod.func', 'pkg.mod.Class.method'
        3. qualified name: 'Class.method' (or a top-level function name)
        4. short name: 'method'"""
        name = name.strip().strip("`()").strip()
        if name in self.by_id:
            return [self.by_id[name]]
        if "::" in name:
            path, _, rest = name.partition("::")
            qual = ".".join(p.split("[", 1)[0] for p in rest.split("::") if p)
            hits = self._by_path_qual(path, qual) if qual else []
            if hits:
                return hits
            name = qual or name
        if "." in name:
            hits = self.resolve_dotted(name)
            if hits:
                return hits
        if name in self.by_qual:
            return list(self.by_qual[name])
        return list(self.by_short.get(name.split(".")[-1], []))

    # ---- import-aware call resolution ------------------------------------------------------------------------
    def imports_of(self, chunk: Chunk) -> dict:
        """Local name -> dotted target bound by the imports of the chunk's file (from its module chunks) and by
        imports inside the chunk itself. `from x.y import f as g` binds g -> 'x.y.f'; `import x.y` binds x -> 'x'."""
        if chunk.path not in self._file_imports:
            text = "\n".join(c.text for c in self.by_path.get(chunk.path, []) if c.kind == "module")
            self._file_imports[chunk.path] = _import_bindings(text, chunk.path)
        own = _import_bindings(chunk.text, chunk.path) if chunk.kind != "module" and "import" in chunk.text else {}
        return {**self._file_imports[chunk.path], **own} if own else self._file_imports[chunk.path]

    def name_matches(self, chunk: Chunk, name: str) -> list:
        return [c for c in self.by_short.get(name.split(".")[-1], []) if c.id != chunk.id and c.kind in _CALLABLE]

    def resolve_call(self, chunk: Chunk, name: str, imports: dict | None = None) -> list:
        """Candidates for one call made by `chunk`: same-file definition, then the file's imports, then (only if
        neither applies) every function/method with that short name, as before."""
        imports = self.imports_of(chunk) if imports is None else imports
        head, _, rest = name.partition(".")
        hits = []
        if rest:
            if head in ("self", "cls") and chunk.kind == "method" and "." not in rest:
                own = f"{chunk.path}::{chunk.name.rsplit('.', 1)[0]}.{rest}"
                hits = [self.by_id[own]] if own in self.by_id else []
            elif head in imports:
                hits = self.resolve_dotted(f"{imports[head]}.{rest}")
        else:
            same = f"{chunk.path}::{name}"
            if same in self.by_id:
                hits = [self.by_id[same]]
            elif name in imports:
                hits = self.resolve_dotted(imports[name])
        hits = [c for c in hits if c.id != chunk.id and c.kind in _CALLABLE]
        return hits or self.name_matches(chunk, name)

    def call_names(self, chunk: Chunk, imports: dict | None = None) -> list:
        """The calls of a chunk, one entry per call site form. A bare attribute name (`func` from `z.func()`) is
        left out when a dotted form covers it, unless it is itself defined in the file or imported."""
        imports = self.imports_of(chunk) if imports is None else imports
        tails = {n.rsplit(".", 1)[1] for n in chunk.calls if "." in n}
        return [n for n in chunk.calls
                if "." in n or n not in tails or n in imports or f"{chunk.path}::{n}" in self.by_id]

    def callees(self, chunk: Chunk) -> list:
        out, seen = [], set()
        imports = self.imports_of(chunk)
        for name in self.call_names(chunk, imports):
            for c in self.resolve_call(chunk, name, imports):
                if c.id not in seen:
                    seen.add(c.id)
                    out.append(c)
        return out


_CALLABLE = ("function", "method")


def _import_nodes(text: str) -> list:
    try:
        return [n for n in ast.walk(parse_source(textwrap.dedent(text))) if isinstance(n, (ast.Import, ast.ImportFrom))]
    except (SyntaxError, ValueError):
        pass
    nodes, buf = [], []                      # module chunks can hold class headers without bodies: parse per statement
    for line in text.splitlines():
        s = line.strip()
        if not buf and not s.startswith(("import ", "from ")):
            continue
        buf.append(s)
        joined = " ".join(buf)
        if joined.count("(") > joined.count(")") or s.endswith("\\"):
            continue
        try:
            nodes.extend(n for n in parse_source(joined.replace("\\ ", " ")).body if isinstance(n, (ast.Import, ast.ImportFrom)))
        except (SyntaxError, ValueError):
            pass
        buf = []
    return nodes


def _import_bindings(text: str, path: str) -> dict:
    pkg = path.split("/")[:-1]          # package of this file; for pkg/__init__.py that is the package itself
    out = {}
    for n in _import_nodes(text):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    first = a.name.split(".")[0]
                    out[first] = first
            continue
        if n.level:
            up = n.level - 1
            base = pkg[:len(pkg) - up] if up <= len(pkg) else []
            module = ".".join(base + ([n.module] if n.module else []))
        else:
            module = n.module or ""
        for a in n.names:
            if a.name != "*" and module:
                out[a.asname or a.name] = f"{module}.{a.name}"
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
