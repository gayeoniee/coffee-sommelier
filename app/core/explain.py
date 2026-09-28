import json
import re

from app.core.explain_check import (allowed_latin, consequence_errors, direction_errors, sentence_spans, sentences,
                                    violation_mentioned)
from app.core.flavors import CATEGORY_KO
from app.models import ATTRS, Item, Prediction, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
CONFIDENCE_KO = {"high": "높음", "medium": "보통", "low": "낮음"}
CLOSE = 0.75
LIKED_FLAVOR_MIN = 0.2   # flavor weight above which the guest counts as liking a category
# 3차(ADR 0005): the model used to read the raw 1~5 numbers itself and flattened 2·4·2 into "모두 보통". The payload
# now states each attribute's comparison in words ('맛 비교'), and the prompt tells the model to follow it.
SYSTEM_PROMPT = ("너는 카페에서 손님에게 커피를 추천하는 친절한 바리스타다. 주어진 데이터만 근거로, 왜 이 음료가 손님 취향에 "
                 "맞는지(또는 안 맞는지) 한국어로 설명해라. 데이터에 없는 수치나 사실을 지어내지 마라. "
                 "손님 취향 요약과 반대되는 말을 하지 마라. 산미·바디·단맛은 '맛 비교'의 판정(비슷함, 조금 강함, 훨씬 약함 등)을 "
                 "그대로 따르고, 서로 다른 속성을 '모두 보통'처럼 한데 묶지 마라. 향미는 '향미'에 있는 것만 한국어 이름으로 말하고, "
                 "손님이 좋아하는 향미와 겹치는지는 '향미 비교'만 따르라. 음료의 맛을 그릴 때는 '맛 한 줄'에 있는 표현만 쓰고, "
                 "데이터에 없는 인상 평가('전체적인 맛', 균형, 밸런스, 조화, 입감, 기본적인 맛, 편안함, 풍부함, 부드러움, "
                 "밋밋함 등)는 쓰지 마라. '적합도'의 수준(높음·중간·낮음)과 반대로 "
                 "말하지 말고, '높음이며'처럼 그 라벨을 문장에 옮기지 마라. "
                 "취향에 맞는 이유는 '맞는 점'에 있는 것으로만 대고, '아쉬운 점'에 있는 속성은 아쉬운 점으로만 말하라. "
                 "'디카페인'이 '디카페인 음료'일 때만 디카페인 음료라고 말하라. '디카페인으로 바꿔 주문 가능'이면 원래는 "
                 "디카페인이 아니니 '디카페인으로 바꿔 주문하면 (+N원)'처럼 안내하고(N은 거기 적힌 금액; 금액이 없으면 "
                 "'디카페인으로 바꿔 주문하면'만), 이미 디카페인이라고 말하지 마라. 디카페인 주문과 추가요금은 카드에 따로 "
                 "표시되니 두 문장은 맛·향미 설명을 우선하라.")
# No example sentence: a placeholder example ("〔향미〕가 …") was copied verbatim into live explanations, and a
# generic one ("이 음료의 향미가 …") was copied as a vague non-answer. The shape is described instead.
LENGTH_RULE = (" 반드시 지킬 규칙: 설명은 줄바꿈 없는 한 문단, 정확히 2문장(마침표 두 개)이다. {first}. "
               "세 번째 문장, 괄호 속 메모, 고쳐 쓴 두 번째 답은 절대 쓰지 마라. 수치를 나열하지 말고 '강한 편', "
               "'선호보다 약해요'처럼 말로 써라. 한국어만 써라: 영어·스페인어·포르투갈어 단어(parcialmente, true, false, null, "
               "dislike 등)와 데이터의 항목 이름('맛 비교', '향미 비교' 같은)을 옮기지 마라. 두루뭉술한 말('이 음료의 향미가', "
               "'한 가지 맛') 대신 산미·바디·단맛·향미 이름을 구체적으로 대라.")
