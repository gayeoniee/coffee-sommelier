"""One OpenAI-compatible client for local Ollama and the NVIDIA API catalog."""
import json
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


def _post(target: Target, path: str, payload: dict, transport, sleep, max_retries: int = 3) -> dict:
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    with httpx.Client(base_url=target.base_url, timeout=target.timeout, transport=transport) as client:
        for attempt in range(max_retries + 1):
            try:
                r = client.post(path, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMError(f"timeout after {target.timeout}s: {target.model}") from e
            except httpx.HTTPError as e:
                raise LLMError(f"{type(e).__name__}: {e}") from e
            if r.status_code == 429 and attempt < max_retries:
                sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {target.model}: {r.text[:200]}")
            return r.json()
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
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
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


class Embedder:
    def __init__(self, target: Target, transport=None, sleep=time.sleep):
        self.target, self._transport, self._sleep = target, transport, sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        data = _post(self.target, "/embeddings", {"model": self.target.model, "input": texts},
                     self._transport, self._sleep)
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


def load_targets(task: str) -> tuple[Target, Target | None]:
    cfg = settings.load_config("models.yaml")
    spec = cfg["tasks"][task]

    def make(s: dict) -> Target:
        p = cfg["providers"][s["provider"]]
        key = os.getenv(p["api_key_env"]) if p.get("api_key_env") else None
        return Target(s["provider"], p["base_url"], key, s["model"], float(s.get("timeout", 30)),
                      int(s.get("max_tokens", 1024)), s.get("extra"))

    return make(spec), (make(spec["fallback"]) if spec.get("fallback") else None)


def client_for(task: str, **kw) -> LLMClient:
    primary, fallback = load_targets(task)
    return LLMClient(primary, fallback, **kw)


def embedder_for(task: str = "embed", **kw) -> Embedder:
    return Embedder(load_targets(task)[0], **kw)
