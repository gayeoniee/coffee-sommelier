# Coffee Sommelier 2단계 — 추천 + 기록 + 모바일 웹앱 설계

- 작성일: 2026-09-26
- 상태: 사용자 리뷰 대기
- 상위 설계: `docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md` (로드맵 2단계)
- 선행: 1단계 데이터 기반 완료(`main` b036bc7) — coffees 9,048 · reviews 7,401 · menu_items 304 · brands 10 · flavor_taxonomy 121, pgvector 적재

---

## 1. 목표와 합의 사항

### 1.1 목표
카페에서 폰으로 열어 **30초 안에** "내 조건(디카페인 등)을 지키면서 내 취향에 맞는" 음료·원두를 **근거와 함께** 추천받고, 마신 뒤 기록하면 취향이 갱신되는 웹앱. AI 개발자 포트폴리오용이며, 실제 링크로 배포해 누구나 써볼 수 있어야 한다.

### 1.2 사용자와 합의한 결정
| 항목 | 결정 |
|---|---|
| 사용자 | 여러 명. **로그인 없음 — 첫 방문 시 익명 게스트 자동 생성(쿠키)**, 닉네임은 선택 |
| 개인 카페 대응 | 원두 정보 텍스트 **입력 1칸**: 입력 중 DB 자동완성(있으면 실제 데이터) + 없으면 유사 원두 기반 **예측** |
| 취향 온보딩 | 간단 설정(조건·슬라이더·향미 칩) + **샘플 음료 3개 좋아요/별로**로 미세조정 |
| 배포 | **무료 온라인 배포** (로컬 완성 후 2단계 마지막에 배포) |
| 추천 방식 | **A: 점수가 추천, LLM은 설명만** (필터 → 점수 → 다양성 top3 → 설명) |
| 오케스트레이션 | **LangGraph** — 분기·폴백·병렬이 있는 흐름에만 사용. LangChain은 쓰지 않음(LLM 호출은 1단계 `pipeline/llm.py`) |
| 추가 기능 | **스트리밍(SSE), 트레이싱(Langfuse), 그래프 평가** 모두 포함 |
| 배포 임베딩 | **NVIDIA 임베딩 모델로 DB 전체 재임베딩** (서버에서 bge-m3를 돌리지 않음) |
| 기술 선택 근거 | **ADR 문서 + 프로젝트 내 실측 수치**로 증명 (FastAPI, LangGraph) |

### 1.3 범위 밖 (YAGNI)
친구·소셜, 지도·카페 검색, 푸시 알림, 사진 업로드(3단계), 채팅 에이전트·대화 메모리/checkpointer(4단계), 이메일/소셜 로그인.

### 1.4 성공 기준
1. 배포 링크에서 게스트로 온보딩 → 브랜드 추천 → 원두 텍스트 분석 → 기록 → 취향 변화 확인까지 폰에서 끝까지 동작.
2. 추천 결과 카드가 1초 안에 뜨고, 설명은 스트리밍으로 이어서 나온다(LLM 실패 시 템플릿 설명).
3. 평가 지표: **조건 위반율 0%**, 원두 예측 leave-one-out ±1 이내 비율, 학습 수렴(모의 사용자 10회 기록 후 프로필 오차 감소)이 README에 수치로 기록된다.
4. ADR 2건과 실측(병렬 vs 순차 설명 생성 시간, 첫 글자 표시 시간, 폴백 테스트, Langfuse 노드별 시간)이 문서에 있다.

---

## 2. 아키텍처

```
[Next.js PWA]  — Vercel
   │ REST + SSE
[FastAPI (async)] — Render
   ├─ api/        엔드포인트, 게스트 쿠키
   ├─ graphs/     LangGraph: analyze_bean, recommend, log_tasting
   ├─ core/       순수 도메인 함수: 필터·점수·예측·프로필 학습·파싱 규칙
   ├─ repo/       DB 접근 (psycopg, async)
   └─ pipeline/llm.py (1단계) — Ollama(로컬 개발) / NVIDIA(배포)
[Postgres + pgvector] — 로컬 Docker / 배포 Neon
[Langfuse Cloud (free)] — 그래프 트레이싱
```

