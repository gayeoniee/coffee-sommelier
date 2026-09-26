from app.core.flavors import category_vector, cosine
from app.models import ATTRS, Item, Profile

LOW_CAFFEINE_MG = 100
ATTR_WEIGHT, FLAVOR_WEIGHT = 0.6, 0.4
MILK_WORDS = ("라떼", "우유", "밀크", "크림", "카푸치노", "플랫화이트", "모카", "프라푸치노",
              "latte", "milk", "cream", "cappuccino", "flat white", "mocha", "frappuccino")


def is_milk_drink(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in MILK_WORDS)


def passes(profile: Profile, item: Item) -> tuple[bool, str | None]:
    decaf_ok = item.is_decaf or item.decaf_option
    if profile.caffeine_rule == "decaf_only" and not decaf_ok:
        return False, "디카페인이 아니에요"
    if profile.caffeine_rule == "low" and not (
            decaf_ok or (item.caffeine_mg is not None and item.caffeine_mg <= LOW_CAFFEINE_MG)):
        return False, "카페인이 100mg을 넘거나 알 수 없어요"
    if not profile.milk_ok and item.is_milk:
        return False, "우유가 들어가요"
    return True, None


def attr_fit(profile: Profile, item: Item) -> float | None:
    num = den = 0.0
    for a in ATTRS:
        v = item.attr(a)
        if v is None:
            continue
        w = 0.5 if (a == "acidity" and item.is_milk) else 1.0   # milk mutes acidity
        num += w * (1 - abs(getattr(profile, a) - v) / 4)
        den += w
    return num / den if den else None


def flavor_fit(profile: Profile, item: Item, tag_to_cat: dict[str, str]) -> float | None:
    c = cosine(profile.flavor_weights, category_vector(item.tags, tag_to_cat))
    return None if c is None else (c + 1) / 2


def score_item(profile: Profile, item: Item, tag_to_cat: dict[str, str]) -> float:
    a, f = attr_fit(profile, item), flavor_fit(profile, item, tag_to_cat)
    if a is None and f is None:
        return 0.5
    if a is None:
        return f
    if f is None:
        return a
    return ATTR_WEIGHT * a + FLAVOR_WEIGHT * f


def _features(item: Item, tag_to_cat: dict[str, str]) -> dict[str, float]:
    feats = {a: (item.attr(a) if item.attr(a) is not None else 3.0) / 5 for a in ATTRS}
    feats.update({f"cat:{c}": v for c, v in category_vector(item.tags, tag_to_cat).items()})
    return feats


def mmr_top_k(scored: list[tuple[Item, float]], tag_to_cat: dict[str, str], k: int = 3,
              lam: float = 0.7) -> list[tuple[Item, float]]:
    pool = sorted(scored, key=lambda x: -x[1])
    feats = {it.key: _features(it, tag_to_cat) for it, _ in pool}
    chosen: list[tuple[Item, float]] = []
    while pool and len(chosen) < k:
        def mmr(candidate: tuple[Item, float]) -> float:
            sim = max((cosine(feats[candidate[0].key], feats[c.key]) or 0.0 for c, _ in chosen), default=0.0)
            return lam * candidate[1] - (1 - lam) * sim
        best = max(pool, key=mmr)
        chosen.append(best)
        pool.remove(best)
    return chosen
