"""Interpretable feature model for acidity/body/sweetness of an unknown bean, open-data variant
(docs/adr/0011-roaster-gauges-feature-model.md). scripts/train_feature_model.py trains it on Korean roasters'
own published intensity gauges and ships config/feature_model_open.json; this module is BOTH the feature
extractor the trainer uses and the runtime inference, so training and serving features can never drift.

Deliberately pure Python (no numpy): app/ deploys without the "pipeline" dependency group. The model is a
per-attribute ridge regression over a few dozen hand-named facts -- origin region/country, altitude band,
process, roast, variety group, decaf method, SCA flavor-category counts of the bean's own note words, and
(optionally) the neighbour average -- so each prediction decomposes into per-feature contributions
(weight x value), which is what the Korean evidence line ("고지대(1,900m)·워시드 → 산미↑") is built from.
"""
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.models import ATTRS

log = logging.getLogger("coffee.featuremodel")
_warned_missing = False

CLIP_MIN, CLIP_MAX = 1.0, 5.0
NBR_CENTER = 3.0              # neighbour-average feature is centred on the scale midpoint; missing -> 0
MAX_CAT_COUNT = 3             # a note-word category contributes at most this many hits

REGIONS = {
    "east_africa": ("Ethiopia", "Kenya", "Rwanda", "Burundi", "Uganda", "Tanzania", "Malawi", "Zambia",
                    "Zimbabwe", "Democratic Republic of the Congo", "Madagascar", "Cameroon", "Mauritius"),
    "central_america": ("Guatemala", "Costa Rica", "Panama", "Honduras", "El Salvador", "Nicaragua", "Mexico",
                        "Jamaica", "Haiti", "Dominican Republic", "Puerto Rico", "Cuba"),
    "south_america": ("Colombia", "Brazil", "Peru", "Bolivia", "Ecuador", "Venezuela"),
    "asia_pacific": ("Indonesia", "Papua New Guinea", "India", "Vietnam", "Thailand", "Laos", "Myanmar", "China",
                     "Taiwan", "Philippines", "Timor-Leste", "Nepal", "Australia", "United States"),
    "yemen": ("Yemen",),
}
_REGION_OF = {c: r for r, cs in REGIONS.items() for c in cs}
COUNTRIES = ("Ethiopia", "Kenya", "Colombia", "Brazil", "Guatemala")
ALT_BANDS = (("alt_lt1200", 0, 1200), ("alt_1200_1600", 1200, 1600), ("alt_1600_2000", 1600, 2000),
             ("alt_ge2000", 2000, 10_000))
PROCESSES = ("washed", "natural", "honey", "anaerobic")
ROASTS = ("light", "medium-light", "medium", "medium-dark", "dark")
CATEGORIES = ("fruity", "floral", "sweet", "nutty/cocoa", "roasted", "spices", "sour/fermented",
              "green/vegetative", "other")

FEATURES = (
    *(f"region_{r}" for r in REGIONS), "blend_or_unknown_origin",
    *(f"country_{c}" for c in COUNTRIES),
    *(a for a, _, _ in ALT_BANDS),
    *(f"process_{p}" for p in PROCESSES),
    *(f"roast_{r}" for r in ROASTS),
    "variety_geisha", "variety_kenyan_sl",
    "decaf", "decaf_water", "decaf_sugarcane",
    *(f"notes_{c}" for c in CATEGORIES),
    "nbr",
)

_GEISHA = re.compile(r"geisha|gesha|게이샤|게샤", re.I)
_SL = re.compile(r"\bsl[\s-]?(28|34)\b|\bruiru\b|\bbatian\b", re.I)
# "1,900m", "1800-2000 masl", "해발 1,600~2,000m", "고도 1950m" -- metres only (feet would need converting).
_ALTITUDE = re.compile(r"(\d{1,2},?\d{3})(?:\s*[-~–]\s*(\d{1,2},?\d{3}))?\s*(?:m\b|masl|미터|m\s)", re.I)


def altitude_from_text(text: str | None) -> int | None:
    """Growing altitude stated in free text, metres (midpoint of a range); None when absent/implausible."""
    m = _ALTITUDE.search(text or "")
    if not m:
        return None
    nums = [int(g.replace(",", "")) for g in m.groups() if g]
    alt = round(sum(nums) / len(nums))
    return alt if 200 <= alt <= 3000 else None


def region_of(country: str | None) -> str | None:
    return _REGION_OF.get(country or "")


