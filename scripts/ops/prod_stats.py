"""운영 통계: Render 로그 API에서 텔레메트리 JSON 줄을 모아 폴백 비율·첫 토큰 p50/p95·에러율을 집계한다.

사용법: `uv run python scripts/ops/prod_stats.py --hours 24`
`RENDER_API_KEY`는 환경 변수 또는 레포 루트 `.env`에서 읽는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Iterable

import httpx

RENDER_API_BASE = "https://api.render.com/v1"
DEFAULT_SERVICE_NAME = "coffee-sommelier-api"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "data" / "eval"


def load_api_key() -> str:
    """환경 변수 `RENDER_API_KEY`를 먼저 보고, 없으면 레포 루트 `.env`에서 읽는다."""
    key = os.environ.get("RENDER_API_KEY")
    if key:
        return key
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "RENDER_API_KEY":
                return value.strip()
    raise RuntimeError("RENDER_API_KEY가 환경 변수에도 .env에도 없습니다.")


def parse_events(lines: Iterable[str]) -> list[dict]:
    """로그 메시지 한 줄에서 JSON 오브젝트(첫 `{` ~ 마지막 `}`)를 뽑아 `evt` 키가 있는 것만 반환한다.

    Render 로그는 타임스탬프·레벨 접두어를 붙여 오므로, JSON 앞뒤의 잡음은 무시한다.
    JSON이 아니거나 `evt` 키가 없는 줄은 조용히 건너뛴다.
    """
    events: list[dict] = []
    for line in lines:
        start = line.find("{")
        end = line.rfind("}")
        if start == -1 or end == -1 or end < start:
            continue
        chunk = line[start : end + 1]
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "evt" not in obj:
            continue
        events.append(obj)
    return events


def _percentile(values: list[float], pct: float) -> float | None:
    """선형 보간 백분위수(numpy.percentile 기본값과 동일한 방식). 값이 없으면 None(0ms로 읽히지 않게)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    frac = rank - lower
    return ordered[lower] * (1 - frac) + ordered[upper] * frac


def summarize(events: list[dict], window: dict | None = None) -> dict:
    """텔레메트리 이벤트 목록을 집계한다.

    - `requests_by_evt`: evt별 이벤트 수
    - `fallback_rate`: 전체 카드 중 폴백으로 처리된 카드 비율(폴백 카드 합 / 전체 카드 합)
    - `first_token_ms_p50`/`first_token_ms_p95`: 모든 이벤트의 `ms_first_token`을 합친 분포
    - `error_rate`: `error: true`인 이벤트 비율
    - `window`: 호출자가 넘긴 조회 구간 메타데이터(그대로 반영, 없으면 `{}`)
    - `hedged`/`hedge_won`: 헤지 요청을 보낸 횟수 / 헤지 쪽이 이긴 횟수(로그에 해당 필드가 있을 때만)

    분모가 없으면(카드 0장, 첫 토큰 기록 없음, 이벤트 0개) 해당 지표는 0이 아니라 None — "데이터 없음"이다.
    """
    requests_by_evt: dict[str, int] = {}
    total_cards = 0
    total_fallback = 0
    first_tokens: list[float] = []
    error_count = 0

    for event in events:
        evt = event.get("evt", "unknown")
        requests_by_evt[evt] = requests_by_evt.get(evt, 0) + 1
        total_cards += int(event.get("cards") or 0)
        total_fallback += int(event.get("fallback") or 0)
        first_tokens.extend(event.get("ms_first_token") or [])
        if event.get("error"):
            error_count += 1

    total_events = len(events)
    summary = {
        "requests_by_evt": requests_by_evt,
        "fallback_rate": (total_fallback / total_cards) if total_cards else None,
        "first_token_ms_p50": _percentile(first_tokens, 0.5),
        "first_token_ms_p95": _percentile(first_tokens, 0.95),
        "error_rate": (error_count / total_events) if total_events else None,
        "window": dict(window) if window else {},
    }
    # 헤지 요청(ADR 0004) 카운터: 이를 기록하는 버전의 로그가 있을 때만 싣는다.
    if any("hedged" in e or "hedge_won" in e for e in events):
        summary["hedged"] = sum(int(e.get("hedged") or 0) for e in events)
        summary["hedge_won"] = sum(int(e.get("hedge_won") or 0) for e in events)
    return summary


