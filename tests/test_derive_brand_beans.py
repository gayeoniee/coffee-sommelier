"""scripts/derive_brand_beans.py: official franchise bean copy → brand profile values (docs/adr/0012)."""
from app.core.featuremodel import FeatureModel
from scripts.derive_brand_beans import (derive_open, half_step, official_decaf_process, official_origin_country,
                                        official_text, official_word_tags, query_text)

VOCAB = {"초콜릿": "chocolate", "다크초콜릿": "dark chocolate", "시나몬": "cinnamon", "브라운슈가": "brown sugar",
         "와인": "winey", "신맛": "sour", "쓴맛": "bitter", "캬라멜": "caramelized"}


def test_official_word_tags_keeps_the_longest_match_in_order_of_appearance():
    text = "시나몬, 브라운슈가, 다크초콜릿의 단맛과 초콜릿 향"
    assert official_word_tags(text, VOCAB) == ["cinnamon", "brown sugar", "dark chocolate", "chocolate"]


def test_official_word_tags_does_not_invent_words_across_spaces():
    # "크레마와 인상" must not read as "와인" (the Blue Bottle copy did, with spaces stripped)
    assert official_word_tags("풍성한 황갈색 크레마와 인상 깊은 점도", VOCAB) == []


def test_basic_tastes_are_left_to_the_attribute_cues():
    assert official_word_tags("고소한 단맛과 구수한 쓴맛, 상큼한 신맛", VOCAB) == []


def test_half_step_rounds_to_guest_facing_halves_within_range():
    assert [half_step(v) for v in (1.99, 2.37, 3.68, 4.2, 5.7, 0.2)] == [2.0, 2.5, 3.5, 4.0, 5.0, 1.0]


def test_query_text_uses_only_the_brands_own_words_in_catalogue_format():
    bean = {"bean_name": "시그니처 블렌드", "roast_level": "dark", "notes": ["달콤한 초콜릿"], "origins": "브라질+에티오피아",
            "card_note": "카드 문구는 쿼리에 안 들어감"}
    assert official_text(bean) == "달콤한 초콜릿 브라질+에티오피아"
    q = query_text("폴바셋", bean, is_decaf=False)
    assert q == "폴바셋 시그니처 블렌드 | dark | 달콤한 초콜릿 브라질+에티오피아"


# ---- open variant (licence-clean) ------------------------------------------------------------------------
TAG_TO_CAT = {"chocolate": "nutty/cocoa", "caramelized": "sweet"}
ESTIMATE = {"acidity": 2, "body": 3, "sweetness": 2, "flavor_tags": ["nutty"]}
FMODEL = FeatureModel.from_doc({"attrs": {
    "acidity": {"intercept": 3.0, "weights": {"roast_dark": -1.0, "nbr": 0.5}},
    "sweetness": {"intercept": 3.0, "weights": {"notes_nutty/cocoa": 0.6}}}})


def test_open_origin_is_a_single_country_or_a_blend_and_never_the_roasting_country():
    assert official_origin_country({"origins": "브라질 산 원두와 화사한 산미의 에티오피아 산 원두가 블렌딩"}) is None
    assert official_origin_country({"origins": "'파젠다 엄'에서 생산된 스페셜티 원두와 좋은 품질의 브라질 원두"}) == "Brazil"
    assert official_origin_country({"origins": "원두커피 100 % 원산지 : 미국 (로스팅 국가)"}) is None
    assert official_origin_country({"origins": None}) is None


def test_open_decaf_process_maps_a_generic_water_process_to_the_water_feature():
    assert official_decaf_process({"process": "워터 프로세스(Water Process) 공정"}) == "swiss-water"
    assert official_decaf_process({"process": "비 화학적 카페인 제거법인 Swiss Water Process"}) == "swiss-water"
    assert official_decaf_process({"process": None}) is None


def test_derive_open_priority_cue_then_feature_model_then_estimate_and_only_official_words_as_tags():
    bean = {"status": "official", "roast_level": "dark", "notes": ["묵직한 바디감과 초콜릿 향"]}
    d = derive_open(bean, ESTIMATE, False, FMODEL, TAG_TO_CAT, VOCAB)
    assert d["values"] == {"acidity": 2.0, "body": 4.5, "sweetness": 3.5, "flavor_tags": ["chocolate"]}
    assert d["label_source"] == {"acidity": "open_feature_model", "body": "official_cue",
                                 "sweetness": "open_feature_model", "flavor_tags": "official_cue"}
    assert "nbr" not in d["features"]                  # a brand bean has no neighbours


def test_derive_open_without_official_facts_keeps_the_hand_estimate():
    d = derive_open({"status": "unavailable", "notes": []}, ESTIMATE, True, FMODEL, TAG_TO_CAT, VOCAB)
    assert d["values"] == {"acidity": 2.0, "body": 3.0, "sweetness": 2.0, "flavor_tags": ["nutty"]}
    assert set(d["label_source"].values()) == {"estimate"}


def test_derive_open_with_no_flavor_word_falls_back_to_the_estimate_tags_not_a_model():
    d = derive_open({"status": "official", "notes": ["풍부한 바디감"]}, ESTIMATE, False, FMODEL, TAG_TO_CAT, VOCAB)
    assert (d["values"]["flavor_tags"], d["label_source"]["flavor_tags"]) == (["nutty"], "estimate")
