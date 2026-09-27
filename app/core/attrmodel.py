"""Inference for the learned per-attribute (acidity/body/sweetness) regressor (scripts/train_attr_model.py
trains it; config/attr_model.json[.gz] or config/attr_model_open.json holds the weights). Deliberately pure
Python, same reason as app/core/tagmodel.py: app/ deploys without the "pipeline" dependency group (numpy,
scikit-learn, ...), so this forward pass uses plain lists and loops, never numpy.

Each attribute independently picked ridge regression or a small MLP (1024 -> 128 ReLU -> 1 identity) by
5-fold CV MAE (docs/adr/0009-learned-attribute-model.md) -- ridge = W (length 1024) + b; MLP = W1/b1/W2/b2,
same input-major layout as app/core/tagmodel.py so sklearn's coefs_/intercepts_ need no transposing.

An attribute can be missing from the config entirely (the open variant ships no sweetness -- too few
open-licence labels): predict() returns None for it and the caller (app/graphs/analyze_bean.py) falls back to
predict_from_neighbors's neighbour average, same as when the whole file is missing.
"""
import gzip
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.models import ATTRS

log = logging.getLogger("coffee.attrmodel")
_warned_missing = False

CLIP_MIN, CLIP_MAX = 1.0, 5.0


def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _layer(x: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    """One dense layer, identity activation. `w` is input-major: w[i] is the list of weights from input i to
    every output unit (matches sklearn's coefs_ layout)."""
    out = list(b)
    for xi, row in zip(x, w):
        if xi:
            for j, wij in enumerate(row):
                out[j] += xi * wij
    return out


@dataclass
class _Ridge:
    w: list[float]
    b: float

    def predict(self, x: list[float]) -> float:
        s = self.b
        for xi, wi in zip(x, self.w):
            s += xi * wi
        return s


@dataclass
class _Mlp:
    w1: list[list[float]]
    b1: list[float]
    w2: list[list[float]]   # hidden -> 1, input-major (w2[i] = [weight from hidden unit i])
    b2: list[float]         # length 1

    def predict(self, x: list[float]) -> float:
        hidden = [_relu(v) for v in _layer(x, self.w1, self.b1)]
        return _layer(hidden, self.w2, self.b2)[0]


def _load_one(spec: dict):
    kind = spec["type"]
    if kind == "ridge":
        return _Ridge(w=spec["W"], b=spec["b"])
    if kind == "mlp":
        return _Mlp(w1=spec["W1"], b1=spec["b1"], w2=spec["W2"], b2=spec["b2"])
    raise ValueError(f"unknown attr model type {kind!r}")


@dataclass
class AttrModel:
    embed_model: str
    models: dict[str, "_Ridge | _Mlp"]     # attribute name -> its regressor; missing key -> not shipped

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AttrModel | None":
        """`path` names an exact file (.json or .json.gz). With no `path`, picks the file for the active
        data variant (config/attr_model_open.json under DATA_VARIANT=open, else config/attr_model.json),
        trying the plain .json then .json.gz. Missing/unreadable -> None (logged once)."""
        global _warned_missing
        if path is not None:
            candidates = [Path(path)]
        else:
            from app import config
            from pipeline import settings
            name = "attr_model_open.json" if config.DATA_VARIANT == "open" else "attr_model.json"
            candidates = [settings.CONFIG_DIR / name, settings.CONFIG_DIR / (name + ".gz")]
        for p in candidates:
            if not p.exists():
                continue
            try:
                if p.suffix == ".gz":
                    with gzip.open(p, "rt", encoding="utf-8") as f:
                        doc = json.load(f)
                else:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                models = {a: _load_one(spec) for a, spec in doc["attrs"].items()}
                return cls(embed_model=doc.get("embed_model", ""), models=models)
            except (OSError, ValueError, KeyError) as e:
                log.warning("attribute model at %s failed to load: %s", p, e)
        if not _warned_missing:
            log.warning("no learned attribute model found (looked for %s); attribute prediction falls back to "
                       "the neighbour average", ", ".join(str(c) for c in candidates))
            _warned_missing = True
        return None

    def predict(self, embedding: list[float]) -> dict[str, float | None]:
        """Every attribute's predicted value, clipped to [1, 5] and rounded to 2 decimals; an attribute this
        model doesn't cover (not shipped, e.g. open-variant sweetness) comes back as None."""
        out: dict[str, float | None] = {}
        for a in ATTRS:
            m = self.models.get(a)
            if m is None:
                out[a] = None
                continue
            v = m.predict(embedding)
            out[a] = round(min(CLIP_MAX, max(CLIP_MIN, v)), 2)
        return out
