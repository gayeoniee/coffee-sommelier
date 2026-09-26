"""Async LLM calls for the app: token streaming for explanations, JSON for parsing.

Reuses the provider/task config of pipeline.llm (config/models.yaml) and its fallback rule:
if the primary target fails before producing any content, the fallback target is tried.
"""
import json
from typing import AsyncIterator

import httpx
from pydantic import BaseModel, ValidationError

from pipeline.llm import LLMError, Target, extract_json, load_targets


def _headers(t: Target) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if t.api_key:
        h["Authorization"] = f"Bearer {t.api_key}"
    return h


def _payload(t: Target, messages: list[dict], **extra) -> dict:
    p = {"model": t.model, "messages": messages, "max_tokens": t.max_tokens, **extra}
    if t.extra:
        p.update(t.extra)
    return p


async def _stream_once(t: Target, messages: list[dict], transport) -> AsyncIterator[str]:
    async with httpx.AsyncClient(base_url=t.base_url, timeout=t.timeout, transport=transport) as client:
        async with client.stream("POST", "/chat/completions", headers=_headers(t),
                                 json=_payload(t, messages, temperature=0.3, stream=True)) as r:
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {t.model}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = (json.loads(data)["choices"][0].get("delta") or {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    continue
                if delta:
                    yield delta


async def astream_text(task: str, messages: list[dict], transport=None) -> AsyncIterator[str]:
    primary, fallback = load_targets(task)
    last: Exception | None = None
    for t in [primary] + ([fallback] if fallback else []):
        started = False
        try:
            async for tok in _stream_once(t, messages, transport):
                started = True
                yield tok
        except (httpx.HTTPError, LLMError) as e:
            if started:
                raise LLMError(f"stream from {t.model} broke after output: {e}") from e
            last = e
            continue
        if started:
            return
        last = LLMError(f"empty stream from {t.model}")
    raise LLMError(f"all targets failed for {task}: {last}")


async def _chat_once(t: Target, messages: list[dict], transport) -> str:
    async with httpx.AsyncClient(base_url=t.base_url, timeout=t.timeout, transport=transport) as client:
        try:
            r = await client.post("/chat/completions", headers=_headers(t), json=_payload(t, messages, temperature=0))
        except httpx.HTTPError as e:
            raise LLMError(f"{type(e).__name__} from {t.model}: {e}") from e
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code} from {t.model}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"malformed response from {t.model}") from e
    if not content or not content.strip():
        raise LLMError(f"empty content from {t.model}")
    return content


async def achat_json(task: str, messages: list[dict], schema: type[BaseModel], transport=None) -> BaseModel:
    primary, fallback = load_targets(task)
    last: Exception | None = None
    for t in [primary] + ([fallback] if fallback else []):
        for _ in range(2):                      # one retry on invalid JSON, same target
            try:
                return schema.model_validate(extract_json(await _chat_once(t, messages, transport)))
            except LLMError as e:               # transport failure: move to the next target
                last = e
                break
            except (ValueError, ValidationError) as e:
                last = e
    raise LLMError(f"achat_json failed for {task}: {last}")
