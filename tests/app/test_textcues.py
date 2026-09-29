import pytest

from app.core.predict import predict_from_neighbors, with_text_cues
from app.core.textcues import attr_cues, text_tags
from app.models import Neighbor

TAG_TO_CAT = {"grapefruit": "fruity", "berry": "fruity", "chocolate": "nutty/cocoa"}
TAG_KO = {"grapefruit": "자몽", "berry": "베리", "chocolate": "초콜릿"}

# (text, expected {attr: value}) -- ambiguous/ absent cases expect an empty dict for that attribute.
CUE_CASES = [
    ("산미가 강한 원두예요", {"acidity": 4.5}),
    ("산미 강해요", {"acidity": 4.5}),
    ("상큼한 산미가 매력적", {"acidity": 4.5}),
    ("bright and juicy acidity", {"acidity": 4.5}),
    ("산미가 약한 편", {"acidity": 1.5}),
    ("부드러운 산미", {"acidity": 1.5}),
    ("low acid espresso blend", {"acidity": 1.5}),
    ("묵직한 바디감", {"body": 4.5}),
    ("무거운 바디", {"body": 4.5}),
    ("풀바디 원두", {"body": 4.5}),
    ("heavy, syrupy body", {"body": 4.5}),
    ("가벼운 바디", {"body": 1.5}),
    ("라이트한 바디감", {"body": 1.5}),
    ("tea-like light body", {"body": 1.5}),
    ("달콤한 원두", {"sweetness": 4.0}),
    ("단맛이 좋아요", {"sweetness": 4.0}),
    ("sweet finish", {"sweetness": 4.0}),
    ("쓴맛이 강해요", {"sweetness": 2.0}),
    ("bitter aftertaste", {"sweetness": 2.0}),
    ("에티오피아 예가체프 워시드", {}),                    # no cue at all
    ("에티오피아 구지 라이트 로스트", {}),                 # a roast, not a light body
    ("라이트 로스팅 원두", {}),
    ("라이트 배전", {}),
    ("산미가 강하면서도 약한 느낌", {}),                    # both sides hit -> ambiguous, no override
]


@pytest.mark.parametrize("text,expected", CUE_CASES)
def test_attr_cues_parsing_table(text, expected):
    cues = attr_cues(text)
    assert {attr: value for attr, (value, _) in cues.items()} == expected


def test_attr_cues_returns_the_matched_phrase():
    value, phrase = attr_cues("묵직한 바디, 다크한 향")["body"]
    assert value == 4.5 and "묵직" in phrase


def test_attr_cues_independent_across_attributes():
    cues = attr_cues("산미 강하고 묵직한 바디, 달콤함")
    assert {a: v for a, (v, _) in cues.items()} == {"acidity": 4.5, "body": 4.5, "sweetness": 4.0}


def test_text_tags_reads_english_and_korean_note_words():
    assert text_tags("grapefruit and chocolate notes", TAG_TO_CAT, TAG_KO) == ["grapefruit", "chocolate"]
    assert text_tags("자몽, 베리", TAG_TO_CAT, TAG_KO) == ["grapefruit", "berry"]


def test_text_tags_skips_korean_prose_not_a_note_list():
    # is_note_list() gate: Korean substring matching only fires on short comma-separated note lists, same
    # safety net as pipeline.enrich, to avoid false hits inside ordinary sentences.
    assert text_tags("에티오피아 원두는 자몽 향이 나는 것으로 유명한 산지에서 재배됩니다.", TAG_TO_CAT, TAG_KO) == []


def test_text_tags_empty_when_no_match():
    assert text_tags("아무 향미 언급 없음", TAG_TO_CAT, TAG_KO) == []


def nb(i, tags=()):
    return Neighbor(coffee_id=i, name=f"n{i}", similarity=0.9, acidity=3.0, body=3.0, sweetness=3.0, tags=tags)


def test_with_text_cues_overrides_attribute_and_adds_evidence():
    pred = predict_from_neighbors([nb(i) for i in range(3)])
    out = with_text_cues(pred, "묵직한 바디감이 좋아요", TAG_TO_CAT, TAG_KO)
    assert out.body == 4.5 and out.acidity == pred.acidity        # only body has a cue
    assert any("묵직" in e and "바디 4.5" in e for e in out.evidence)
    assert out.confidence == pred.confidence and out.n_neighbors == pred.n_neighbors


