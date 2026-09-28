"""3차 explanation changes (docs/adr/0005): pre-verbalised payload and the post-processing guard. Pure functions."""
import json

import pytest

from app.core.explain import (
    TOP_PICK_RULE,
    attr_gap,
    condition_check,
    explain_messages,
    finalize_explanation,
    flavor_comparison,
    taste_comparison,
    violation_lead,
)
from app.core.explain_check import direction_errors, sentence_spans, sentences
from app.models import Item, Profile

MOCHA = Item(key="menu:9", name="카페모카 아이스 블렌디드", source="brand_bean", acidity=2, body=4, sweetness=2,
             tags=("dark chocolate", "caramelized"), is_milk=True)
# live-site review: decaf_only, milk X, acidity 4.5, fruity+floral × 이디야 아메리카노 (산미 2)
LIVE = Profile(caffeine_rule="decaf_only", milk_ok=False, acidity=4.5, body=2.5, sweetness=3,
               flavor_weights={"fruity": 0.6, "floral": 0.5})
EDIYA = Item(key="menu:1", name="아메리카노", source="brand_bean", brand="이디야", acidity=2, body=4, sweetness=2,
             tags=("chocolate", "nutty"), decaf_option=True, order_decaf=True, decaf_surcharge_krw=300)


def _payload(item, profile, score=0.8, violation=None, **kw):
    return json.loads(explain_messages(item, profile, score, violation=violation, **kw)[1]["content"])


def test_taste_comparison_keeps_2_4_2_apart_instead_of_all_average():
    # the model wrote "산미·바디·단맛이 모두 보통" for 2·4·2 (both judges: contradiction)
    t = taste_comparison(MOCHA, Profile())
    assert t["산미"] == "음료 2(약함) · 손님 3(산미는 보통) → 손님 선호보다 조금 약함"
    assert t["바디"].endswith("→ 손님 선호보다 조금 강함")
    assert t["단맛"].endswith("→ 손님 선호보다 조금 약함")
    assert attr_gap(3.2, 3.0) == "손님 선호와 비슷함"            # kenya: "바디가 약간 낮다" was a contradiction
    assert attr_gap(2, 4.5) == "손님 선호보다 훨씬 약함"
    assert taste_comparison(Item(key="x", name="x", source="db"), Profile())["산미"] == "음료 정보 없음"


def test_flavor_comparison_states_the_overlap():
    cat = {"lemon": "fruity", "chocolate": "nutty/cocoa", "floral": "floral"}
    ko = {"lemon": "레몬", "chocolate": "초콜릿"}
    it = Item(key="x", name="x", source="db", tags=("lemon", "chocolate"))
    s = flavor_comparison(it, Profile(flavor_weights={"fruity": 0.6, "floral": 0.5}), cat, ko)
    assert s == "손님이 좋아하는 향미(과일, 꽃)와 겹침: 레몬(lemon) → 과일 / 겹치지 않음: 초콜릿(chocolate)"
    assert flavor_comparison(it, Profile(flavor_weights={"sweet": 0.6}), cat, ko).endswith("겹치는 향미 없음")
    assert flavor_comparison(it, Profile(), cat, ko).startswith("손님이 고른 좋아하는 향미 없음")
    p = _payload(it, Profile(flavor_weights={"fruity": 0.6}), tag_to_cat=cat, tag_ko=ko)
    assert p["향미"] == ["레몬(lemon)", "초콜릿(chocolate)"] and "향미 비교" in p
    assert "향미 비교" not in _payload(it, Profile())            # no taxonomy (offline re-score): no comparison


def test_condition_check_reads_milk_and_decaf_order_as_met():
    c = condition_check(EDIYA, LIVE, None)
    assert c == {"카페인(손님: 디카페인만)": "디카페인으로 바꿔 주문하면 충족 (+300원)", "우유(손님: 불가)": "우유가 없어 충족"}
    latte = Item(key="x", name="라떼", source="brand_bean", is_milk=True)
    assert condition_check(latte, LIVE, "우유가 들어가요")["우유(손님: 불가)"] == "우유가 들어가 충족 안 됨"
    assert condition_check(latte, LIVE, "디카페인이 아니에요")["카페인(손님: 디카페인만)"] == "충족 안 됨 — 디카페인이 아니에요"
    assert set(condition_check(latte, Profile(), None).values()) == {"제한 없음"}


@pytest.mark.parametrize("violation, lead", [
    ("디카페인이 아니에요", "디카페인이 아니어서"),
    ("카페인이 100mg을 넘거나 알 수 없어요", "카페인이 100mg을 넘거나 알 수 없어서"),
    ("우유가 들어가요", "우유가 들어가서"),
    ("디카페인 메뉴지만 카페인이 12mg이에요(초콜릿·차 등)", "디카페인 메뉴지만 카페인이 12mg이라서(초콜릿·차 등)"),
])
def test_violation_lead_turns_every_passes_message_into_a_clause(violation, lead):
    assert violation_lead(violation) == lead


