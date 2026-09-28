import json

from app.core.flavors import CATEGORY_KO
from app.models import ATTRS, Item, Prediction, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
CONFIDENCE_KO = {"high": "높음", "medium": "보통", "low": "낮음"}
CLOSE = 0.75
LIKED_FLAVOR_MIN = 0.2   # flavor weight above which the guest counts as liking a category
SYSTEM_PROMPT = ("너는 카페에서 손님에게 커피를 추천하는 친절한 바리스타다. 주어진 데이터만 근거로, 왜 이 음료가 손님 취향에 "
                 "맞는지(또는 안 맞는지) 한국어 2문장 이내로 설명해라. 데이터에 없는 수치나 사실을 지어내지 마라. "
                 "손님 취향 요약과 반대되는 말을 하지 마라. "
                 "'디카페인 음료'가 true일 때만 디카페인 음료라고 말하라. '디카페인으로 주문 권장'이 true면 원래는 "
                 "디카페인이 아니니 '디카페인으로 바꿔 주문하면 (+N원)'처럼 안내하고(N은 '디카페인 추가요금(원)'; 그 값이 "
                 "null이면 금액 없이 '디카페인으로 바꿔 주문하면'만), 이미 디카페인이라고 말하지 마라.")
LENGTH_RULE = (" 반드시 지킬 규칙: 설명은 줄바꿈 없는 한 문단, 최대 2문장이다({first}, 둘째 문장은 보충 한 가지). "
               "세 번째 문장, 괄호 속 메모, 고쳐 쓴 두 번째 답은 절대 쓰지 마라. 향미 이름 말고는 한국어만 쓰고, "
               "데이터의 항목 이름이나 true/false를 그대로 옮기지 마라. 〔 〕는 이 음료의 데이터로 채울 자리이고 예시 내용을 "
               "사실처럼 옮기지 마라. 형식 예: '〔향미〕가 손님이 좋아하는 〔취향〕과 맞아 취향에 잘 맞아요. "
               "〔다른 속성〕은 〔선호와의 차이〕라 아쉬울 수 있어요.'")
FIRST_SENTENCE = "첫 문장은 결론과 가장 큰 이유"
FIRST_SENTENCE_VIOLATION = "첫 문장 하나에 조건 위반('{violation}')과 그래서 주문 전 확인이 필요하다는 결론을 함께"
NO_THINK = " /no_think"      # qwen: skip the reasoning phase
DECAF_CAFFEINE_RULE = (" 이 음료의 원래 카페인 수치는 데이터에 없고 말하지도 마라. 카페인을 말할 때는 "
                       "'디카페인 주문 시 카페인(mg, 추정)' 값만 '약 N mg(추정)'처럼 쓰고, 그 값이 null이면 수치 없이 "
                       "'디카페인으로 주문하면 카페인이 줄어요'라고만 하라.")
VIOLATION_RULE = " 조건 위반이 있으니 첫 문장에서 그 위반 사실(카페인·우유 조건)을 먼저 분명히 말하라."
CAFFEINE_RULE_KO = {"decaf_only": "디카페인만", "low": "저카페인", "any": "제한 없음"}
TOPIC_KO = {"acidity": "산미는", "body": "바디는", "sweetness": "단맛은"}
OBJECT_KO = {"acidity": "산미를", "body": "바디를", "sweetness": "단맛을"}


def _subject(word: str) -> str:
    """Korean subject particle: 이 after a final consonant, 가 otherwise."""
    last = word[-1]
    return word + ("이" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "가")


def template_explanation(item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                         violation: str | None = None) -> str:
    """At most two sentences: score (+ closeness, + violation warning), then the item facts joined by '·'."""
    close = [ATTR_KO[a] for a in ATTRS if item.attr(a) is not None and abs(item.attr(a) - getattr(profile, a)) <= CLOSE]
    first = f"취향 적합도 {round(score * 100)}%" + (f"로 {_subject('·'.join(close))} 선호와 가까워요." if close else "예요.")
    if violation:
        first = f"주의: {violation} — {first}"
    facts = []
    if item.tags:
        facts.append("향미: " + ", ".join(tag_ko.get(t, t) for t in item.tags[:3]))
    if item.order_decaf:
        extra = [f"+{item.decaf_surcharge_krw}원"] if item.decaf_surcharge_krw else []
        if item.caffeine_mg is not None and item.caffeine_mg_note:
            extra.append(f"카페인 약 {item.caffeine_mg:g}mg 추정")
        facts.append("디카페인으로 바꿔 주문하세요" + (f" ({', '.join(extra)})" if extra else ""))
    if item.source == "predicted":
        facts.append(f"유사 원두 기반 예측이에요(신뢰도 {CONFIDENCE_KO[item.confidence]})")
    return first + (" " + " · ".join(facts) + "." if facts else "")


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