FIRST_SENTENCE = ("첫 문장은 결론과 이유를 한 문장에 담는다: '맞는 점'(없으면 '아쉬운 점')을 이유로 들고 같은 문장 끝을 "
                  "'{verdict}'로 맺는다(결론만 따로 한 문장으로 쓰지 마라). "
                  "둘째 문장은 '맛 한 줄'을 그대로 써서 '예요'로 맺는다(아쉬운 점은 그 안에 이미 들어 있으니 따로 덧붙이지 마라). "
                  "마침표는 두 문장 끝에만 찍는다")
FIRST_SENTENCE_NO_MATCH = ("첫 문장은 '{verdict}'라고만 쓴다. "
                           "둘째 문장은 '맛 한 줄'을 그대로 써서 '예요'로 맺는다(아쉬운 점은 그 안에 이미 들어 있으니 따로 덧붙이지 마라). "
                           "마침표는 두 문장 끝에만 찍는다")
FIRST_SENTENCE_TOP_PICK = ("첫 문장은 '이 브랜드 메뉴 중에서는 손님 취향에 가장 가까운 선택이에요'라고 쓴다. "
                           "둘째 문장은 '맛 한 줄'을 그대로 써서 '예요'로 맺는다(아쉬운 점은 그 안에 이미 들어 있으니 따로 덧붙이지 마라). "
                           "마침표는 두 문장 끝에만 찍는다")
FIRST_SENTENCE_VIOLATION = ("첫 문장은 반드시 '{lead} 주문 전 확인이 필요하지만,'으로 시작하고, 이어서 '맞는 점'(없으면 '아쉬운 "
                            "점') 한 가지를 이유로 끝낸다(조건 위반('{violation}')을 빠뜨리지 마라). "
                            "둘째 문장은 '맛 한 줄'을 그대로 써서 '예요'로 맺는다(아쉬운 점은 그 안에 이미 들어 있으니 따로 덧붙이지 마라). "
                            "마침표는 두 문장 끝에만 찍는다")
NO_THINK = " /no_think"      # qwen: skip the reasoning phase
DECAF_CAFFEINE_RULE = (" 이 음료의 원래 카페인 수치는 데이터에 없고 말하지도 마라. 카페인을 말할 때는 "
                       "'디카페인 주문 시 카페인(mg, 추정)' 값만 '약 N mg(추정)'처럼 쓰고, 그 값이 '추정치 없음'이면 수치 없이 "
                       "'디카페인으로 주문하면 카페인이 줄어요'라고만 하라.")
VIOLATION_RULE = " 조건 위반이 있으니 첫 문장에서 그 위반 사실(카페인·우유 조건)을 먼저 분명히 말하라."
TOP_PICK_RULE = (" 이 음료는 이 브랜드 메뉴 중 손님 취향에 가장 가까운 1순위 추천이다. 적합도가 높지 않아도 '취향에 맞지 않아요'로 "
                 "결론 내지 말고, 첫 문장은 '이 브랜드 메뉴 중에서는 손님 취향에 가장 가까운 선택이에요'로 쓰고(선호와 어긋나는 "
                 "점을 이유로 들지 마라), 아쉬운 점(선호와 가장 어긋나는 속성)은 둘째 문장에 써라.")
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


def violation_lead(violation: str) -> str:
    """The violation as a 'because' clause that can open the first sentence: '디카페인이 아니에요' → '디카페인이 아니어서'."""
    v = violation.strip()
    note = ""
    m = re.search(r"\([^()]*\)$", v)                 # "…mg이에요(초콜릿·차 등)": keep the note after the clause
    if m:
        v, note = v[:m.start()], m.group()
    for end, lead in (("이에요", "이라서"), ("에요", "어서"), ("어요", "어서"), ("아요", "아서"), ("가요", "가서")):
        if v.endswith(end):
            return v[:-len(end)] + lead + note
    return f"{violation}(조건 위반)이라"


def length_rule(violation: str | None = None, verdict: str = "추천해요", top_pick: bool = False,
                no_match: bool = False) -> str:
    """The hard two-sentence rule, stated last; with a violation its first sentence must open with the violation,
    otherwise it ends with the card's own verdict phrase (or the top-pick framing)."""
    first = (FIRST_SENTENCE_VIOLATION.format(lead=violation_lead(violation), violation=violation) if violation
             else FIRST_SENTENCE_TOP_PICK if top_pick
             else FIRST_SENTENCE_NO_MATCH.format(verdict=verdict) if no_match else FIRST_SENTENCE.format(verdict=verdict))
    return LENGTH_RULE.format(first=first)