원칙:
- **그래프 노드는 `core/` 함수의 얇은 래퍼**다. 로직은 그래프 없이 단위 테스트하고, 그래프는 흐름(분기·폴백·병렬)만 책임진다.
- 단순 조회(브랜드 목록, 자동완성, 내 정보)는 그래프를 쓰지 않는다.
- 레포 구조: 기존 `pipeline/`(데이터) 옆에 `app/`(백엔드)와 `web/`(프론트엔드)를 추가한다. `app/`은 `pipeline.llm`, `pipeline.db`의 연결 설정을 재사용한다.

---

## 3. LangGraph 그래프

### 3.1 `analyze_bean` — 개인 카페 원두 분석
```
입력(텍스트 또는 coffee_id)
 → parse        : 산지·가공·로스팅·디카페인 추출 (규칙 우선, 불확실하면 LLM)
 → match_db     : coffee_id 또는 이름·로스터 정확 일치?
     ├ yes → actual     : DB 속성 사용 (신뢰도 high)
     └ no  → retrieve   : pgvector k=10, 같은 산지/가공 우선(5개 미만이면 완화)
             → predict  : 유사도 가중 평균 속성 + 신뢰도 + 공통 향미 + 근거 요약
 → score        : 조건 검사 + 취향 적합도
 → explain      : LLM 한국어 설명 (스트리밍) ── 실패/타임아웃 → template_explain
```

### 3.2 `recommend` — 프랜차이즈 브랜드 추천
```
입력(brand_key, user)
 → load_candidates : 브랜드 메뉴 + 브랜드 원두 속성 상속
 → filter          : 하드 조건 (카페인, 우유)
 → score           : 취향 적합도
 → select_top3     : 다양성 패널티(MMR)
 → explain ×3      : 병렬 fan-out (각각 폴백 포함) → fan-in
```

### 3.3 `log_tasting` — 기록과 프로필 갱신
```
입력(대상, 별점, 한 줄 후기)
 → parse_note   : LLM이 후기를 신호로 변환 (예: {"acidity": "lower", "liked_flavors": ["citrus"]}) ── 실패 → 신호 없음
 → update       : 별점 기반 이동 + 명시 신호 보정
 → summarize    : 변화 요약 문장(템플릿)
 → persist      : tastings, taste_profiles, profile_history 저장
```

### 3.4 공통
- 상태는 TypedDict, 노드는 async 함수. 설명 노드는 토큰 스트리밍을 SSE로 전달한다.
- 모든 그래프 실행은 Langfuse에 트레이스(노드별 시간·LLM 토큰·경로). 키가 없으면 트레이싱만 비활성화되고 앱은 동작한다.
- 구현 전에 사용 중인 LangGraph 버전의 API(StateGraph, 조건 엣지, 병렬 fan-out, 스트리밍)를 공식 문서로 확인하고 플랜에 버전을 고정한다.

---

## 4. 도메인 로직 (`core/`)

### 4.1 하드 조건 필터 (위반 0%)
- `caffeine_rule`: `decaf_only` → `is_decaf` 또는 `decaf_option`인 메뉴/원두만. `low` → 위 조건 또는 `caffeine_mg ≤ 100`. `any` → 제한 없음.
- `milk_ok=false` → 메뉴명 키워드(라떼, 우유, 밀크, 크림, 카푸치노, 플랫화이트, 모카 등)로 판별한 우유 음료 제외.
- 필터에서 빠진 항목은 점수 계산·설명 대상이 아니다.

