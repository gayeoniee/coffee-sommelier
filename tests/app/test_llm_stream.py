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


# --- hedged requests -------------------------------------------------------------------------------

class FakeStreams:
    """Stands in for `_stream_once`: per-call scripts of (delay, token-or-exception) steps."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls: list[str] = []          # target model per call
        self.cancelled: list[int] = []      # call indexes that saw CancelledError
        self.closed: list[int] = []         # call indexes whose generator finished (any way)

    async def __call__(self, t, messages, transport):
        i = len(self.calls)
        self.calls.append(t.model)
        script = self.scripts[i] if i < len(self.scripts) else []
        try:
            for delay, step in script:
                await asyncio.sleep(delay)
                if isinstance(step, BaseException):
                    raise step
                yield step
        except asyncio.CancelledError:
            self.cancelled.append(i)
            raise
        finally:
            self.closed.append(i)


def hedge_env(monkeypatch, fake, hedge_after=0.05, fallback="f"):
    targets(monkeypatch, fallback=fallback)
    monkeypatch.setattr(llm, "_stream_once", fake)
    monkeypatch.setattr(llm.config, "HEDGE_AFTER_S", hedge_after)
    counts: dict[str, int] = {}
    monkeypatch.setattr(llm.telemetry, "add", lambda k, v: counts.__setitem__(k, counts.get(k, 0) + v))
    return counts


def run_stream():
    """Collect the stream; also report elapsed time and any tasks still alive afterwards."""
    async def run():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        toks = [tok async for tok in llm.astream_text("explain", [{"role": "user", "content": "x"}])]
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return toks, loop.time() - t0, leftover
    return asyncio.run(run())


def test_fast_primary_is_not_hedged(monkeypatch):
    fake = FakeStreams([(0.1, "빠른 "), (0.0, "답")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    toks, _, leftover = run_stream()
    assert toks == ["빠른 ", "답"]
    assert fake.calls == ["p"] and counts == {} and leftover == []


def test_slow_primary_is_hedged_and_hedge_wins(monkeypatch):
    fake = FakeStreams([(10.0, "느린 답")],                  # primary: silent for 10 s
                       [(0.2, "헤지 "), (0.0, "답")])        # hedge: answers 0.2 s after firing
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, elapsed, leftover = run_stream()
    assert toks == ["헤지 ", "답"]
    assert elapsed < 1.0
    assert fake.calls == ["p", "p"]                           # same target, not the fallback
    assert fake.cancelled == [0] and sorted(fake.closed) == [0, 1]
    assert counts == {"hedged": 1, "hedge_won": 1}
    assert leftover == []


def test_primary_can_still_win_after_hedge_fired(monkeypatch):
    fake = FakeStreams([(0.1, "원래 "), (0.0, "답")], [(10.0, "헤지")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, elapsed, leftover = run_stream()
    assert toks == ["원래 ", "답"] and elapsed < 1.0
    assert fake.cancelled == [1]
    assert counts == {"hedged": 1} and leftover == []


def test_both_hedged_attempts_fail_then_fallback_target(monkeypatch):
    fake = FakeStreams([(0.1, LLMError("primary 500"))],
                       [(0.1, httpx.ReadTimeout("hedge timeout"))],
                       [(0.0, "폴백")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, _, leftover = run_stream()
    assert toks == ["폴백"]
    assert fake.calls == ["p", "p", "f"]
    assert counts == {"hedged": 1} and leftover == []


def test_one_hedged_attempt_failing_waits_for_the_other(monkeypatch):
    fake = FakeStreams([(0.1, LLMError("primary 500"))], [(0.2, "헤지")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, _, _ = run_stream()
    assert toks == ["헤지"] and fake.calls == ["p", "p"]
    assert counts == {"hedged": 1, "hedge_won": 1}


def test_fast_primary_failure_goes_straight_to_fallback(monkeypatch):
    fake = FakeStreams([(0.0, LLMError("500"))], [(0.0, "폴백")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    toks, _, _ = run_stream()
    assert toks == ["폴백"] and fake.calls == ["p", "f"] and counts == {}


def test_hedge_disabled_sends_single_request(monkeypatch):
    fake = FakeStreams([(0.2, "느려도 "), (0.0, "하나")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0)
    toks, _, leftover = run_stream()
    assert toks == ["느려도 ", "하나"]
    assert fake.calls == ["p"] and counts == {} and leftover == []


def test_winner_breaking_after_output_raises(monkeypatch):
    fake = FakeStreams([(10.0, "x")], [(0.1, "부분"), (0.0, httpx.ReadError("lost"))])
    hedge_env(monkeypatch, fake, hedge_after=0.05)
    with pytest.raises(LLMError, match="broke after output"):
        run_stream()
    assert fake.calls == ["p", "p"]                           # no fallback once output started


def test_consumer_cancel_cleans_up_both_attempts(monkeypatch):
    fake = FakeStreams([(10.0, "x")], [(10.0, "y")])
    hedge_env(monkeypatch, fake, hedge_after=0.05)

    async def run():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                async for _ in llm.astream_text("explain", [{"role": "user", "content": "x"}]):
                    pass
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert asyncio.run(run()) == []
    assert sorted(fake.cancelled) == [0, 1]


def test_hedge_loser_http_response_is_closed(monkeypatch):
    """Real httpx path: the losing request's response stream is closed, not leaked."""
    targets(monkeypatch)
    monkeypatch.setattr(llm.config, "HEDGE_AFTER_S", 0.05)
    closed: list[bool] = []

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield sse({"content": "늦음"}).encode()

        async def aclose(self):
            closed.append(True)

    n = {"calls": 0}

    def handler(r):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(200, stream=Slow())
        return httpx.Response(200, text=sse({"content": "헤지"}))
    assert collect("explain", httpx.MockTransport(handler)) == ["헤지"]
    assert n["calls"] == 2 and closed == [True]
