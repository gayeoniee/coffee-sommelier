"""--pin-holdout: both training scripts read the held-out ids (in drawn order) from the shipped LOO cache
instead of re-drawing, so a retrain is compared on the exact targets the shipped metrics used."""
import json

import pytest

pytest.importorskip("sklearn")

from scripts import train_attr_model, train_tag_model  # noqa: E402


def _write_cache(path, ids):
    path.write_text("".join(json.dumps({"id": i, "vector": [0.0]}) + "\n" for i in ids) + "\n", encoding="utf-8")


def test_tag_script_reads_pinned_ids_in_order(tmp_path):
    p = tmp_path / "loo_tagfree_query_embeddings.jsonl"
    _write_cache(p, [42, 7, 1003])
    assert train_tag_model.pinned_holdout_ids(p) == [42, 7, 1003]


def test_attr_script_reads_the_same_file(tmp_path, monkeypatch):
    _write_cache(tmp_path / "loo_tagfree_query_embeddings.jsonl", [5, 3, 9])
    monkeypatch.setattr(train_attr_model.settings, "EVAL_DIR", tmp_path)
    assert train_attr_model.pinned_holdout_ids() == [5, 3, 9]
