<!-- 로컬 첫 DRY_RUN(2026-09-28, 개발 DB `coffee`/`coffee_open` 대상, 실제 수집)으로 scripts/refresh/refresh.py가 만든 report.md 그대로 — 자동 갱신 PR 본문이 이 형식이다. 설계: docs/adr/0015-automated-refresh.md -->

# 데이터 자동 갱신 리포트 2026-09-28 (DRY_RUN)

- 게이트: **통과** · 변경 있음: **예** · 소요 1124.2초
- 원본 `local:coffee` → 스테이징 `local:coffee_refresh` / `local:coffee_refresh_open` → 게시 대상 `local:coffee` / `local:coffee_open` · enrich 작업 `enrich`

## 1. 수집

| 소스 | 상태 |
|---|---|
| starbucks | ok |
| mega | ok |
| paik | ok |
| coffeebean | ok |
| compose | ok |
| hollys | ok |
| paulbassett | ok |
| ediya | ok |
| shopify | ok |
| shopify_gauged | ok |
| sca_wheel | ok |
| roasters_kr | ok |

## 2. 변경 (현재 DB 대비)

|  | 현재 | 수집 | 신규 | 삭제 | 변경 |
|---|---|---|---|---|---|
| 메뉴 | 512 | 512 | 0 | 0 | 카페인 0(20%↑ 0) · 디카페인 0 |
| 원두(라이브 소스) | 417 | 415 | 2 | 4 | 내용 변경 2 |

- 새 브랜드: 없음 · 새 로스터리/매장: 없음
- **라벨 필요 0개** — `data/curated/menu_milk_labels.yaml`에 없는 새 메뉴명. needs_review로 적재돼 라벨을 달기 전까지 추천에서 빠진다

<details><summary>신규 원두 (2)</summary>

- 커피 리브레 — [골드문트] 페루 라 플로르 데 라 팔마 게이샤
- 모모스커피 — 원두 에티오피아 사포 클래식 워시드

</details>

<details><summary>삭제 원두 (4)</summary>

- 펠트 — 코스타리카 에르바수 핀카 텔리아 산 로케 내추럴 100g
- 펠트 — 콜롬비아 비야 마리아 시드라 워시드
- 나무사이로 — 녹음
- 나무사이로 — 실속형.녹음

</details>

<details><summary>내용이 바뀐 원두 (2)</summary>

- roasters_kr:coffeelibre:8153
- roasters_kr:coffeelibre:8162

</details>

## 3. 보강·임베딩 (신규·변경 원두만)

- 대상 4개 · LLM 호출 4(실패 0, 작업 `enrich`) · 임베딩 4개(API 요청 1, 로컬 캐시 재사용 0)

- 스테이징 적재: 원두 415 · 메뉴 512 · 삭제 원두 4 / 메뉴 0 · needs_review 0

## 4. 품질 게이트

| 게이트 | 결과 | 내용 |
|---|---|---|
| size_check | 통과 | 삭제 비율 정상 |
| violations_full | 통과 | 0/108건 위반 |
| loo_acidity_full | 통과 | 0.745 vs 기준선 0.745 (허용 0.725 이상) |
| violations_open | 통과 | 0/108건 위반 |
| loo_acidity_open | 통과 | 0.525 vs 기준선 0.525 (허용 0.505 이상) |
| readme_numbers | 통과 | README 수치 12개 갱신, 불일치 0 |
| pytest | 통과 | -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html / 690 passed, 2 warnings in 44.77s |

## 5. 게시 (카탈로그 테이블만 — users·tastings 불변)

| 판 | 대상 | 상태 | 테이블별 +신규/~변경/-삭제 |
|---|---|---|---|
| full | local:coffee | 계획만(쓰기 없음) | coffees +2/~2/-4 |
| open | local:coffee_open | 계획만(쓰기 없음) | coffees +2/~2/-4 |

- PR: DRY_RUN — PR·운영 DB 쓰기 없음
