from dataclasses import replace

from app.core.flavors import CATEGORIES, CATEGORY_KO, category_vector
from app.models import ATTRS, Item, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
SIGNAL_STEP = 0.5            # explicit "too acidic" etc. from the note
FLAVOR_SIGNAL_STEP = 0.3
PUSH_RANGE = 2.0             # a dislike only pushes when the item was this close to the preference
REPORT_THRESHOLD = 0.05


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def update_profile(profile: Profile, item: Item, rating: int, tag_to_cat: dict[str, str],
                   signals: dict | None = None) -> tuple[Profile, list[str]]:
    new = replace(profile, flavor_weights=dict(profile.flavor_weights))
    s = (rating - 3) / 2
    eta = 1 / (profile.n_updates + 2)

    for a in ATTRS:
        v = item.attr(a)
        if v is None or s == 0:
            continue
        pref = getattr(new, a)
        d = v - pref
        if s > 0:
            pref += eta * s * d
        elif d != 0 and abs(d) <= PUSH_RANGE:
            pref -= eta * abs(s) * (1 if d > 0 else -1) * (1 - abs(d) / PUSH_RANGE)
        setattr(new, a, _clamp(pref, 1, 5))

    if s != 0:
        for cat, share in category_vector(item.tags, tag_to_cat).items():
            new.flavor_weights[cat] = _clamp(new.flavor_weights.get(cat, 0.0) + eta * s * share, -1, 1)

    signals = signals or {}
    for a in ATTRS:
        direction = signals.get(a)
        if direction in ("lower", "higher"):
            step = -SIGNAL_STEP if direction == "lower" else SIGNAL_STEP
            setattr(new, a, _clamp(getattr(new, a) + step, 1, 5))
    for key, step in (("liked_flavors", FLAVOR_SIGNAL_STEP), ("disliked_flavors", -FLAVOR_SIGNAL_STEP)):
        for cat in signals.get(key) or []:
            if cat in CATEGORIES:
                new.flavor_weights[cat] = _clamp(new.flavor_weights.get(cat, 0.0) + step, -1, 1)

    new.n_updates = profile.n_updates + 1
    changes = [f"{ATTR_KO[a]} 선호 {getattr(profile, a):.1f}→{getattr(new, a):.1f}"
               for a in ATTRS if abs(getattr(new, a) - getattr(profile, a)) >= REPORT_THRESHOLD]
    for cat, w in new.flavor_weights.items():
        old = profile.flavor_weights.get(cat, 0.0)
        if abs(w - old) >= REPORT_THRESHOLD:
            changes.append(f"'{CATEGORY_KO.get(cat, cat)}' 선호 {'↑' if w > old else '↓'}")
    return new, changes