### 4.2 취향 적합도 (0~100%)
- 속성 적합도: 산미·바디·단맛 각각 `1 − |선호 − 값| / 4`, 값이 없는 속성은 제외 후 재정규화.
- 향미 적합도: 대상의 태그를 SCA 1·2단계 카테고리로 묶은 벡터와 사용자 `flavor_weights`의 코사인 유사도. 태그가 없으면 속성 적합도만 사용.
- 최종 = 속성 0.6 + 향미 0.4. 우유 음료는 산미 가중치를 절반으로 낮춘다(우유가 산미를 누름).
- 신뢰도 라벨: DB 실측 high / 브랜드 원두 상속 medium / 예측은 이웃 편차로 high·medium·low.
- top3 다양성: MMR(λ=0.7), 유사도는 속성+향미 벡터 코사인.

### 4.3 원두 예측 (RAG)
- 이웃 k=10, 유사도 가중 평균으로 산미·바디·단맛 예측(값 없는 이웃은 해당 속성에서 제외).
- 신뢰도: 가중 표준편차 ≤0.7 high, ≤1.2 medium, 그 외 low. 유효 이웃 3개 미만이면 low.
- 향미: 이웃의 30% 이상에 등장한 태그(가중 빈도 순, 최대 5개).
- 근거: "유사 원두 10개 중 8개에서 시트러스 언급" 형태의 요약만. **리뷰 원문은 응답에 넣지 않는다.**
- 임베딩 실패 시: 파싱된 산지·가공이 같은 원두들의 평균으로 대체(신뢰도 low).

### 4.4 프로필 학습
- 별점 r → 신호 s = (r − 3) / 2 ∈ [−1, 1]. 학습률 η = 1 / (n + 2) (n = 누적 기록 수, 온보딩 샘플은 n에 포함).
- 좋음(s>0): 각 속성 `선호 += η·s·(값 − 선호)`, 향미 가중치는 대상 카테고리 쪽으로 `+η·s`.
- 별로(s<0): 대상 값이 선호와 가까울수록(차이 ≤2) 반대 방향으로 밀어냄: `선호 −= η·|s|·sign(값 − 선호)·(1 − |값 − 선호|/2)`. 향미 가중치는 `−η·|s|`.
- 후기 명시 신호: `"lower"/"higher"`는 해당 속성 ±0.5를 별도 적용(학습률과 무관). 선호 값은 항상 [1, 5]로 자른다.
- 매 기록 후 스냅샷을 `profile_history`에 저장.

---

## 5. 데이터 모델

### 5.1 신규 테이블
- `users`: id UUID PK, nickname text null, created_at
- `taste_profiles`: user_id PK/FK, caffeine_rule (`decaf_only|low|any`), milk_ok bool, acidity/body/sweetness real(1~5), flavor_weights jsonb(카테고리→가중치), n_updates int, updated_at
- `tastings`: id, user_id FK, coffee_id FK null, menu_item_id FK null, input_text text null, predicted jsonb null, rating smallint(1~5), note text null, parsed_signals jsonb null, created_at — `coffee_id`/`menu_item_id`/`input_text` 중 정확히 하나 필수(CHECK)
- `profile_history`: id, user_id FK, snapshot jsonb, tasting_id FK null, created_at

### 5.2 기존 데이터 변경
- **적재를 key 기반 upsert로 전환 (2단계 첫 작업):** 현재 `run_load`는 TRUNCATE CASCADE로 전체 재적재한다. 사용자 테이블이 `coffees`·`menu_items`를 참조하면 재적재 시 기록이 지워지므로, `INSERT … ON CONFLICT (key) DO UPDATE` + 사라진 key 삭제(참조 중인 행은 보존하고 경고)로 바꾼다. 사용자 테이블은 절대 TRUNCATE 대상이 아니다.
- **브랜드 원두 속성:** `data/curated/brands.yaml`에 브랜드별 일반/디카페인 원두의 acidity·body·sweetness·flavor_tags를 수기로 추가(조사 노트 근거, 약 20값). `brands` 테이블에 컬럼 추가, 메뉴는 브랜드 원두 속성을 상속(디카페인 메뉴 → 디카페인 원두 속성).
- **임베딩 모델 전환:** 배포 서버는 NVIDIA 임베딩 API를 쓰므로 DB 전체를 같은 모델로 재임베딩한다. 모델·차원은 구현 시 실측으로 확정하고(후보: `nvidia/llama-3.2-nv-embedqa-1b-v1`, `nvidia/nemotron-3-embed-1b`), `coffees.embedding` 차원을 마이그레이션한다. 전환 전후 검색 품질(leave-one-out 예측 정확도)을 bge-m3와 비교해 기록한다.

