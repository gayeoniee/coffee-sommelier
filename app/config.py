import os

from pipeline import settings  # noqa: F401  (loads .env)

COOKIE_NAME = "cs_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
EXPLAIN_TASK = "explain"
PARSE_NOTE_TASK = "parse_note"
PARSE_BEAN_TASK = "parse_bean"
EXPLAIN_DEADLINE_S = 12.0     # whole explanation stream, first token to last


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() == "true"


def cookie_samesite() -> str:
    return os.getenv("COOKIE_SAMESITE", "lax").lower()


def allowed_origins() -> list[str]:
    origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("ALLOWED_ORIGINS에 '*'는 쓸 수 없어요 (쿠키 인증과 함께 쓰면 안전하지 않음)")
    return origins
