import json
import logging

from app import telemetry


def test_end_logs_one_json_line(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:hollys")
    telemetry.add("ms_first_token", 700)
    telemetry.add("fallback", 1)
    telemetry.end(tok, cards=3)
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["evt"] == "recommend" and rec["brand"] == "brand:hollys" and rec["cards"] == 3
    assert rec["ms_first_token"] == [700] and rec["fallback"] == 1 and rec["ms_total"] >= 0


def test_add_outside_request_is_noop(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    telemetry.add("fallback", 1)          # no begin() → must not raise, and must not leak into the next request
    assert telemetry._ctx.get() is None
    tok = telemetry.begin("analyze", input="text")
    telemetry.end(tok)
    assert json.loads(caplog.records[-1].getMessage())["fallback"] == 0


def test_defaults_when_nothing_added(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("analyze", input="text")
    telemetry.end(tok, cards=1)
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["ms_first_token"] == [] and rec["fallback"] == 0
    assert "_t0" not in rec


def test_fallback_accumulates_across_multiple_adds(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.add("fallback", 1)
    telemetry.add("fallback", 1)
    telemetry.add("ms_first_token", 100)
    telemetry.add("ms_first_token", 200)
    telemetry.end(tok, cards=3)
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["fallback"] == 2 and rec["ms_first_token"] == [100, 200]


def test_hedge_counters_accumulate_across_cards(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.add("hedged", 1)
    telemetry.add("hedged", 1)
    telemetry.add("hedge_won", 1)
    telemetry.end(tok, cards=3)
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["hedged"] == 2 and rec["hedge_won"] == 1


def test_cards_field_is_overwritten_not_accumulated(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.add("cards", 1)
    telemetry.add("cards", 3)
    telemetry.end(tok)
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["cards"] == 3


def test_error_field_only_present_when_set(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.end(tok, cards=0)
    rec = json.loads(caplog.records[-1].getMessage())
    assert "error" not in rec


def test_end_swallows_a_raising_logger(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log boom")
    monkeypatch.setattr(logging.getLogger("telemetry"), "info", boom)
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.add("fallback", 1)
    telemetry.end(tok, cards=3)          # must not raise


def test_context_is_reset_after_end(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.end(tok, cards=1)
    assert telemetry._ctx.get() is None
    telemetry.add("fallback", 1)         # context gone → no-op, must not raise
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.end(tok, cards=1)
    assert json.loads(caplog.records[-1].getMessage())["fallback"] == 0     # no leftover from the stray add


def test_end_from_another_context_still_logs_the_request(caplog):
    # an async generator finalised after a client disconnect can run its `finally` in a different Context
    import contextvars
    caplog.set_level(logging.INFO, logger="telemetry")
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.add("cards", 2)
    contextvars.Context().run(telemetry.end, tok, aborted=True)       # must not raise
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["cards"] == 2 and rec["aborted"] is True
    telemetry._ctx.set(None)             # this test's own Context still holds the record; the real one is gone


def test_configure_enables_info_with_one_stdout_handler():
    lg = logging.getLogger("telemetry")
    telemetry.configure()
    telemetry.configure()                # idempotent
    assert lg.isEnabledFor(logging.INFO) and lg.propagate is False
    assert len([h for h in lg.handlers if h.get_name() == telemetry.HANDLER_NAME]) == 1
