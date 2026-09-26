import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable


@dataclass
class Deps:
    repo: Any
    embed: Callable[[str], Awaitable[list[float]]]
    stream_text: Callable[[str, list[dict]], AsyncIterator[str]]
    chat_json: Callable[[str, list[dict], type], Awaitable[Any]]


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for()

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(embedder.embed_query, text)

    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json)