---

## 6. API

| 메서드 · 경로 | 설명 | 그래프 |
|---|---|---|
| `POST /session` | 쿠키 없으면 게스트 생성, 쿠키 설정 | — |
| `GET /me` | 프로필, 변화 이력, 최근 기록 | — |
| `PUT /me/profile` | 온보딩 설정·닉네임 저장 | — |
| `GET /onboarding/samples` | 대비되는 샘플 3개 (밝은 워시드 / 초콜릿 계열 / 발효 내추럴) | — |
| `POST /onboarding/samples` | 좋아요/별로 반영 | `log_tasting`(후기 없음) |
| `GET /brands` | 브랜드 목록 + 디카페인 정보 | — |
| `GET /coffees/search?q=` | 원두 자동완성 (이름·로스터·산지, 최대 8개) | — |
| `POST /recommend` | 브랜드 top3, SSE로 카드 → 설명 스트리밍 | `recommend` |
| `POST /analyze` | 원두 텍스트/ID 분석, SSE | `analyze_bean` |
| `POST /tastings` | 기록 + 프로필 갱신 + 변화 요약 | `log_tasting` |

SSE 이벤트: `cards`(결과 카드 배열, 즉시) 또는 `empty`(후보 없음 사유) → `explain_delta`(설명 토큰) → `explain_done` / `explain_fallback` → `done` (처리 중 예외는 `error`). 쿠키는 `HttpOnly; Secure; SameSite=Lax`, 1년 유지.

---

## 7. 화면 (모바일 우선)

1. **온보딩** (게스트 최초): ① 조건(카페인·우유) ② 취향 슬라이더(산미·바디·단맛) + 향미 칩 ③ 샘플 3개 좋아요/별로
2. **홈 "지금 어디세요?"**: [프랜차이즈] 브랜드 선택 → top3 / [개인 카페] 원두 입력 1칸(자동완성)
3. **결과 카드**: 적합도 %, 나 vs 이 커피 속성 막대, 추천 이유(템플릿 즉시 → LLM 스트리밍), 출처 표시("DB 실측" / "유사 원두 N개 기반 예측, 신뢰도"), 디카페인·추가요금·카페인 mg, [마셔봤어요]
4. **기록**: 별점 + 한 줄 후기 → 저장 후 변화 요약
5. **내 취향**: 프로필 차트, 변화 이력, 지난 기록, 닉네임 설정
- 콜드 스타트 대응: 백엔드 깨우는 동안 "깨우는 중" 화면.
- 알려진 한계(화면에 안내): 게스트 기록은 이 브라우저에만 연결되어 기기 변경·데이터 삭제 시 사라진다.

---

## 8. 오류 처리

| 상황 | 동작 |
|---|---|
| LLM 타임아웃/실패/빈 응답 | 설명 → 템플릿, 후기 파싱 → 신호 없음, 입력 파싱 → 규칙만. **추천 자체는 실패하지 않는다** |
| 429 | 1단계 클라이언트의 백오프 → 계속되면 템플릿 |
| 임베딩 실패 | 산지·가공 평균 예측(신뢰도 low) |
| 필터 후 후보 0개 | "조건에 맞는 메뉴가 없어요" + 조건 완화 안내(예: 이 브랜드는 디카페인 미제공) |
| 쿠키 없음/무효 | 새 게스트 생성 |
| Langfuse 키 없음/장애 | 트레이싱만 끔 |

