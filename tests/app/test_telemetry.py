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


def test_add_outside_request_is_noop():
    telemetry.add("fallback", 1)          # no begin() → must not raise


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


def test_context_is_reset_after_end():
    tok = telemetry.begin("recommend", brand="brand:sb")
    telemetry.end(tok, cards=1)
    telemetry.add("fallback", 1)         # context gone → no-op, must not raise