def _num(v: float) -> str:
    return f"{round(v, 1):g}"


def attr_band(v: float) -> str:
    """A 1~5 attribute value in words."""
    if v <= 1.5:
        return "매우 약함"
    if v < 2.5:
        return "약함"
    if v < 3.5:
        return "보통"
    if v < 4.5:
        return "강함"
    return "매우 강함"


def attr_gap(drink: float, guest: float) -> str:
    """Drink vs guest preference on the same 1~5 scale, in words (the judgement the model must not re-derive)."""
    d = drink - guest
    if abs(d) <= 0.5:
        return "손님 선호와 비슷함"
    size = "조금" if abs(d) <= 1.5 else "훨씬"
    return f"손님 선호보다 {size} {'강함' if d > 0 else '약함'}"


def taste_comparison(item: Item, profile: Profile) -> dict[str, str]:
    """'산미': '음료 2(약함) · 손님 4.5(산미를 강하게 좋아함) → 손님 선호보다 훨씬 약함' for each attribute."""
    out = {}
    for a in ATTRS:
        v, g = item.attr(a), getattr(profile, a)
        out[ATTR_KO[a]] = ("음료 정보 없음" if v is None else
                           f"음료 {_num(v)}({attr_band(v)}) · 손님 {_num(g)}({_level(a, g)}) → {attr_gap(v, g)}")
    return out


NO_MATCH = "없음 — 잘 맞는다고 말하지 마라"


