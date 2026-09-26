import json

from app.core.flavors import CATEGORY_KO
from app.models import ATTRS, Item, Prediction, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
CONFIDENCE_KO = {"high": "높음", "medium": "보통", "low": "낮음"}
CLOSE = 0.75
LIKED_FLAVOR_MIN = 0.2   # flavor weight above which the guest counts as liking a category
SYSTEM_PROMPT = ("너는 카페에서 손님에게 커피를 추천하는 친절한 바리스타다. 주어진 데이터만 근거로, 왜 이 음료가 손님 취향에 "
                 "맞는지(또는 안 맞는지) 한국어 2문장 이내로 설명해라. 데이터에 없는 수치나 사실을 지어내지 마라. "
                 "손님 취향 요약과 반대되는 말을 하지 마라. /no_think")
TOPIC_KO = {"acidity": "산미는", "body": "바디는", "sweetness": "단맛은"}
OBJECT_KO = {"acidity": "산미를", "body": "바디를", "sweetness": "단맛을"}


def template_explanation(item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                         violation: str | None = None) -> str:
    parts = [f"취향 적합도 {round(score * 100)}%."]
    close = [ATTR_KO[a] for a in ATTRS if item.attr(a) is not None and abs(item.attr(a) - getattr(profile, a)) <= CLOSE]
    if close:
        parts.append(f"{', '.join(close)}이(가) 선호와 가까워요.")
    if item.tags:
        parts.append("향미: " + ", ".join(tag_ko.get(t, t) for t in item.tags[:3]) + ".")
    if item.order_decaf:
        surcharge = f" (+{item.decaf_surcharge_krw}원)" if item.decaf_surcharge_krw else ""
        parts.append(f"디카페인으로 변경해서 주문하세요{surcharge}.")
    if item.source == "predicted":
        parts.append(f"유사 원두 기반 예측이에요(신뢰도 {CONFIDENCE_KO[item.confidence]}).")
    if violation:
        parts.insert(0, f"주의: {violation}.")
    return " ".join(parts)


def _level(a: str, v: float) -> str:
    if v >= 4:
        return f"{OBJECT_KO[a]} 강하게 좋아함"
    if v >= 3.5:
        return f"{OBJECT_KO[a]} 좋아함"
    if v <= 2:
        return f"{OBJECT_KO[a]} 싫어함"
    if v <= 2.5:
        return f"{TOPIC_KO[a]} 약한 편을 선호"
    return f"{TOPIC_KO[a]} 보통"


def liked_flavors_ko(profile: Profile) -> list[str]:
    return [CATEGORY_KO.get(c, c) for c, w in profile.flavor_weights.items() if w > LIKED_FLAVOR_MIN]


def preference_sentence(profile: Profile) -> str:
    """Plain-Korean profile summary, so the LLM cannot misread the 1-5 numbers (e.g. 4.5 as 'dislikes')."""
    s = ", ".join(_level(a, getattr(profile, a)) for a in ATTRS)
    liked = liked_flavors_ko(profile)
    return s + (f". 좋아하는 향미: {', '.join(liked)}" if liked else "")


def explain_messages(item: Item, profile: Profile, score: float, prediction: Prediction | None = None) -> list[dict]:
    payload = {
        "음료": item.name, "브랜드": item.brand, "산미": item.acidity, "바디": item.body, "단맛": item.sweetness,
        "향미": list(item.tags), "디카페인": item.is_decaf or item.order_decaf, "우유": item.is_milk,
        "적합도": round(score * 100),
        "손님 취향 요약": preference_sentence(profile),
        "손님 선호": {"산미": round(profile.acidity, 1), "바디": round(profile.body, 1),
                  "단맛": round(profile.sweetness, 1),
                  "좋아하는 향미": liked_flavors_ko(profile)},
        "근거": prediction.evidence if prediction else None,
    }
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def card(item: Item, score: float, template: str, violation: str | None = None,
         prediction: Prediction | None = None) -> dict:
    c = {"key": item.key, "name": item.name, "brand": item.brand, "score": round(score * 100),
         "source": item.source, "confidence": item.confidence, "acidity": item.acidity, "body": item.body,
         "sweetness": item.sweetness, "tags": list(item.tags), "is_decaf": item.is_decaf,
         "order_decaf": item.order_decaf, "decaf_surcharge_krw": item.decaf_surcharge_krw,
         "caffeine_mg": item.caffeine_mg, "is_milk": item.is_milk, "coffee_id": item.coffee_id,
         "menu_item_id": item.menu_item_id, "violation": violation, "template": template}
    if prediction is not None:
        c["evidence"] = prediction.evidence
        c["n_neighbors"] = prediction.n_neighbors
    return c
