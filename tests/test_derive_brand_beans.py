"""scripts/derive_brand_beans.py: official franchise bean copy → brand profile values (docs/adr/0012)."""
from scripts.derive_brand_beans import half_step, official_text, official_word_tags, query_text

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