def bean_features(*, origin_country: str | None, process: str | None, roast_level: str | None,
                  is_decaf: bool = False, decaf_process: str | None = None, variety: str | None = None,
                  altitude_m: int | None = None, text: str | None = None,
                  note_categories: list[str] | None = None, neighbor_value: float | None = None
                  ) -> dict[str, float]:
    """Sparse feature dict (name -> value, zeros omitted) for one bean.

    `origin_country`/`process`/`roast_level`/`decaf_process` use the pipeline's canonical names
    (pipeline.rules normalizers -- the same ones app.core.parse applies to user text). `text` is the bean's
    own name/notes (or the user's input): variety and altitude are also looked up there when not given.
    `note_categories` is the SCA level-1 category of each flavor tag found in the bean's own notes (one entry
    per tag). `neighbor_value` is the open-pool neighbour average for the attribute being predicted."""
    f: dict[str, float] = {}
    region = region_of(origin_country)
    if region:
        f[f"region_{region}"] = 1.0
    else:
        f["blend_or_unknown_origin"] = 1.0
    if origin_country in COUNTRIES:
        f[f"country_{origin_country}"] = 1.0
    alt = altitude_m or altitude_from_text(text)
    for name, lo, hi in ALT_BANDS:
        if alt is not None and lo <= alt < hi:
            f[name] = 1.0
    if process in PROCESSES:
        f[f"process_{process}"] = 1.0
    if roast_level in ROASTS:
        f[f"roast_{roast_level}"] = 1.0
    blob = f"{variety or ''} {text or ''}"
    if _GEISHA.search(blob):
        f["variety_geisha"] = 1.0
    if _SL.search(blob):
        f["variety_kenyan_sl"] = 1.0
    if is_decaf:
        f["decaf"] = 1.0
        if decaf_process in ("swiss-water", "mountain-water"):
            f["decaf_water"] = 1.0
        elif decaf_process == "sugarcane-ea":
            f["decaf_sugarcane"] = 1.0
    counts: dict[str, int] = {}
    for c in note_categories or ():
        if c in CATEGORIES:
            counts[c] = counts.get(c, 0) + 1
    for c, n in counts.items():
        f[f"notes_{c}"] = float(min(n, MAX_CAT_COUNT))
    if neighbor_value is not None:
        f["nbr"] = round(neighbor_value - NBR_CENTER, 4)
    return {k: v for k, v in f.items() if v}


# ---- Korean evidence labels ------------------------------------------------------------------------
_COUNTRY_KO = {"Ethiopia": "에티오피아", "Kenya": "케냐", "Colombia": "콜롬비아", "Brazil": "브라질",
               "Guatemala": "과테말라"}
_LABEL_KO = {
    "region_east_africa": "동아프리카", "region_central_america": "중미", "region_south_america": "남미",
    "region_asia_pacific": "아시아·태평양", "region_yemen": "예멘", "blend_or_unknown_origin": "블렌드/산지 미상",
    "alt_lt1200": "저지대", "alt_1200_1600": "중간 고도", "alt_1600_2000": "고지대", "alt_ge2000": "고지대",
    "process_washed": "워시드", "process_natural": "내추럴", "process_honey": "허니", "process_anaerobic": "무산소 발효",
    "roast_light": "약배전", "roast_medium-light": "중약배전", "roast_medium": "중배전",
    "roast_medium-dark": "중강배전", "roast_dark": "강배전",
    "variety_geisha": "게이샤", "variety_kenyan_sl": "SL 품종", "decaf": "디카페인",
    "decaf_water": "물 공정 디카페인", "decaf_sugarcane": "슈가케인 디카페인",
    "notes_fruity": "과일 노트", "notes_floral": "꽃 노트", "notes_sweet": "단 노트",
    "notes_nutty/cocoa": "견과·초콜릿 노트", "notes_roasted": "로스티 노트", "notes_spices": "향신료 노트",
    "notes_sour/fermented": "발효 노트", "notes_green/vegetative": "풀 노트", "notes_other": "기타 노트",
    "nbr": "유사 원두 평균",
}
ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}


def feature_label_ko(name: str, altitude_m: int | None = None) -> str:
    if name.startswith("country_"):
        return _COUNTRY_KO.get(name[len("country_"):], name[len("country_"):])
    label = _LABEL_KO.get(name, name)
    if name.startswith("alt_") and altitude_m:
        label = f"{label}({altitude_m:,}m)"
    return label


