from adaptive_context.adaptive import AdaptiveDebugger
from adaptive_context.baseline import StaticRAG
from adaptive_context.bench.generate import GenParams, generate_case, verify_fix
from adaptive_context.bench.run import _work, summarize
from adaptive_context.budget import pack
from adaptive_context.chunking import chunk_repo, chunk_source
from adaptive_context.compress import compress
from adaptive_context.protocol import parse_reply
from adaptive_context.retrieval import BM25Retriever, tokenize
from adaptive_context.sequencing import order
from adaptive_context.sim import SimulatedLLM
from adaptive_context.tokens import count_tokens

SRC = '''
import os

def apply_discount(price, discount):
    """Return the discounted price.

    Long notes here.
    """
    # comment
    return price * (1 - discount)


def checkout(price, discount):
    """Checkout total."""
    apply_discount(price, discount)
    return price


class Cart:
    def total(self):
        return apply_discount(1, 0)
'''


def test_chunking_functions_methods_module():
    ch = {c.name: c for c in chunk_source("a.py", SRC)}
    assert {"apply_discount", "checkout", "Cart.total", "<module>"} <= set(ch)
    assert "apply_discount" in ch["checkout"].calls
    assert ch["Cart.total"].short_name == "total"


def test_chunking_syntax_error_falls_back_to_windows():
    out = chunk_source("bad.py", "def (:\n" * 100)
    assert out and all(c.kind == "window" for c in out)


def test_tokenize_splits_snake_and_camel():
    assert tokenize("apply_discountRate2") == ["apply", "discount", "rate", "2"]


def test_bm25_search_lookup_callees():
    r = BM25Retriever(chunk_source("a.py", SRC))
    top = r.search("discounted price", 1)[0][0]
    assert top.name == "apply_discount"
    assert r.lookup("Cart.total")[0].id == "a.py::Cart.total"
    assert [c.name for c in r.callees(r.lookup("checkout")[0])] == ["apply_discount"]
    assert all(c.id != top.id for c, _ in r.search("discounted price", 5, exclude={top.id}))


def test_compress_levels_keep_contract_and_shrink():
    c = next(c for c in chunk_source("a.py", SRC) if c.name == "apply_discount")
    sizes = [count_tokens(compress(c, l)) for l in range(4)]
    assert sizes == sorted(sizes, reverse=True)
    for l in (1, 2, 3):
        assert "Return the discounted price." in compress(c, l)
    assert "Long notes" not in compress(c, 1) and "# comment" not in compress(c, 1)
    assert "return price" in compress(c, 2) and "return price" not in compress(c, 3)


def test_pack_respects_budget():
    case = generate_case(3)
    chunks = chunk_repo(case.files)[:30]
    res = pack([(c, 30 - i) for i, c in enumerate(chunks)], 600)
    assert res.tokens <= 600 and res.items
    assert res.items[0].chunk.id == chunks[0].id          # best chunk survives


def test_sequencing_callers_before_callees():
    r = BM25Retriever(chunk_source("a.py", SRC))
    res = pack([(c, 1.0) for c in r.chunks if c.kind != "module"], 10_000)
    names = [p.chunk.name for p in order(res.items, r, "dependency")]
    assert names.index("checkout") < names.index("apply_discount")


def test_parse_reply_handles_noise():
    assert parse_reply('thinking... {"action":"request","symbols":["a"]} done')["symbols"] == ["a"]
    assert parse_reply("no json") is None


def test_generator_deterministic_and_fix_verifies():
    a, b = generate_case(5), generate_case(5)
    assert a.files == b.files and a.bug_report == b.bug_report
    assert not verify_fix(a, a.truth.file, a.truth.function, "return x + 1")        # wrong fix
    assert not verify_fix(a, a.truth.file, a.truth.function, "__import__('os')")    # unsafe text rejected


def test_adaptive_beats_static_on_deep_bug():
    p = GenParams()
    deep = next(generate_case(s, p) for s in range(100) if generate_case(s, p).truth.depth >= 3)
    retr = BM25Retriever(chunk_repo(deep.files))
    llm = SimulatedLLM()
    base = StaticRAG(retr, llm, 3).run(deep.bug_report)
    ad = AdaptiveDebugger(retr, llm, prefetch_depth=2).run(deep.bug_report)
    assert ad.diagnosis.function == deep.truth.function
    assert base.diagnosis.function != deep.truth.function
    assert ad.stats.llm_calls > 1 and base.stats.llm_calls == 1


def test_tight_budget_still_runs_and_respects_it():
    case = generate_case(11)
    retr = BM25Retriever(chunk_repo(case.files))
    res = AdaptiveDebugger(retr, SimulatedLLM(), token_budget=500, prefetch_depth=2).run(case.bug_report)
    assert res.diagnosis.valid
    assert all(t["context_tokens"] < 900 for t in res.trace)   # budget + prompt scaffolding


def test_bench_smoke_and_summary():
    cfg = {"top_k": 3, "wide_k": 10, "budget": 3000, "rounds": 6, "llm": "sim", "model": None, "miss": 0.0}
    rows = [_work((s, GenParams(n_modules=15, n_distractor_chains=8), cfg)) for s in range(30)]
    sm = summarize(rows)
    assert sm["systems"]["adaptive_graph"]["func_acc"] > sm["systems"]["static_k3"]["func_acc"]
    assert sm["systems"]["adaptive_graph"]["calls"] <= sm["systems"]["adaptive"]["calls"]


def test_tools_and_run_failing_test_in_temp_copy(tmp_path):
    import os
    from adaptive_context.chunking import load_repo_dir
    from adaptive_context.tools import Tools
    (tmp_path / "discount.py").write_text("def apply_discount(p, d):\n    return p * (1 - d)\n")
    (tmp_path / "payment.py").write_text("from discount import apply_discount\n\ndef checkout(p, d):\n    apply_discount(p, d)\n    return p\n")
    (tmp_path / "test_payment.py").write_text("from payment import checkout\n\ndef test_it():\n    assert checkout(100, 0.2) == 80\n")
    t = Tools(BM25Retriever(chunk_repo(load_repo_dir(str(tmp_path)))), str(tmp_path))
    assert t.get_callees("checkout") == ["discount.py::apply_discount"]
    assert t.get_callers("apply_discount") == ["payment.py::checkout"]
    assert "return p * (1 - d)" in t.read_function("apply_discount")
    res = t.run_failing_test()
    assert res["ok"] and res["passed"] is False                      # the bug is reproduced
    assert not any(f.startswith(".pytest_cache") for f in os.listdir(tmp_path))   # original tree untouched


def test_run_failing_test_on_example(tmp_path):
    import pathlib, shutil
    from adaptive_context.tools import run_failing_test
    src = pathlib.Path(__file__).parent.parent / "examples" / "payment_bug"
    repo = tmp_path / "payment_bug"
    shutil.copytree(src, repo)
    r = run_failing_test(str(repo), "test_payment.py")
    assert r["passed"] is False and "assert" in r["output"]
    assert not (repo / ".pytest_cache").exists()          # original repo untouched
