# ☕ Coffee Sommelier — 내 커피 취향을 알아주는 앱

> 건강 때문에 디카페인을 마시지만 산미 있는 커피를 좋아하는 사람도, 카페에서 실패 없이 고를 수 있게.

카페에서 음료를 고를 때 **사용자 조건(카페인 등)은 반드시 지키고**, 취향(산미·바디·향미)에 맞는 선택지를 **근거와 함께** 추천하며, 마신 기록으로 점점 개인화되는 앱을 만든다. **라이브 데모: https://coffee-sommelier-psi.vercel.app** (무료 서버라 첫 접속은 30초쯤 깨우는 시간이 걸릴 수 있다)

- 공모전 제출용 오픈 데이터판(coffeereview 미포함): https://coffee-sommelier-open.vercel.app
- API(FastAPI, `/docs`): https://coffee-sommelier-api.onrender.com

![데모: 온보딩 → 추천 → 기록 → 내 취향](docs/demo.gif)

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![Next.js 16](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![Postgres + pgvector](https://img.shields.io/badge/Postgres%2017-pgvector-4169E1?logo=postgresql&logoColor=white)
![NVIDIA API](https://img.shields.io/badge/LLM-NVIDIA%20API%20%2B%20Ollama-76B900?logo=nvidia&logoColor=white)
![Deploy](https://img.shields.io/badge/deploy-Vercel%20%C2%B7%20Render%20%C2%B7%20Neon-555)

## 결과 한눈에

모든 수치는 `data/eval/*.json`(운영 스모크만 ADR)에서 옮겼고, `scripts/check_readme_numbers.py`가 README와 JSON을 대조한다. 자세한 해석은 아래 [평가 상세](#평가-상세)와 [설계 결정](docs/design-decisions.md).

| 무엇을 쟀나 | 결과 | 원자료 |
|---|---|---|
| 조건 위반율 (페르소나 4 × 브랜드 10, top3) | **0.0% — 0/108건** (메뉴 실측 8개 브랜드) | [phase2_violations.json](data/eval/phase2_violations.json) |
| 지식베이스 원두 수 | 9,456 (디카페인 199, 향미 태그 7,763) · 오픈 라이선스판 2,063(해외 Shopify 강도 표기 199 포함, [ADR 0013](docs/adr/0013-open-labels-weak-supervision.md)) | [phase2_coverage.json](data/eval/phase2_coverage.json), [open/phase2_coverage.json](data/eval/open/phase2_coverage.json) |
| 원두 예측 leave-one-out, 산미 ±1 이내 | **0.745 (n=200)** · 오픈 라이선스판 0.5126 (n=199) — 재라벨 전과 같은 대상([ADR 0010](docs/adr/0010-body-heaviness.md)) | [phase2_loo.json](data/eval/phase2_loo.json) |
| 원두 예측 leave-one-out, 바디 ±1 이내 | 0.6536 (n=153) — 200개 대상 중 텍스트에 무게감 언급이 없는 원두는 결측이라 제외([ADR 0010](docs/adr/0010-body-heaviness.md)) | [phase2_loo.json](data/eval/phase2_loo.json) |
| 예측 신뢰도별 산미 ±1 (낮음 / 보통 / 높음) | 0.1818 (n=11) / 0.7438 (n=121) / 0.8382 (n=68) | [phase2_loo.json](data/eval/phase2_loo.json) |
| LOO 향미 태그 F1 (마이크로) | 0.3659 (n=168) — 이웃 투표, 누수 없는 태그 프리 질의 임베딩 기준(이전 0.3974는 정답 태그가 섞인 저장 임베딩으로 잰 값, [ADR 0008](docs/adr/0008-learned-tag-model.md)) | [phase2_loo.json](data/eval/phase2_loo.json) |
| LOO 향미 태그 F1 (학습 모델, 같은 임베딩) | **0.7297 (n=168)** — 이웃 투표 대비 +0.364, coffeereview 파생 라벨이라 오픈판엔 안 씀 | [phase2_tag_model.json](data/eval/phase2_tag_model.json), [ADR 0008](docs/adr/0008-learned-tag-model.md) |
| LOO 산미/바디/단맛 MAE (학습 모델 vs 이웃 평균, 같은 임베딩) | **0.5735/0.7507/0.5382** vs 0.6072/0.8579/0.5547 (전체판) · 오픈판은 이 학습 모델 대신 산미·단맛에 로스터리 게이지 특징 모델([ADR 0011](docs/adr/0011-roaster-gauges-feature-model.md), 산미는 노트 단어 약한 라벨 추가 [ADR 0013](docs/adr/0013-open-labels-weak-supervision.md)), 바디는 이웃 평균(바디 학습 표본이 CQI 결측으로 1,379→29건, [ADR 0010](docs/adr/0010-body-heaviness.md)) | [phase2_attr_model.json](data/eval/phase2_attr_model.json), [ADR 0009](docs/adr/0009-learned-attribute-model.md), [ADR 0010](docs/adr/0010-body-heaviness.md) |
| 오픈판 특징 모델, 로스터리 단위 CV ±1 이내 (산미/바디/단맛) | **0.695 (n=82)** / 0.797 (n=64, 미탑재 — 이웃 평균) / 0.569 (n=72) vs 오픈 이웃 평균 0.524 / 0.766 / 0.542 · 산미는 게이지 82건 + 노트 단어 약한 라벨 186건으로 0.658→0.695(MAE 0.923→0.771), Shopify 강도 표기·로스터 보정은 이득 없음 | [phase4_open_labels.json](data/eval/open/phase4_open_labels.json), [ADR 0013](docs/adr/0013-open-labels-weak-supervision.md) |
| 3-way 비교: 전체 / 오픈 / 오픈 + 국내 로스터리 | 산미 ±1(CQI 고정 200개) 0.5 / 0.49 / 0.49(±1%p 이내는 동점 잡음) · 바디 ±1(coffeereview 고정, 채점 전용) 0.64(n=200) / 0.6595(n=185) / 0.6073(n=191) — CQI 고정 대상은 바디가 전원 결측이라 별도 대상으로 잰다([ADR 0010](docs/adr/0010-body-heaviness.md)) · 디카페인 원두 199 / 25 / 44 (세 판 모두 해외 Shopify 강도 표기 199건 포함) | [phase2_compare3.json](data/eval/phase2_compare3.json) |
| LOO 재현성 (같은 인자로 2회) | 결과 JSON sha256 동일 (`identical: true`) | [phase2_loo_repro.json](data/eval/phase2_loo_repro.json) |
| 설명 품질 (24케이스) | 규칙 통과 22/24(3차: 맛 비교를 말로 풀어 준 페이로드 + 사후 가드; 2차 재채점 18/24) · 모순 없음 판정자 2명 합의 20/22(2차 17/22) · 환각 없음 18/22(2차 14/22) · 폴백 0/24 · 오픈판 23/23·20/22·20/22, 가드 폴백 1/24 | [phase2_explain_quality.json](data/eval/phase2_explain_quality.json) |
| 원두 분석: 전체판 vs 공모전(오픈)판 (한국어 원두 문구 10개, 운영) | 향미 태그 제시 10/10 vs 3/10, 근거 2.6 vs 1.4개/원두, 속성 결측 1 vs 8/30 — 프랜차이즈 추천·설명 품질은 동일, 미지 원두 분석만 오픈판이 약함 | [phase2_analyze_compare.json](data/eval/phase2_analyze_compare.json) |
| 설명 3개 순차 vs 병렬 (지연) | 4.59초 → 2.03초 · 설명 첫 토큰 p50 0.93초 / p95 3.70초 (설명 품질 24건, 3차 실행일 NVIDIA 지연 기준; 2차 0.77/1.37초) | [phase2_bench.json](data/eval/phase2_bench.json), [phase2_explain_quality.json](data/eval/phase2_explain_quality.json) |
| 학습 수렴: 모의 사용자 200명 × 10회 기록 후 프로필 오차 | 0.7792 → 0.7117 | [phase2_convergence.json](data/eval/phase2_convergence.json) |
| 운영 통계 (Render 로그 48시간 집계) | 요청 54건, 카드 폴백 5/56(0.089) — 5건 모두 첫 토큰 전 빠른 실패(요청 전체 1.1~1.7초; NVIDIA 429·빈 응답), 첫 토큰 p50 705ms · p95 1,184.5ms. 조기 실패 재시도 추가 후 운영 조건 재현 A/B 폴백 10/140 → 0/140 ([ADR 0004](docs/adr/0004-explain-thinking.md)) | [prod_stats_2026-09-28.json](data/eval/prod_stats_2026-09-28.json), [phase2_fallback_retry.json](data/eval/phase2_fallback_retry.json) |

## 아키텍처

```mermaid
flowchart LR
    P["pipeline: collect → normalize → enrich → embed → load"] --> DB[("Postgres + pgvector")]
    B["브라우저 (Vercel)"] --> N["Next /api 프록시"] --> F["FastAPI + LangGraph<br/>recommend · analyze_bean · log_tasting"] --> DB
    F --> L["NVIDIA API (설명·파싱·임베딩)"]
```

다이어그램 2장(데이터 파이프라인, 요청 흐름과 SSE 이벤트 순서)과 노드별 설명은 [docs/architecture.md](docs/architecture.md), LangGraph 자동 생성 다이어그램은 [docs/graphs.md](docs/graphs.md).

```
pipeline/
  collect/    robots.txt 준수 + 호스트별 1초 지연, 소스별 격리(하나가 실패해도 계속), 날짜별 원본 스냅샷
  normalize/  공통 스키마, 산지·가공·로스팅 표기 통일, 디카페인 판별, 3개 스크랩 데이터 병합
  enrich.py   규칙 우선 → 빈 칸만 로컬 LLM(qwen3.5:9b) → JSON 스키마 검증 → 재개 가능한 캐시
  embed.py    nvidia/nemotron-3-embed-1b (passage/query, 2048→1024 Matryoshka), 모델별 텍스트 해시 캐시, 배치별 저장
              (로컬 bge-m3는 EMBED_TASK=embed_bge_m3, ADR 0003)
  load.py     단일 트랜잭션 적재(실패 시 롤백)
  llm.py      Ollama·NVIDIA 공용 OpenAI 호환 클라이언트: 타임아웃·로컬 폴백·빈 응답 처리·429 백오프,
              임베딩은 배치·5xx 재시도·분당 요청 제한
  gold.py     정답셋 샘플링(디카페인 우선), judge 라벨링, 값 출처별 채점, judge 간 일치도
```

### 2단계 백엔드 (추천 + 기록)

- **구조:** FastAPI + LangGraph 그래프 3개(`recommend`, `analyze_bean`, `log_tasting`) — [그래프 다이어그램](docs/graphs.md), 결정 근거 [ADR 0001](docs/adr/0001-fastapi.md) · [ADR 0002](docs/adr/0002-langgraph.md)
- **추천 방식:** 하드 조건 필터(카페인·우유) → 취향 적합도(속성 0.6 + 향미 0.4) → MMR 다양성 top3 → 설명 3개 병렬 스트리밍(실패 시 템플릿)
- **처음 보는 원두:** 규칙 파싱(불확실하면 LLM) → DB에 있으면 실측, 없으면 유사 원두 10개 가중 평균으로 예측 + 신뢰도 + 근거 요약
- **학습:** 별점(좋음 끌어당김 / 별로 밀어냄, 학습률 1/(n+2)) + 한 줄 후기에서 LLM이 뽑은 신호("산미 너무 셈" → 산미 −0.5)

### 설계 원칙
- **카페인은 절대 조건(필터), 산미·바디는 취향 점수.** 와인 v1의 "프랑스 제외 = 논리 필터"를 한 단계 발전시킨 구조.
- **데이터가 적은 도메인 대응:** 규칙으로 먼저 채우고 빈 칸만 LLM → 약 9천 건 중 5,035건만 LLM 호출(로컬, 비용 0원).
- **무료 모델 구성:** 대량 배치는 로컬 Ollama, 실시간·평가는 NVIDIA 무료 엔드포인트. 실측 결과 큰 모델 다수가 무료 등급에서 90초 이상 응답하지 않아, 모든 원격 호출에 타임아웃 + 로컬 폴백을 둔다.
- **RAG 원문은 사용자에게 그대로 보여주지 않는다.** 리뷰 원문은 LLM의 근거 입력이고, 화면에는 예측·근거 요약·추천 이유만 노출한다(2단계).

왜 이렇게 골랐는지(FastAPI·LangGraph·pgvector·필터 vs 점수·thinking 끔·임베딩 교체·결정적 정렬·판정자 2명·데이터 라이선스, 그리고 무엇이 안 됐나)는 질문-답 형식으로 [docs/design-decisions.md](docs/design-decisions.md)에 모았다.

### 알려진 한계 / 다음 할 일
- 적재는 매번 전체 삭제 후 재적재(TRUNCATE)다. **2단계에서 사용자 기록 테이블이 `coffees`를 참조하기 전에 key 기반 upsert로 바꿔야 한다.** → 해결: 지금은 key 기반 upsert이고, 소스에서 사라졌지만 기록이 참조하는 행은 지우지 않고 `active = false`로 남긴다(`pipeline/load.py`).
- 규칙 태그의 부정 표현 오탐 개선, 원본 점수의 절대 척도 보정. 일반어(초콜릿 등) 오탐은 전역 빈도 대비 lift 게이트로
  완화했다([ADR 0007](docs/adr/0007-tag-lift-gate.md)) — 그래도 (미해결 — LOO 태그 F1 0.3974)
- 리뷰 코퍼스가 영어라 한국어 질의 검색 품질이 낮다 → 2단계에서 질의 번역 또는 한국어 요약 임베딩. → 다국어 임베딩(nemotron)으로 바꾼 뒤 한국어 질의 top5가 의도에 맞게 나온다([ADR 0003](docs/adr/0003-embedding-model.md)). 번역·요약 임베딩은 하지 않았다.
- 프랜차이즈 음료 단위 수집은 8개 브랜드(스타벅스·메가·빽다방·할리스·커피빈·폴바셋·컴포즈·이디야). 이디야는 "더보기"가 robots.txt 차단 경로(`/inc/`)라 서버가 그리는 첫 카드만 검색어로 모아 41종이다([ADR 0014](docs/adr/0014-public-sources-ediya-twosome-kca-zenodo.md)). 투썸(메뉴 목록 봇 차단 — 원두 게이지만 공식 페이지에서 손으로 옮김)·블루보틀(카페 음료 메뉴 미공개)은 브랜드 원두 카드만 나온다.

## 빠른 시작

```bash
docker compose up -d db          # Postgres 17 + pgvector
uv sync
ollama pull qwen3.5:9b          # enrich(대량 구조화)용 로컬 LLM
cp .env.example .env             # NVIDIA_API_KEY (임베딩 nemotron-3-embed-1b, 설명·파싱, 평가 judge)
# Kaggle 키: ~/.kaggle/kaggle.json

uv run python -m pipeline run                    # collect → normalize → enrich → embed → load
uv run python -m pipeline query "산미 밝은 에티오피아" --decaf
uv run python -m pipeline gold-sample && uv run python -m pipeline gold-label && uv run python -m pipeline gold-score
uv run pytest -q                                 # DB 테스트 포함, 전체 실행
uv run python scripts/check_links.py             # 문서의 깨진 상대 링크 검사
uv run python scripts/check_readme_numbers.py    # README 수치 ↔ data/eval/*.json 대조
```

`run --only <stage>`로 단계별 실행, `--limit N`으로 LLM 호출 수 제한, `--retry-failed`로 실패 행 재시도. enrich·embed는 캐시로 **중단 후 이어서** 실행된다(실제로 컴퓨터 재시작·메모리 부족으로 여러 번 끊겼지만 한 건도 잃지 않았다).

API 실행: `COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --reload` → http://localhost:8000/docs

### 웹앱 (2단계 화면)

| 온보딩 | 추천 결과 | 기록 | 내 취향 |
|---|---|---|---|
| ![](docs/screenshots/onboarding.png) | ![](docs/screenshots/home.png) | ![](docs/screenshots/log.png) | ![](docs/screenshots/me.png) |

```bash
COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --port 8000   # 백엔드
cd web && cp .env.example .env.local && npm install && npm run dev            # http://localhost:3000
npm test          # 단위 테스트 (Vitest)
npm run e2e       # 온보딩→추천→기록 스모크 (백엔드·DB 실행 필요)
```
브라우저는 같은 출처의 `/api/*`만 호출하고 Next Route Handler가 FastAPI로 쿠키·SSE를 그대로 중계한다(배포 시 교차 사이트 쿠키 문제 회피).

홈 화면에 설치할 수 있는 PWA다(아이콘·매니페스트·앱 셸 서비스 워커·오프라인 페이지, `/api/*`는 캐시하지 않음). 설치 가능성 확인 기록은 [docs/screenshots/lighthouse.md](docs/screenshots/lighthouse.md).

### 배포

Vercel(웹) + Render(FastAPI 도커) + Neon(Postgres·pgvector), 모두 무료 등급·싱가포르 리전. 운영 중: 웹 https://coffee-sommelier-psi.vercel.app , API https://coffee-sommelier-api.onrender.com/health 계정 로그인(GitHub) 뒤에는 스크립트 하나로 끝난다.

```bash
npx neonctl auth && npx vercel login     # (선택) export RENDER_API_KEY=...
bash scripts/deploy/deploy_all.sh        # Neon 생성·DB 이전 → Render → Vercel → 연결 확인
```

API 이미지는 파이프라인 의존성을 뺀 356 MB, 실행 메모리 약 75 MiB(Render 한도 512 MB). 단계별 수동 절차·환경변수·콜드 스타트 대응·운영 통계(`scripts/ops/prod_stats.py`)는 [docs/deploy.md](docs/deploy.md).

## 데이터 출처·라이선스

| 소스 | 내용 | 라이선스·비고 |
|---|---|---|
| [CQI 2018](https://github.com/jldbc/coffee-quality-database), [CQI 2023](https://github.com/fatih-boyar/coffee-quality-data-CQI) | 산지·가공·산미/바디 점수 | MIT |
| Kaggle: [patkle](https://www.kaggle.com/datasets/patkle/coffeereviewcom-over-7000-ratings-and-reviews), [hanifalirsyad](https://www.kaggle.com/datasets/hanifalirsyad/coffee-scrap-coffeereview), [schmoyote](https://www.kaggle.com/datasets/schmoyote/coffee-reviews-dataset) | coffeereview.com 리뷰 스크랩 | 원 저작권은 Coffee Review에 있음. 비상업 포트폴리오 용도로만 사용, 원본 데이터는 레포에 포함하지 않음 |
| [RoasterDB 샘플](https://github.com/RoasterDB/specialty-coffee-roasterdb) | 로스터리 원두 + SCA 노트 | CC BY-NC 4.0 |
| [SCA 플레이버 휠 JSON](https://github.com/fschlz/coffee-flavor-api) | 향미 분류 체계 | © SCA/WCR 2016, CC BY-NC-ND 4.0 (원본 수정 없이 별도 한국어 매핑) |
| 스타벅스·메가MGC·빽다방·할리스·커피빈·폴바셋·컴포즈·이디야 공식 메뉴 | 음료, 카페인 mg, 디카페인(이디야는 DECAF 카테고리의 디카페인 SKU) | robots.txt 허용 범위(폴바셋은 인증서 오류로 해당 호스트만 검증 해제, 이디야는 `/inc/` 미호출), 1회 스냅샷 |
| 블루보틀 코리아 `products.json` | 원두 상품 설명 | Shopify 공개 엔드포인트 |
| 해외 Shopify 로스터 8곳 `products.json` (Kiss the Hippo·Intelligentsia·Volcanica·Ozone·Café Don Pablo·Café Britt·Fresh Roasted·Coffee Supreme, `shopify_gauged`) | 원두 사실 정보 + **로스터가 직접 붙인 강도 표기**("VIBRANT & BRIGHT"·"Low Acid"·"Body: full" → 한 사전으로 1~5, 원두 199개: 산미 170·바디 46·단맛 2, [ADR 0013](docs/adr/0013-open-labels-weak-supervision.md)) | Shopify 공개 엔드포인트, robots.txt 확인, 설명 문구 미저장 |
| 국내 로스터리 12곳(프릳츠·나무사이로·커피 리브레·1kg커피·블루보틀 코리아·앤트러사이트·펠트·빈브라더스·모모스·매뉴팩트·G로스팅·내일의커피) 상품 페이지 (`pipeline roasters-kr`) | 원두 사실 정보만(산지·가공·로스팅·디카페인·노트 단어·가격·표기된 고도/품종) + **로스터가 공개한 맛 게이지**(산미·바디·단맛, 4곳 82건 — 오픈판 특징 모델의 사람 라벨, [ADR 0011](docs/adr/0011-roaster-gauges-feature-model.md)) | robots.txt 준수, 설명 문구 미저장, 이미지 게이지는 읽지 않음, 테라로사·헬카페(둘 다 robots.txt 차단) 등 제외 |
| `data/curated/brands.yaml` | 10개 브랜드 디카페인 정보 + 하우스/디카페인 원두 값(값마다 출처 `label_source`) | 공식 페이지·뉴스 수기 정리(확인 수준 표기) |
| `data/curated/brand_beans_official.yaml` | 10개 브랜드 공식 원두 설명(이름·로스팅·블렌드·맛 문구 원문, 브랜드별 출처 URL·확인일) + 투썸 산미·바디 막대(%) | robots.txt 준수 수집, 사실만(20개 원두 중 14개 공식 맛 설명 확보, 투썸 게이지는 브라우저로 열어 손으로 옮김, [ADR 0012](docs/adr/0012-official-brand-beans.md)) |
| [한국소비자원 차음료 품질비교](https://www.consumer.go.kr/user/ftc/consumer/cnsmrBBS/79/selectInfoRptDetail.do?infoId=A1081353&cntntsId=00000566) (비교공감 제2026-8호) | 6개 브랜드 말차·녹차라떼·밀크티 12종 실측 카페인·당류(`data/curated/kca_tea_drinks_2026.yaml`) | 공공누리(제1유형 출처표시 조건 준수). 메뉴 카페인 결측 보충·교차검증 |
| [Zenodo Q그레이더 패널](https://doi.org/10.5281/zenodo.20840464) (Golovinsky 외, v1.1) | 196개 샘플 산미·단맛 강도·바디 서술 — **외부 검증 전용**([phase2_zenodo_external.json](data/eval/phase2_zenodo_external.json)) | CC BY-NC 4.0으로 취급(레코드 표기가 BY/BY-NC로 엇갈려 엄격한 쪽), 학습·적재·재배포 안 함 |

coffeereview.com 원본 사이트, 투썸플레이스 메뉴(봇 차단)는 직접 수집하지 않았다. 컴포즈커피는 처음엔 캡차로 막혀 제외했지만, 이후 공식 사이트의 영양정보 메뉴 페이지에서 수집해 위 8개 브랜드에 들어갔다.

라이선스 때문에 두 판을 운영한다. **전체판**(포트폴리오, 위 모든 소스)과 **오픈 라이선스판**(coffeereview 제외 + 국내 로스터리 사실 정보, https://coffee-sommelier-open.vercel.app). 두 판의 수치 차이는 아래 [데이터 출처별 성능](#평가-상세)과 [설계 결정 12번](docs/design-decisions.md#12-데이터-라이선스는-어떻게-정했나-전체판과-오픈판).

## 로드맵

| 단계 | 내용 | 상태 |
|---|---|---|
| **1. 데이터 기반** | 수집 → 정규화 → 구조화(규칙 + 로컬 LLM) → 임베딩 → pgvector 적재, 품질 리포트, 태깅 평가 | ✅ 완료 |
| 2. 추천 + 기록 | 취향 온보딩, 조건 필터 + 취향 점수 추천, 음용 기록, PWA | ✅ 완료 · 배포됨 |
| 3. 스캔 + 추론 + 평가 | 원두카드/메뉴 사진 Vision 추출, 근거 기반 향미 예측, 모델 비교 | 미착수 (사진 스캔·Vision은 시작하지 않음. 텍스트 입력 기반 향미 예측·설명 품질 평가·임베딩/판정자 모델 비교는 2단계에서 먼저 함) |
| 4. 에이전트 | 멀티턴 대화("아까 거보다 산미 센 걸로") | 미착수 |

설계 문서: [`docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md`](docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md)

## 설계 결정

- 면접 질문 13개와 답(수치·근거 링크 포함): [docs/design-decisions.md](docs/design-decisions.md)
- ADR: [0001 FastAPI](docs/adr/0001-fastapi.md) · [0002 LangGraph](docs/adr/0002-langgraph.md) · [0003 임베딩 모델](docs/adr/0003-embedding-model.md) · [0004 thinking 끔](docs/adr/0004-explain-thinking.md) · [0005 설명 품질 평가](docs/adr/0005-explain-quality-eval.md) · [0006 결정적 이웃 정렬](docs/adr/0006-deterministic-neighbors.md)

## 와인 v1에서 배운 점

이 레포는 원래 LangChain + Pinecone 기반 와인 추천 RAG였다(태그 [`wine-v1`](../../tree/wine-v1)). 거기서 얻은 교훈을 이번에 반영했다.
- 사이드바 필터가 프롬프트 텍스트로만 전달돼 실제로는 걸러지지 않았다 → 이번엔 **DB 필터(SQL WHERE)**로 강제한다.
- 평가 체계가 없어 "정확도 100%" 같은 주장을 검증할 수 없었다 → 이번엔 **품질 리포트와 정답셋 수치를 먼저** 만들었다.
- README에는 "맛 태그 12개"라고 적었지만 실제 적재된 태그는 2개였다 → 이번엔 README의 모든 숫자를 리포트 파일에서 옮겼다.

---

## 평가 상세

### 1단계: 지식베이스 (Postgres + pgvector)

| 항목 | 행 수 |
|---|---|
| 원두 (`coffees`, 전부 1024차원 임베딩) | **9,456** |
| └ 디카페인 원두 | 199 |
| 리뷰 텍스트 (`reviews`, RAG 근거 전용) | 7,401 |
| 프랜차이즈 메뉴 (`menu_items`, 카페인 mg 포함) | 512 (스타벅스 71 · 메가 119 · 빽다방 114 · 폴바셋 56 · 커피빈 42 · 컴포즈 42 · 이디야 41 · 할리스 27) |
| └ 디카페인 메뉴 | 129 (+ 디카페인 샷 변경 가능 162) |
| 브랜드 (`brands`, 디카페인 가능 여부·추가요금) | 10 (메뉴 실측 8 · 투썸·블루보틀은 브랜드 원두 카드만) |
| └ 원두 값 출처 (하우스·디카페인 20개) | 공식 설명 → 모델·문구 14(투썸은 산미·바디 공식 게이지) · 손 추정 6 · 오픈판은 별도 값(공식 문구 단서·특징 모델·손 추정만, 전부 추정 4) ([ADR 0012](docs/adr/0012-official-brand-beans.md#오픈판-브랜드-값)) |
| SCA 향미 택소노미 (`flavor_taxonomy`, 1·2단계 한국어) | 121 |

원두 소스별: coffeereview(Kaggle) 7,393 · CQI 1,546 · RoasterDB 100 · 블루보틀 코리아 9 · 국내 로스터리 209 (수집 217건 중 블루보틀 코리아 Shopify 상품과 URL이 같은 8건 제외)

LLM 구조화(enrich): 규칙으로 못 채운 **5,035건**을 로컬 `qwen3.5:9b`로 처리, **실패 0건**. 적재 시 키가 맞지 않아 버려진 리뷰·메뉴 0건.

아래 결측률과 enrich 건수는 국내 로스터리 107건을 더하기 전(원두 9,048건) 기준이다.

#### 필드 결측률 (원두)

| 필드 | 결측 | 비고 |
|---|---|---|
| embedding | 0.0% | |
| body | 0.8% | |
| acidity | 13.3% | 원본 점수 2,166건뿐 → 나머지는 LLM이 텍스트에서 추론 |
| origin_country | 13.7% | 블렌드·산지 미기재 |
| flavor_tags | 17.2% | CQI는 텍스트가 없어 태그 불가 |
| roast_level | 22.0% | |
| process | 45.9% | 리뷰에 가공방식 언급이 없는 경우 |
| sweetness | 46.8% | 단맛만으로는 LLM을 부르지 않음(비용 절감) |

#### 구조화 품질 평가 (정답셋 50건)

정답 라벨은 사람 대신 **enrich 모델(qwen3.5 9B)과 다른 두 모델**이 붙인 실버 라벨이다.
- judge 1: `deepseek-v4.1-flash` (NVIDIA 무료 엔드포인트, 28건 — 나머지는 타임아웃)
- judge 2: `nemotron-3-super-120b` (다른 회사 모델, 49건)

**judge 간 일치도** — 라벨 자체를 얼마나 믿을 수 있나

| 필드 | 정확 일치 | ±1 이내 |
|---|---|---|
| 산미 | 78% | 100% |
| 바디 | 75% | 100% |
| 단맛 | 67% | 96% |
| 디카페인 여부 | 100% | |
| 향미 태그 (Jaccard) | 0.81 | |

**파이프라인 값 vs 정답** (judge 2 기준, 값이 어디서 왔는지별)

| 필드 | 값 출처 | n | 정확 일치 | ±1 이내 |
|---|---|---|---|---|
| 산미 | **LLM 추론** | 23 | 52% | **96%** |
| 산미 | 원본 점수(5분위 변환) | 16 | 38% | 63% |
| 단맛 | **LLM 추론** | 30 | 77% | **100%** |
| 바디 | 원본 점수(5분위 변환) | 43 | 30% | 86% |
| 디카페인 여부 | 규칙 | 49 | 98% | |
| 향미 태그 | 규칙(키워드 매칭) | 48 | Jaccard 0.40 | |

읽는 법:
- 두 judge가 서로 잘 일치하므로(±1 이내 96~100%) 실버 라벨로 쓸 만하다.
- **LLM이 채운 값은 정답과 ±1 이내 96~100%**로 가장 정확하다.
- 원본 점수를 소스 내 5분위로 바꾼 값은 오히려 낮다. 5분위는 "스페셜티 안에서의 상대 위치"인데 judge는 절대 척도로 매기기 때문이다 → 2단계에서 절대 척도 보정 필요.
- **규칙 기반 태그(Jaccard 0.40)가 가장 약한 고리**다. 부정 표현("no bitterness")과 일반어("fresh") 오탐이 원인으로 보인다 → 개선 1순위.

원자료: `data/eval/gold_*.csv`, `gold_scores.json`, `gold_enrich_judge2_scores.json`, `gold_agreement.json`

#### 검색 예시

```text
$ python -m pipeline query "bright citrus floral Ethiopia washed" -k 5 --decaf
0.616  Decaf Ethiopia Sidamo | Old Soul Co. | Ethiopia | natural | decaf=True | acidity=2 ...
0.607  Ethiopia Sidamo Natural Water Decaf | Jackrabbit Java | Ethiopia | washed | decaf=True ...
0.598  Decaf Harfusa Ethiopia Yirgacheffe | Counter Culture Coffee | Ethiopia | washed | decaf=True ...
```
디카페인이 전체의 2%뿐이라 pgvector HNSW가 필터 후 k개를 못 채우는 문제가 있었고, `hnsw.iterative_scan = relaxed_order`로 해결했다.

### 2단계: 추천·예측·설명 평가

| 평가 (`python -m app.eval`) | 결과 |
|---|---|
| 조건 위반율 (페르소나 4 × 브랜드 10, top3) | 0.0% — 0/108건 (우유 판정은 메뉴 453종 수기 라벨 기준; 메뉴 실측 8개 브랜드) |
| 원두 예측 leave-one-out, 산미 ±1 이내 | 0.745 (n=200) |
| 원두 예측 leave-one-out, 바디 ±1 이내 | 0.6536 (n=153/200) |
| 학습 수렴: 모의 사용자 10회 기록 후 프로필 오차 | 0.7792 → 0.7117 |
| 설명 3개 순차 vs 병렬 (thinking 끔, 최대 2문장, 첫 토큰 0.81~0.99초) | 4.59초 → 2.03초 |
| 설명 품질(24케이스, `explain_quality`, [ADR 0005](docs/adr/0005-explain-quality-eval.md)) | 규칙 통과 22/24 — 3차: 산미·바디·단맛을 손님 선호와 비교한 판정을 말로 넣은 페이로드, 조건 위반 첫 문장 고정, 사후 가드(외국어 제거·2문장 자르기·빠진 위반 붙이기, 자리표시·true·항목 이름·방향 모순은 템플릿 폴백). 2차는 재채점 18/24(1차 10/22). 모순 없음 판정자 2명 합의 20/22(2차 17/22), 환각 없음 18/22(2차 14/22), 도움 평균 4.00·3.08/5 (judge2 = gpt-oss-20b, 생성 모델과 다른 벤더) |

**데이터 출처별 성능 (포트폴리오판 vs 공개·오픈 라이선스판)** — coffeereview(Kaggle) 데이터는 라이선스상 비상업 포트폴리오 용도로만 쓰므로, 그 데이터를 뺀 공개·오픈 라이선스 소스(CQI·RoasterDB·블루보틀)만으로도 같은 평가를 다시 돌렸다.

| 항목 | 포트폴리오판 (전체 소스) | 공개·오픈 라이선스판 (coffeereview 제외) |
|---|---|---|
| LOO 산미 ±1 이내 | 0.745 (n=200) | 0.5126 (n=199) |
| LOO 바디 ±1 이내 | 0.6536 (n=153) | 1.0 (n=3) |
| LOO 향미 태그 F1 (마이크로) | 0.3659 (n=168) — 이웃 투표 | 0.5294 (n=9) |
| └ 같은 임베딩, 학습 모델 | 0.7297 (n=168) | 오픈판은 라이선스상 미사용 |
| LOO 향미 카테고리 F1 | 0.6259 (n=168) — 이웃 투표, 학습 모델 0.8259 | 0.7619 (n=9) |
| 원두 수 (전체) | 9,456 | 1,655 |
| └ 향미 태그 보유 | 7,763 | 109 |
| └ 디카페인 | 199 | 6 |

태그 F1은 정답 태그(`truth.tags`)가 있는 대상만 채점한다(예측 태그 집합 vs 실제 집합, 정밀도·재현율·F1 마이크로 평균; 카테고리 F1은 `tag_to_cat`으로 묶은 집합 기준). 전체 소스판은 200개 중 168개가 향미 태그를 갖고 있어 표본이 충분하지만, 오픈 라이선스판은 대상이 대부분 CQI 원두(노트 텍스트가 없어 태그 자체가 없음)라 태그 있는 대상이 200개 중 9개뿐이다 — 이 n=9 수치는 참고용일 뿐 신뢰 구간이 넓다.

이웃 태그 예측(위 표의 "이웃 투표")은 이웃 10개 중 일정 비율(기본 30%) 이상에서 나온 향미만 채택하는데, "초콜릿"처럼 원두 전체에 흔한 태그(전역 31%)는 그 비율만으로 거의 항상 뽑혀 게이샤 같은 원두에도 붙는 문제가 있었다. 전역 등장 빈도 대비 이웃에서 얼마나 더 자주 나오는지(lift)를 반영해, 흔한 태그일수록 더 높은 비율을 요구하는 게이트를 추가했다(정밀도·F1·카테고리 F1 모두 소폭 개선 — 게이트 도입 전후·LIFT 후보 비교는 [ADR 0007](docs/adr/0007-tag-lift-gate.md), 단 그 비교는 아래에서 설명하는 "임베딩 누수"를 고치기 전 수치다).

**위 태그 F1 0.3659/0.7297은 태그가 섞이지 않은("누수 없는") 질의 임베딩 기준이다.** `pipeline/embed.py`가 원두 자기 자신의 `flavor_tags`를 그 원두의 저장 임베딩 텍스트에 이미 포함시키고 있어서, `repo.coffee_embedding(cid)`로 LOO를 돌리면 정답이 입력에 섞여 있는 채로 채점하는 셈이었다(이전 문서의 0.3974/0.6426이 그 값). 태그를 뺀 텍스트로 고정 200개 대상만 다시 임베딩한 캐시(`data/eval/loo_tagfree_query_embeddings.jsonl`)로 이제는 이웃 투표·학습 모델 둘 다 같은 누수 없는 임베딩으로 채점한다. **학습 모델(MLP, 1024→128→54)은 같은 조건에서 이웃 투표를 F1 기준 2배 가까이 이긴다**(0.7297 vs 0.3659) — 학습 데이터도 태그 프리 임베딩으로 재구성했고(활성 태그 보유 원두 7,327건 재임베딩), 학습 라벨이 coffeereview 파생이라 오픈판(`DATA_VARIANT=open`)에는 올리지 않는다. 자세한 임계값 선택·크기·지연·국내 로스터리 데이터로의 독립 검증은 [ADR 0008](docs/adr/0008-learned-tag-model.md), [phase2_tag_model.json](data/eval/phase2_tag_model.json).

**산미·바디·단맛도 같은 태그 프리 임베딩 위에서 학습형 회귀(릿지/MLP, 속성마다 5-fold CV MAE로 독립 선택)로 바꿨다.** 고정 200개 held-out을 같은 임베딩으로 채점하면(전체판) 산미 MAE **0.6233**(이웃 평균 0.7325, n=200/198) · 바디 **0.6706**(0.8709, n=153/153) · 단맛 **0.4406**(0.5653, n=99/95) — 세 속성 모두 학습 모델이 이웃 평균을 이긴다(±1 이내도 모두 개선: 0.835/0.7778/0.9293 vs 0.7273/0.634/0.8842). 2026-09-28에 국내 로스터리 공개 게이지(산미 +96건 등)까지 넣어 같은 고정 200개로 다시 학습·비교했더니 **산미만 좋아져(MAE 0.6272→0.6211, ±1 0.81→0.83) 산미만 교체**했고, 바디(0.6695→0.6740)·단맛(0.4407→0.4459)은 나빠져 기존 모델을 유지했다. 태그 모델은 학습 풀이 그대로(로스터리는 태그 학습에서 제외)라 held-out F1 0.7329로 동일 — 교체하지 않았다([phase2_retrain_2026-09-28.json](data/eval/phase2_retrain_2026-09-28.json)). **바디는 원래 coffeereview·CQI의 "body" 서브스코어를 그대로 5분위화한 값이라 무게감이 아니라 큐핑 품질 점수였다** — 리뷰 원문에서 mouthfeel 문장을 뽑아 LLM으로 무게감(1=라이트~5=헤비)을 다시 매기고(CQI는 판단할 원문이 없어 결측 처리) 모델을 다시 학습했다(스팟체크 20건 중 19건 ±1 이내 일치, [ADR 0010](docs/adr/0010-body-heaviness.md)). LOO 대상 게이트를 "산미 AND 바디"에서 "산미만"으로 고쳐서 held-out 200개는 재라벨 전과 **같은 산미 대상**이고(산미 MAE·within1이 ADR 0009와 사실상 동일), 바디는 그 200개 중 실제로 무게감 값이 있는 153개만으로 채점한다(n=153) — 표본은 8,798건 → 6,436건으로 줄었지만(CQI 결측 + 무게감 언급 없는 리뷰) MAE·±1 모두 오히려 좋아졌다. 오픈판은 coffeereview·RoasterDB를 빼고 CQI(사람 커핑)·국내 로스터리·shopify만으로 따로 학습했는데, 바디 학습 표본이 CQI 결측으로 1,379건 → 31건으로 무너져 최소 기준(300건) 미달이라 탑재하지 않고, 산미는 이제 진짜 200개짜리 held-out(재라벨 전과 동일 대상)에서 모델이 이웃 평균보다 MAE·±1 둘 다 나쁘다고 확인해(1.14/0.45 vs 1.1004/0.48) 제외 방침을 유지한다 — 그래서 **오픈판(공모전 제출본)은 이 학습 모델 대신 산미·단맛에 로스터리 공개 게이지로 학습한 해석 가능한 특징 모델을 쓰고, 바디만 이웃 평균을 쓴다**([ADR 0011](docs/adr/0011-roaster-gauges-feature-model.md)). 향미 태그도 오픈판 전용(SCA 7대 카테고리 분류기)을 시도했지만, 측정 풀 145건의 5-fold CV 카테고리 F1(0.6459)이 이웃 투표(0.6593)를 못 넘어 탑재하지 않았다. 자세한 표·크기·지연은 [ADR 0009](docs/adr/0009-learned-attribute-model.md), [ADR 0010](docs/adr/0010-body-heaviness.md), [phase2_attr_model.json](data/eval/phase2_attr_model.json), [phase2_attr_model_open.json](data/eval/phase2_attr_model_open.json), [phase2_tag_model_open_feasibility.json](data/eval/phase2_tag_model_open_feasibility.json).

LOO 수치는 한국 로스터리 데이터를 적재한 뒤 다시 돌린 값이다(처음 측정은 0.76/0.67, 0.5306/0.5816). 대상 원두와 데이터는 그대로인데 1~3%p 움직인 이유는 CQI 원두 1,546개 중 744개가 임베딩이 완전히 같은 쌍둥이(산지·지역·농장·가공 텍스트가 같음)라 이웃 10개를 고를 때 동점이 많고, 재적재로 행의 물리적 순서가 바뀌면 동점 중 뽑히는 원두가 달라지기 때문이다. **±1~3%p 차이는 이 잡음 안**이라고 보고 읽어야 한다.

LOO·compare3 수치는 임베딩을 `nvidia/nemotron-3-embed-1b`(1024차원으로 자름)로 바꾼 뒤 다시 잰 값이다. bge-m3 때는 0.75/0.65, 0.5204/0.551 — 차이가 잡음 안이라 배포 가능한 호스팅 모델로 바꿨다. 비교표와 한국어 질의 검색 예시는 [ADR 0003](docs/adr/0003-embedding-model.md).

이웃 검색(`Repo.neighbors`)은 동점(같은 유사도)일 때 id로 순서를 고정한다 — 근거와 측정은 [ADR 0006](docs/adr/0006-deterministic-neighbors.md). 같은 인덱스·같은 질의에서는 결과가 동일하다 — `loo_accuracy`를 같은 인자로 두 번 돌리면 결과 JSON이 바이트 단위로 같고(`python -m app.eval loo_repro` → `data/eval/phase2_loo_repro.json`, `identical: true`), 동점 정렬로 물리적 행 순서 의존을 제거했다. 다만 HNSW 인덱스는 다시 만들 때마다 그래프가 무작위로 구성되므로 재적재 간 동일성까지 보장하지는 않는다(잡음을 줄였을 뿐). 위 LOO 수치가 한국 로스터리 데이터 적재 전후로 갱신되며 함께 살짝 바뀐 것도 이 수정 때문이다(동점 재정렬이 바뀌어 이웃 10개 중 경계값이 달라짐 — ±1%p 내외, 위에서 말한 잡음 범위 안).

#### 3가지 비교: 전체 vs 오픈 vs 오픈 + 한국 로스터리 (`python -m app.eval compare3`)

오픈 라이선스판에 국내 로스터리 12곳(프릳츠·나무사이로·커피 리브레·1kg커피·블루보틀 코리아·앤트러사이트·펠트·빈브라더스·모모스·매뉴팩트·G로스팅·내일의커피)의 원두 **사실 정보만**(이름·산지·가공·로스팅·디카페인·향미 노트 단어·가격) 더한 판을 같이 비교했다. 설명 문구(산문)는 저장하지 않았고, robots.txt를 지켰으며 `/market/` 경로가 막힌 테라로사는 제외했다. 217건 중 블루보틀 8건은 이미 들어 있는 블루보틀 코리아(Shopify) 상품과 URL이 같아 빼고 **209건**을 적재했다.

파이프라인 처리: 향미 노트 단어(예: "초콜릿, 건무화과, 호두")를 SCA 휠 한국어 이름(`data/curated/sca_ko.yaml`)과 표기 별칭으로 규칙 매핑해 태그를 달고, 산미·바디·단맛은 노트만 있는 RoasterDB와 똑같이 LLM 보강에 맡겼다(근거가 없으면 null). 노트가 있는 94건은 모두 태그가 붙었고 산미 추정은 43건, 노트가 없는 83건(1kg커피 다수 등)은 이름·산지·가공만 임베딩된다.

LOO 대상은 세 판 모두 **같은 CQI 원두 200개로 고정**했다(사람이 매긴 커핑 점수가 있는 오픈 라이선스 원두). 바디를 무게감으로 재라벨링하면서([ADR 0010](docs/adr/0010-body-heaviness.md)) 대상 선정 게이트를 "산미 AND 바디 둘 다 not null"에서 **"산미 not null"만으로 좁혔다** — CQI의 "Body"는 애초에 무게감이 아니라 품질 점수였다는 게 이 재라벨의 요지라(위 표) CQI 원두 전부(200개 모두)의 바디를 결측(`null`)으로 비웠는데, 예전처럼 바디까지 게이트에 넣으면 CQI가 대상 후보에서 통째로 사라져 산미 비교 자체가 불가능해지기 때문이다. 그 결과 대상 200개(시드 42) 자체는 **재라벨 전과 완전히 동일**하고, 산미 ±1 이내도 0.49~0.5로 전과 잡음 안에서 같다. 다만 바디는 CQI 200개 **전원**이 결측이라 이 고정 대상으로는 정확도(±1 이내) 자체가 존재하지 않는다 — n=0은 버그가 아니라 CQI라는 소스 자체의 한계다.

**바디는 그래서 별도의 고정 대상 200개(시드 42, coffeereview_kaggle 중 무게감 라벨이 있는 원두)로 따로 잰다** — coffeereview 라벨은 채점 기준으로만 쓰고 앱·학습에는 쓰지 않는다. 오픈/오픈+로스터리판은 애초에 coffeereview_kaggle 자체를 이웃 풀에서 빼므로(`VARIANTS`) 이 라벨이 그 두 판에 새어 들어갈 일이 없다.

| 항목 | 전체 | 오픈 | 오픈 + 로스터리 |
|---|---|---|---|
| 원두 수 | 9,456 | 1,854 | 2,063 |
| └ 향미 태그 보유 | 7,763 | 256 | 377 |
| └ 디카페인 (태그 보유) | 199 (188) | 25 (21) | 44 (33) |
| LOO 산미 ±1 이내 (CQI 고정 200개) | 0.5 | 0.49 | 0.49 |
| LOO 바디 ±1 이내 (coffeereview 고정, 채점 전용) | 0.64 (n=200) | 0.6595 (n=185) | 0.6073 (n=191) |
| LOO 바디 MAE (coffeereview 고정, 채점 전용) | 0.8803 | 0.9462 | 0.9987 |
| 디카페인+산미 페르소나 후보 (근거 있음) | 199 (196) | 25 (25) | 44 (41) |

표의 모든 칸은 현재 DB(국내 로스터리 12곳 209건, 세 판 모두 해외 Shopify 강도 표기 199건 포함 — [ADR 0013](docs/adr/0013-open-labels-weak-supervision.md))로 다시 실행한 `compare3` 값이다.

디카페인+산미 페르소나(디카페인만, 산미 4.5, 과일·꽃 선호) top5 — 오픈판은 RoasterDB(해외) 4개 + 블루보틀 1개로 채워지고 5위는 산미 정보가 없어 점수 0.5375, 오픈 + 로스터리판은 **1위 모모스커피 「원두 에티오피아 구지 우라가 시코 내추럴 디카페인」(0.9113)**과 **3위 커피 리브레 「[골드문트] [디카페인] 에티오피아 예가체프 할로 베리티 내추럴」(0.8777)**이 들어와 한국에서 살 수 있는 후보가 두 곳 생긴다. 전체판에서도 모모스커피는 2위, 커피 리브레는 5위다.

해석:
- **LOO 산미 비교는 오픈판과 오픈+로스터리판 사이에서 완전히 같다(둘 다 0.49) — nemotron 임베딩에서 CQI 원두의 이웃이 100% 같은 CQI 원두라서다.** 전체판만 국내 로스터리 원두 추가로 물리적 행 순서가 바뀌어 0.5로 1%p 안에서 움직였다 — 동점 재정렬에 의한 잡음이지 실제 정확도 변화가 아니다(동점을 id로 고정한 뒤 — [ADR 0006](docs/adr/0006-deterministic-neighbors.md) — 도 행 순서 자체가 바뀌면 동점 중 뽑히는 이웃이 달라질 수 있다). **바디는 세 판 모두 ±1 0.64 안팎으로 비슷하지만 MAE는 전체판(0.8814)이 오픈(0.988)·오픈+로스터리(0.9843)보다 낮다** — 전체판만 coffeereview_kaggle을 이웃 풀에 남겨 둬서(대상 자기 자신은 제외해도 다른 coffeereview 원두는 이웃이 될 수 있다) 같은 소스끼리의 신호를 더 쓸 수 있기 때문이다. n(200/151/169)이 판마다 다른 것도 같은 이유다 — 오픈·오픈+로스터리는 후보 풀 자체가 작아 바디 값이 있는 이웃 3개(예측에 필요한 최소치)를 못 채우는 대상이 더 많다.
- **더해지는 것은 커버리지, 특히 디카페인이다.** 오픈판 디카페인 25개 → 44개(국내 19개), 태그 있는 원두 256 → 377. 한국 디카페인 사용자에게 "국내에서 살 수 있는 원두"를 추천할 수 있게 되는 것이 이 데이터의 실제 기여다.
- 다만 국내 디카페인 19개 중 7개는 노트가 없어 태그·산미가 비어 있고, 추천 점수 계산에서 근거 없음으로 빠진다.

조건 위반 판정은 추천 코드와 독립이다: 우유는 메뉴 이름을 사람이 하나씩 라벨링한(처음 302종, 메뉴 브랜드를 늘린 뒤 지금 427종) `data/curated/menu_milk_labels.yaml`, 디카페인만 조건은 원본 메뉴 필드(디카페인 음료이거나, 디카페인 변경 가능 + "디카페인으로 주문" 표시)로 확인한다. 이 기준으로 수정 전 판정 로직을 돌리면 3/78건(마키아또·콘 파나·플랫 화이트를 우유 불가 손님에게 추천)이 위반으로 잡혔고, 수정 후 0건이다.

프랜차이즈 추천(위 조건 위반율 0.0%)은 coffeereview 데이터에 의존하지 않는다 — 필터 대상이 프랜차이즈 메뉴(`menu_items`)이고 위반율은 그 메뉴에 대한 조건 필터 정확도를 재는 지표라, 원두 지식베이스의 소스 구성과 무관하게 같은 지표로 측정된다.
