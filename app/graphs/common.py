import httpx
from langgraph.config import get_stream_writer

from app.config import EXPLAIN_TASK
from app.core.explain import explain_messages, template_explanation
from app.models import Item, Prediction, Profile
from pipeline.llm import LLMError


async def explain_to_stream(deps, item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                            prediction: Prediction | None = None, violation: str | None = None) -> dict:
    """Stream an LLM explanation token by token; on any failure replace it with the template."""
    writer = get_stream_writer()
    template = template_explanation(item, profile, score, tag_ko, violation)
    parts: list[str] = []
    try:
        async for tok in deps.stream_text(EXPLAIN_TASK, explain_messages(item, profile, score, prediction)):
            parts.append(tok)
            writer({"type": "explain_delta", "key": item.key, "delta": tok})
        text = "".join(parts).strip()
        if not text:
            raise LLMError("empty explanation")
        writer({"type": "explain_done", "key": item.key, "text": text})
        return {"key": item.key, "text": text, "fallback": False}
    except (LLMError, httpx.HTTPError):
        writer({"type": "explain_fallback", "key": item.key, "text": template})
        return {"key": item.key, "text": template, "fallback": True}
