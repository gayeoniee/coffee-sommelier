import re
from statistics import median

from app.core.flavors import category_vector, cosine
from app.models import ATTRS, Item, Profile

LOW_CAFFEINE_MG = 100
ATTR_WEIGHT, FLAVOR_WEIGHT = 0.6, 0.4
# Matched against the lowercased name with all whitespace removed. Golden-tested against the hand labels in
# data/curated/menu_milk_labels.yaml (tests/app/test_milk_labels.py).
MILK_WORDS = (
    "라떼", "라테", "우유", "밀크", "크림", "크리미", "카푸치노", "플랫화이트", "모카", "프라푸치노", "프라페",
    "마키아또", "마끼아또", "코르타도", "브레베", "비안코", "아포가토", "콘파나", "아인슈페너", "쉐이크", "셰이크",
    "초코", "초콜릿", "요거트", "요구르트", "야쿠르트", "퐁크러쉬", "커피스무디",
    "스타벅스더블샷",            # espresso shaken with milk
    "할메가", "원조커피",         # mix-coffee style signatures made with condensed milk / cream
    "수아", "믹스커피",           # 카페수아 (condensed milk), 컴포즈믹스커피 (creamer)
    "마끼아토", "마키아토",       # spelling variants (커피빈 마끼아토네, 폴바셋 카라멜 마키아토)
    "블렌디드", "오트",           # 커피빈 아이스 블렌디드 (milk base), oat-milk drinks
    "딜라이트", "에어리",         # 할리스 딜라이트 (milk drink), 컴포즈 에어리 폼 (cream foam, low confidence)
    "연유", "달달커피",           # condensed milk (이디야 연유 콜드브루), 이디야 달달커피 (coffee-mix style, milk)
    "꿀화이트", "흑당콜드브루", "에스프레소코코넛",   # 이디야: honey + signature milk, black sugar + milk, coconut-milk latte
    "latte", "milk", "cream", "cappuccino", "flatwhite", "mocha", "frappuccino", "frappe", "macchiato",
    "cortado", "breve", "bianco", "affogato", "conpanna", "einspanner", "shake", "choco", "yogurt", "yoghurt",
)

# checked in order: "바닐라 크림 콜드브루" is cold brew, "디카페인 카페 라떼" is latte
DRINK_FAMILIES = (("frappuccino", ("frappuccino", "프라푸치노")),
                  ("cold_brew", ("cold brew", "콜드 브루", "콜드브루")),
                  ("flat_white", ("flat white", "플랫화이트", "플랫 화이트")),
                  ("cappuccino", ("cappuccino", "카푸치노")),
                  ("mocha", ("mocha", "모카")),
                  ("latte", ("latte", "라떼")),
                  ("americano", ("americano", "아메리카노")),
                  ("espresso", ("espresso", "에스프레소")))


def drink_family(name: str) -> str:
    n = name.lower()
    return next((fam for fam, words in DRINK_FAMILIES if any(w in n for w in words)), "other")


def is_milk_drink(name: str) -> bool:
    n = "".join(name.lower().split())
    return any(w in n for w in MILK_WORDS)


def needs_decaf_order(caffeine_rule: str, is_decaf: bool, decaf_option: bool, caffeine_mg: float | None) -> bool:
    """Should the guest order the decaf version? decaf_only: always; low: only if the drink isn't already <=100mg."""
    low_enough = caffeine_mg is not None and caffeine_mg <= LOW_CAFFEINE_MG
    return bool(decaf_option and not is_decaf
                and (caffeine_rule == "decaf_only" or (caffeine_rule == "low" and not low_enough)))


# Stripped when pairing a drink with the brand's own decaf SKU of it ("카페 라떼" ~ "디카페인 카페라떼",
# "스페니쉬 카페 라떼 [연유]" ~ "디카페인 스페니쉬 카페라떼", "디카페인 아메리카노(HOT)" ~ "아메리카노"). "아이스" stays:
# 폴바셋 sells "아이스 카페라떼" and "아이스 디카페인 카페라떼" as their own pair.
_TWIN_NOISE = re.compile(r"\(디카페인\s*원두\)|디카페인|decaf|\[[^\]]*\]|\((?:hot|iced?)\)|\b(?:hot|iced?)\b")
DECAF_ESTIMATE_NOTE = "디카페인 주문 시 추정"        # caffeine_mg is an estimate for the decaf order
DECAF_UNKNOWN_NOTE = "디카페인 주문 시 카페인 ↓"     # no estimate: the regular drink's mg must not be shown


def decaf_twin_key(name: str) -> str:
    return "".join(_TWIN_NOISE.sub(" ", name.lower()).split())


def decaf_twins(menus: list[dict]) -> dict[str, list[float | None]]:
    """twin key -> caffeine_mg of the brand's decaf SKUs with that key (HOT/ICED can give two)."""
    out: dict[str, list[float | None]] = {}
    for m in menus:
        if m["is_decaf"]:
            out.setdefault(decaf_twin_key(m["name"]), []).append(m["caffeine_mg"])
    return out


def decaf_order_caffeine(name: str, twins: dict[str, list[float | None]]) -> tuple[float | None, str]:
    """(shown caffeine_mg, note) for a drink ordered decaf: never the regular drink's mg. The twin SKU's
    caffeine when the brand sells one, else the median of the brand's decaf SKUs, else unknown."""
    mgs = [v for v in twins.get(decaf_twin_key(name), ()) if v is not None]
    if not mgs:
        mgs = [v for vs in twins.values() for v in vs if v is not None]
    if not mgs:
        return None, DECAF_UNKNOWN_NOTE
    return float(round(median(mgs))), DECAF_ESTIMATE_NOTE


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
    feats[f"fam:{drink_family(item.name)}"] = 1.0   # same-brand drinks share bean attributes; family separates them
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
