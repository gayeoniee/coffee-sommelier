from dataclasses import replace
from math import log, sqrt

from app.core.textcues import ATTR_KO, attr_cues, text_tags
from app.models import ATTRS, Item, Neighbor, ParsedBean, Prediction

MIN_NEIGHBORS = 3
TAG_SHARE = 0.3
MAX_TAGS = 5
HIGH_STD, MEDIUM_STD = 0.7, 1.2
# Lift gate (Fix A): a tag that is common everywhere (e.g. "chocolate") needs a much higher share among the
# neighbours than a rare one before it counts as evidence. `_MIN_GLOBAL_SHARE` is a floor used only when a tag
# has no known base rate (unseen tag), so it neither gets a free pass nor blows up log(1/0).
LIFT = 1.2
_MIN_GLOBAL_SHARE = 1e-6


def _weighted(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    total = sum(w for _, w in pairs)
    mean = sum(v * w for v, w in pairs) / total
    var = sum(w * (v - mean) ** 2 for v, w in pairs) / total
    return mean, sqrt(var)


def _tag_kept(share: float, global_share: float, lift: float = LIFT) -> bool:
    """True when a tag's share among the neighbours clears the lift-adjusted gate.

    `global_share` is the tag's base rate over all active coffees with >=1 tag. A tag no more common than
    the flat TAG_SHARE floor everywhere never needs the lift; one that dominates the whole catalog (e.g.
    "chocolate") needs `lift` times its base rate before it counts as evidence for this particular bean."""
    return share >= max(TAG_SHARE, lift * global_share)


def _tag_rank_score(share: float, global_share: float) -> float:
    """Rarer tags (lower global_share) outrank common ones at the same neighbour share."""
    return share * log(1 / max(global_share, _MIN_GLOBAL_SHARE))


def predict_from_neighbors(neighbors: list[Neighbor], tag_ko: dict[str, str] | None = None,
                           base_rates: dict[str, float] | None = None) -> Prediction:
    tag_ko = tag_ko or {}
    weighted = [(n, max(n.similarity, 0.01)) for n in neighbors]
    values: dict[str, float | None] = {}
    stds: list[float] = []
    for a in ATTRS:
        pairs = [(getattr(n, a), w) for n, w in weighted if getattr(n, a) is not None]
        if len(pairs) < MIN_NEIGHBORS:
            values[a] = None
            continue
        mean, std = _weighted(pairs)
        values[a] = round(mean, 2)
        stds.append(std)
    if len(neighbors) < MIN_NEIGHBORS or not stds:
        confidence = "low"
    else:
        mean_std = sum(stds) / len(stds)
        confidence = "high" if mean_std <= HIGH_STD else "medium" if mean_std <= MEDIUM_STD else "low"

    total = sum(w for _, w in weighted) or 1.0
    weight_by_tag: dict[str, float] = {}
    count_by_tag: dict[str, int] = {}
    for n, w in weighted:
        for t in {x.lower() for x in n.tags}:
            weight_by_tag[t] = weight_by_tag.get(t, 0.0) + w
            count_by_tag[t] = count_by_tag.get(t, 0) + 1
    if base_rates is None:
        ranked = sorted(weight_by_tag.items(), key=lambda kv: (-kv[1], kv[0]))
        tags = [t for t, _ in ranked if count_by_tag[t] / len(neighbors) >= TAG_SHARE][:MAX_TAGS]
    else:
        kept = []
        for t in weight_by_tag:
            share = count_by_tag[t] / len(neighbors)
            global_share = base_rates.get(t, 0.0)
            if _tag_kept(share, global_share):
                kept.append((t, _tag_rank_score(share, global_share)))
        tags = [t for t, _ in sorted(kept, key=lambda kv: (-kv[1], kv[0]))][:MAX_TAGS]

    evidence = [f"유사 원두 {len(neighbors)}개 중 {count_by_tag[t]}개에서 '{tag_ko.get(t, t)}' 언급" for t in tags[:2]]
    if values["acidity"] is not None:
        evidence.append(f"유사 원두 산미 평균 {values['acidity']:.1f}/5")
    return Prediction(acidity=values["acidity"], body=values["body"], sweetness=values["sweetness"],
                      confidence=confidence, tags=tags, evidence=evidence, n_neighbors=len(neighbors))


def with_model_tags(pred: Prediction, tag_probs: list[tuple[str, float]], tag_ko: dict[str, str] | None = None
                    ) -> Prediction:
    """Replace the neighbour-vote tags/evidence with the learned tag model's predictions (app/core/tagmodel.py),
    keeping the attribute predictions (acidity/body/sweetness/confidence) and n_neighbors untouched -- the
    model only replaces the TAG half of `predict_from_neighbors`'s output (docs/adr/0008-learned-tag-model.md)."""
    tag_ko = tag_ko or {}
    tags = [t for t, _ in tag_probs]
    # drop only the neighbour-vote's tag-mention lines ("유사 원두 N개 중 M개에서 '...' 언급"); the acidity
    # evidence line ("유사 원두 산미 평균 ...") also starts with "유사 원두" but has no "언급" -- keep it.
    kept_evidence = [e for e in pred.evidence if not (e.startswith("유사 원두") and "언급" in e)]
    model_evidence = [f"향미 모델: {', '.join(f'{tag_ko.get(t, t)} {p:.2f}' for t, p in tag_probs)}"] if tag_probs else []
    return replace(pred, tags=tags, evidence=model_evidence + kept_evidence)


def with_model_attrs(pred: Prediction, attr_values: dict[str, float | None]) -> Prediction:
    """Replace the neighbour-average acidity/body/sweetness with the learned attribute model's predictions
    (app/core/attrmodel.py) wherever it shipped a value; an attribute the model doesn't cover (None -- e.g. the
    open variant ships no sweetness) keeps the neighbour-average value. Confidence/tags/evidence/n_neighbors are
    untouched -- the model only replaces the ATTRIBUTE half of `predict_from_neighbors`'s output
    (docs/adr/0009-learned-attribute-model.md)."""
    return replace(pred, **{a: v if v is not None else getattr(pred, a) for a, v in attr_values.items()})


def with_text_cues(pred: Prediction, text: str, tag_to_cat: dict[str, str], tag_ko: dict[str, str] | None = None
                   ) -> Prediction:
    """Explicit acidity/body/sweetness/flavor cues read straight out of the user's own text
    (app/core/textcues.py) outrank both the learned model and the neighbour average -- the user is stating
    the fact directly, not something we're inferring. Text-extracted tags are UNIONED ahead of the predicted
    tags (their own evidence line goes first) and the combined list is capped at MAX_TAGS. Confidence and
    n_neighbors are untouched (docs/adr/0010-body-heaviness.md)."""
    tag_ko = tag_ko or {}
    cues = attr_cues(text)
    evidence = list(pred.evidence)
    for attr, (value, phrase) in cues.items():
        evidence.append(f"문구에 '{phrase}' 명시 → {ATTR_KO[attr]} {value}")

    extra_tags = text_tags(text, tag_to_cat, tag_ko)
    tags = pred.tags
    if extra_tags:
        tags = list(dict.fromkeys([*extra_tags, *pred.tags]))[:MAX_TAGS]
        evidence = [f"문구의 향미: {', '.join(tag_ko.get(t, t) for t in extra_tags)}"] + evidence

    if not cues and not extra_tags:
        return pred
    return replace(pred, tags=tags, evidence=evidence, **{a: v for a, (v, _) in cues.items()})


def item_from_prediction(parsed: ParsedBean, pred: Prediction) -> Item:
    return Item(key="input", name=parsed.text, source="predicted", acidity=pred.acidity, body=pred.body,
                sweetness=pred.sweetness, tags=tuple(pred.tags), is_decaf=parsed.is_decaf,
                confidence=pred.confidence, origin_country=parsed.origin_country, process=parsed.process)
