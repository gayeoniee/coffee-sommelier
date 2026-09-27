"""Inference for the learned multi-label flavor-tag model (scripts/train_tag_model.py trains it;
config/tag_model.json[.gz] holds the weights). Deliberately pure Python: `app/` deploys without the
"pipeline" dependency group (numpy, scikit-learn, ...) -- see pyproject.toml / Dockerfile -- so this forward
pass uses plain lists and loops, never numpy.

Architecture: 1024 -> 128 (ReLU) -> T (sigmoid), one sklearn MLPClassifier trained on tag-free query
embeddings (docs/adr/0008-learned-tag-model.md). Missing config file -> `load()` returns None and the caller
(app/graphs/analyze_bean.py) falls back to the neighbour-vote `predict_from_neighbors`.
"""
import gzip
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("coffee.tagmodel")
_warned_missing = False


def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _sigmoid(x: float) -> float:
    # numerically stable: avoid overflow in math.exp for large |x|
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _layer(x: list[float], w: list[list[float]], b: list[float], activation: Callable[[float], float]
          ) -> list[float]:
    """One dense layer. `w` is input-major: w[i] is the list of weights from input i to every output unit
    (matches sklearn's MLPClassifier.coefs_ layout, so the trained weights need no transposing)."""
    out = list(b)
    for xi, row in zip(x, w):
        if xi:
            for j, wij in enumerate(row):
                out[j] += xi * wij
    return [activation(v) for v in out]


@dataclass
class TagModel:
    labels: list[str]
    threshold: float
    embed_model: str
    w1: list[list[float]]
    b1: list[float]
    w2: list[list[float]]
    b2: list[float]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TagModel | None":
        """`path` names an exact file (.json or .json.gz). With no `path`, picks the file for the active data
        variant: under DATA_VARIANT=open, ONLY config/tag_model_open.json[.gz] -- the category-level model
        trained on licence-clean sources (docs/adr/0009-learned-attribute-model.md Goal B2), shipped only if it
        beat the neighbour vote's category F1 by >=0.05 in CV. It never falls back to config/tag_model.json:
        that model's labels are coffeereview_kaggle-derived (licence-restricted, docs/adr/0008-learned-tag-
        model.md), so it must never load under the open-data deployment, open-file-missing or not. Any other
        variant loads config/tag_model.json[.gz]. Missing/unreadable -> None (logged once)."""
        global _warned_missing
        if path is not None:
            candidates = [Path(path)]
        else:
            from app import config
            from pipeline import settings
            name = "tag_model_open.json" if config.DATA_VARIANT == "open" else "tag_model.json"
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
                return cls(labels=doc["tags"], threshold=doc["threshold"], embed_model=doc.get("embed_model", ""),
                          w1=doc["W1"], b1=doc["b1"], w2=doc["W2"], b2=doc["b2"])
            except (OSError, ValueError, KeyError) as e:
                log.warning("tag model at %s failed to load: %s", p, e)
        if not _warned_missing:
            log.warning("no learned tag model found (looked for %s); flavor-tag prediction falls back to "
                       "the neighbour vote", ", ".join(str(c) for c in candidates))
            _warned_missing = True
        return None

    def predict(self, embedding: list[float]) -> list[tuple[str, float]]:
        """Every label's probability, sorted descending (ties broken by label name for determinism)."""
        hidden = _layer(embedding, self.w1, self.b1, _relu)
        probs = _layer(hidden, self.w2, self.b2, _sigmoid)
        return sorted(zip(self.labels, probs), key=lambda kv: (-kv[1], kv[0]))

    def tags(self, embedding: list[float], threshold: float | None = None, cap: int = 5
            ) -> list[tuple[str, float]]:
        """Labels at/above `threshold` (defaults to the trained threshold), highest-probability first, capped."""
        thr = self.threshold if threshold is None else threshold
        return [(t, p) for t, p in self.predict(embedding) if p >= thr][:cap]
