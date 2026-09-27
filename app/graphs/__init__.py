import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.core.attrmodel import AttrModel
from app.core.tagmodel import TagModel


@dataclass
class Deps:
    repo: Any
    embed: Callable[[str], Awaitable[list[float]]]
    stream_text: Callable[[str, list[dict]], AsyncIterator[str]]
    chat_json: Callable[[str, list[dict], type], Awaitable[Any]]
    tag_model: TagModel | None = None      # learned flavor-tag model; None -> analyze_bean falls back to the vote
    attr_model: AttrModel | None = None    # learned attribute regressor; None -> falls back to the neighbour average


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


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for()

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(embedder.embed_query, text)

    tag_model = TagModel.load() if _tag_model_enabled() else None
    attr_model = AttrModel.load() if _attr_model_enabled() else None
    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json,
               tag_model=tag_model, attr_model=attr_model)
