import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

import app.llm as llm
from pipeline.llm import LLMError, Target


def sse(*chunks, done=True, finish_reason=None):
    lines = []
    for i, d in enumerate(chunks):
        choice = {"delta": d}
        if finish_reason and i == len(chunks) - 1:
            choice["finish_reason"] = finish_reason
        lines.append(f"data: {json.dumps({'choices': [choice]})}")
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


# --- finish_reason == "length" (token-limit truncation) --------------------------------------------

def test_finish_reason_stop_is_unchanged(monkeypatch):
    targets(monkeypatch)
    body = sse({"content": "산미가 "}, {"content": "좋아요"}, finish_reason="stop")
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    toks = collect("explain", tr)
    assert toks == ["산미가 ", "좋아요"]
    assert llm.TRUNCATED not in toks


def test_finish_reason_length_yields_truncated_sentinel_hedged_path(monkeypatch):
    """Default HEDGE_AFTER_S > 0 routes the primary through the `_Attempt`/queue machinery."""
    targets(monkeypatch)
    body = sse({"content": "산미가 좋아요. "}, {"content": "바디는"}, finish_reason="length")
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    toks = collect("explain", tr)
    assert toks == ["산미가 좋아요. ", "바디는", llm.TRUNCATED]


def test_finish_reason_length_yields_truncated_sentinel_non_hedged_path(monkeypatch):
    """HEDGE_AFTER_S <= 0 sends `_stream_once` tokens straight through with no `_Attempt` involved."""
    targets(monkeypatch)
    monkeypatch.setattr(llm.config, "HEDGE_AFTER_S", 0)
    body = sse({"content": "산미가 좋아요. "}, {"content": "바디는"}, finish_reason="length")
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    toks = collect("explain", tr)
    assert toks == ["산미가 좋아요. ", "바디는", llm.TRUNCATED]


def run_explain(monkeypatch, stream_text):
    """Run `explain_to_stream` with a fake `deps.stream_text`, collecting the events its writer emits."""
    from app.graphs import common as graphs_common
    from app.models import Item, Profile

    events: list[dict] = []
    monkeypatch.setattr(graphs_common, "get_stream_writer", lambda: events.append)

    class Deps:
        pass

    deps = Deps()
    deps.stream_text = stream_text
    item = Item(key="coffee:1", name="Test Coffee", source="db", acidity=3, body=3, sweetness=3)
    result = asyncio.run(graphs_common.explain_to_stream(deps, item, Profile(), 0.8, {}))
    return result, events


def test_explain_to_stream_unchanged_when_not_truncated(monkeypatch):
    async def stream_text(task, messages):
        yield "산미가 좋아요."

    result, events = run_explain(monkeypatch, stream_text)
    assert result == {"key": "coffee:1", "text": "산미가 좋아요.", "fallback": False}
    assert any(e["type"] == "explain_done" and e["text"] == "산미가 좋아요." for e in events)
    assert not any(e["type"] == "explain_fallback" for e in events)


def test_explain_to_stream_trims_truncated_text_to_last_complete_sentence(monkeypatch):
    async def stream_text(task, messages):
        yield "산미가 좋아요. 바디는"
        yield llm.TRUNCATED

    result, events = run_explain(monkeypatch, stream_text)
    assert result == {"key": "coffee:1", "text": "산미가 좋아요.", "fallback": False}
    done = [e for e in events if e["type"] == "explain_done"]
    assert done == [{"type": "explain_done", "key": "coffee:1", "text": "산미가 좋아요."}]
    assert not any(e["type"] == "explain_fallback" for e in events)


def test_explain_to_stream_falls_back_when_truncated_with_no_complete_sentence(monkeypatch):
    async def stream_text(task, messages):
        yield "가격 대비 좋은 원두라 계속 설명하자면"
        yield llm.TRUNCATED

    result, events = run_explain(monkeypatch, stream_text)
    assert result["fallback"] is True
    fb = [e for e in events if e["type"] == "explain_fallback"]
    assert len(fb) == 1 and fb[0]["text"] == result["text"]
    assert not any(e["type"] == "explain_done" for e in events)


def _explain_with_counts(monkeypatch, stream_text, deadline=None):
    from app.graphs import common as graphs_common
    counts: dict[str, int] = {}
    monkeypatch.setattr(graphs_common.telemetry, "add",
                        lambda k, v: counts.__setitem__(k, counts.get(k, 0) + (v if isinstance(v, int) else 0)))
    if deadline is not None:
        monkeypatch.setattr(graphs_common.config, "EXPLAIN_DEADLINE_S", deadline)
    result, _ = run_explain(monkeypatch, stream_text)
    assert result["fallback"] is True
    return {k: v for k, v in counts.items() if k.startswith("fb_")}


def test_fallback_reason_error_before_token(monkeypatch):
    async def stream_text(task, messages):
        raise LLMError("all targets failed: HTTP 429")
        yield  # pragma: no cover

    assert _explain_with_counts(monkeypatch, stream_text) == {"fb_error_before_token": 1}


def test_fallback_reason_error_midstream(monkeypatch):
    async def stream_text(task, messages):
        yield "산미가"
        raise LLMError("broke after output")

    assert _explain_with_counts(monkeypatch, stream_text) == {"fb_error_midstream": 1}


