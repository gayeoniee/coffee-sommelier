import json

import httpx
import pytest
from pydantic import BaseModel

from pipeline.llm import Embedder, LLMClient, LLMError, Target, extract_json, load_targets


def target(model, provider="p"):
    return Target(provider, "http://llm.test/v1", "k", model, 5.0)


def transport(fn):
    def handler(request):
        return fn(json.loads(request.content), request)
    return httpx.MockTransport(handler)


def reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_chat_sends_openai_payload_and_returns_content():
    seen = {}

    def fn(body, req):
        seen.update(body=body, url=str(req.url), auth=req.headers.get("authorization"))
        return reply("hi")

    c = LLMClient(target("m"), transport=transport(fn))
    assert c.chat([{"role": "user", "content": "x"}]) == "hi"
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m" and seen["body"]["temperature"] == 0
    assert c.calls == 1 and c.last_model == "m"


def test_fallback_on_error_timeout_and_empty_content():
    def fn(body, req):
        if body["model"] == "err":
            return httpx.Response(500, text="boom")
        if body["model"] == "slow":
            raise httpx.ReadTimeout("slow", request=req)
        if body["model"] == "empty":
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "..."}}]})
        return reply("from-fallback")

    for primary in ("err", "slow", "empty"):
        c = LLMClient(target(primary), fallback=target("fb"), transport=transport(fn))
        assert c.chat([{"role": "user", "content": "x"}]) == "from-fallback"
        assert c.last_model == "fb"


def test_error_without_fallback_raises():
    c = LLMClient(target("err"), transport=transport(lambda b, r: httpx.Response(500)))
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "x"}])


def test_retries_429_with_backoff():
    calls, sleeps = [], []

    def fn(body, req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else reply("ok")

    c = LLMClient(target("m"), transport=transport(fn), sleep=sleeps.append)
    assert c.chat([{"role": "user", "content": "x"}]) == "ok"
    assert sleeps == [1, 2]


class Out(BaseModel):
    x: int


def test_chat_json_retries_once_on_bad_json():
    answers = iter(["not json", '<think>hmm</think> {"x": 3}'])
    c = LLMClient(target("m"), transport=transport(lambda b, r: reply(next(answers))))
    assert c.chat_json([{"role": "user", "content": "x"}], Out) == Out(x=3)


def test_chat_json_gives_up_after_retry():
    c = LLMClient(target("m"), transport=transport(lambda b, r: reply('{"x": "nope"}')))
    with pytest.raises(LLMError):
        c.chat_json([{"role": "user", "content": "x"}], Out)


def test_extract_json_finds_object():
    assert extract_json('Sure! ```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_embedder_orders_by_index():
    def fn(body, req):
        assert body["input"] == ["a", "b"]
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})

    e = Embedder(target("bge-m3"), transport=transport(fn))
    assert e.embed(["a", "b"]) == [[1.0], [2.0]]


def test_load_targets_reads_config(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    primary, fallback = load_targets("vision")
    assert (primary.provider, primary.api_key, primary.timeout) == ("nvidia", "secret", 30.0)
    assert (fallback.provider, fallback.model, fallback.api_key) == ("ollama", "qwen3.5:9b", None)
    assert load_targets("embed")[1] is None
