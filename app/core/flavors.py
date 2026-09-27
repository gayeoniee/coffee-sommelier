from pathlib import Path
from math import sqrt
from typing import Iterable

import yaml

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


def load_tag_ko_extra() -> dict[str, str]:
    """Korean names (config/tag_ko_extra.yaml) for tags that reach coffees.flavor_tags without a matching
    SCA-wheel node in flavor_taxonomy (LLM enrich / the Korean roastery note mapper can produce off-wheel tags).
    Lives under config/ because that is what the API image ships (data/ is not copied); a missing file is an
    empty map, never an error."""
    from pipeline import settings

    path = Path(settings.__file__).resolve().parents[1] / "config" / "tag_ko_extra.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k.lower(): v for k, v in data.items()}


def merge_tag_ko(taxonomy_ko: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    """Merge tag_ko_extra.yaml under a taxonomy-derived tag_ko map; the taxonomy's own names win on conflict."""
    return {**extra, **taxonomy_ko}


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