# ---- model -------------------------------------------------------------------------------------------
@dataclass
class _Linear:
    intercept: float
    weights: dict[str, float]
    uses_nbr: bool

    def contributions(self, feats: dict[str, float]) -> dict[str, float]:
        return {k: self.weights[k] * v for k, v in feats.items() if k in self.weights and self.weights[k]}

    def predict(self, feats: dict[str, float]) -> float:
        return self.intercept + sum(self.contributions(feats).values())


@dataclass
class FeatureModel:
    models: dict[str, _Linear]        # attribute -> ridge; an attribute missing here isn't shipped

    @classmethod
    def from_doc(cls, doc: dict) -> "FeatureModel":
        return cls(models={a: _Linear(intercept=float(s["intercept"]), weights=dict(s["weights"]),
                                      uses_nbr="nbr" in s["weights"])
                           for a, s in doc["attrs"].items()})

    @classmethod
    def load(cls, path: str | Path | None = None) -> "FeatureModel | None":
        """config/feature_model_open.json (or `path`); missing/unreadable -> None (logged once). Only the open
        data variant uses this model (app/graphs/__init__.py)."""
        global _warned_missing
        if path is None:
            from pipeline import settings
            path = settings.CONFIG_DIR / "feature_model_open.json"
        p = Path(path)
        try:
            return cls.from_doc(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as e:
            if not _warned_missing:
                log.warning("feature model at %s not loaded (%s); open-variant attributes fall back to the "
                            "neighbour average", p, e)
                _warned_missing = True
            return None

    def predict(self, feats_by_attr: dict[str, dict[str, float]]) -> dict[str, tuple[float, dict[str, float]]]:
        """{attr: features} -> {attr: (value clipped to [1, 5] and rounded to 2 dp, contributions)} for every
        shipped attribute present in the input."""
        out = {}
        for a in ATTRS:
            m = self.models.get(a)
            if m is None or a not in feats_by_attr:
                continue
            feats = feats_by_attr[a]
            out[a] = (round(min(CLIP_MAX, max(CLIP_MIN, m.predict(feats))), 2), m.contributions(feats))
        return out


def evidence_line(attr: str, contributions: dict[str, float], altitude_m: int | None = None, top: int = 3,
                  min_abs: float = 0.05) -> str | None:
    """"특징 모델: 고지대(1,900m)·워시드 → 산미↑" from the top contributors (|weight x value| >= min_abs);
    features pushing the other way get their own "→ 산미↓" clause. None when nothing clears the bar."""
    ranked = sorted(((k, v) for k, v in contributions.items() if abs(v) >= min_abs), key=lambda kv: -abs(kv[1]))
    ranked = ranked[:top]
    if not ranked:
        return None
    labels: dict[str, list[str]] = {"↑": [], "↓": []}
    for k, v in ranked:
        label = feature_label_ko(k, altitude_m)
        bucket = labels["↑" if v > 0 else "↓"]
        if label not in bucket:
            bucket.append(label)
    clauses = [f"{'·'.join(ls)} → {ATTR_KO[attr]}{arrow}" for arrow, ls in labels.items() if ls]
    return "특징 모델: " + ", ".join(clauses)


def with_feature_model(pred, model: "FeatureModel", parsed, tag_to_cat: dict[str, str],
                       tag_ko: dict[str, str] | None = None):
    """Open-variant attribute prediction (app/graphs/analyze_bean.py): replace the neighbour average with the
    feature model wherever it shipped the attribute, and append one Korean evidence line per replaced
    attribute. The current `pred` value (the neighbour average) is the model's `nbr` feature. Attributes the
    model doesn't ship keep the neighbour average; text cues are applied after this and still win."""
    from dataclasses import replace

    from app.core.textcues import text_tags

    tags = text_tags(parsed.text, tag_to_cat, tag_ko or {})
    cats = [tag_to_cat[t] for t in tags if t in tag_to_cat]
    altitude = altitude_from_text(parsed.text)
    feats = {a: bean_features(origin_country=parsed.origin_country, process=parsed.process,
                              roast_level=parsed.roast_level, is_decaf=parsed.is_decaf,
                              decaf_process=parsed.decaf_process, altitude_m=altitude, text=parsed.text,
                              note_categories=cats, neighbor_value=getattr(pred, a))
             for a in model.models}
    out = model.predict(feats)
    if not out:
        return pred
    evidence = list(pred.evidence)
    for a, (_, contrib) in out.items():
        line = evidence_line(a, contrib, altitude_m=altitude)
        if line:
            evidence.append(line)
    return replace(pred, evidence=evidence, **{a: v for a, (v, _) in out.items()})
