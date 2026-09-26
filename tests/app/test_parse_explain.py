import json

from app.core.explain import card, explain_messages, template_explanation
from app.core.parse import BeanParse, NoteSignals, merge_llm_parse, needs_llm_parse, parse_bean_text
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
