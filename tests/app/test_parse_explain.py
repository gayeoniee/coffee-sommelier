import json

from app.core.explain import (
    SYSTEM_PROMPT,
    VIOLATION_RULE,
    card,
    explain_messages,
    verdict_phrase,
    length_rule,
    preference_sentence,
    template_explanation,
)
from app.core.explain_check import check_explanation
from app.core.parse import (
    BeanParse,
    NoteSignals,
    merge_llm_parse,
    needs_llm_parse,
    note_messages,
    parse_bean_text,
)
from app.models import Item, Prediction, Profile


def test_parse_bean_text_rules():
    p = parse_bean_text("  에티오피아 예가체프 워시드 디카페인 스위스워터 ")
    assert (p.text, p.origin_country, p.process, p.is_decaf, p.decaf_process) == (
        "에티오피아 예가체프 워시드 디카페인 스위스워터", "Ethiopia", "washed", True, "unknown")
    assert needs_llm_parse(p) is False
    unknown = parse_bean_text("동네 로스터리 하우스 블렌드")
    assert unknown.origin_country is None and needs_llm_parse(unknown) is True


def test_parse_bean_text_flavor_notes_do_not_set_roast():
    # ADR 0014: "dark chocolate" in the notes read as a dark roast on 65 of 196 Zenodo panel cards
    assert parse_bean_text("Colombia Huila washed. Notes: dark chocolate, dark cherry").roast_level is None
    assert parse_bean_text("브라질 세하도 내추럴, 다크 초콜릿·다크 체리").roast_level is None
    assert parse_bean_text("Kenya AA, medium roast, dark fruit").roast_level == "medium"
    assert parse_bean_text("과테말라 강배전 다크초콜릿").roast_level == "dark"


def test_merge_llm_parse_normalizes_and_keeps_rule_values():
    p = parse_bean_text("하우스 블렌드 내추럴")
    merged = merge_llm_parse(p, BeanParse(origin_country="브라질", process="washed", roast_level="Medium-Dark",
                                          is_decaf=True))
    assert merged.origin_country == "Brazil"
    assert merged.process == "natural"               # rule result wins over the LLM
    assert (merged.roast_level, merged.is_decaf) == ("medium-dark", True)


def test_note_signals_schema():
    s = NoteSignals.model_validate({"acidity": "lower", "liked_flavors": ["fruity"]})
    assert s.model_dump(exclude_none=True) == {"acidity": "lower", "liked_flavors": ["fruity"], "disliked_flavors": []}


def test_template_explanation():
    it = Item(key="menu:1", name="카페 아메리카노", source="brand_bean", acidity=4, body=3, tags=("lemon", "floral"),
              decaf_option=True, order_decaf=True, decaf_surcharge_krw=300)
    text = template_explanation(it, Profile(acidity=4, body=3), 0.87, {"lemon": "레몬"})
    assert text.startswith("취향 적합도 87%로 산미·바디가 선호와 가까워요.")
    assert "향미: 레몬, floral" in text
    assert "디카페인으로 바꿔 주문하세요 (+300원)" in text
    warn = template_explanation(it, Profile(), 0.5, {}, violation="우유가 들어가요")
    assert warn.startswith("주의: 우유가 들어가요 — 취향 적합도 50%")
    assert template_explanation(Item(key="x", name="x", source="db"), Profile(), 0.5, {}) == "취향 적합도 50%예요."
    assert "단맛이 선호와" in template_explanation(Item(key="x", name="x", source="db", sweetness=3), Profile(), 0.5, {})


def test_template_explanation_is_at_most_two_sentences():
    it = Item(key="input", name="예가체프", source="predicted", confidence="low", acidity=4, body=3, sweetness=3,
              tags=("lemon", "floral", "honey"), decaf_option=True, order_decaf=True, decaf_surcharge_krw=300)
    for violation in (None, "카페인이 100mg을 넘거나 알 수 없어요"):
        text = template_explanation(it, Profile(), 0.84, {"lemon": "레몬"}, violation)
        assert text.count(".") <= 2, text
        payload = json.loads(explain_messages(it, Profile(), 0.84, violation=violation)[1]["content"])
        assert check_explanation(text, payload, 84, violation)["length"] is True