---

## 9. 테스트와 평가

- **단위(`core/`)**: 필터(조건별), 점수(결측 속성 재정규화, 우유 가중), 예측(가중 평균·신뢰도·향미·임베딩 실패 대체), 학습(좋음/별로/명시 신호/클램프).
- **그래프**: 가짜 LLM으로 모든 분기(DB 일치/예측/LLM 폴백, fan-out 3개 중 1개 실패) 검증.
- **API**: FastAPI TestClient + 테스트 DB, SSE 이벤트 순서 검증, 게스트 쿠키 흐름.
- **E2E**: Playwright 스모크 1개(온보딩 → 추천 → 기록).
- **평가 명령 `python -m app.eval`** — 결과를 `data/eval/phase2_*.json`에 저장하고 README에 기록:
  1. 조건 위반율 — 고정 페르소나(디카페인+산미 선호, 저카페인+우유, 제한 없음 등) × 10개 브랜드 → **0% 필수**
  2. 원두 예측 leave-one-out — DB 원두 200개를 가리고 이웃으로 예측, 산미·바디·단맛 ±1 이내 비율 (bge-m3 vs NVIDIA 임베딩 비교 포함)
  3. 학습 수렴 — 숨은 "진짜 취향"을 가진 모의 사용자가 10회 기록할 때 프로필 오차(평균 절대 오차) 추이
- **ADR + 실측**: `docs/adr/0001-fastapi.md`, `docs/adr/0002-langgraph.md` (맥락→대안→결정→결과). 실측: 설명 3개 순차 vs 병렬 총 시간, 스트리밍 유무에 따른 첫 글자 표시 시간, 폴백 테스트, Langfuse 노드별 시간 스크린샷, LangGraph 자동 생성 다이어그램.

---

## 10. 배포

- 프론트: Next.js → **Vercel**(무료). 백엔드: FastAPI → **Render**(무료, 슬립 후 콜드 스타트 30~60초). DB: **Neon**(무료, pgvector 지원). LLM·임베딩: **NVIDIA API**. 트레이싱: **Langfuse Cloud**(무료).
- 데이터 이전: 로컬 DB → Neon 1회 적재(upsert라 재실행 안전). 수집·구조화 파이프라인은 로컬에서만 돈다.
- 환경변수: `DATABASE_URL`, `NVIDIA_API_KEY`, `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST`, `ALLOWED_ORIGINS`.
- **사용자가 직접 해야 하는 것(대체 불가):** Vercel·Render·Neon·Langfuse 계정 생성과 키 발급(각 무료). 나머지 설정·배포 명령은 구현 측이 수행한다.

---

## 11. 작업 순서 (구현 계획의 뼈대)
1. 적재 upsert 전환 + 사용자 테이블 + 브랜드 원두 속성
2. `core/` 도메인 함수(필터·점수·예측·학습) + 단위 테스트
3. LangGraph 3개 그래프 + 그래프 테스트
4. FastAPI 엔드포인트 + SSE + 게스트 세션 + Langfuse
5. Next.js 화면 5개
6. 평가 명령 + ADR + 실측
7. NVIDIA 임베딩 전환 + Neon/Render/Vercel 배포 + README 갱신

## 12. 열린 사항 (구현 중 확인)
- LangGraph 현재 버전 API와 스트리밍 방식(플랜 작성 시 문서 확인 후 고정).
- NVIDIA 임베딩 모델의 차원·무료 호출 한도, 9,048건 재임베딩 소요 시간.
- 설명 생성 모델: `nemotron-3-super-120b`(프로브 5초) 기본, 한국어 품질이 부족하면 `deepseek-v4.1-flash`로 교체 — 실측 후 확정.
- Render 무료 등급의 메모리 한도 안에서 FastAPI + LangGraph가 도는지 확인.
