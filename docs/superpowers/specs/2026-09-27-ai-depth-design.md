# AI 엔지니어링 깊이: 설명 품질 평가, 결정적 검색, 운영 관측 (설계)

2026-09-27. 완성도 로드맵 2/4. 앞: 실사용 품질(빠른 설명 + 메뉴). 뒤: 공모전 → 포트폴리오 마감.

## 1. 왜

지금 README가 증명하는 것은 "조건 위반 0", "속성 예측 정확도", "수렴", "지연"이다. 증명하지 못하는 것 세 가지:
1. **LLM 설명이 맞는 말을 하는가.** 지금은 눈으로 몇 장 본 게 전부다(2단계 리뷰에서 llama가 프로필과 반대로 말한 걸 잡은 적 있음).
2. **평가가 재현되는가.** 이웃 검색이 동점 임베딩(CQI 1,546개 중 744개가 다른 원두와 임베딩이 같다)에서 임의로 뽑혀 LOO가 재적재마다 1~3점 흔들린다.
3. **운영에서 실제로 어떻게 도는가.** 폴백 비율·첫 토큰 지연을 운영 로그로 재지 않는다. 한 번 curl로 8장 본 것뿐.

## 2. 목표 / 비목표

목표
- 설명 품질을 **숫자로**: 고정 케이스 세트 × 실제 생성 × LLM 판정(두 판정자, 일치율 포함) → `data/eval/phase2_explain_quality.json`, README 표 한 줄. 규칙으로 잡히는 결함(외국어 혼입, 문장 수, 페이로드에 없는 숫자)은 pytest 회귀 테스트로 고정.
- 이웃 검색 결정화: 같은 DB·같은 질의 → 같은 이웃. LOO를 두 번 돌려 완전 일치 확인.
- 운영 관측: 요청마다 구조화 로그(JSON 한 줄: 그래프, 노드별 소요, 첫 토큰, 폴백 여부, 브랜드/입력 종류 — 프로필·후기·이름 같은 개인 데이터 없음). Render 로그 API로 최근 24시간을 읽어 폴백 비율·첫 토큰 p50/p95를 내는 스크립트. Langfuse는 키가 있으면 켜지는 현재 방식 유지(계정은 사용자 몫).
- 향미 태그 예측 평가: 속성 LOO 옆에 태그 LOO(정밀도·재현율).

비목표
- 사람 라벨링(판정은 LLM 두 개 + 일치율로 대신하고, 불일치 사례는 리포트에 남긴다).
- 새 모델 탐색. 온라인 A/B.
- 대시보드 UI. (스크립트 + JSON + README 표.)

## 3. 설명 품질 평가

### 3.1 케이스 세트 (`data/eval/explain_cases.yaml`, 손으로 작성, 24개)
차원을 섞는다: 손님 4 페르소나(app/eval.PERSONAS) × 항목 유형(메뉴 실측 / 브랜드 원두 추정 / DB 원두 / 예측 원두) × 조건 위반 있음/없음 × 점수 높음/낮음. 각 케이스는 `Item` 필드와 `Profile`, `score`, `violation`, `prediction`(있으면)을 그대로 적어 DB 없이 `explain_messages`를 만들 수 있게 한다.

### 3.2 생성
`python -m app.eval explain_quality` — 케이스마다 운영과 같은 경로(`app.llm.astream_text(EXPLAIN_TASK, …)`, 마감 12초)로 1회 생성. 폴백이면 그대로 기록(폴백도 하나의 결과).

### 3.3 규칙 검사 (결정적, LLM 없음) — `app/core/explain_check.py`
- `foreign_words`: 한글·숫자·기본 문장부호·허용 영문 태그(페이로드의 tags) 외 라틴 단어 → 실패.
- `sentences ≤ 3`, 길이 ≤ 220자.
- `numbers_grounded`: 본문의 소수/정수(카페인 mg, 점수, 1~5 값)가 페이로드에 있는 값 집합(±0.05) 안에 있어야 한다.
- `condition_mentioned`: `violation`이 있으면 본문에 그 취지(디카페인/카페인/우유)가 들어 있어야 한다.
- `polarity`: 점수 < 40이면 "잘 맞다/추천" 류 긍정 결론 금지, ≥ 80이면 "맞지 않다" 류 부정 결론 금지(간단한 어휘 목록).

