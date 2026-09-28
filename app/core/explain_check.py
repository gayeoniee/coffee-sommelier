"""Deterministic checks on a generated explanation: no LLM, no network. Used by `app.eval explain_quality`."""
import re

MAX_SENTENCES, MAX_CHARS = 2, 220        # the product rule: at most two sentences (explain.LENGTH_RULE)
POSITIVE = ("잘 맞", "추천해", "딱 맞", "어울려요", "잘 어울")
NEGATIVE = ("맞지 않", "어울리지 않", "거리가 있", "안 맞", "추천하기 어려")
_FIT_LOW = re.compile(r"적합도[^.!?]{0,10}낮")      # "적합도가 90%로 낮아" — the fit score itself called low
_FIT_HIGH = re.compile(r"적합도[^.!?]{0,10}높")
CONDITION_WORDS = ("디카페인", "카페인", "우유")
_VIOLATION_WORDS = (("우유", ("우유",)), ("카페인", ("카페인", "디카페인")))   # "디카페인" contains "카페인"
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<!\d)[.!?]|[.!?](?!\d)")    # a '.' between digits (4.5) ends nothing
_HANGUL = re.compile(r"[가-힣]")


def sentences(text: str) -> list[str]:
    """Split into sentences; fragments with no Hangul syllable (e.g. "(84%)") are not sentences."""
    return [s for s in _SENTENCE_END.split(text) if _HANGUL.search(s)]


def sentence_spans(text: str) -> list[str]:
    """Like `sentences`, but the pieces keep their punctuation and spacing so "".join(spans) == text; a fragment
    with no Hangul syllable (e.g. " (84%)") stays attached to the sentence before it."""
    spans: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        piece = text[start:m.end()]
        start = m.end()
        if spans and not _HANGUL.search(piece):
            spans[-1] += piece
        else:
            spans.append(piece)
    rest = text[start:]
    if rest:
        if spans and not _HANGUL.search(rest):
            spans[-1] += rest
        else:
            spans.append(rest)
    return spans


def allowed_latin(payload: dict) -> set[str]:
    """Latin words an explanation may contain: the drink's own flavor tags, plus units/temperatures."""
    allowed = {w.lower() for tag in payload.get("향미", []) for w in _LATIN.findall(str(tag))}
    return allowed | {"mg", "hot", "iced", "ice"}


def payload_numbers(payload: dict) -> set[float]:
    out: set[float] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for m in _NUM.findall(v):
                out.add(float(m))
    walk(payload)
    return out


def polarity(sentence: str) -> str | None:
    """Return "negative", "positive" or None. Negative patterns are checked first: "잘 맞지 않" contains "잘 맞"."""
    if any(w in sentence for w in NEGATIVE) or _FIT_LOW.search(sentence):
        return "negative"
    if any(w in sentence for w in POSITIVE) or _FIT_HIGH.search(sentence):
        return "positive"
    return None


def _required_words(violation: str) -> list[tuple[str, ...]]:
    """Keyword groups the explanation must mention for this violation: each violated condition by its own word
    (a milk violation is not covered by talking about caffeine). Unknown violation text → any condition word."""
    groups = [words for key, words in _VIOLATION_WORDS if key in violation]
    return groups or [CONDITION_WORDS]


def violation_mentioned(text: str, violation: str) -> bool:
    return all(any(w in text for w in words) for words in _required_words(violation))


_ATTR = "산미|바디|단맛"
_UP, _DOWN = ("높", "강", "무거", "진하", "많"), ("낮", "약", "가벼", "연하", "적")
_DIR = r"(높|강|무거|진하|많|낮|약(?!간)|가벼|연하|적)"
_ADV = r"(?:(?:훨씬|매우|아주|꽤|조금|약간|다소|살짝|비교적|좀|더)\s?)*"
_SUBJECT = re.compile(rf"((?:{_ATTR})(?:\s?(?:와|과|,|·|및)\s?(?:{_ATTR}))*)\s?(?:이|가|은|는|도)\s")
_COMPARATIVE = re.compile(rf"^[^,.!?]{{0,14}}?(?:선호|취향|원하시는 것|원하는 것|기대)\S{{0,2}}보다\s?{_ADV}{_DIR}")
_ABSOLUTE = re.compile(rf"^{_ADV}{_DIR}")
_ADNOMINAL = re.compile(r"(?:한|은|운|하게|게)\s(?!편)")    # "강한 커피", "강하게 좋아" — but "강한 편이에요" is a claim
_GUEST_SIDE = ("손님", "좋아", "선호", "원하", "싫어")
_ATTR_KEY = {"산미": "acidity", "바디": "body", "단맛": "sweetness"}


