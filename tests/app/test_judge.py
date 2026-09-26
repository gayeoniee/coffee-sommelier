import pytest
from pydantic import ValidationError

from app.core.judge import Verdict, judge_messages


def test_judge_messages_contain_facts_and_text():
    m = judge_messages({"음료": "x", "산미": 2}, "산미가 낮아요.")
    assert m[0]["role"] == "system" and "JSON" in m[0]["content"]
    assert '"산미": 2' in m[1]["content"] and "산미가 낮아요." in m[1]["content"]


def test_verdict_schema():
    v = Verdict.model_validate({"contradiction": False, "hallucination": True, "hallucination_why": "150mg 없음", "helpful": 3})
    assert v.helpful == 3 and v.hallucination


def test_verdict_rejects_helpful_out_of_range():
    with pytest.raises(ValidationError):
        Verdict.model_validate({"contradiction": False, "hallucination": False, "helpful": 6})


def test_judges_differ_from_the_explain_model_and_explain_is_short():
    from pipeline import settings
    tasks = settings.load_config("models.yaml")["tasks"]
    from app.eval import EXPLAIN_JUDGE_TASKS
    judges = [tasks[t] for t in EXPLAIN_JUDGE_TASKS.values()]
    assert EXPLAIN_JUDGE_TASKS == {"judge": "judge", "judge2": "judge_explain2"}
    assert all(j["model"] != tasks["explain"]["model"] for j in judges)          # no self-grading
    assert len({j["model"].split("/")[0] for j in judges}) == 2                  # different vendors
    assert tasks["explain"]["max_tokens"] <= 300                        # two Korean sentences, not a paragraph


def test_judge2_stays_the_phase1_gold_labelling_model():
    # data/eval/gold_enrich_judge2.csv was labelled with this model; `pipeline gold-label --judge judge2`
    # must keep reproducing it, so the explain-quality judge has its own task
    from pipeline import settings
    tasks = settings.load_config("models.yaml")["tasks"]
    assert tasks["judge2"]["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert tasks["judge_explain2"]["model"] == "openai/gpt-oss-20b"
    assert tasks["judge_explain2"]["extra"] == {"reasoning_effort": "low"}
