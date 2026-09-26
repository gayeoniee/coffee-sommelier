import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from scripts.ops.prod_stats import _format_table, fetch_logs, parse_events, run, summarize

FIXTURES = Path(__file__).parent / "fixtures" / "render_logs"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


RAW_LINES = [
    "2026-09-26T01:00:00.000Z GET /health 200 OK",
    '2026-09-26T01:00:05.123Z app[web] INFO {"evt":"recommend","brand":"brand:hollys","cards":3,'
    '"ms_total":1200,"ms_first_token":[300,350,400],"fallback":1}',
    '2026-09-26T01:00:10.456Z app[web] INFO {"evt":"recommend","brand":"brand:starbucks","cards":2,'
    '"ms_total":900,"ms_first_token":[250,280],"fallback":0}',
    '2026-09-26T01:00:15.000Z app[web] INFO {"level":"info","msg":"session created"}',
    '2026-09-26T01:30:00.000Z app[web] INFO {"evt":"analyze","input":"text","cards":1,'
    '"ms_total":500,"ms_first_token":[200],"fallback":0}',
    '2026-09-26T01:30:05.000Z app[web] ERROR {"evt":"analyze","input":"coffee_id","cards":0,'
    '"ms_total":150,"ms_first_token":[],"fallback":0,"error":true}',
    "not json at all, no braces here",
    "trailing garbage { not valid json ] }",
]


class TestParseEvents:
    def test_extracts_only_evt_lines(self):
        events = parse_events(RAW_LINES)
        assert len(events) == 4
        assert [e["evt"] for e in events] == ["recommend", "recommend", "analyze", "analyze"]

    def test_ignores_non_json_and_json_without_evt(self):
        events = parse_events(
            [
                "plain text line",
                '{"level":"info","msg":"no evt key"}',
                "prefix { garbage",
            ]
        )
        assert events == []

    def test_handles_malformed_braces_gracefully(self):
        # A line with braces that don't form valid JSON must not raise.
        events = parse_events(["2026-01-01 INFO { this is not json }"])
        assert events == []


class TestSummarize:
    def test_expected_aggregates_from_fixture_events(self):
        events = parse_events(RAW_LINES)
        summary = summarize(events, window={"hours": 24, "start": "s", "end": "e"})

        assert summary["requests_by_evt"] == {"recommend": 2, "analyze": 2}
        # total cards = 3 + 2 + 1 + 0 = 6, total fallback = 1 + 0 + 0 + 0 = 1
        assert summary["fallback_rate"] == pytest.approx(1 / 6)
        # flattened ms_first_token = [300,350,400,250,280,200] -> sorted [200,250,280,300,350,400]
        assert summary["first_token_ms_p50"] == pytest.approx(290.0)
        assert summary["first_token_ms_p95"] == pytest.approx(387.5)
        # 1 of 4 events has error: true
        assert summary["error_rate"] == pytest.approx(0.25)
        assert summary["window"] == {"hours": 24, "start": "s", "end": "e"}

    def test_empty_events_are_none_not_zero(self):
        # no data must not read as "0% fallback, 0 ms first token"
        summary = summarize([])
        assert summary["requests_by_evt"] == {}
        assert summary["fallback_rate"] is None
        assert summary["first_token_ms_p50"] is None
        assert summary["first_token_ms_p95"] is None
        assert summary["error_rate"] is None
        assert summary["window"] == {}

    def test_events_without_cards_or_first_tokens(self):
        summary = summarize([{"evt": "analyze", "cards": 0, "ms_first_token": [], "fallback": 0, "error": True}])
        assert summary["fallback_rate"] is None and summary["first_token_ms_p50"] is None
        assert summary["error_rate"] == 1.0

    def test_table_prints_no_data_for_missing_values(self):
        table = _format_table(summarize([]))
        assert table.count("데이터 없음") == 4
        assert "0.000" not in table


def _make_mock_transport():
    """owners/services/logs 세 엔드포인트를 흉내 내는 MockTransport. logs는 2페이지로 나눠 응답한다."""
    state = {"logs_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/owners"):          # the service's own ownerId is used; the first owner may be another team
            raise AssertionError("must not guess the owner from /owners")
        if path.endswith("/services"):
            assert request.url.params["name"] == "coffee-sommelier-api"
            return httpx.Response(200, json=[{"service": {"id": "srv-test123", "ownerId": "own-svc456"},
                                              "cursor": "c1"}])
        if path.endswith("/logs"):
            assert request.url.params["ownerId"] == "own-svc456"
            assert request.url.params.get_list("resource") == ["srv-test123"]
            state["logs_calls"] += 1
            if state["logs_calls"] == 1:
                return httpx.Response(200, json=_load_fixture("page1.json"))
            assert request.url.params["startTime"] == "2026-09-26T01:00:16.000Z"
            assert request.url.params["endTime"] == "2026-09-26T02:00:00.000Z"
            return httpx.Response(200, json=_load_fixture("page2.json"))
        raise AssertionError(f"unexpected path: {path}")

    return httpx.MockTransport(handler), state


class TestFetchLogs:
    def test_paginates_until_has_more_is_false(self):
        transport, state = _make_mock_transport()
        client = httpx.Client(transport=transport, base_url="https://api.render.com/v1")

        lines = fetch_logs("fake-key", service_name="coffee-sommelier-api", hours=24, client=client)

        assert state["logs_calls"] == 2
        assert len(lines) == 7  # 4 from page1 + 3 from page2
        events = parse_events(lines)
        assert len(events) == 4


class TestRun:
    def test_run_writes_json_and_returns_summary(self, tmp_path):
        transport, _state = _make_mock_transport()
        client = httpx.Client(transport=transport, base_url="https://api.render.com/v1")
        now = dt.datetime(2026, 9, 27, 0, 0, tzinfo=dt.timezone.utc)

        summary = run(
            hours=24,
            service_name="coffee-sommelier-api",
            api_key="fake-key",
            output_dir=tmp_path,
            client=client,
            now=now,
        )

        assert summary["requests"] == 4
        assert summary["fallback_rate"] == pytest.approx(1 / 6)

        out_path = tmp_path / "prod_stats_2026-09-27.json"
        assert out_path.exists()
        saved = json.loads(out_path.read_text(encoding="utf-8"))
        assert saved["requests"] == 4
        assert saved["window"]["hours"] == 24