def direction_errors(text: str, drink: dict[str, float | None], guest: dict[str, float]) -> list[str]:
    """Direction words the text states about the drink's 산미/바디/단맛 that the numbers contradict.

    `drink`/`guest` are keyed acidity/body/sweetness on the 1~5 scale. Two forms are checked:
    comparative "산미와 바디가 손님 선호보다 높아" (wrong when the drink is not on the stated side of the guest's
    value — "선호보다 훨씬 약해" for 2 vs 2 included) and absolute "산미가 강하고" (wrong when the drink is <= 2.5 for 강/높, >= 3.5 for 약/낮).
    Absolute claims that describe the guest ("…산미가 강한 걸 좋아하시는") are skipped."""
    errors = []
    for m in _SUBJECT.finditer(text):
        attrs = re.findall(_ATTR, m.group(1))
        before, rest = text[max(0, m.start() - 12):m.start()], text[m.end():]
        comp = _COMPARATIVE.search(rest)
        absolute = None if comp else _ABSOLUTE.search(rest)
        if absolute and (any(w in before for w in _GUEST_SIDE) or _ADNOMINAL.match(rest[absolute.end():])):
            absolute = None      # "손님은 산미가 강한…", "산미가 강한 커피를 좋아하시는": the guest's taste, not the drink
        for a in attrs:
            v, g = drink.get(_ATTR_KEY[a]), guest.get(_ATTR_KEY[a])
            if v is None:
                continue
            if comp:
                up = comp.group(1).startswith(_UP)
                if (up and v - g <= 0) or (not up and v - g >= 0):
                    errors.append(f"{a}: '{m.group()}{comp.group()}' but drink {v:g} vs guest {g:g}")
            elif absolute:
                up = absolute.group(1).startswith(_UP)
                if (up and v <= 2.5) or (not up and v >= 3.5):
                    errors.append(f"{a}: '{m.group()}{absolute.group()}' but drink {v:g}")
    return errors


_CLAUSE_BREAK = re.compile(r"지만|는데|반면|[,.!?]")
_CAUSE = r"(?:서|해|며|니까|므로|기\s?때문에)\s(?:[가-힣]+\s){0,3}"
_FIT_GOOD = re.compile(rf"{_CAUSE}(?:잘|딱|대체로|꽤)?\s?(?:맞아|맞는|맞습|어울려|어울리)")
_FIT_BAD = re.compile(rf"{_CAUSE}(?:잘\s)?(?:맞지\s?않|안\s?맞|어울리지\s?않|아쉬)")
_GAP_CLAIM = re.compile(rf"(?:선호|취향)\S{{0,2}}보다\s?{_ADV}{_DIR}")
_SAME_CLAIM = re.compile(r"(?:선호|취향)\S{0,2}\s?(?:와|과)\s?(?:비슷|같|가깝)")
GAP_TOO_BIG = 1.5      # "잘 맞아요" because of an attribute this far from the guest is a wrong conclusion


def consequence_errors(text: str, drink: dict[str, float | None], guest: dict[str, float]) -> list[str]:
    """Wrong conclusions drawn from 산미/바디/단맛 within one clause (docs/adr/0005 3차 보완).

    `direction_errors` checks the direction words; this checks what the text concludes from them:
    - a stated gap given as the reason for a good fit ("산미가 선호보다 조금 강해서 취향에 잘 맞아요");
    - a good fit caused by an attribute that is GAP_TOO_BIG or more away from the guest
      ("산미가 약하고 바디가 강해 손님 선호와 잘 맞아요" when the guest wants body 2);
    - "비슷해서" given as the reason for a bad fit ("산미가 선호와 비슷해 잘 안 맞아요").
    Only the clause that carries the conclusion is read (split at 지만/는데/반면/commas), so
    "산미는 강하지만, 바디가 비슷해 잘 맞아요" is not blamed on 산미."""
    errors = []
    start = 0
    for m in _CLAUSE_BREAK.finditer(text + "."):
        clause, start = text[start:m.start()], m.end()
        attrs = [a for a in _ATTR_KEY if a in clause]
        if not attrs:
            continue
        good, bad = _FIT_GOOD.search(clause), _FIT_BAD.search(clause)
        if good and not bad:
            head = clause[:good.start() + 1]
            if _GAP_CLAIM.search(head):
                errors.append(f"gap→fit: '{clause.strip()}'")
                continue
            for a in attrs:
                v, g = drink.get(_ATTR_KEY[a]), guest.get(_ATTR_KEY[a])
                if v is not None and g is not None and abs(v - g) >= GAP_TOO_BIG:
                    errors.append(f"{a} {v:g} vs guest {g:g} but '{clause.strip()}'")
        elif bad and _SAME_CLAIM.search(clause[:bad.start() + 1]) and not _GAP_CLAIM.search(clause):
            errors.append(f"same→misfit: '{clause.strip()}'")
    return errors


def check_explanation(text: str, payload: dict, score: int, violation: str | None) -> dict[str, bool]:
    allowed = allowed_latin(payload)
    foreign_ok = all(w.lower() in allowed for w in _LATIN.findall(text))
    length_ok = len(sentences(text)) <= MAX_SENTENCES and len(text) <= MAX_CHARS
    nums = payload_numbers(payload) | {float(score)}
    plain = _THOUSANDS.sub(lambda m: m.group().replace(",", ""), text)     # "+1,500원" → 1500, not 1 and 500
    numbers_ok = all(any(abs(float(n) - p) <= 0.05 for p in nums) for n in _NUM.findall(plain))
    condition_ok = (not violation) or violation_mentioned(text, violation)
    if violation:          # a violation makes a negative conclusion right even for a high score
        polarity_ok = True
    else:
        first = (sentences(text) or [text])[0]      # the conclusion; a second sentence may add a caveat
        pol = polarity(first)
        polarity_ok = not ((score < 40 and pol == "positive") or (score >= 80 and pol == "negative"))
    return {"foreign_words": foreign_ok, "length": length_ok, "numbers_grounded": numbers_ok,
            "condition_mentioned": condition_ok, "polarity": polarity_ok}


def all_ok(result: dict[str, bool]) -> bool:
    return all(result.values())