def test_explain_messages_carry_data_not_review_text():
    it = Item(key="input", name="예가체프", source="predicted", acidity=4.2, confidence="medium")
    pred = Prediction(acidity=4.2, body=None, sweetness=None, confidence="medium", tags=["lemon"],
                      evidence=["유사 원두 10개 중 8개에서 '레몬' 언급"], n_neighbors=10)
    msgs = explain_messages(it, Profile(flavor_weights={"fruity": 0.6}), 0.8, pred)
    assert msgs[0]["role"] == "system" and "2문장" in msgs[0]["content"]
    payload = json.loads(msgs[1]["content"])
    assert payload["근거"] == ["유사 원두 10개 중 8개에서 '레몬' 언급"]
    assert payload["손님 선호"]["좋아하는 향미"] == ["과일"]


def test_card_shape():
    it = Item(key="coffee:7", name="Kenya", source="db", acidity=4, coffee_id=7)
    c = card(it, 0.834, "t")
    assert c["score"] == 83 and c["key"] == "coffee:7" and c["coffee_id"] == 7 and c["template"] == "t"
    assert c["violation"] is None and "evidence" not in c


def test_franchise_card_shows_the_official_bean_note_as_evidence():
    it = Item(key="menu:3", name="아메리카노", source="brand_bean", acidity=2, body=4,
              bean_note="스타벅스 공식: 에스프레소 로스트(다크) — 강한 바디감과 캬라멜 향")
    c = card(it, 0.8, "t")
    assert c["evidence"] == ["스타벅스 공식: 에스프레소 로스트(다크) — 강한 바디감과 캬라멜 향"]
    assert "n_neighbors" not in c


def test_explain_payload_carries_the_official_bean_note_only_when_present():
    with_note = Item(key="menu:3", name="아메리카노", source="brand_bean", bean_note="공식: 캐러멜 향 99.9%")
    payload = json.loads(explain_messages(with_note, Profile(), 0.8)[1]["content"])
    assert payload["원두 공식 설명"] == "공식: 캐러멜 향 99.9%"
    # a number quoted from the official note is grounded (app/core/explain_check.py numbers_grounded)
    assert check_explanation("디카페인 원두는 99.9% 카페인을 뺐어요.", payload, 80, None)["numbers_grounded"]
    without = json.loads(explain_messages(Item(key="menu:1", name="카페 라떼", source="brand_bean"),
                                          Profile(), 0.8)[1]["content"])
    assert "원두 공식 설명" not in without       # unchanged payload for every other item (explain_quality cases)


def test_template_explanation_predicted_source_shows_confidence():
    low = Item(key="input", name="예가체프", source="predicted", confidence="low", acidity=4)
    text = template_explanation(low, Profile(), 0.5, {})
    assert "유사 원두 기반 예측이에요(신뢰도 낮음)." in text

    high = Item(key="input", name="예가체프", source="predicted", confidence="high", acidity=4)
    text = template_explanation(high, Profile(), 0.5, {})
    assert "유사 원두 기반 예측이에요(신뢰도 높음)." in text


def test_card_includes_prediction_evidence_and_neighbors():
    it = Item(key="input", name="예가체프", source="predicted", acidity=4.2, confidence="medium")
    pred = Prediction(acidity=4.2, body=None, sweetness=None, confidence="medium", tags=["lemon"],
                      evidence=["유사 원두 10개 중 8개에서 '레몬' 언급"], n_neighbors=10)
    c = card(it, 0.8, "t", prediction=pred)
    assert c["evidence"] == pred.evidence
    assert c["n_neighbors"] == pred.n_neighbors


