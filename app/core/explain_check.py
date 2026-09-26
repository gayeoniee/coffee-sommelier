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


def check_explanation(text: str, payload: dict, score: int, violation: str | None) -> dict[str, bool]:
    allowed = {w.lower() for tag in payload.get("향미", []) for w in _LATIN.findall(str(tag))}
    allowed |= {"mg", "hot", "iced", "ice"}
    foreign_ok = all(w.lower() in allowed for w in _LATIN.findall(text))
    length_ok = len(sentences(text)) <= MAX_SENTENCES and len(text) <= MAX_CHARS
    nums = payload_numbers(payload) | {float(score)}
    plain = _THOUSANDS.sub(lambda m: m.group().replace(",", ""), text)     # "+1,500원" → 1500, not 1 and 500
    numbers_ok = all(any(abs(float(n) - p) <= 0.05 for p in nums) for n in _NUM.findall(plain))
    condition_ok = (not violation) or all(any(w in text for w in words) for words in _required_words(violation))
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