def fit_points(item: Item, profile: Profile, tag_to_cat: dict[str, str] | None,
               tag_ko: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """(맞는 점, 아쉬운 점) sorted for the model, so a gap is never offered as the reason for a good fit
    ("산미가 선호보다 조금 강해서 잘 맞아요" — docs/adr/0005 3차 보완)."""
    good, bad = [], []
    for a in ATTRS:
        v, g = item.attr(a), getattr(profile, a)
        if v is None:
            continue
        gap = attr_gap(v, g)
        # a noun phrase ("손님 선호와 비슷한 단맛"): "단맛: 손님 선호와 비슷함" was pasted into the text as is
        (good if gap == "손님 선호와 비슷함" else bad).append(f"{gap[:-1]}한 {ATTR_KO[a]}")
    if tag_to_cat is not None and item.tags:
        liked = {c for c, w in profile.flavor_weights.items() if w > LIKED_FLAVOR_MIN}
        disliked = {c for c, w in profile.flavor_weights.items() if w < -LIKED_FLAVOR_MIN}
        names = {t: (tag_ko or {}).get(t.lower(), t) for t in item.tags}      # Korean only: it goes into the text
        hit = [names[t] for t in item.tags if tag_to_cat.get(t.lower()) in liked]
        worse = [names[t] for t in item.tags if tag_to_cat.get(t.lower()) in disliked]
        if hit:
            good.append(f"좋아하는 향미와 겹치는 {'·'.join(hit)} 향")
        elif liked:
            bad.append("좋아하는 향미와 겹치는 향이 없는 점")
        if worse:
            bad.append(f"싫어하는 향미와 겹치는 {'·'.join(worse)} 향")
    return good or [NO_MATCH], bad or ["없음"]


TASTE_WORDS = {   # attr_band → (connective, adnominal); every phrase is tied to the number, so it is not filler
    "acidity": {"매우 약함": ("산미가 거의 없고", "산미가 거의 없는"), "약함": ("산미가 적은 편이고", "산미가 적은 편인"),
                "보통": ("산미가 적당하고", "산미가 적당한"), "강함": ("산미가 또렷하고", "산미가 또렷한"),
                "매우 강함": ("산미가 아주 강하고", "산미가 아주 강한")},
    # every body phrase names 바디: a judge did not map "무게감이 중간" to the payload's 바디 and called it invented
    "body": {"매우 약함": ("바디가 아주 가볍고", "바디가 아주 가벼운"), "약함": ("바디가 가벼운 편이고", "바디가 가벼운 편인"),
             "보통": ("바디가 중간 정도이고", "바디가 중간 정도인"), "강함": ("바디가 묵직한 편이고", "바디가 묵직한 편인"),
             "매우 강함": ("바디가 아주 묵직하고", "바디가 아주 묵직한")},
    "sweetness": {"매우 약함": ("단맛이 거의 없고", "단맛이 거의 없는"), "약함": ("단맛이 적은 편이고", "단맛이 적은 편인"),
                  "보통": ("단맛이 적당하고", "단맛이 적당한"), "강함": ("단맛이 잘 느껴지고", "단맛이 잘 느껴지는"),
                  "매우 강함": ("단맛이 아주 진하고", "단맛이 아주 진한")},
}


GAP_WORDS = {"acidity": ("산미가", ("강하고", "강한"), ("약하고", "약한")),
             "body": ("바디가", ("무겁고", "무거운"), ("가볍고", "가벼운")),
             "sweetness": ("단맛이", ("진하고", "진한"), ("약하고", "약한"))}


def _attr_words(a: str, v: float, g: float) -> tuple[str, str]:
    """Close to the guest → the drink's own band ('무게감이 중간이고'); otherwise relative to the guest
    ('산미가 선호보다 조금 강하고'), so a line never says '산미가 적당' next to '선호보다 조금 강한 산미'."""
    gap = attr_gap(v, g)
    if gap == "손님 선호와 비슷함":
        return TASTE_WORDS[a][attr_band(v)]
    subject, up, down = GAP_WORDS[a]
    size = "조금" if "조금" in gap else "훨씬"
    conn, adn = up if v > g else down
    return f"{subject} 선호보다 {size} {conn}", f"{subject} 선호보다 {size} {adn}"


def taste_line(item: Item, profile: Profile, tag_ko: dict[str, str] | None) -> str | None:
    """'재스민·레몬 향에 산미가 선호보다 조금 강하고 무게감이 중간이고 단맛이 선호보다 조금 진한 음료' — the drink in one
    phrase the model uses as is (labels like '산미: 강함' were pasted verbatim; absolute and relative words side by side
    read as a contradiction — docs/adr/0005 5차)."""
    words = [_attr_words(a, item.attr(a), getattr(profile, a)) for a in ATTRS if item.attr(a) is not None]
    names = [(tag_ko or {}).get(t.lower(), t) for t in item.tags][:3]
    if not words:
        return f"{'·'.join(names)} 향의 음료" if names else None
    body = " ".join(w[0] for w in words[:-1]) + (" " if len(words) > 1 else "") + words[-1][1]
    return (f"{'·'.join(names)} 향에 " if names else "") + body + " 음료"


def verdict_phrase(pct: int, has_match: bool = True) -> str:
    """Sentence 1's ending, from the fit score. With nothing close to the guest (no '맞는 점') there is no reason to
    give, so the phrase is the whole first sentence ("아쉬운 점을 고려할 점은 있지만 추천해요" came out otherwise)."""
    if not has_match:
        return "딱 맞는 점이 없어 다른 메뉴가 더 나을 수 있어요" if fit_level(pct) == "낮음" else "딱 맞는 점은 없지만 고려해 볼 만해요"
    return {"높음": "추천해요", "중간": "고려할 점은 있지만 추천해요", "낮음": "다른 메뉴가 더 나을 수 있어요"}[fit_level(pct)]


def fit_level(pct: int) -> str:
    return "높음" if pct >= 80 else "중간" if pct >= 50 else "낮음"


def flavor_names(tags, tag_ko: dict[str, str] | None) -> list[str]:
    """'lemon' → '레몬(lemon)' when a Korean name is known, so the model can name it in Korean."""
    out = []
    for t in tags:
        ko = (tag_ko or {}).get(t.lower())
        out.append(f"{ko}({t})" if ko and ko != t else t)
    return out


def flavor_comparison(item: Item, profile: Profile, tag_to_cat: dict[str, str], tag_ko: dict[str, str] | None) -> str:
    """Which of the drink's flavors fall in a category the guest likes (or dislikes) — stated, not left to the model."""
    if not item.tags:
        return "향미 정보 없음"
    liked = [c for c, w in profile.flavor_weights.items() if w > LIKED_FLAVOR_MIN]
    disliked = [c for c, w in profile.flavor_weights.items() if w < -LIKED_FLAVOR_MIN]
    if not liked and not disliked:
        return "손님이 고른 좋아하는 향미 없음 — 향미가 취향에 맞는다거나 안 맞는다고 말하지 마라"
    names = dict(zip(item.tags, flavor_names(item.tags, tag_ko)))
    parts = []
    if liked:
        hit = [f"{names[t]} → {CATEGORY_KO.get(tag_to_cat[t.lower()])}" for t in item.tags
               if tag_to_cat.get(t.lower()) in liked]
        miss = [names[t] for t in item.tags if tag_to_cat.get(t.lower()) not in liked]
        head = f"손님이 좋아하는 향미({', '.join(CATEGORY_KO.get(c, c) for c in liked)})"
        parts.append(f"{head}와 겹침: {', '.join(hit)}" if hit else f"{head}와 겹치는 향미 없음")
        if hit and miss:
            parts.append(f"겹치지 않음: {', '.join(miss)}")
    if disliked:
        bad = [names[t] for t in item.tags if tag_to_cat.get(t.lower()) in disliked]
        if bad:
            parts.append(f"손님이 싫어하는 향미와 겹침: {', '.join(bad)}")
    return " / ".join(parts)


def decaf_state(item: Item) -> str:
    """One Korean phrase instead of two booleans (the model used to copy '…권장이 true이므로')."""
    if item.is_decaf:
        return "디카페인 음료"
    if item.order_decaf:
        fee = f" (+{item.decaf_surcharge_krw:,}원)" if item.decaf_surcharge_krw else ""
        return f"원래는 디카페인이 아님 — 디카페인으로 바꿔 주문 가능{fee}"
    return "디카페인 아님"


def condition_check(item: Item, profile: Profile, violation: str | None) -> dict[str, str]:
    """The guest's caffeine/milk conditions and whether this drink meets them, in words (the model read "우유: false"
    as "우유 여부 불명", and a decaf-order drink as breaking the decaf condition)."""
    if profile.caffeine_rule == "any":
        caffeine = "제한 없음"
    elif violation and "우유" not in violation:
        caffeine = f"충족 안 됨 — {violation}"
    elif item.is_decaf:
        caffeine = "디카페인 음료라 충족"
    elif item.order_decaf:
        fee = f" (+{item.decaf_surcharge_krw:,}원)" if item.decaf_surcharge_krw else ""
        caffeine = f"디카페인으로 바꿔 주문하면 충족{fee}"
    else:
        caffeine = "충족"
    milk = "제한 없음" if profile.milk_ok else ("우유가 들어가 충족 안 됨" if item.is_milk else "우유가 없어 충족")
    return {f"카페인(손님: {CAFFEINE_RULE_KO[profile.caffeine_rule]})": caffeine,
            f"우유(손님: {'가능' if profile.milk_ok else '불가'})": milk}


def rule_explanation(item: Item, profile: Profile, score: float, violation: str | None = None,
                     tag_to_cat: dict[str, str] | None = None, tag_ko: dict[str, str] | None = None,
                     top_pick: bool = False) -> str | None:
    """The whole explanation from data when nothing is close to the guest: both sentences are fixed by the code
    (the verdict and the taste line), so there is nothing for the model to add — it only dropped the period or broke
    the line ("…나을 수 있어요
산미가…"). None whenever the model has something to explain."""
    pct = round(score * 100)
    top_pick = top_pick and fit_level(pct) != "높음"
    good, _ = fit_points(item, profile, tag_to_cat, tag_ko)
    line = taste_line(item, profile, tag_ko)
    if violation or top_pick or good != [NO_MATCH] or line is None:
        return None
    return f"{verdict_phrase(pct, False)}. {line}예요."


def explain_messages(item: Item, profile: Profile, score: float, prediction: Prediction | None = None,
                     violation: str | None = None, tag_to_cat: dict[str, str] | None = None,
                     tag_ko: dict[str, str] | None = None, top_pick: bool = False) -> list[dict]:
    """`top_pick`: the first card of a brand recommendation — with a fit below 높음 it is framed as the brand's
    closest option rather than "doesn't suit you" (TOP_PICK_RULE)."""
    pct = round(score * 100)
    top_pick = top_pick and fit_level(pct) != "높음"
    good, bad = fit_points(item, profile, tag_to_cat, tag_ko)
    no_match = good == [NO_MATCH]
    verdict = verdict_phrase(pct, not no_match)
    payload: dict = {"조건 위반": violation} if violation else {}     # first: the one thing the text must not omit
    payload |= {
        "음료": item.name, "브랜드": item.brand,
        "적합도": f"{pct}%({fit_level(pct)})",
        # the verdict the text must end sentence 1 with, stated as a fact so a judge (and the model) can see it
        "추천 여부": ("주문 전 조건 확인 필요" if violation else "이 브랜드 메뉴 중 가장 가까운 선택" if top_pick
                  else verdict),
        **({"추천 순위": "이 브랜드 메뉴 중 1순위(가장 가까운 선택)"} if top_pick else {}),
        "맛 비교": taste_comparison(item, profile),
        "향미": flavor_names(item.tags, tag_ko),
        "맛 한 줄": taste_line(item, profile, tag_ko),
    }
    if tag_to_cat is not None:
        payload["향미 비교"] = flavor_comparison(item, profile, tag_to_cat, tag_ko)
    payload["맞는 점"], payload["아쉬운 점"] = good, bad
    payload["디카페인"] = decaf_state(item)
    if item.order_decaf:    # only the decaf-order estimate, never the regular drink's caffeine
        payload["디카페인 주문 시 카페인(mg, 추정)"] = (item.caffeine_mg if item.caffeine_mg is not None
                                                  and item.caffeine_mg_note else "추정치 없음")
    payload |= {
        "우유": "우유 들어감" if item.is_milk else "우유 없음",
        "조건 확인": condition_check(item, profile, violation),
        "손님 취향 요약": preference_sentence(profile),
        "손님 선호": {"좋아하는 향미": liked_flavors_ko(profile),
                  "카페인 조건": CAFFEINE_RULE_KO[profile.caffeine_rule],
                  "우유": "가능" if profile.milk_ok else "불가"},
        "근거": prediction.evidence if prediction else None,
    }
    if item.bean_note:      # franchise drink with an official bean description (docs/adr/0012); absent otherwise
        payload["원두 공식 설명"] = item.bean_note
    payload = {k: v for k, v in payload.items() if v is not None}     # a JSON null got copied as "null"
    system = (SYSTEM_PROMPT + (DECAF_CAFFEINE_RULE if item.order_decaf else "") + (VIOLATION_RULE if violation else "")
              + (TOP_PICK_RULE if top_pick and not violation else "")
              + length_rule(violation, verdict, top_pick and not violation, no_match) + NO_THINK)
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


# ── post-processing guard (3차, ADR 0005): run on the finished stream, before explain_done ───────────────────
PLACEHOLDER = re.compile(r"[〔〕]")
BOOL_TOKEN = re.compile(r"(?<![A-Za-z])(?:true|false|null|None|True|False)(?![A-Za-z])")
# payload key names that never occur in natural Korean (plain words like 디카페인·우유·향미 are also keys, so not here);
# the last three are keys of the pre-3차 payload that the model still reproduced from habit
KEY_NAMES = ("맛 비교", "향미 비교", "손님 취향 요약", "디카페인 주문 시 카페인(", "디카페인으로 주문 권장",
             "디카페인 추가요금", "맛 한 줄", "추천 여부")
LATIN_FIX = {"parcialmente": "부분적으로", "partially": "부분적으로"}
# Latin runs, also when a Korean particle follows ("Yirgacheffe는" — \b sees no boundary between e and 는)
_LATIN_WORD = re.compile(r"\s?(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])")
GUARD_REJECTS = ("placeholder", "bool_copy", "key_copy", "direction", "consequence")


def _strip_foreign(text: str, allowed: set[str], name: str = "") -> str:
    if re.search(r"[A-Za-z]{2,}", name) and name in text:
        text = text.replace(name, "이 음료")     # "Decaf Ethiopia Yirgacheffe는" → "이 음료는", not a stranded "는"

    def fix(m: re.Match) -> str:
        w = m.group().strip()
        if w.lower() in allowed:
            return m.group()
        ko = LATIN_FIX.get(w.lower())
        return (" " + ko) if ko else ""
    return _LATIN_WORD.sub(fix, text)


def _trim_sentences(text: str, violation: str | None) -> str:
    """At most two sentences. A first sentence that is just the violation ("디카페인이 아니에요.") is merged into the
    next instead of using up one of the two. Dropped sentences are usually the decaf-order advice, which the card
    shows anyway (ResultCard's "디카페인으로 변경 +N원" badge)."""
    spans = sentence_spans(text)
    if len(spans) > 2 and violation and spans[0].strip().rstrip(".!?") == violation.strip():
        spans = [spans[0].rstrip().rstrip(".!?") + " —" + spans[1]] + spans[2:]
    return "".join(spans[:2]).strip()


# only a taste word as the object is wrong ("단맛을 추천해요"); "아메리카노를 추천해요" is fine and stays
_OBJECT_VERDICT = re.compile(r"(\S*(?:산미|바디|단맛|향))[을를] (고려할 점은 있지만 추천해요|추천해요)")
_BARE_DRINK_END = re.compile(r"음료\.(?=\s|$)")


def _polish(text: str) -> str:
    """Two recurring slips around the code-given phrases (docs/adr/0005 5차, live cards):
    '비슷한 단맛을 고려할 점은 있지만 추천해요' → '단맛이 맞아 …', and a taste line ending '…음료.' → '…음료예요.'."""
    text = _OBJECT_VERDICT.sub(lambda m: f"{_subject(m.group(1))} 맞아 {m.group(2)}", text)
    return _BARE_DRINK_END.sub("음료예요.", text)


def finalize_explanation(text: str, item: Item, profile: Profile, payload: dict,
                         violation: str | None) -> tuple[str, str | None]:
    """Deterministic guard on a finished LLM explanation (docs/adr/0005 3차) → (text, event).

    Rejected (text '', event one of GUARD_REJECTS — the caller shows the template instead, telemetry `fb_guard`):
    a copied 〔placeholder〕, a copied true/false/null/None, a payload key name, or a 산미/바디/단맛 direction word
    the numbers contradict ("산미가 손님 선호보다 높아" when it is lower), or a wrong conclusion drawn from them
    ("산미가 선호보다 조금 강해서 잘 맞아요" — explain_check.consequence_errors).
    Edited (event "edited"): a Latin-script drink name becomes "이 음료", other Latin words that are not the
    drink's flavor tags are dropped (parcialmente → 부분적으로), the text is cut to two sentences, and a violation it never mentions gets the template's
    '주의: … — ' prefix (no extra sentence). Otherwise event None."""
    if PLACEHOLDER.search(text):
        return "", "placeholder"
    if BOOL_TOKEN.search(text):
        return "", "bool_copy"
    if any(k in text for k in KEY_NAMES):
        return "", "key_copy"
    out = _strip_foreign(text, allowed_latin(payload), item.name)
    out = re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.!?])", r"\1", out)).strip()
    out = _trim_sentences(out, violation)
    out = _polish(out)
    drink = {a: item.attr(a) for a in ATTRS}
    guest = {a: getattr(profile, a) for a in ATTRS}
    if direction_errors(out, drink, guest):
        return "", "direction"
    if consequence_errors(out, drink, guest):
        return "", "consequence"
    if violation and not violation_mentioned(out, violation):
        out = f"주의: {violation} — {out}"
    if not sentences(out):
        return "", "placeholder"
    return out, (None if out == text.strip() else "edited")


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
