"""Simulated users with a hidden true taste: does the profile approach it as they log drinks?"""
import random

from app.core.flavors import PREFERENCE_CHIPS
from app.core.learning import update_profile
from app.core.scoring import score_item
from app.models import ATTRS, Item, Profile


def _mae(p: Profile, truth: Profile) -> float:
    return sum(abs(getattr(p, a) - getattr(truth, a)) for a in ATTRS) / len(ATTRS)


def _rating(truth: Profile, item: Item, tag_to_cat: dict[str, str], rng: random.Random) -> int:
    fit = score_item(truth, item, tag_to_cat)
    # People rate relative to what they usually drink: a fit of 0.55 is "meh", 0.95 is "love it".
    return max(1, min(5, round(1 + 4 * (fit - 0.55) / 0.4 + rng.gauss(0, 0.5))))


def simulate_convergence(items: list[Item], tag_to_cat: dict[str, str], users: int = 100, steps: int = 10,
                         seed: int = 1, explore: float = 0.3, candidates: int = 20) -> dict:
    rng = random.Random(seed)
    curves: list[list[float]] = []
    for _ in range(users):
        truth = Profile(**{a: rng.uniform(1.5, 4.5) for a in ATTRS},
                        flavor_weights={c: rng.uniform(-1, 1) for c in PREFERENCE_CHIPS})
        profile = Profile()
        curve = [_mae(profile, truth)]
        for _ in range(steps):
            pool = rng.sample(items, min(candidates, len(items)))
            if rng.random() < explore:
                chosen = rng.choice(pool)
            else:
                chosen = max(pool, key=lambda it: score_item(profile, it, tag_to_cat))
            profile, _ = update_profile(profile, chosen, _rating(truth, chosen, tag_to_cat, rng), tag_to_cat)
            curve.append(_mae(profile, truth))
        curves.append(curve)
    mae = [round(sum(c[i] for c in curves) / len(curves), 4) for i in range(steps + 1)]
    return {"users": users, "steps": steps, "mae_by_step": mae, "improvement": round(mae[0] - mae[-1], 4)}
