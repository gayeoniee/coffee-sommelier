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
