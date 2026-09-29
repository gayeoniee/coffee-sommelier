import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.core.attrmodel import AttrModel
from app.core.featuremodel import FeatureModel
from app.core.tagcooc import TagCooc
from app.core.tagmodel import TagModel


@dataclass
class Deps:
    repo: Any
    embed: Callable[[str], Awaitable[list[float]]]
    stream_text: Callable[[str, list[dict]], AsyncIterator[str]]
    chat_json: Callable[[str, list[dict], type], Awaitable[Any]]
    tag_model: TagModel | None = None      # learned flavor-tag model; None -> analyze_bean falls back to the vote
    attr_model: AttrModel | None = None    # learned attribute regressor; None -> falls back to the neighbour average
    # open-variant interpretable feature model (docs/adr/0011-roaster-gauges-feature-model.md); None -> neighbour avg
    feature_model: FeatureModel | None = None
    # open variant: top thin neighbour-vote tags up from the tagged-only neighbours (docs/adr/0017-open-tag-fill.md)
    tag_fill: bool = False
    # open variant: top a guest's lone note word up from tag co-occurrence (docs/adr/0018-open-tag-cooccurrence.md)
    tag_cooc: TagCooc | None = None


def _tag_model_enabled() -> bool:
    """`TAG_MODEL=off` is an explicit operator override, in either variant. The licence gate itself lives in
    TagModel.load()'s file selection, not here: under DATA_VARIANT=open it only ever looks for config/
    tag_model_open.json (the category-level model trained on licence-clean sources, shipped only if it beat the
    neighbour vote by >=0.05 CV category F1 -- docs/adr/0009-learned-attribute-model.md Goal B2) and never falls
    back to the coffeereview_kaggle-derived config/tag_model.json (docs/adr/0008-learned-tag-model.md). So the
    open deployment loads no tag model at all unless that open file has actually been shipped."""
    return os.getenv("TAG_MODEL", "").lower() != "off"


def _attr_model_enabled() -> bool:
    """Unlike the tag model, the attribute regressor has a licence-clean open variant of its own
    (config/attr_model_open.json, trained only on cqi/roasters_kr/shopify -- docs/adr/0009-learned-attribute-
    model.md), so DATA_VARIANT=open still loads a model (AttrModel.load() picks the right file); only an
    explicit `ATTR_MODEL=off` disables it, in either variant."""
    return os.getenv("ATTR_MODEL", "").lower() != "off"


def _feature_model_enabled() -> bool:
    """Only the open data variant uses the roaster-gauge feature model (config/feature_model_open.json,
    docs/adr/0011-roaster-gauges-feature-model.md): the full variant has coffeereview's much larger labelled
    pool behind its neighbour average/attribute model. `FEATURE_MODEL=off` disables it explicitly."""
    from app import config
    return config.DATA_VARIANT == "open" and os.getenv("FEATURE_MODEL", "").lower() != "off"


def _tag_fill_enabled() -> bool:
    """Only the open data variant fills thin tag votes from tagged-only neighbours (its pool is ~75% untagged CQI
    rows; the full variant's learned tag model replaces the vote anyway). `TAG_FILL=off` disables it."""
    from app import config
    return config.DATA_VARIANT == "open" and os.getenv("TAG_FILL", "").lower() != "off"


def _tag_cooc_enabled() -> bool:
    """Only the open data variant tops the guest's own note words up from the licence-clean co-occurrence table
    (config/tag_cooc_open.json); the full variant's learned tag model is untouched. `TAG_COOC=off` disables it."""
    from app import config
    return config.DATA_VARIANT == "open" and os.getenv("TAG_COOC", "").lower() != "off"


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for()

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(embedder.embed_query, text)

    tag_model = TagModel.load() if _tag_model_enabled() else None
    attr_model = AttrModel.load() if _attr_model_enabled() else None
    feature_model = FeatureModel.load() if _feature_model_enabled() else None
    tag_cooc = TagCooc.load() if _tag_cooc_enabled() else None
    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json,
               tag_model=tag_model, attr_model=attr_model, feature_model=feature_model, tag_fill=_tag_fill_enabled(),
               tag_cooc=tag_cooc)
