import os

from pipeline import settings  # noqa: F401  (loads .env)

COOKIE_NAME = "cs_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
EXPLAIN_TASK = "explain"
PARSE_NOTE_TASK = "parse_note"
PARSE_BEAN_TASK = "parse_bean"
EXPLAIN_DEADLINE_S = 12.0     # whole explanation stream, first token to last
DATA_VARIANT = os.getenv("DATA_VARIANT", "full")
# No first token from the primary LLM within this many seconds → send the same request once more and
# use whichever answers first (hedged request, ADR 0004). 0 disables.
HEDGE_AFTER_S = float(os.getenv("HEDGE_AFTER_S", "3.0"))
# The primary's FIRST request ends with no output (HTTP 429/5xx or an empty 200 stream) → wait this long and
# send the same request once more instead of failing over (the fallback target is local-only; in production
# that meant the template). Shares the one-extra-request budget with the hedge. EARLY_RETRY=0 disables.
EARLY_RETRY = os.getenv("EARLY_RETRY", "1") == "1"
EARLY_RETRY_BACKOFF_S = float(os.getenv("EARLY_RETRY_BACKOFF_S", "1.0"))


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() == "true"


def cookie_samesite() -> str:
    return os.getenv("COOKIE_SAMESITE", "lax").lower()


def allowed_origins() -> list[str]:
    origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("ALLOWED_ORIGINS에 '*'는 쓸 수 없어요 (쿠키 인증과 함께 쓰면 안전하지 않음)")
    return origins
