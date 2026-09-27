import asyncio
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


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for()

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(embedder.embed_query, text)

    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json,
               tag_model=TagModel.load())