def length_rule(violation: str | None = None) -> str:
    """The hard two-sentence rule, stated last; with a violation its first sentence must carry the violation."""
    first = FIRST_SENTENCE_VIOLATION.format(violation=violation) if violation else FIRST_SENTENCE
    return LENGTH_RULE.format(first=first)


def explain_messages(item: Item, profile: Profile, score: float, prediction: Prediction | None = None,
                     violation: str | None = None) -> list[dict]:
    payload = {
        "음료": item.name, "브랜드": item.brand, "산미": item.acidity, "바디": item.body, "단맛": item.sweetness,
        "향미": list(item.tags), "디카페인 음료": item.is_decaf,
        "디카페인으로 주문 권장": item.order_decaf,
        "디카페인 추가요금(원)": (item.decaf_surcharge_krw or None) if item.order_decaf else None,
        "우유": item.is_milk,
        "적합도": round(score * 100),
        "손님 취향 요약": preference_sentence(profile),
        "손님 선호": {"산미": round(profile.acidity, 1), "바디": round(profile.body, 1),
                  "단맛": round(profile.sweetness, 1),
                  "좋아하는 향미": liked_flavors_ko(profile),
                  "카페인 조건": CAFFEINE_RULE_KO[profile.caffeine_rule],
                  "우유": "가능" if profile.milk_ok else "불가"},
        "조건 위반": violation,
        "근거": prediction.evidence if prediction else None,
    }
    if item.order_decaf:    # only the decaf-order estimate, never the regular drink's caffeine
        payload["디카페인 주문 시 카페인(mg, 추정)"] = item.caffeine_mg if item.caffeine_mg_note else None
    if item.bean_note:      # franchise drink with an official bean description (docs/adr/0012); absent otherwise
        payload["원두 공식 설명"] = item.bean_note
    system = (SYSTEM_PROMPT + (DECAF_CAFFEINE_RULE if item.order_decaf else "") + (VIOLATION_RULE if violation else "")
              + length_rule(violation) + NO_THINK)
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def sample_card(coffee_id: int, name: str, tags: list[str], tag_ko: dict[str, str]) -> dict:
    """Onboarding sample: only our own tags and a templated sentence — never the review-derived flavor_summary."""
    tags_ko = [tag_ko.get(t.lower(), t) for t in tags]
    return {"coffee_id": coffee_id, "name": name, "tags": list(tags), "tags_ko": tags_ko,
            "description": f"{'·'.join(tags_ko[:3])} 향이 나는 원두"}


def card(item: Item, score: float, template: str, violation: str | None = None,
         prediction: Prediction | None = None, tag_ko: dict[str, str] | None = None) -> dict:
    c = {"key": item.key, "name": item.name, "brand": item.brand, "score": round(score * 100),
         "source": item.source, "confidence": item.confidence, "acidity": item.acidity, "body": item.body,
         "sweetness": item.sweetness, "tags": list(item.tags),
         "tags_ko": [(tag_ko or {}).get(t.lower(), t) for t in item.tags], "is_decaf": item.is_decaf,
         "order_decaf": item.order_decaf, "decaf_surcharge_krw": item.decaf_surcharge_krw,
         "caffeine_mg": item.caffeine_mg, "caffeine_mg_note": item.caffeine_mg_note, "is_milk": item.is_milk,
         "coffee_id": item.coffee_id, "menu_item_id": item.menu_item_id, "violation": violation, "template": template}
    if prediction is not None:
        c["evidence"] = prediction.evidence
        c["n_neighbors"] = prediction.n_neighbors
        if prediction.attr_confidence is not None:     # open variant: calibrated per attribute (ADR 0016)
            c["attr_confidence"] = prediction.attr_confidence
    elif item.bean_note:    # franchise card: the brand's own bean line is the evidence (docs/adr/0012)
        c["evidence"] = [item.bean_note]
    return c