### 3.4 LLM 판정
판정자 2개(`judge`=deepseek-v4.1-flash, `judge2`=nemotron, 둘 다 thinking 끔)에게 페이로드 사실 + 설명을 주고 JSON으로: `contradiction`(프로필·항목 수치와 모순, bool + 근거), `hallucination`(페이로드에 없는 사실, bool + 근거), `helpful`(1~5). 결과: 케이스별 두 판정, 일치율(Cohen κ는 24개라 불안정 → 단순 일치율), 규칙 검사 결과.
합격 기준(README에 적을 수치): 규칙 검사 전부 통과 비율, 두 판정자 모두 contradiction=false 비율, helpful 평균.

### 3.5 회귀 테스트
`tests/app/test_explain_check.py`: 규칙 검사 함수 자체의 단위 테스트(모순·외국어·근거 없는 숫자 예문). 실제 LLM은 pytest에서 부르지 않는다. 최근 평가의 생성문을 `data/eval/phase2_explain_quality.json`에 남기므로, 프롬프트를 바꾸면 이 커맨드를 다시 돌리고 표를 갱신하는 것이 규칙(ADR에 명시).

## 4. 결정적 검색
- `Repo.neighbors`: `ORDER BY embedding <=> v, id`. `random_coffee_ids_for_loo`도 이미 `ORDER BY id` + 시드 샘플이니 그대로.
- 확인: `loo`를 연속 두 번 → JSON 완전 일치(`data/eval/phase2_loo_repro.json`에 두 실행의 해시).
- HNSW는 근사 검색이므로 "같은 인덱스·같은 질의"에서의 결정성만 주장한다(ADR에 명시). 위 변경으로 LOO 수치가 바뀌면 README 갱신.

## 5. 태그 예측 평가
- `loo`에 `tags` 항목 추가: 태그가 있는 대상만, `predict_from_neighbors`가 낸 태그 집합 vs 실제 태그 집합의 정밀도·재현율·F1(마이크로). 카테고리 수준(`tag_to_cat`)도 함께.

## 6. 운영 관측
- `app/telemetry.py`: 요청 컨텍스트(`contextvars`)에 타이밍을 모으고 스트림 끝에 `logging`으로 JSON 한 줄(`{"evt":"recommend","brand":"brand:hollys","ms_total":…, "ms_first_token":[…], "fallback":1, "cards":3}` / `analyze`는 `input":"text|coffee_id"`). 스트림 `done` 이벤트 뒤에 기록. 개인 데이터 없음.
- 호출 지점: `app/graphs/common.explain_to_stream`(첫 토큰·폴백), `api.stream`(총 시간).
- `scripts/ops/prod_stats.py`: Render 로그 API(`RENDER_API_KEY`, 서비스 id는 이름으로 조회)로 최근 N시간 로그를 받아 `evt` 줄만 파싱 → 폴백 비율, 첫 토큰 p50/p95, 요청 수/그래프 → `data/eval/prod_stats_<date>.json` + 표 출력. README에 "운영 24시간" 표.
- 테스트: 로그 한 줄 생성 단위 테스트(그래프 실행 후 caplog), 파서 테스트(픽스처 로그).

## 7. 문서
- ADR 0005 `explain-quality-eval.md`(판정 설계, 한계), ADR 0006 `deterministic-neighbors.md`. README "2단계 결과"에 설명 품질·태그 LOO·운영 통계 행 추가.

## 8. 검증
- `uv run pytest -q` 전부 통과. `python -m app.eval explain_quality|loo|prod_stats` 실행 결과 커밋. 운영 배포 후 24시간 뒤 `prod_stats` 1회(사용자 복귀 전에 시간이 안 되면 수집 가능한 만큼).
