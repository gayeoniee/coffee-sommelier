"""Deterministic checks on a generated explanation: no LLM, no network. Used by `app.eval explain_quality`."""
import re

MAX_SENTENCES, MAX_CHARS = 3, 220
POSITIVE = ("잘 맞", "추천해", "딱 맞", "어울려요", "잘 어울")
NEGATIVE = ("맞지 않", "어울리지 않", "거리가 있", "안 맞")
CONDITION_WORDS = ("디카페인", "카페인", "우유")
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


def check_explanation(text: str, payload: dict, score: int, violation: str | None) -> dict[str, bool]:
    allowed = {w.lower() for tag in payload.get("향미", []) for w in _LATIN.findall(str(tag))}
    allowed |= {"mg", "hot", "iced", "ice"}
    foreign_ok = all(w.lower() in allowed for w in _LATIN.findall(text))
    length_ok = len(sentences(text)) <= MAX_SENTENCES and len(text) <= MAX_CHARS
    nums = payload_numbers(payload) | {float(score)}
    numbers_ok = all(any(abs(float(n) - p) <= 0.05 for p in nums) for n in _NUM.findall(text))
    condition_ok = (not violation) or any(w in text for w in CONDITION_WORDS)
    if violation:          # a violation makes a negative conclusion right even for a high score
        polarity_ok = True
    elif score < 40:
        polarity_ok = not any(w in text for w in POSITIVE)
    elif score >= 80:
        polarity_ok = not any(w in text for w in NEGATIVE)
    else:
        polarity_ok = True
    return {"foreign_words": foreign_ok, "length": length_ok, "numbers_grounded": numbers_ok,
            "condition_mentioned": condition_ok, "polarity": polarity_ok}


def all_ok(result: dict[str, bool]) -> bool:
    return all(result.values())
