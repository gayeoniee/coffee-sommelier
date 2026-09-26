"""One OpenAI-compatible client for local Ollama and the NVIDIA API catalog."""
import json
import math
import os
import re
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from pipeline import settings


class LLMError(Exception):
    pass


@dataclass
class Target:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    timeout: float
    max_tokens: int = 1024
    extra: dict | None = None
    # embedding-only options (config/models.yaml embed tasks)
    dims: int | None = None         # Matryoshka: keep the first `dims` values and renormalise
    asymmetric: bool = False        # send input_type "passage" (documents) / "query" (runtime text)
    batch: int = 32                 # inputs per request
    rpm: float | None = None        # client-side request-rate cap


def _backoff(r: httpx.Response | None, attempt: int) -> float:
    after = r.headers.get("retry-after") if r is not None else None
    try:
        return min(float(after), 60.0) if after else min(2 ** attempt, 30)
    except ValueError:
        return min(2 ** attempt, 30)


def _post(target: Target, path: str, payload: dict, transport, sleep, max_retries: int = 3,
          retry_transient: bool = False) -> dict:
    """POST with retries on 429; with retry_transient also on 5xx, timeouts and connection errors
    (batch jobs such as embedding, where a retry is cheaper than a failed run)."""
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    with httpx.Client(base_url=target.base_url, timeout=target.timeout, transport=transport) as client:
        for attempt in range(max_retries + 1):
            try:
                r = client.post(path, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                if retry_transient and attempt < max_retries:
                    sleep(_backoff(None, attempt))
                    continue
                raise LLMError(f"timeout after {target.timeout}s: {target.model}") from e
            except httpx.HTTPError as e:
                if retry_transient and attempt < max_retries:
                    sleep(_backoff(None, attempt))
                    continue
                raise LLMError(f"{type(e).__name__}: {e}") from e
            if (r.status_code == 429 or (retry_transient and r.status_code >= 500)) and attempt < max_retries:
                sleep(_backoff(r, attempt))
                continue
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {target.model}: {r.text[:200]}")
            try:
                return r.json()
            except ValueError as e:
                raise LLMError(f"non-JSON response from {target.model}: {r.text[:200]}") from e
    raise LLMError(f"rate limited: {target.model}")


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


class LLMClient:
    def __init__(self, primary: Target, fallback: Target | None = None, transport=None, sleep=time.sleep):
        self.primary, self.fallback = primary, fallback
        self._transport, self._sleep = transport, sleep
        self.calls = 0
        self.last_model: str | None = None

    def _chat_once(self, target: Target, messages: list[dict]) -> str:
        payload = {"model": target.model, "messages": messages, "max_tokens": target.max_tokens, "temperature": 0}
        if target.extra:
            payload.update(target.extra)
        self.calls += 1
        data = _post(target, "/chat/completions", payload, self._transport, self._sleep)
        try:
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            raise LLMError(f"malformed chat response from {target.model}: {e!r}") from e
        if not content or not content.strip():
            raise LLMError(f"empty content from {target.model}")
        self.last_model = target.model
        return content

    def chat(self, messages: list[dict]) -> str:
        try:
            return self._chat_once(self.primary, messages)
        except LLMError:
            if self.fallback is None:
                raise
            return self._chat_once(self.fallback, messages)

    def chat_json(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        last: Exception | None = None
        for _ in range(2):
            raw = self.chat(messages)
            try:
                return schema.model_validate(extract_json(raw))
            except (ValueError, ValidationError) as e:
                last = e
        raise LLMError(f"invalid JSON after retry: {last}")


def truncate_normalize(v: list[float], dims: int) -> list[float]:
    """Matryoshka slice: the first `dims` values, rescaled to unit length."""
    head = v[:dims]
    n = math.sqrt(sum(x * x for x in head))
    return [x / n for x in head] if n else head


class Embedder:
    """Embeds in requests of target.batch inputs. Asymmetric models (target.asymmetric) get an input_type:
    stored documents are "passage", runtime search text is "query"."""

    def __init__(self, target: Target, transport=None, sleep=time.sleep, clock=time.monotonic):
        self.target, self._transport, self._sleep, self._clock = target, transport, sleep, clock
        self._last: float | None = None
        self.requests = 0

    def _throttle(self) -> None:
        if not self.target.rpm:
            return
        gap = 60.0 / self.target.rpm
        now = self._clock()
        if self._last is not None and now - self._last < gap:
            self._sleep(gap - (now - self._last))
        self._last = self._clock()

    def _embed_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        t = self.target
        payload: dict = {"model": t.model, "input": texts}
        if t.asymmetric:
            payload.update(input_type=input_type, encoding_format="float")
        self._throttle()
        self.requests += 1
        data = _post(t, "/embeddings", payload, self._transport, self._sleep, max_retries=5, retry_transient=True)
        try:
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            raise LLMError(f"malformed embedding response from {t.model}: {e!r}") from e
        if len(vecs) != len(texts):
            raise LLMError(f"{t.model} returned {len(vecs)} embeddings for {len(texts)} inputs")
        if t.dims:
            if any(len(v) < t.dims for v in vecs):
                raise LLMError(f"{t.model} returned fewer than {t.dims} dims")
            vecs = [truncate_normalize(v, t.dims) for v in vecs]
        return vecs

    def embed(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        step = max(self.target.batch, 1)
        out: list[list[float]] = []
        for i in range(0, len(texts), step):
            out.extend(self._embed_batch(texts[i:i + step], input_type))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text], input_type="query")[0]


def load_targets(task: str) -> tuple[Target, Target | None]:
    cfg = settings.load_config("models.yaml")
    spec = cfg["tasks"][task]

    def make(s: dict) -> Target:
        p = cfg["providers"][s["provider"]]
        key = os.getenv(p["api_key_env"]) if p.get("api_key_env") else None
        return Target(s["provider"], p["base_url"], key, s["model"], float(s.get("timeout", 30)),
                      int(s.get("max_tokens", 1024)), s.get("extra"), s.get("dims"), bool(s.get("asymmetric")),
                      int(s.get("batch", 32)), s.get("rpm"))

    return make(spec), (make(spec["fallback"]) if spec.get("fallback") else None)


def client_for(task: str, **kw) -> LLMClient:
    primary, fallback = load_targets(task)
    return LLMClient(primary, fallback, **kw)


def embed_task() -> str:
    """The embed task in config/models.yaml that pipeline, app and eval all use: $EMBED_TASK or "embed".
    Stored vectors and runtime queries must come from the same model, so this is one global switch."""
    return os.getenv("EMBED_TASK") or "embed"


def embed_model(task: str | None = None) -> str:
    return settings.load_config("models.yaml")["tasks"][task or embed_task()]["model"]


def embedder_for(task: str | None = None, **kw) -> Embedder:
    return Embedder(load_targets(task or embed_task())[0], **kw)
