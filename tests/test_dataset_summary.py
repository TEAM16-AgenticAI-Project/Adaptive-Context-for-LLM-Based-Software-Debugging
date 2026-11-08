import json

import pytest

from adaptive_context.bench.dataset_summary import summarize_manifest


def test_reproduction_summary_retains_failures_and_filters_only_successes(tmp_path):
    manifest = tmp_path / "cases.jsonl"
    rows = [{"id": "good", "source": {"reproduced": True}},
            {"id": "bad", "source": {"reproduced": False, "buggy_result": ["passed"]}},
            {"id": "unknown"}]
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = summarize_manifest(str(manifest), write_filtered=True)
    assert result["attempted"] == 3
    assert result["reproduced"] == result["not_reproduced"] == result["unknown"] == 1
    assert result["unsuccessful"][0]["case"] == "bad"
    assert [json.loads(line)["id"] for line in (tmp_path / "cases.reproduced.jsonl").read_text().splitlines()] == ["good"]


def test_reproduction_summary_rejects_nonboolean_or_duplicate_labels(tmp_path):
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps({"id": "one", "source": {"reproduced": "yes"}}) + "\n")
    with pytest.raises(ValueError, match="boolean"):
        summarize_manifest(str(manifest))
    manifest.write_text((json.dumps({"id": "one"}) + "\n") * 2)
    with pytest.raises(ValueError, match="unique"):
        summarize_manifest(str(manifest))
