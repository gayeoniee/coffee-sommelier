from typing import Literal

from pydantic import BaseModel, Field

from app.models import ParsedBean
from pipeline.rules import detect_decaf, normalize_country, normalize_process, normalize_roast


def parse_bean_text(text: str) -> ParsedBean:
    t = " ".join((text or "").split())
    is_decaf, decaf_process = detect_decaf(t)
    return ParsedBean(text=t, origin_country=normalize_country(t), process=normalize_process(t),
                      roast_level=normalize_roast(t), is_decaf=is_decaf, decaf_process=decaf_process)


def needs_llm_parse(parsed: ParsedBean) -> bool:
    return bool(parsed.text) and parsed.origin_country is None


class BeanParse(BaseModel):
    origin_country: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool | None = None


def merge_llm_parse(parsed: ParsedBean, llm: BeanParse) -> ParsedBean:
    """Rule values win; the LLM only fills gaps, and its strings go through the same normalizers."""
    return ParsedBean(
        text=parsed.text,
        origin_country=parsed.origin_country or normalize_country(llm.origin_country),
        process=parsed.process or normalize_process(llm.process),
        roast_level=parsed.roast_level or normalize_roast(llm.roast_level),
        is_decaf=parsed.is_decaf or bool(llm.is_decaf),
        decaf_process=parsed.decaf_process or ("unknown" if llm.is_decaf else None),
    )


def bean_parse_messages(text: str) -> list[dict]:
    return [
        {"role": "system", "content": "You extract coffee bean facts from a café bean card. Reply with one JSON object only."},
        {"role": "user", "content": (
            f"Bean card text: {text}\n\nReturn JSON: {{\"origin_country\": English country name or null, "
            "\"process\": washed|natural|honey|anaerobic|null, \"roast_level\": light|medium|dark|null, "
            "\"is_decaf\": true|false|null}. Use null when the text does not say.")},
    ]


class NoteSignals(BaseModel):
    acidity: Literal["lower", "higher"] | None = None
    body: Literal["lower", "higher"] | None = None
    sweetness: Literal["lower", "higher"] | None = None
    liked_flavors: list[str] = Field(default_factory=list)
    disliked_flavors: list[str] = Field(default_factory=list)


def note_messages(note: str) -> list[dict]:
    return [
        {"role": "system", "content": "손님이 마신 커피에 남긴 한 줄 후기에서 취향 신호를 뽑는다. JSON 객체 하나만 답한다."},
        {"role": "user", "content": (
            f"후기: {note}\n\nJSON: {{\"acidity\": \"lower\"|\"higher\"|null, \"body\": ..., \"sweetness\": ..., "
            "\"liked_flavors\": [...], \"disliked_flavors\": [...]}\n"
            "- '산미가 너무 셌다' → acidity: \"lower\" (다음엔 산미가 더 낮은 게 좋다는 뜻)\n"
            "- '너무 달고 무거웠어요' → sweetness: \"lower\", body: \"lower\"\n"
            "- '산미가 약해서 아쉬웠다' → acidity: \"higher\"\n"
            "- '쓴맛 없이 고소해서 좋았다' → liked_flavors: [\"nutty/cocoa\"]\n"
            "- 향미는 다음 중에서만: fruity, floral, sweet, nutty/cocoa, roasted, spices, sour/fermented\n"
            "- 후기에 근거가 없으면 null/빈 배열")},
    ]