def test_fallback_reason_timeouts(monkeypatch):
    async def silent(task, messages):
        await asyncio.sleep(1)
        yield "늦음"

    async def slow_after_first(task, messages):
        yield "산미가 "
        await asyncio.sleep(1)
        yield "좋아요."

    assert _explain_with_counts(monkeypatch, silent, deadline=0.05) == {"fb_timeout_first_token": 1}
    assert _explain_with_counts(monkeypatch, slow_after_first, deadline=0.05) == {"fb_timeout_midstream": 1}


def test_fallback_reason_truncated_empty_and_empty(monkeypatch):
    async def cut(task, messages):
        yield "문장이 끝나지 않고"
        yield llm.TRUNCATED

    async def blank(task, messages):
        yield "   "

    assert _explain_with_counts(monkeypatch, cut) == {"fb_truncated_empty": 1}
    assert _explain_with_counts(monkeypatch, blank) == {"fb_empty": 1}


def test_guard_rejected_text_falls_back_with_reason_guard(monkeypatch):
    """docs/adr/0005 3차: a complete text that copies a payload boolean shows the template instead."""
    from app.graphs import common as graphs_common
    counts: dict[str, int] = {}
    monkeypatch.setattr(graphs_common.telemetry, "add",
                        lambda k, v: counts.__setitem__(k, counts.get(k, 0) + (v if isinstance(v, int) else 0)))

    async def copies_bool(task, messages):
        yield "디카페인 음료가 false이고 "
        yield "주문 권장이 true이므로 바꿔 주문하세요."

    result, events = run_explain(monkeypatch, copies_bool)
    assert result["fallback"] is True
    assert [e["type"] for e in events if e["type"] != "explain_delta"] == ["explain_fallback"]
    assert counts["fb_guard"] == 1 and counts["guard_bool_copy"] == 1


def test_guard_edited_text_is_what_explain_done_carries(monkeypatch):
    async def three_sentences(task, messages):
        yield "산미가 손님 선호와 비슷해 잘 맞아요. 바디도 비슷해요. "
        yield "디카페인으로 바꿔 주문하면 돼요."

    result, events = run_explain(monkeypatch, three_sentences)
    final = "산미가 손님 선호와 비슷해 잘 맞아요. 바디도 비슷해요."
    assert result == {"key": "coffee:1", "text": final, "fallback": False}
    streamed = "".join(e["delta"] for e in events if e["type"] == "explain_delta")
    assert streamed.endswith("바꿔 주문하면 돼요.")           # deltas go out as generated...
    assert [e["text"] for e in events if e["type"] == "explain_done"] == [final]   # ...explain_done replaces them


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
    monkeypatch.setattr(llm.config, "EARLY_RETRY_BACKOFF_S", 0.01)
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


def test_fast_primary_failure_goes_straight_to_fallback_when_retry_disabled(monkeypatch):
    fake = FakeStreams([(0.0, LLMError("500"))], [(0.0, "폴백")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    monkeypatch.setattr(llm.config, "EARLY_RETRY", False)
    toks, _, _ = run_stream()
    assert toks == ["폴백"] and fake.calls == ["p", "f"] and counts == {}


# --- early-failure retry (ADR 0004 "폴백 원인 분해") ------------------------------------------------------

def test_fast_primary_error_is_retried_on_the_same_target(monkeypatch):
    """HTTP 429 before any token → one more request to the primary, not the (local-only) fallback."""
    fake = FakeStreams([(0.0, LLMError("HTTP 429 from p"))], [(0.05, "재시도 "), (0.0, "답")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    toks, elapsed, leftover = run_stream()
    assert toks == ["재시도 ", "답"] and elapsed < 0.4
    assert fake.calls == ["p", "p"]
    assert counts == {"retried": 1, "hedge_won": 1} and leftover == []


def test_empty_primary_stream_is_retried(monkeypatch):
    """A 200 stream that ends with no text (seen in production) is an early failure too."""
    fake = FakeStreams([(0.0, llm.TRUNCATED)], [(0.0, "두 번째")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    toks, _, leftover = run_stream()
    assert toks == ["두 번째"] and fake.calls == ["p", "p"]
    assert counts == {"retried": 1, "hedge_won": 1} and leftover == []


def test_completely_empty_primary_stream_is_retried(monkeypatch):
    fake = FakeStreams([], [(0.0, "두 번째")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.5)
    toks, _, _ = run_stream()
    assert toks == ["두 번째"] and fake.calls == ["p", "p"] and counts["retried"] == 1


def test_retry_failing_too_falls_back_and_never_hedges(monkeypatch):
    """Retry and hedge share one extra request: after a failed retry there is no hedge, only the fallback."""
    fake = FakeStreams([(0.0, LLMError("HTTP 429"))], [(0.0, LLMError("HTTP 429"))], [(0.0, "폴백")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, _, leftover = run_stream()
    assert toks == ["폴백"] and fake.calls == ["p", "p", "f"]
    assert counts == {"retried": 1} and leftover == []


def test_slow_retry_is_not_hedged_again(monkeypatch):
    fake = FakeStreams([(0.0, LLMError("HTTP 429"))], [(0.2, "늦은 재시도")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, _, _ = run_stream()
    assert toks == ["늦은 재시도"] and fake.calls == ["p", "p"]
    assert counts == {"retried": 1, "hedge_won": 1}


def test_primary_failing_after_hedge_fired_does_not_retry(monkeypatch):
    fake = FakeStreams([(0.1, LLMError("primary 500"))], [(0.2, "헤지")])
    counts = hedge_env(monkeypatch, fake, hedge_after=0.05)
    toks, _, _ = run_stream()
    assert toks == ["헤지"] and fake.calls == ["p", "p"] and "retried" not in counts


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