def test_with_text_cues_unions_text_tags_ahead_of_predicted_tags_capped_at_five():
    pred = predict_from_neighbors([nb(i, ("chocolate",)) for i in range(6)])
    assert pred.tags == ["chocolate"]
    out = with_text_cues(pred, "자몽, 베리 향이 좋아요", TAG_TO_CAT, TAG_KO)
    assert out.tags == ["grapefruit", "berry", "chocolate"]
    assert out.evidence[0] == "문구의 향미: 자몽, 베리"


def test_with_text_cues_no_cue_returns_prediction_unchanged():
    pred = predict_from_neighbors([nb(i) for i in range(3)])
    out = with_text_cues(pred, "에티오피아 예가체프 워시드", TAG_TO_CAT, TAG_KO)
    assert out == pred


def test_with_text_cues_caps_union_at_five_tags():
    pred = predict_from_neighbors([nb(i, ("chocolate",)) for i in range(6)])
    tag_to_cat = {**TAG_TO_CAT, "vanilla": "sweet", "honey": "sweet", "nutty": "nutty/cocoa"}
    tag_ko = {**TAG_KO, "vanilla": "바닐라", "honey": "꿀", "nutty": "고소한"}
    out = with_text_cues(pred, "자몽, 베리, 바닐라, 꿀, 고소한 향", tag_to_cat, tag_ko)
    assert len(out.tags) == 5


def test_text_tags_free_text_card_line_reads_whole_korean_note_tokens():
    t2c = {**TAG_TO_CAT, "strawberry": "fruity", "jasmine": "floral", "fermented": "sour/fermented"}
    ko = {**TAG_KO, "strawberry": "딸기", "jasmine": "재스민", "fermented": "발효"}
    line = "에티오피아 구지 내추럴 딸기 자스민 라이트 로스트"
    assert text_tags(line, t2c, ko) == []                                  # pipeline rules: not a note list
    assert text_tags(line, t2c, ko, free_text=True) == ["strawberry", "jasmine"]
    assert text_tags("예가체프 재스민향 딸기와 초콜릿", t2c, ko, free_text=True) == ["jasmine", "strawberry", "chocolate"]
    assert text_tags("콜롬비아 우일라 무산소 발효 워시드 미디엄 로스트", t2c, ko, free_text=True) == []  # process word
    # prose stays out even in free-text mode
    assert text_tags("에티오피아 원두는 자몽 향이 나는 것으로 유명한 산지에서 재배됩니다.", TAG_TO_CAT, TAG_KO,
                     free_text=True) == []


def test_text_tags_reads_common_korean_note_words_guests_type():
    # docs/adr/0017-open-tag-fill.md: aliases to the nearest SCA wheel node, in card lines and note lists alike
    t2c = {**TAG_TO_CAT, "nutty": "nutty/cocoa", "grape": "fruity", "peach": "fruity", "other fruit": "fruity",
           "brown sugar": "sweet", "floral": "floral"}
    ko = {**TAG_KO, "nutty": "견과", "grape": "포도", "peach": "복숭아", "other fruit": "기타 과일",
          "brown sugar": "흑설탕", "floral": "꽃향"}
    assert text_tags("과테말라 우에우에테낭고 중배전 견과류 밀크초콜릿", t2c, ko, free_text=True) == ["nutty", "chocolate"]
    assert text_tags("시다모 내추럴 청포도 리치", t2c, ko, free_text=True) == ["grape", "other fruit"]
    assert text_tags("살구, 자두, 갈색설탕, 히비스커스", t2c, ko) == ["peach", "other fruit", "brown sugar", "floral"]
    assert text_tags("브라질 산토스 고소한 맛", t2c, ko, free_text=True) == ["nutty"]
    assert text_tags("건자두, 커피나무", {**t2c, "prune": "fruity"}, {**ko, "prune": "말린 자두"}) == ["prune"]
