import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

import app.llm as llm
from pipeline.llm import LLMError, Target


def sse(*chunks, done=True):
    lines = [f"data: {json.dumps({'choices': [{'delta': d}]})}" for d in chunks]
    if done:
        lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def targets(monkeypatch, primary="p", fallback="f"):
    t = lambda m: Target("x", "http://llm.test/v1", None, m, 5.0)  # noqa: E731
    monkeypatch.setattr(llm, "load_targets", lambda task: (t(primary), t(fallback) if fallback else None))


def collect(task, transport):
    async def run():
        return [tok async for tok in llm.astream_text(task, [{"role": "user", "content": "x"}], transport=transport)]
    return asyncio.run(run())


def test_stream_yields_content_and_skips_reasoning(monkeypatch):
    targets(monkeypatch)
    body = sse({"reasoning_content": "hmm"}, {"content": "산미가 "}, {"content": "좋아요"})
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    assert collect("explain", tr) == ["산미가 ", "좋아요"]


def test_stream_falls_back_before_first_token(monkeypatch):
    targets(monkeypatch)

    def handler(r):
        model = json.loads(r.content)["model"]
        if model == "p":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=sse({"content": "폴백"}))
    assert collect("explain", httpx.MockTransport(handler)) == ["폴백"]


def test_empty_stream_falls_back_and_total_failure_raises(monkeypatch):
    targets(monkeypatch)
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=sse({"reasoning_content": "only thinking"})))
    with pytest.raises(LLMError):
        collect("explain", tr)


def test_stream_broken_after_output_raises(monkeypatch):
    targets(monkeypatch, fallback=None)

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse({"content": "부분"}, done=False).encode()
            raise httpx.ReadError("connection lost")

    tr = httpx.MockTransport(lambda r: httpx.Response(200, stream=Broken()))
    with pytest.raises(LLMError):
        collect("explain", tr)


class Out(BaseModel):
    acidity: str | None = None


def test_achat_json_retries_then_falls_back(monkeypatch):
    targets(monkeypatch)
    calls = []

    def handler(r):
        model = json.loads(r.content)["model"]
        calls.append(model)
        if model == "p":
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"acidity": "lower"}'}}]})
    out = asyncio.run(llm.achat_json("parse_note", [{"role": "user", "content": "x"}], Out,
                                     transport=httpx.MockTransport(handler)))
    assert out == Out(acidity="lower")
    assert calls == ["p", "p", "f"]