def test_top_pick_framing_only_when_fit_is_not_high():
    low = explain_messages(EDIYA, LIVE, 0.58, top_pick=True)
    assert TOP_PICK_RULE in low[0]["content"] and "추천 순위" in json.loads(low[1]["content"])
    high = explain_messages(EDIYA, LIVE, 0.85, top_pick=True)
    assert TOP_PICK_RULE not in high[0]["content"] and "추천 순위" not in json.loads(high[1]["content"])
    assert TOP_PICK_RULE not in explain_messages(EDIYA, LIVE, 0.58)[0]["content"]


def test_sentence_spans_round_trip():
    text = "산미가 4.5로 강해요.(84%). 바디는 약해요"
    spans = sentence_spans(text)
    assert "".join(spans) == text and len(spans) == 2 and len(sentences(text)) == 2


def test_direction_errors_catch_the_live_ediya_contradiction():
    drink, guest = {"acidity": 2, "body": 4, "sweetness": 2}, {"acidity": 4.5, "body": 2.5, "sweetness": 3}
    assert direction_errors("산미와 바디가 손님 선호보다 높아 아쉬워요.", drink, guest)      # 산미 2 < 4.5
    assert not direction_errors("산미는 손님 선호보다 훨씬 약하고 바디는 조금 강해요.", drink, guest)
    assert direction_errors("산미가 강하고 향이 좋아요.", drink, guest)                   # absolute: 2 is not strong
    assert not direction_errors("바디가 강한 편이에요.", drink, guest)
    assert not direction_errors("손님은 산미가 강한 걸 좋아하세요.", drink, guest)          # the guest, not the drink
    assert not direction_errors("산미가 강한 커피를 좋아하시는데 아쉬워요.", drink, guest)
    assert not direction_errors("산미가 약간 아쉬워요.", drink, guest)                     # 약간 is not 약하다
    assert not direction_errors("산미가 강해요.", {"acidity": None}, guest)


def test_finalize_rejects_copied_placeholders_booleans_and_keys():
    p = _payload(EDIYA, LIVE)
    live = [("〔향미〕가 손님이 좋아하는 〔취향〕과 맞지 않아요.", "placeholder"),
            ("디카페인 음료가 false이고 주문 권장이 true이므로 바꿔 주문하세요.", "bool_copy"),
            ("추정치가 None이라 알 수 없어요.", "bool_copy"),
            ("맛 비교를 보면 산미가 손님 선호보다 훨씬 약해요.", "key_copy"),
            ("디카페인으로 주문 권장이 켜져 있어 바꿔 주문하면 돼요.", "key_copy"),
            ("산미와 바디가 손님 선호보다 높아 취향에 맞지 않아요.", "direction")]
    for text, reason in live:
        assert finalize_explanation(text, EDIYA, LIVE, p, None) == ("", reason), text


def test_finalize_edits_foreign_words_length_and_missing_violation():
    it = Item(key="c", name="x", source="db", acidity=3, body=3, sweetness=3, tags=("chocolate",))
    p = _payload(it, Profile())
    ok = "산미가 손님 선호와 비슷해 잘 맞아요. 바디도 비슷해요."
    assert finalize_explanation(ok, it, Profile(), p, None) == (ok, None)
    text, ev = finalize_explanation("초콜릿(chocolate) 향이 선호와 parcialmente 맞아요. 바디는 비슷해요 okay.", it,
                                    Profile(), p, None)
    assert (text, ev) == ("초콜릿(chocolate) 향이 선호와 부분적으로 맞아요. 바디는 비슷해요.", "edited")
    three = "산미가 비슷해 잘 맞아요. 바디도 비슷해요. 디카페인으로 바꿔 주문하면 돼요."
    assert finalize_explanation(three, it, Profile(), p, None)[0] == "산미가 비슷해 잘 맞아요. 바디도 비슷해요."
    short_first = "디카페인이 아니에요. 산미는 비슷해 잘 맞아요. 바디는 비슷해요."
    text, _ = finalize_explanation(short_first, it, Profile(), p, "디카페인이 아니에요")
    assert text == "디카페인이 아니에요 — 산미는 비슷해 잘 맞아요. 바디는 비슷해요." and len(sentences(text)) == 2
    text, ev = finalize_explanation(ok, it, Profile(), p, "카페인이 100mg을 넘거나 알 수 없어요")
    assert text == "주의: 카페인이 100mg을 넘거나 알 수 없어요 — " + ok and ev == "edited"
    assert len(sentences(text)) == 2
