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


EMPTY_EXPLANATION = "empty explanation"
FALLBACK_REASONS = ("timeout_first_token", "timeout_midstream", "error_before_token", "error_midstream",
                    "truncated_empty", "empty")


def fallback_reason(exc: BaseException, had_output: bool, truncated: bool) -> str:
    """Why a card fell back to the template -- one of FALLBACK_REASONS, logged as a `fb_<reason>` telemetry
    counter so scripts/ops/prod_stats.py can break the fallback rate down (ADR 0004 "폴백 원인 분해"):
    - timeout_first_token / timeout_midstream: the EXPLAIN_DEADLINE_S deadline hit before / after the first token
    - error_before_token: every target failed before any text (HTTP 429/5xx, connection error, empty stream)
    - error_midstream: the stream broke after text had started
    - truncated_empty: cut off by max_tokens with no complete sentence to keep
    - empty: the model finished normally but produced no usable text"""
    if isinstance(exc, TimeoutError):
        return "timeout_midstream" if had_output else "timeout_first_token"
    if isinstance(exc, LLMError) and str(exc) == EMPTY_EXPLANATION:
        return "truncated_empty" if truncated else "empty"
    return "error_midstream" if had_output else "error_before_token"


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
            raise LLMError(EMPTY_EXPLANATION)
        writer({"type": "explain_done", "key": item.key, "text": text})
        return {"key": item.key, "text": text, "fallback": False}
    except (LLMError, httpx.HTTPError, TimeoutError) as e:
        telemetry.add("fallback", 1)
        telemetry.add(f"fb_{fallback_reason(e, bool(parts), truncated)}", 1)
        writer({"type": "explain_fallback", "key": item.key, "text": template})
        return {"key": item.key, "text": template, "fallback": True}
