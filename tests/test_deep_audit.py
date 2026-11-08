import json
from pathlib import Path

import pytest

from adaptive_context.bench import audit
from adaptive_context.bench.controlled import build, validate
from adaptive_context.cases import RealCase, load_cases


def fixture_case(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("def match(x):\n    return x + 1\n")
    (tmp_path / "pkg/b.py").write_text("def match(x):\n    return x - 1\n")
    (tmp_path / "test_a.py").write_text("from pkg.a import match\n\ndef test_it():\n    assert match(2) == 2\n")
    return RealCase("tiny", "fixture", str(tmp_path),
                    "Failing test output (last lines):\npkg/a.py:2: AssertionError\n", ["pkg/a.py"],
                    ["pkg/a.py::match"], "pytest test_a.py::test_it")


def test_audit_ranks_names_imports_and_does_not_mutate(tmp_path):
    case = fixture_case(tmp_path)
    before = (tmp_path / "pkg/a.py").read_bytes()
    row = audit.audit_case(case)
    assert row["status"] == "ok"
    assert row["gold_short_name_counts"] == {"match": 2}
    assert row["test_output_names_gold_file"]
    assert row["test_imports_gold_module"]
    assert row["retrievers"]["bm25"]["hit_at_3"]
    assert row["retrievers"]["bm25"]["gold_ranks"]["pkg/a.py::match"] >= 1
    assert (tmp_path / "pkg/a.py").read_bytes() == before


def test_duplicate_hits_keep_the_first_gold_rank(tmp_path):
    case = fixture_case(tmp_path)
    chunks = audit.chunk_repo(audit.load_repo_dir(case.repo))
    gold = next(chunk for chunk in chunks if chunk.id == "pkg/a.py::match")
    other = next(chunk for chunk in chunks if chunk.id != gold.id)
    result = audit.rank_metrics([(gold, 9), (other, 8), (other, 7), (gold, 6)], [gold.id])
    assert result["best_rank"] == 1 and result["hit_at_3"]


def test_labels_never_enter_retrieval_query(tmp_path, monkeypatch):
    case = fixture_case(tmp_path)
    case.gold_functions = ["LABEL_SENTINEL"]
    queries = []
    original = audit.BM25Retriever.search

    def search(self, query, *args):
        queries.append(query)
        return original(self, query, *args)

    monkeypatch.setattr(audit.BM25Retriever, "search", search)
    audit.audit_case(case)
    assert queries == [case.bug_report]
    assert "LABEL_SENTINEL" not in queries[0]


def test_duplicate_definition_variant_applies_to_both_rankers(tmp_path):
    case = fixture_case(tmp_path)
    with (tmp_path / "pkg/a.py").open("a") as source:
        source.write("\ndef match(x):\n    return x + 5\n")
    seen = []

    class Semantic:
        def __init__(self, chunks):
            seen.extend(chunks)

        def search(self, query, count):
            return [(chunk, 1.0) for chunk in seen][:count]

    row = audit.audit_case(case, chroma=True, chroma_factory=Semantic, deduplicate=True)
    assert row["duplicate_chunks"] == 1
    assert row["deduplicated"]
    assert len(seen) == row["chunks"]
    assert len({chunk.id for chunk in seen}) == len(seen)
    assert "+ 5" in next(chunk.text for chunk in seen if chunk.id == "pkg/a.py::match")


def test_imports_resolve_relative_and_aliases_without_executing():
    source = "from .rules import chosen as alias\nfrom pkg.rules.target import run\nimport os.path as p\n"
    modules = audit.imported_modules(source, "pkg/test_it.py")
    assert {"pkg.rules.chosen", "pkg.rules.target", "os.path"} <= modules
    assert audit.test_paths("pytest -q tests/t.py::TestA::test_x") == ["tests/t.py"]


def test_absent_evidence_is_unknown_and_missing_repo_is_error(tmp_path):
    case = fixture_case(tmp_path)
    case.bug_report = "A user sees incorrect results."
    case.test_cmd = ""
    row = audit.audit_case(case)
    assert row["test_output_names_gold_file"] is None
    assert row["test_imports_gold_module"] is None
    case.repo = str(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        audit.audit_case(case)
    summary = audit.summarize([row, {"status": "error"}])
    assert summary["processed"] == 1 and summary["errors"] == 1
    assert summary["test_imports_gold_module"]["observed"] == 0


def test_audit_cli_writes_json_and_rejects_duplicate_ids(tmp_path):
    fixture_case(tmp_path)
    manifest = tmp_path / "cases.jsonl"
    value = {"id": "tiny", "repo": ".", "bug_report": "match", "gold": {"functions": ["pkg/a.py::match"]}}
    manifest.write_text(json.dumps(value) + "\n")
    summary = audit.run_audit(str(manifest), str(tmp_path / "output"))
    assert summary["processed"] == 1 and summary["llm_calls"] == 0
    assert json.loads((tmp_path / "output/summary.json").read_text())["manifest_sha256"]
    manifest.write_text((json.dumps(value) + "\n") * 2)
    with pytest.raises(ValueError, match="unique"):
        audit.run_audit(str(manifest), str(tmp_path / "output"))


def test_controlled_manifest_has_no_labels_inside_repositories(tmp_path):
    manifest = build(str(tmp_path))
    cases = load_cases(manifest)
    assert len(cases) == 8
    for case in cases:
        assert "kernel.py" not in case.bug_report
        assert "fold" not in case.bug_report
        assert Path(case.repo).is_dir()
        assert not list(Path(case.repo).glob("*.patch"))


def test_manifest_can_use_exported_repos_without_rewriting_reports(tmp_path):
    exported = tmp_path / "exports"
    exported.mkdir()
    fixture_case(exported)
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps({"id": "tiny", "repo": ".", "bug_report": "match",
                                    "gold": {"functions": ["pkg/a.py::match"]}}) + "\n")
    result = audit.run_audit(str(manifest), str(tmp_path / "output"), repo_root=str(exported))
    assert result["processed"] == 1 and result["repo_root"] == str(exported)


def test_all_controlled_cases_fail_before_and_pass_after(tmp_path):
    manifest = build(str(tmp_path / "data"))
    originals = {str(p): p.read_bytes() for p in (tmp_path / "data/repos").rglob("*.py")}
    result = validate(manifest, str(tmp_path / "results"))
    assert result["cases"] == result["reproduced"] == 8
    assert all(Path(name).read_bytes() == content for name, content in originals.items())
