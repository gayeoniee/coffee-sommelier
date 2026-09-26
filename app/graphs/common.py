import asyncio
from time import perf_counter

import httpx
from langgraph.config import get_stream_writer

from app import config, telemetry
from app.config import EXPLAIN_TASK
from app.core.explain import explain_messages, template_explanation
from app.llm import TRUNCATED
from app.models import Item, Prediction, Profile
from pipeline.llm import LLMError

_SENTENCE_ENDERS = ".!?"


def _last_complete_sentence(text: str) -> str:
    """Cut a truncated explanation back to its last complete sentence; '' if it has none."""
    idx = max((text.rfind(c) for c in _SENTENCE_ENDERS), default=-1)
    return text[:idx + 1].strip() if idx >= 0 else ""


async def explain_to_stream(deps, item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                            prediction: Prediction | None = None, violation: str | None = None) -> dict:
    """Stream an LLM explanation token by token; on failure or past the deadline, replace it with the template.

    A completion cut off by `max_tokens` (surfaced as the `TRUNCATED` sentinel) is trimmed back to its last
    complete sentence rather than shown mid-sentence; if nothing complete remains, it falls back like any
    other failure.
    """
    writer = get_stream_writer()
    template = template_explanation(item, profile, score, tag_ko, violation)
    parts: list[str] = []
    truncated = False
    t0 = perf_counter()
    try:
        async with asyncio.timeout(config.EXPLAIN_DEADLINE_S):
            async for tok in deps.stream_text(EXPLAIN_TASK,
                                              explain_messages(item, profile, score, prediction, violation)):
                if tok is TRUNCATED:
                    truncated = True
                    continue
                if not parts:
                    telemetry.add("ms_first_token", int((perf_counter() - t0) * 1000))
                parts.append(tok)
                writer({"type": "explain_delta", "key": item.key, "delta": tok})
        text = "".join(parts).strip()
        if truncated:
            telemetry.add("truncated", 1)
            text = _last_complete_sentence(text)
        if not text:
            raise LLMError("empty explanation")
        writer({"type": "explain_done", "key": item.key, "text": text})
        return {"key": item.key, "text": text, "fallback": False}
    except (LLMError, httpx.HTTPError, TimeoutError):
        telemetry.add("fallback", 1)
        writer({"type": "explain_fallback", "key": item.key, "text": template})
        return {"key": item.key, "text": template, "fallback": True}