def _get_service(client: httpx.Client, service_name: str) -> tuple[str, str]:
    """(service id, 그 서비스의 ownerId). owner는 서비스 응답에서 가져온다 — `/owners`의 첫 항목은 다른 팀일 수 있다."""
    resp = client.get(f"{RENDER_API_BASE}/services", params={"name": service_name, "limit": 1})
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError(f"Render 서비스 '{service_name}'를 찾을 수 없습니다.")
    service = data[0]["service"]
    return service["id"], service["ownerId"]


def fetch_logs(
    api_key: str,
    service_name: str = DEFAULT_SERVICE_NAME,
    hours: int = 24,
    *,
    client: httpx.Client | None = None,
    now: dt.datetime | None = None,
) -> list[str]:
    """Render 로그 API에서 최근 `hours`시간의 로그 메시지를 모두 가져온다(페이지네이션 포함).

    서비스 id와 그 서비스의 ownerId를 `/v1/services?name=<service_name>` 한 번으로 조회한 뒤
    `/v1/logs`를 `hasMore`가 꺼질 때까지 `nextStartTime`/`nextEndTime`으로 계속 요청한다.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0)
    try:
        service_id, owner_id = _get_service(client, service_name)

        end_time = now or dt.datetime.now(dt.timezone.utc)
        start_time = end_time - dt.timedelta(hours=hours)
        start = start_time.isoformat()
        end = end_time.isoformat()

        messages: list[str] = []
        while True:
            resp = client.get(
                f"{RENDER_API_BASE}/logs",
                params={
                    "ownerId": owner_id,
                    "resource": [service_id],
                    "startTime": start,
                    "endTime": end,
                    "limit": 100,
                    "direction": "forward",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            messages.extend(entry["message"] for entry in data.get("logs", []))
            if not data.get("hasMore"):
                break
            start = data["nextStartTime"]
            end = data["nextEndTime"]
        return messages
    finally:
        if owns_client:
            client.close()


def _fmt(value: float | None, spec: str) -> str:
    return "데이터 없음" if value is None else format(value, spec)


def _format_table(summary: dict) -> str:
    lines = ["| 지표 | 값 |", "|---|---|"]
    window = summary.get("window", {})
    if window:
        lines.append(f"| 조회 구간 | {window.get('start', '?')} ~ {window.get('end', '?')} ({window.get('hours', '?')}h) |")
    total_requests = sum(summary["requests_by_evt"].values())
    by_evt = ", ".join(f"{k}={v}" for k, v in sorted(summary["requests_by_evt"].items())) or "-"
    lines.append(f"| 요청 수 | {total_requests} ({by_evt}) |")
    lines.append(f"| 폴백 비율 | {_fmt(summary['fallback_rate'], '.3f')} |")
    lines.append(f"| 첫 토큰 p50 (ms) | {_fmt(summary['first_token_ms_p50'], '.1f')} |")
    lines.append(f"| 첫 토큰 p95 (ms) | {_fmt(summary['first_token_ms_p95'], '.1f')} |")
    lines.append(f"| 에러율 | {_fmt(summary['error_rate'], '.3f')} |")
    if "hedged" in summary:
        lines.append(f"| 헤지 발사 / 헤지 승 | {summary['hedged']} / {summary['hedge_won']} |")
    return "\n".join(lines)


def run(
    hours: int,
    service_name: str = DEFAULT_SERVICE_NAME,
    api_key: str | None = None,
    output_dir: Path = OUTPUT_DIR,
    client: httpx.Client | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """로그를 가져와 집계하고 `<output_dir>/prod_stats_<date>.json`에 쓴 뒤 요약을 반환한다."""
    key = api_key or load_api_key()
    end_time = now or dt.datetime.now(dt.timezone.utc)
    start_time = end_time - dt.timedelta(hours=hours)

    lines = fetch_logs(key, service_name=service_name, hours=hours, client=client, now=end_time)
    events = parse_events(lines)
    window = {"hours": hours, "start": start_time.isoformat(), "end": end_time.isoformat()}
    summary = summarize(events, window=window)
    summary["requests"] = len(events)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"prod_stats_{end_time.date().isoformat()}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["_output_path"] = str(out_path)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="조회할 최근 시간 범위(기본 24시간)")
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME, help="Render 서비스 이름")
    args = parser.parse_args(argv)

    summary = run(hours=args.hours, service_name=args.service_name)
    print(_format_table(summary))
    print(f"\n저장: {summary['_output_path']}")


if __name__ == "__main__":
    main()