def test_preference_sentence_levels_and_flavors():
    s = preference_sentence(Profile(acidity=4.5, body=2.5, sweetness=3, flavor_weights={"fruity": 0.6, "floral": 0.3,
                                                                                         "roasted": 0.1}))
    assert s == "산미를 강하게 좋아함, 바디는 약한 편을 선호, 단맛은 보통. 좋아하는 향미: 과일, 꽃"
    s = preference_sentence(Profile(acidity=1.5, body=3.6, sweetness=2.0))
    assert s == "산미를 싫어함, 바디를 좋아함, 단맛을 싫어함"
    s = preference_sentence(Profile(acidity=4.0, body=3.5, sweetness=3.4))   # boundaries are inclusive
    assert s == "산미를 강하게 좋아함, 바디를 좋아함, 단맛은 보통"


def test_explain_payload_states_preference_summary():
    msgs = explain_messages(Item(key="menu:1", name="카페 라떼", source="brand_bean"),
                            Profile(acidity=4.5, flavor_weights={"fruity": 0.6}), 0.7)
    payload = json.loads(msgs[1]["content"])
    assert payload["손님 취향 요약"].startswith("산미를 강하게 좋아함")
    assert "손님 취향 요약과 반대되는 말을 하지 마라." in SYSTEM_PROMPT


def test_note_messages_few_shot_examples():
    user = note_messages("그냥 그랬다")[1]["content"]
    assert "'너무 달고 무거웠어요' → sweetness: \"lower\", body: \"lower\"" in user
    assert "'산미가 약해서 아쉬웠다' → acidity: \"higher\"" in user
    assert "'쓴맛 없이 고소해서 좋았다' → liked_flavors: [\"nutty/cocoa\"]" in user


def test_explain_payload_carries_rules_and_violation():
    item = Item(key="menu:1", name="카페 라떼", source="brand_bean", is_milk=True)
    ok = explain_messages(item, Profile(caffeine_rule="low", milk_ok=True), 0.7)
    payload = json.loads(ok[1]["content"])
    assert payload["손님 선호"]["카페인 조건"] == "저카페인" and payload["손님 선호"]["우유"] == "가능"
    assert "조건 위반" not in payload and VIOLATION_RULE not in ok[0]["content"]
    bad = explain_messages(item, Profile(caffeine_rule="decaf_only", milk_ok=False), 0.7, violation="우유가 들어가요")
    payload = json.loads(bad[1]["content"])
    assert payload["조건 위반"] == "우유가 들어가요" and next(iter(payload)) == "조건 위반"    # stated first
    assert payload["손님 선호"]["카페인 조건"] == "디카페인만" and payload["손님 선호"]["우유"] == "불가"
    assert VIOLATION_RULE in bad[0]["content"] and "첫 문장에서 그 위반" in VIOLATION_RULE
    assert bad[0]["content"].endswith(length_rule("우유가 들어가요") + " /no_think")   # the hard length rule comes last
    assert "'우유가 들어가서 주문 전 확인이 필요하지만,'으로 시작" in length_rule("우유가 들어가요")
    assert json.loads(explain_messages(item, Profile(), 0.7)[1]["content"])["손님 선호"]["카페인 조건"] == "제한 없음"


def test_card_has_korean_tags():
    it = Item(key="coffee:1", name="x", source="db", tags=("lemon", "floral"))
    assert card(it, 0.5, "t", tag_ko={"lemon": "레몬"})["tags_ko"] == ["레몬", "floral"]
    assert card(it, 0.5, "t")["tags_ko"] == ["lemon", "floral"]


def test_explain_payload_separates_decaf_states_and_surcharge():
    order = Item(key="menu:1", name="카페 아메리카노", source="brand_bean", decaf_option=True, order_decaf=True,
                 decaf_surcharge_krw=300)
    raw = explain_messages(order, Profile(caffeine_rule="decaf_only"), 0.8)[1]["content"]
    p = json.loads(raw)
    # words, not booleans: the model copied "디카페인으로 주문 권장이 true이므로" into live explanations
    assert p["디카페인"] == "원래는 디카페인이 아님 — 디카페인으로 바꿔 주문 가능 (+300원)"
    assert p["조건 확인"]["카페인(손님: 디카페인만)"] == "디카페인으로 바꿔 주문하면 충족 (+300원)"
    assert "true" not in raw and "false" not in raw and "null" not in raw
    decaf = Item(key="coffee:1", name="콜롬비아 디카페인", source="db", is_decaf=True)
    p = json.loads(explain_messages(decaf, Profile(), 0.8)[1]["content"])
    assert p["디카페인"] == "디카페인 음료" and "원" not in p["디카페인"]
    plain = json.loads(explain_messages(Item(key="coffee:2", name="케냐", source="db"), Profile(), 0.8)[1]["content"])
    assert plain["디카페인"] == "디카페인 아님"


