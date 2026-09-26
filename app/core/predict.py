from math import sqrt

from app.models import ATTRS, Item, Neighbor, ParsedBean, Prediction

MIN_NEIGHBORS = 3
TAG_SHARE = 0.3
MAX_TAGS = 5
HIGH_STD, MEDIUM_STD = 0.7, 1.2


def _weighted(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    total = sum(w for _, w in pairs)
    mean = sum(v * w for v, w in pairs) / total
    var = sum(w * (v - mean) ** 2 for v, w in pairs) / total
    return mean, sqrt(var)


def predict_from_neighbors(neighbors: list[Neighbor], tag_ko: dict[str, str] | None = None) -> Prediction:
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
    ranked = sorted(weight_by_tag.items(), key=lambda kv: (-kv[1], kv[0]))
    tags = [t for t, _ in ranked if count_by_tag[t] / len(neighbors) >= TAG_SHARE][:MAX_TAGS]

    evidence = [f"유사 원두 {len(neighbors)}개 중 {count_by_tag[t]}개에서 '{tag_ko.get(t, t)}' 언급" for t in tags[:2]]
    if values["acidity"] is not None:
        evidence.append(f"유사 원두 산미 평균 {values['acidity']:.1f}/5")
    return Prediction(acidity=values["acidity"], body=values["body"], sweetness=values["sweetness"],
                      confidence=confidence, tags=tags, evidence=evidence, n_neighbors=len(neighbors))


def item_from_prediction(parsed: ParsedBean, pred: Prediction) -> Item:
    return Item(key="input", name=parsed.text, source="predicted", acidity=pred.acidity, body=pred.body,
                sweetness=pred.sweetness, tags=tuple(pred.tags), is_decaf=parsed.is_decaf,
                confidence=pred.confidence, origin_country=parsed.origin_country, process=parsed.process)
