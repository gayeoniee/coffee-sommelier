"""Async LLM calls for the app: token streaming for explanations, JSON for parsing.

Reuses the provider/task config of pipeline.llm (config/models.yaml) and its fallback rule:
if the primary target fails before producing any content, the fallback target is tried.
A primary stream that is slow to produce its first token is hedged with one identical request.
"""
import asyncio
import contextlib
import json
from typing import AsyncIterator

import httpx
from pydantic import BaseModel, ValidationError

from app import config, telemetry
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


_END = object()


class _Attempt:
    """One streamed request, pumped by its own task into a queue so it can be raced and cancelled.

    Queue items: `str` tokens, then either `_END` (finished) or the `Exception` that ended it.
    """

    def __init__(self, t: Target, messages: list[dict], transport):
        self.q: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._pump(t, messages, transport))

    async def _pump(self, t: Target, messages: list[dict], transport) -> None:
        try:
            async with contextlib.aclosing(_stream_once(t, messages, transport)) as stream:
                async for tok in stream:
                    self.q.put_nowait(tok)
        except Exception as e:  # handed to the consumer, which decides (fail over / propagate)
            self.q.put_nowait(e)
        else:
            self.q.put_nowait(_END)


async def _cancel_and_wait(tasks) -> None:
    """Cancel tasks and wait until each has really finished (connections closed, no pending-task warnings)."""
    tasks = [t for t in tasks if not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _hedged_stream(t: Target, messages: list[dict], transport, hedge_after_s: float) -> AsyncIterator[str]:
    """Stream from `t`; if no first token within `hedge_after_s`, race an identical second request.

    Tokens come from whichever request yields its first token first; the other is cancelled (its task
    awaited, its response closed) before anything is forwarded. A request that fails before output just
    drops out of the race; if all do, the last error is raised (nothing yielded → caller may fail over).
    Once the winner has produced output, its later errors propagate unchanged.
    """
    if hedge_after_s <= 0:
        async for tok in _stream_once(t, messages, transport):
            yield tok
        return

    loop = asyncio.get_running_loop()
    hedge_at = loop.time() + hedge_after_s
    attempts = [_Attempt(t, messages, transport)]
    waiting: dict[asyncio.Future, _Attempt] = {asyncio.ensure_future(attempts[0].q.get()): attempts[0]}
    try:
        winner, first, error = None, None, None
        while waiting and winner is None:
            timeout = max(0.0, hedge_at - loop.time()) if len(attempts) == 1 else None
            done, _ = await asyncio.wait(waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:                                    # primary still silent → fire the hedge
                telemetry.add("hedged", 1)
                hedge = _Attempt(t, messages, transport)
                attempts.append(hedge)
                waiting[asyncio.ensure_future(hedge.q.get())] = hedge
                continue
            for g in sorted(done, key=lambda g: attempts.index(waiting[g])):   # tie → primary
                a, item = waiting.pop(g), g.result()
                if isinstance(item, str):
                    if winner is None:
                        winner, first = a, item
                elif isinstance(item, Exception):
                    error = item                            # this attempt is out; the other may still win
        if winner is None:
            if error is not None:
                raise error
            return                                          # every attempt ended without output

        await _cancel_and_wait([*waiting, *(a.task for a in attempts if a is not winner)])
        waiting.clear()
        if winner is not attempts[0]:
            telemetry.add("hedge_won", 1)
        yield first
        while True:
            item = await winner.q.get()
            if isinstance(item, str):
                yield item
            elif item is _END:
                return
            else:
                raise item
    finally:                                                # any exit: consumer cancel/close, error, done
        await _cancel_and_wait([*waiting, *(a.task for a in attempts)])


async def astream_text(task: str, messages: list[dict], transport=None) -> AsyncIterator[str]:
    primary, fallback = load_targets(task)
    last: Exception | None = None
    for t in [primary] + ([fallback] if fallback else []):
        started = False
        hedge_after = config.HEDGE_AFTER_S if t is primary else 0   # hedge only the remote primary
        try:
            async with contextlib.aclosing(_hedged_stream(t, messages, transport, hedge_after)) as stream:
                async for tok in stream:
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