def test_system_prompt_decaf_order_wording():
    assert "디카페인으로 바꿔 주문하면" in SYSTEM_PROMPT
    assert "'디카페인 음료'일 때만" in SYSTEM_PROMPT and "'디카페인으로 바꿔 주문 가능'이면" in SYSTEM_PROMPT


def test_length_rule_is_last_and_has_no_copyable_example():
    p = Profile()
    msgs = explain_messages(Item(key="menu:1", name="카페 라떼", source="brand_bean", acidity=p.acidity, body=p.body,
                                 sweetness=p.sweetness), p, 0.7)
    rule = length_rule(verdict=verdict_phrase(70))
    assert msgs[0]["content"].endswith(rule + " /no_think")
    assert "'고려할 점은 있지만 추천해요'로 맺는다" in rule           # the card's own verdict, not a label to copy
    assert "정확히 2문장" in rule and "첫 문장은 결론과 이유" in rule and "true, false, null" in rule
    # a concrete example got copied as fact, a 〔placeholder〕 one verbatim, a generic one as a vague answer
    assert "〔" not in msgs[0]["content"] and "예:" not in rule and "예(" not in rule



def test_order_decaf_card_never_carries_the_regular_caffeine():
    """이디야 꿀화이트 아메리카노 202mg ordered decaf: card, template and LLM payload show only the decaf estimate."""
    est = Item(key="menu:1", name="꿀화이트 아메리카노", source="brand_bean", decaf_option=True, order_decaf=True,
               caffeine_mg=7.0, caffeine_mg_note="디카페인 주문 시 추정")
    c = card(est, 0.7, "t")
    assert (c["caffeine_mg"], c["caffeine_mg_note"]) == (7.0, "디카페인 주문 시 추정")
    assert "디카페인으로 바꿔 주문하세요 (카페인 약 7mg 추정)" in template_explanation(est, Profile(), 0.7, {})
    msgs = explain_messages(est, Profile(caffeine_rule="decaf_only"), 0.7)
    p = json.loads(msgs[1]["content"])
    assert p["디카페인 주문 시 카페인(mg, 추정)"] == 7.0 and "202" not in msgs[1]["content"]
    assert "원래 카페인 수치" in msgs[0]["content"]
    assert check_explanation("디카페인으로 주문하면 카페인은 약 7mg(추정)이에요.", p, 70, None)["numbers_grounded"]
    assert not check_explanation("원래 카페인은 202mg이에요.", p, 70, None)["numbers_grounded"]
    unknown = Item(key="menu:2", name="카페 아메리카노", source="brand_bean", decaf_option=True, order_decaf=True,
                   decaf_surcharge_krw=300, caffeine_mg=None, caffeine_mg_note="디카페인 주문 시 카페인 ↓")
    assert "디카페인으로 바꿔 주문하세요 (+300원)." in template_explanation(unknown, Profile(), 0.7, {})
    assert json.loads(explain_messages(unknown, Profile(), 0.7)[1]["content"])["디카페인 주문 시 카페인(mg, 추정)"] == "추정치 없음"
    plain = Item(key="menu:3", name="카페 아메리카노", source="brand_bean", caffeine_mg=150)
    msgs = explain_messages(plain, Profile(), 0.7)
    assert "디카페인 주문 시 카페인(mg, 추정)" not in json.loads(msgs[1]["content"])
    assert "원래 카페인 수치" not in msgs[0]["content"]
    assert card(plain, 0.7, "t")["caffeine_mg_note"] is None
