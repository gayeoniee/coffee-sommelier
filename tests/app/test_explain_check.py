from app.core.explain_check import all_ok, check_explanation, payload_numbers

P = {"음료": "카페 아메리카노", "브랜드": "할리스", "산미": 2, "바디": 4, "단맛": 2, "향미": ["dark chocolate", "caramelized"],
     "디카페인 음료": True, "디카페인으로 주문 권장": False, "디카페인 추가요금(원)": None, "우유": False, "적합도": 72,
     "손님 취향 요약": "산미가 강한 커피를 좋아해요", "손님 선호": {"산미": 4.5, "바디": 2.5, "단맛": 3.0,
     "좋아하는 향미": ["과일", "꽃"], "카페인 조건": "디카페인만", "우유": "가능"}, "조건 위반": None, "근거": None}


def test_payload_numbers_collects_all_values():
    assert {2, 4, 72, 4.5, 2.5, 3.0} <= payload_numbers(P)


def test_good_explanation_passes():
    t = "산미가 2로 손님 선호(4.5)보다 낮아 취향과 거리가 있지만, 디카페인이라 카페인 조건은 맞습니다. 적합도 72%."
    assert all_ok(check_explanation(t, P, 72, None))


def test_foreign_words_and_length():
    r = check_explanation("산미가 낮지만 embora 향미는 좋아요.", P, 72, None)
    assert r["foreign_words"] is False
    assert check_explanation("좋아요. " * 60, P, 72, None)["length"] is False
    assert check_explanation("dark chocolate 향이 나요.", P, 72, None)["foreign_words"] is True   # payload tag allowed


def test_numbers_must_come_from_payload():
    assert check_explanation("카페인이 150mg이라 조건에 안 맞아요.", P, 72, None)["numbers_grounded"] is False
    assert check_explanation("산미 2, 선호 4.5.", P, 72, None)["numbers_grounded"] is True


def test_condition_and_polarity():
    v = "디카페인이 아니에요"
    assert check_explanation("산미가 좋아요.", P, 30, v)["condition_mentioned"] is False
    assert check_explanation("디카페인이 아니라 카페인 조건에 맞지 않아요.", P, 30, v)["condition_mentioned"] is True
    assert check_explanation("취향에 잘 맞아 추천해요.", P, 30, None)["polarity"] is False
    assert check_explanation("취향과 맞지 않아요.", P, 85, None)["polarity"] is False


def test_sentence_splitter_ignores_fragments_without_hangul():
    t = "산미 4.5를 좋아하는 손님께 맞아요. 바디 2.5도 가까워요. (72%)."
    assert check_explanation(t, P, 72, None)["length"] is True       # decimals and "(72%)." are not sentences
    assert check_explanation("하나예요. 둘이에요. 셋이에요. 넷이에요.", P, 72, None)["length"] is False


def test_polarity_relaxed_when_violation_set():
    v = "카페인이 100mg을 넘거나 알 수 없어요"
    assert check_explanation("카페인 조건에 맞지 않아요.", P, 86, v)["polarity"] is True
    assert check_explanation("취향에 잘 맞아 추천해요.", P, 30, v)["polarity"] is True
    assert check_explanation("취향과 맞지 않아요.", P, 86, None)["polarity"] is False


def test_negative_patterns_win_over_positive_substrings():
    # "잘 맞지 않" contains the positive "잘 맞" but is a negative conclusion
    assert check_explanation("산미가 약해 손님 취향과 잘 맞지 않아요.", P, 30, None)["polarity"] is True


def test_polarity_is_judged_on_the_first_sentence_only():
    t = "과일 향과 강한 산미가 취향에 잘 맞아요. 바디만 선호와 조금 거리가 있지만 큰 차이는 아니에요."
    assert check_explanation(t, P, 85, None)["polarity"] is True
    assert check_explanation("취향과 거리가 있어요. 그래도 향은 잘 맞아요.", P, 30, None)["polarity"] is True


def test_fit_score_described_as_low_or_high_is_a_polarity_signal():
    assert check_explanation("적합도가 90%로 낮아 추천하기 어려워요.", P, 90, None)["polarity"] is False
    assert check_explanation("적합도가 30%로 높아 부담 없이 드실 수 있어요.", P, 30, None)["polarity"] is False
    assert check_explanation("적합도가 90%로 높아 취향에 가까워요.", P, 90, None)["polarity"] is True


def test_thousands_separators_are_grounded():
    p = P | {"디카페인 추가요금(원)": 1500}
    assert check_explanation("디카페인으로 바꿔 주문하면 +1,500원이에요.", p, 72, None)["numbers_grounded"] is True
    assert check_explanation("디카페인으로 바꿔 주문하면 +2,500원이에요.", p, 72, None)["numbers_grounded"] is False


def test_condition_mentioned_requires_the_violated_conditions_own_keyword():
    milk = "우유가 들어가요"
    assert check_explanation("디카페인이라 카페인 걱정은 없어요.", P, 50, milk)["condition_mentioned"] is False
    assert check_explanation("우유가 들어가 조건에 맞지 않아요.", P, 50, milk)["condition_mentioned"] is True
    caf = "카페인이 100mg을 넘거나 알 수 없어요"
    assert check_explanation("우유가 없어 가볍게 즐기기 좋아요.", P, 50, caf)["condition_mentioned"] is False
    assert check_explanation("카페인이 많을 수 있어 확인이 필요해요.", P, 50, caf)["condition_mentioned"] is True
    assert check_explanation("디카페인으로 바꿔 드세요.", P, 50, "디카페인이 아니에요")["condition_mentioned"] is True


def test_max_sentences_matches_the_two_sentence_product_rule():
    assert check_explanation("하나예요. 둘이에요.", P, 72, None)["length"] is True
    assert check_explanation("하나예요. 둘이에요. 셋이에요.", P, 72, None)["length"] is False
