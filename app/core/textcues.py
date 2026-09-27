"""Explicit acidity/body/sweetness values and flavor tags read straight out of the user's OWN text
(docs/adr/0010-body-heaviness.md) -- pure rules, no LLM, no data dependency, licence-clean.

app/graphs/analyze_bean.py applies these AFTER predict_from_neighbors / with_model_tags / with_model_attrs
(app/core/predict.py::with_text_cues): when the user states a fact directly ("묵직한 바디", "bright acidity"),
that outranks both the learned model and the neighbour average -- we're not inferring it, they told us.
"""
import re

from pipeline.enrich import KO_TAG_ALIASES, rule_tags

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}


def _near(a: str, b: str, window: int = 10) -> str:
    """`a` and `b` within `window` characters of each other, either order -- Korean word order is free enough
    ("산미가 강한" vs "강한 산미") that a fixed a-then-b sequence would miss half of real phrasings."""
    return rf"{a}.{{0,{window}}}{b}|{b}.{{0,{window}}}{a}"


# (high pattern, high value, low pattern, low value). A hit on BOTH sides for one attribute is ambiguous --
# attr_cues() leaves it alone rather than guess. "강한"/"약한"/"밝은"/"부드러운" alone are too generic (they
# describe all sorts of things, not just acidity), so those require "산미" nearby; the rest are acidity-
# specific words on their own.
_ACIDITY_HIGH = re.compile(
    rf"{_near('산미', '강')}|{_near('산미', '밝')}|상큼(?:한|해요)?|bright|juicy", re.I)
_ACIDITY_LOW = re.compile(
    rf"{_near('산미', '약')}|{_near('산미', '부드러')}|low\s*acid", re.I)
_BODY_HIGH = re.compile(
    r"묵직(?:한|해요)?(?:\s*바디)?|무거운(?:\s*바디)?|풀\s*바디|full[\s-]?body|heavy|syrupy", re.I)
_BODY_LOW = re.compile(
    r"가벼운(?:\s*바디)?|라이트(?:한)?(?:\s*바디)?|tea[\s-]?like|light\s*body", re.I)
_SWEET_HIGH = re.compile(r"달콤(?:한|해요)?|단맛|sweet", re.I)
_SWEET_LOW = re.compile(r"쓴맛|쓴(?:맛)?|bitter", re.I)

ATTR_CUES: dict[str, tuple[re.Pattern, float, re.Pattern, float]] = {
    "acidity": (_ACIDITY_HIGH, 4.5, _ACIDITY_LOW, 1.5),
    "body": (_BODY_HIGH, 4.5, _BODY_LOW, 1.5),
    "sweetness": (_SWEET_HIGH, 4.0, _SWEET_LOW, 2.0),
}


def attr_cues(text: str) -> dict[str, tuple[float, str]]:
    """attr -> (anchored value, matched phrase) for every attribute the text states outright. An attribute
    with no cue -- or with cues on BOTH sides (ambiguous) -- is absent from the result, and the caller keeps
    whatever value the model/neighbour average already produced."""
    t = text or ""
    out: dict[str, tuple[float, str]] = {}
    for attr, (hi_pat, hi_val, lo_pat, lo_val) in ATTR_CUES.items():
        hi, lo = hi_pat.search(t), lo_pat.search(t)
        if hi and not lo:
            out[attr] = (hi_val, hi.group(0))
        elif lo and not hi:
            out[attr] = (lo_val, lo.group(0))
    return out


def ko_vocab_from_tag_ko(tag_to_cat: dict[str, str], tag_ko: dict[str, str]) -> dict[str, str]:
    """Korean term (spaces removed) -> English tag, built from the runtime taxonomy maps (app/repo.py
    Repo.taxonomy()) the same way pipeline.enrich.ko_tag_vocab() builds it from the taxonomy jsonl at
    pipeline time: the taxonomy's own Korean names, plus KO_TAG_ALIASES for any alias whose target is in the
    known tag vocabulary (`tag_to_cat`'s keys) -- not only the (smaller) set of tags with a Korean name of
    their own, same as the pipeline side."""
    en = set(tag_to_cat)
    out = {"".join(ko.split()): en_tag for en_tag, ko in tag_ko.items() if ko and en_tag in en}
    out.update({k: v for k, v in KO_TAG_ALIASES.items() if v in en})
    return out


def text_tags(text: str, tag_to_cat: dict[str, str], tag_ko: dict[str, str], limit: int = 6) -> list[str]:
    """Flavor tags the SCA Korean/English note mapper (pipeline.enrich.rule_tags, the same rules pipeline
    enrichment uses on collected review text) finds directly in the user's text. `tag_to_cat`'s keys are the
    known English tag vocabulary (app/repo.py Repo.taxonomy()); ko_rule_tags only fires on note-list-shaped
    text (pipeline.enrich.is_note_list), same safety net as the pipeline side."""
    return rule_tags(text or "", list(tag_to_cat), limit=limit, ko_vocab=ko_vocab_from_tag_ko(tag_to_cat, tag_ko))
