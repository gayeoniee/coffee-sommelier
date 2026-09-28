"""Structured per-request telemetry: one JSON log line per recommend/analyze stream.

Timing is collected in a `contextvars.ContextVar` for the lifetime of the request and flushed as a
single `logging.getLogger("telemetry").info(...)` call. No personal data (review text, nicknames,
user ids) goes into these lines — only profile-shape numbers and counts.

`configure()` (called by `app.api.create_app`) gives the logger its own stdout handler: under uvicorn the
root logger stays at WARNING with no handler, so without it every INFO line would be silently dropped.
"""
import contextvars
import json
import logging
import sys
from time import perf_counter
from typing import Any

log = logging.getLogger("telemetry")
HANDLER_NAME = "telemetry-stdout"

_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("telemetry", default=None)
# token -> its record, so end() finds the record even when it runs in another Context (an async generator
# closed after a client disconnect can be finalised outside the request's Context)
_open: dict[int, dict[str, Any]] = {}      # keyed by id(token): Token is unhashable; the caller holds it

_LIST_FIELDS = {"ms_first_token"}
_COUNTER_FIELDS = {"fallback", "hedged", "hedge_won", "truncated", "retried"}
# per-reason fallback counters (app/graphs/common.py fallback_reason) and explanation-guard events
# (`guard_edited`, `guard_direction`, ... — app/core/explain.py finalize_explanation)
_COUNTER_PREFIXES = ("fb_", "guard_")


class _StdoutHandler(logging.StreamHandler):
    """Writes to the *current* `sys.stdout` unless a stream was set explicitly (`setStream(None)` goes back),
    so a replaced stdout (test capture, reloaders) never leaves the handler holding a closed file."""

    def __init__(self) -> None:
        self._stream = None
        super().__init__()

    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        self._stream = None if value is None or value is sys.stdout or value is sys.stderr else value


def configure() -> None:
    """Send telemetry lines to stdout as bare JSON at INFO. Idempotent: repeated calls add no second handler."""
    if not any(h.get_name() == HANDLER_NAME for h in log.handlers):
        h = _StdoutHandler()
        h.set_name(HANDLER_NAME)
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def begin(evt: str, **fields: Any) -> contextvars.Token:
    """Start a telemetry record for the current request; returns a token for `end()`."""
    data = {"evt": evt, **fields, "_t0": perf_counter()}
    tok = _ctx.set(data)
    _open[id(tok)] = data
    return tok


def add(key: str, value: Any) -> None:
    """Record a field on the in-flight request. No-op outside a `begin()`/`end()` pair.

    List fields (`ms_first_token`) append; counter fields (`fallback`, `hedged`, `hedge_won`, `truncated`,
    `retried`, and every `fb_<reason>` / `guard_<event>`) accumulate; everything else (`cards`, `error`, ...) is overwritten.
    """
    data = _ctx.get()
    if data is None:
        return
    if key in _LIST_FIELDS:
        data.setdefault(key, []).append(value)
    elif key in _COUNTER_FIELDS or key.startswith(_COUNTER_PREFIXES):
        data[key] = data.get(key, 0) + value
    else:
        data[key] = value


def end(token: contextvars.Token, **fields: Any) -> None:
    """Finish the request: compute `ms_total`, log one JSON line, and reset the context.

    Works from any Context (the record is found by its token). Never raises — a logging failure must not
    break the request it is describing.
    """
    try:
        data = _open.pop(id(token), None)
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
        try:
            _ctx.reset(token)
        except ValueError:               # token from another Context: that Context ends with its request
            pass
