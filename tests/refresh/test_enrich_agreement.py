from scripts.refresh.enrich_agreement import agreement


def test_agreement_scores_tags_and_attributes():
    vocab = {"berry", "chocolate", "floral"}
    pairs = [({"flavor_tags": ["Berry", "chocolate", "not-in-vocab"], "acidity": 4, "body": None, "sweetness": 3},
              {"flavor_tags": ["berry", "floral"], "acidity": 2, "body": 3, "sweetness": 3})]
    r = agreement(pairs, vocab)
    assert (r["tags"]["tp"], r["tags"]["fp"], r["tags"]["fn"]) == (1, 1, 1)
    assert r["tags"]["f1"] == 0.5
    assert r["acidity"] == {"n": 1, "within1": 0.0, "exact": 0.0, "null_disagree": 0, "reference_null_only": 0,
                            "candidate_null_only": 0}
    assert r["body"]["n"] == 0 and r["body"]["within1"] is None and r["body"]["reference_null_only"] == 1
    assert r["sweetness"]["exact"] == 1.0
