"""LLM-judge prompt and verdict schema for `app.eval explain_quality`. Pure: builds messages, never calls a model."""
import json

from pydantic import BaseModel, Field, field_validator

JUDGE_SYSTEM = ("너는 커피 추천 설명의 검수자다. 아래 '사실'만이 참이다. 설명이 사실과 모순되면 contradiction=true, "
                "사실에 없는 내용을 단정하면 hallucination=true. helpful은 손님이 고르는 데 도움이 되는 정도 1~5. "
                "JSON만 출력: {contradiction, contradiction_why, hallucination, hallucination_why, helpful}")


class Verdict(BaseModel):
    contradiction: bool
    contradiction_why: str = ""
    hallucination: bool
    hallucination_why: str = ""
    helpful: int = Field(ge=1, le=5)

    @field_validator("contradiction_why", "hallucination_why", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return "" if v is None else v


def judge_messages(payload: dict, explanation: str) -> list[dict]:
    facts = json.dumps(payload, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"사실:\n{facts}\n\n설명:\n{explanation}"}]
