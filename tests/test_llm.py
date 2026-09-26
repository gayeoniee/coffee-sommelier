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


def test_non_json_or_malformed_200_falls_back():
    def fn(body, req):
        if body["model"] == "html":
            return httpx.Response(200, text="<html>gateway</html>")
        if body["model"] == "shape":
            return httpx.Response(200, json={"choices": "oops"})
        return reply("from-fallback")

    for primary in ("html", "shape"):
        c = LLMClient(target(primary), fallback=target("fb"), transport=transport(fn))
        assert c.chat([{"role": "user", "content": "x"}]) == "from-fallback"


def test_embedder_malformed_response_raises_llm_error():
    for resp in (httpx.Response(200, text="not json"), httpx.Response(200, json={"data": [{"index": 0}]})):
        e = Embedder(target("bge-m3"), transport=transport(lambda b, r, resp=resp: resp))
        with pytest.raises(LLMError):
            e.embed(["a"])


def emb_target(**kw):
    return Target("nvidia", "http://llm.test/v1", "k", "nvidia/emb", 5.0, **kw)


def emb_reply(body, dim=4):
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(i + 1)] * dim}
                                              for i in range(len(body["input"]))]})


def test_asymmetric_embedder_sends_input_type_and_batches():
    seen = []

    def fn(body, req):
        seen.append(body)
        return emb_reply(body)

    e = Embedder(emb_target(asymmetric=True, batch=2), transport=transport(fn))
    assert len(e.embed(["a", "b", "c"])) == 3
    assert [b["input"] for b in seen] == [["a", "b"], ["c"]]
    assert all(b["input_type"] == "passage" and b["encoding_format"] == "float" for b in seen)
    e.embed_query("q")
    assert seen[-1]["input_type"] == "query" and seen[-1]["input"] == ["q"]
    assert e.requests == 3


def test_symmetric_embedder_sends_no_input_type():
    seen = []

    def fn(body, req):
        seen.append(body)
        return emb_reply(body)

    Embedder(target("bge-m3"), transport=transport(fn)).embed_query("q")
    assert "input_type" not in seen[0]


def test_embedder_truncates_and_renormalises_matryoshka_dims():
    def fn(body, req):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3.0, 4.0, 100.0, 100.0]}]})

    e = Embedder(emb_target(dims=2), transport=transport(fn))
    assert e.embed(["a"]) == [[0.6, 0.8]]
    with pytest.raises(LLMError):
        Embedder(emb_target(dims=8), transport=transport(fn)).embed(["a"])


def test_embedder_retries_429_and_5xx_with_backoff():
    calls, sleeps = [], []

    def fn(body, req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        if len(calls) == 2:
            return httpx.Response(503, text="busy")
        return emb_reply(body)

    e = Embedder(emb_target(), transport=transport(fn), sleep=sleeps.append)
    assert len(e.embed(["a"])) == 1
    assert sleeps == [3.0, 2]


def test_embedder_gives_up_after_retries_and_on_4xx():
    sleeps = []
    e = Embedder(emb_target(), transport=transport(lambda b, r: httpx.Response(500)), sleep=sleeps.append)
    with pytest.raises(LLMError):
        e.embed(["a"])
    assert len(sleeps) == 5
    e = Embedder(emb_target(), transport=transport(lambda b, r: httpx.Response(410, text="gone")), sleep=sleeps.append)
    with pytest.raises(LLMError, match="410"):
        e.embed(["a"])


def test_embedder_rejects_count_mismatch():
    def fn(body, req):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(LLMError):
        Embedder(emb_target(), transport=transport(fn)).embed(["a", "b"])


def test_embedder_throttles_to_rpm():
    now, sleeps = [0.0], []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    e = Embedder(emb_target(rpm=30, batch=1), transport=transport(lambda b, r: emb_reply(b)), sleep=sleep,
                 clock=lambda: now[0])
    e.embed(["a", "b", "c"])
    assert sleeps == [2.0, 2.0]


def test_embed_task_is_switchable_by_env(monkeypatch):
    from pipeline.llm import embed_model, embed_task, embedder_for
    monkeypatch.delenv("EMBED_TASK", raising=False)
    assert embed_task() == "embed"
    assert embed_model() == "nvidia/nemotron-3-embed-1b"
    t = embedder_for().target
    assert (t.dims, t.asymmetric, t.provider, t.batch) == (1024, True, "nvidia", 32)
    monkeypatch.setenv("EMBED_TASK", "embed_bge_m3")
    assert embed_model() == "bge-m3"
    t = embedder_for().target
    assert (t.dims, t.asymmetric, t.provider) == (None, False, "ollama")


def test_embedded_dir_is_per_model():
    from pipeline import settings
    a, b = settings.embedded_dir("bge-m3"), settings.embedded_dir("nvidia/nemotron-3-embed-1b")
    assert a != b and a.parent == b.parent == settings.EMBEDDED_DIR
    assert b.name == "nvidia_nemotron-3-embed-1b"


def test_explain_target_disables_thinking():
    from pipeline.llm import load_targets
    primary, _ = load_targets("explain")
    assert primary.extra == {"chat_template_kwargs": {"enable_thinking": False}}


def test_payload_puts_extra_at_top_level():
    from app.llm import _payload
    from pipeline.llm import Target
    t = Target(provider="nvidia", model="m", base_url="http://x", api_key=None, timeout=1, max_tokens=5,
               extra={"chat_template_kwargs": {"enable_thinking": False}})
    p = _payload(t, [{"role": "user", "content": "hi"}], stream=True)
    assert p["chat_template_kwargs"] == {"enable_thinking": False} and p["stream"] is True
