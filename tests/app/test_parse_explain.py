import json

from app.core.explain import SYSTEM_PROMPT, card, explain_messages, preference_sentence, template_explanation
from app.core.parse import BeanParse, NoteSignals, merge_llm_parse, needs_llm_parse, note_messages, parse_bean_text
from app.models import Item, Prediction, Profile


def test_parse_bean_text_rules():
    p = parse_bean_text("  에티오피아 예가체프 워시드 디카페인 스위스워터 ")
    assert (p.text, p.origin_country, p.process, p.is_decaf, p.decaf_process) == (
        "에티오피아 예가체프 워시드 디카페인 스위스워터", "Ethiopia", "washed", True, "unknown")
    assert needs_llm_parse(p) is False
    unknown = parse_bean_text("동네 로스터리 하우스 블렌드")
    assert unknown.origin_country is None and needs_llm_parse(unknown) is True


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
    assert text.startswith("취향 적합도 87%.")
    assert "산미, 바디이(가) 선호와 가까워요." in text
    assert "향미: 레몬, floral." in text
    assert "디카페인으로 변경해서 주문하세요 (+300원)." in text
    warn = template_explanation(it, Profile(), 0.5, {}, violation="우유가 들어가요")
    assert warn.startswith("주의: 우유가 들어가요.")


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
