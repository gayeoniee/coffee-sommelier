import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.core.tagmodel import TagModel


@dataclass
class Deps:
    repo: Any
    embed: Callable[[str], Awaitable[list[float]]]
    stream_text: Callable[[str, list[dict]], AsyncIterator[str]]
    chat_json: Callable[[str, list[dict], type], Awaitable[Any]]
    tag_model: TagModel | None = None    # learned flavor-tag model; None -> analyze_bean falls back to the vote


def _tag_model_enabled() -> bool:
    """The learned tag model's training labels come from coffeereview_kaggle-derived flavor_tags (licence-
    restricted; see docs/adr/0008-learned-tag-model.md), so it must never load for the open-data deployment
    (DATA_VARIANT=open, docs/design-decisions.md #12) -- that variant keeps using the neighbour vote, the same
    as it always has. `TAG_MODEL=off` is an explicit operator override for any other variant."""
    from app import config
    return config.DATA_VARIANT != "open" and os.getenv("TAG_MODEL", "").lower() != "off"


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for()

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(embedder.embed_query, text)

    tag_model = TagModel.load() if _tag_model_enabled() else None
    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json,
               tag_model=tag_model)
