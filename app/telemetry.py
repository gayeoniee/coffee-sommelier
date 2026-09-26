"""Structured per-request telemetry: one JSON log line per recommend/analyze stream.

Timing is collected in a `contextvars.ContextVar` for the lifetime of the request and flushed as a
single `logging.getLogger("telemetry").info(...)` call. No personal data (review text, nicknames,
user ids) goes into these lines — only profile-shape numbers and counts.
"""
import contextvars
import json
import logging
from time import perf_counter
from typing import Any

log = logging.getLogger("telemetry")

_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("telemetry", default=None)

_LIST_FIELDS = {"ms_first_token"}
_COUNTER_FIELDS = {"fallback", "hedged", "hedge_won"}


def begin(evt: str, **fields: Any) -> contextvars.Token:
    """Start a telemetry record for the current request; returns a token for `end()`."""
    return _ctx.set({"evt": evt, **fields, "_t0": perf_counter()})


def add(key: str, value: Any) -> None:
    """Record a field on the in-flight request. No-op outside a `begin()`/`end()` pair.

    List fields (`ms_first_token`) append; counter fields (`fallback`, `hedged`, `hedge_won`) accumulate; everything
    else (`cards`, `error`, ...) is overwritten.
    """
    data = _ctx.get()
    if data is None:
        return
    if key in _LIST_FIELDS:
        data.setdefault(key, []).append(value)
    elif key in _COUNTER_FIELDS:
        data[key] = data.get(key, 0) + value
    else:
        data[key] = value


def end(token: contextvars.Token, **fields: Any) -> None:
    """Finish the request: compute `ms_total`, log one JSON line, and reset the context.

    Never raises — a logging failure must not break the request it is describing.
    """
    try:
        data = _ctx.get()
        if data is not None:
            data.update(fields)
            t0 = data.pop("_t0", None)
            data["ms_total"] = int((perf_counter() - t0) * 1000) if t0 is not None else 0
            data.setdefault("ms_first_token", [])
            data.setdefault("fallback", 0)
            log.info(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
    finally:
        _ctx.reset(token)
