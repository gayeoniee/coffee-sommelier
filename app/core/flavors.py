from math import sqrt
from typing import Iterable

CATEGORIES = ("fruity", "floral", "sweet", "nutty/cocoa", "roasted", "spices", "sour/fermented",
              "green/vegetative", "other")
CATEGORY_KO = {"fruity": "과일", "floral": "꽃", "sweet": "단맛", "nutty/cocoa": "견과/코코아", "roasted": "로스팅",
               "spices": "향신료", "sour/fermented": "신맛/발효", "green/vegetative": "풀/채소", "other": "기타"}
# Categories offered as "I like this" chips during onboarding (the last two are defects, not preferences).
PREFERENCE_CHIPS = ("fruity", "floral", "sweet", "nutty/cocoa", "roasted", "spices", "sour/fermented")


def build_tag_to_category(rows: Iterable[tuple[str, int, str]]) -> dict[str, str]:
    """rows: (taxonomy key like 'sca:fruity>berry>blackberry', level, name_en) -> tag -> level-1 category."""
    out: dict[str, str] = {}
    for key, level, name in rows:
        if level < 2:
            continue
        out.setdefault(name.lower(), key.removeprefix("sca:").split(">")[0])
    return out


def category_vector(tags: Iterable[str], tag_to_cat: dict[str, str]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for t in tags:
        c = tag_to_cat.get(t.lower())
        if c:
            counts[c] = counts.get(c, 0) + 1
    total = sum(counts.values())
    return {c: n / total for c, n in counts.items()} if total else {}


def cosine(a: dict[str, float], b: dict[str, float]) -> float | None:
    na = sqrt(sum(v * v for v in a.values()))
    nb = sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return None
    return sum(a[k] * b[k] for k in a.keys() & b.keys()) / (na * nb)
