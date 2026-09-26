"""Langfuse tracing that disappears entirely when keys are not configured."""
import contextlib
import os


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def traced(name: str):
    if not enabled():
        return lambda f: f
    from langfuse import observe
    return observe(name=name, capture_input=False, capture_output=False)   # profiles/notes stay out of traces


def span(name: str):
    if not enabled():
        return contextlib.nullcontext()
    from langfuse import get_client
    return get_client().start_as_current_observation(name=name, as_type="span")


def flush() -> None:
    if enabled():
        from langfuse import get_client
        get_client().flush()
