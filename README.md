# ☕ Coffee Sommelier — 내 커피 취향을 알아주는 앱

> 건강 때문에 디카페인을 마시지만 산미 있는 커피를 좋아하는 사람도, 카페에서 실패 없이 고를 수 있게.

카페에서 음료를 고를 때 **사용자 조건(카페인 등)은 반드시 지키고**, 취향(산미·바디·향미)에 맞는 선택지를 **근거와 함께** 추천하며, 마신 기록으로 점점 개인화되는 앱을 만든다. 이 레포는 그중 **1단계: 데이터 기반**까지 완료된 상태다.

| 단계 | 내용 | 상태 |
|---|---|---|
| **1. 데이터 기반** | 수집 → 정규화 → 구조화(규칙 + 로컬 LLM) → 임베딩 → pgvector 적재, 품질 리포트, 태깅 평가 | ✅ 완료 |
| 2. 추천 + 기록 | 취향 온보딩, 조건 필터 + 취향 점수 추천, 음용 기록, PWA | 🚧 백엔드 완료 (화면·배포 진행 예정) |
| 3. 스캔 + 추론 + 평가 | 원두카드/메뉴 사진 Vision 추출, 근거 기반 향미 예측, 모델 비교 | 예정 |
| 4. 에이전트 | 멀티턴 대화("아까 거보다 산미 센 걸로") | 예정 |

설계 문서: [`docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md`](docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md)

---

## 1단계 결과

### 지식베이스 (Postgres + pgvector)

| 항목 | 행 수 |
|---|---|
| 원두 (`coffees`, 전부 1024차원 임베딩) | **9,048** |
| └ 디카페인 원두 | 161 |
| 리뷰 텍스트 (`reviews`, RAG 근거 전용) | 7,401 |
| 프랜차이즈 메뉴 (`menu_items`, 카페인 mg 포함) | 304 (스타벅스 71 · 메가 119 · 빽다방 114) |
| └ 디카페인 메뉴 | 74 |
| 브랜드 (`brands`, 디카페인 가능 여부·추가요금) | 10 |
| SCA 향미 택소노미 (`flavor_taxonomy`, 1·2단계 한국어) | 121 |

원두 소스별: coffeereview(Kaggle) 7,393 · CQI 1,546 · RoasterDB 100 · 블루보틀 코리아 9

LLM 구조화(enrich): 규칙으로 못 채운 **5,035건**을 로컬 `qwen3.5:9b`로 처리, **실패 0건**. 적재 시 키가 맞지 않아 버려진 리뷰·메뉴 0건.

### 필드 결측률 (원두)

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

### 구조화 품질 평가 (정답셋 50건)

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

### 검색 예시

```text
$ python -m pipeline query "bright citrus floral Ethiopia washed" -k 5 --decaf
0.616  Decaf Ethiopia Sidamo | Old Soul Co. | Ethiopia | natural | decaf=True | acidity=2 ...
0.607  Ethiopia Sidamo Natural Water Decaf | Jackrabbit Java | Ethiopia | washed | decaf=True ...
0.598  Decaf Harfusa Ethiopia Yirgacheffe | Counter Culture Coffee | Ethiopia | washed | decaf=True ...
```
디카페인이 전체의 2%뿐이라 pgvector HNSW가 필터 후 k개를 못 채우는 문제가 있었고, `hnsw.iterative_scan = relaxed_order`로 해결했다.

---

## 2단계 백엔드 (추천 + 기록)

- **구조:** FastAPI + LangGraph 그래프 3개(`recommend`, `analyze_bean`, `log_tasting`) — [그래프 다이어그램](docs/graphs.md), 결정 근거 [ADR 0001](docs/adr/0001-fastapi.md) · [ADR 0002](docs/adr/0002-langgraph.md)
- **추천 방식:** 하드 조건 필터(카페인·우유) → 취향 적합도(속성 0.6 + 향미 0.4) → MMR 다양성 top3 → 설명 3개 병렬 스트리밍(실패 시 템플릿)
- **처음 보는 원두:** 규칙 파싱(불확실하면 LLM) → DB에 있으면 실측, 없으면 유사 원두 10개 가중 평균으로 예측 + 신뢰도 + 근거 요약
- **학습:** 별점(좋음 끌어당김 / 별로 밀어냄, 학습률 1/(n+2)) + 한 줄 후기에서 LLM이 뽑은 신호("산미 너무 셈" → 산미 −0.5)

| 평가 (`python -m app.eval`) | 결과 |
|---|---|
| 조건 위반율 (페르소나 4 × 브랜드 10, top3) | 0.0% — 0/78건 (우유 판정은 메뉴 302종 수기 라벨 기준) |
| 원두 예측 leave-one-out, 산미 ±1 이내 | 0.76 (n=200) |
| 원두 예측 leave-one-out, 바디 ±1 이내 | 0.67 |
| 학습 수렴: 모의 사용자 10회 기록 후 프로필 오차 | 0.7792 → 0.7117 |
| 설명 3개 순차 vs 병렬 | 13.95초 → 11.59초 |

**데이터 출처별 성능 (포트폴리오판 vs 공개·오픈 라이선스판)** — coffeereview(Kaggle) 데이터는 라이선스상 비상업 포트폴리오 용도로만 쓰므로, 그 데이터를 뺀 공개·오픈 라이선스 소스(CQI·RoasterDB·블루보틀)만으로도 같은 평가를 다시 돌렸다.

| 항목 | 포트폴리오판 (전체 소스) | 공개·오픈 라이선스판 (coffeereview 제외) |
|---|---|---|
| LOO 산미 ±1 이내 | 0.76 (n=200) | 0.5306 (n=196) |
| LOO 바디 ±1 이내 | 0.67 (n=200) | 0.5816 (n=196) |
| 원두 수 (전체) | 9,048 | 1,655 |
| └ 향미 태그 보유 | 7,495 | 109 |
| └ 디카페인 | 161 | 6 |

조건 위반 판정은 추천 코드와 독립이다: 우유는 메뉴 이름 302종을 사람이 하나씩 라벨링한 `data/curated/menu_milk_labels.yaml`, 디카페인만 조건은 원본 메뉴 필드(디카페인 음료이거나, 디카페인 변경 가능 + "디카페인으로 주문" 표시)로 확인한다. 이 기준으로 수정 전 판정 로직을 돌리면 3/78건(마키아또·콘 파나·플랫 화이트를 우유 불가 손님에게 추천)이 위반으로 잡혔고, 수정 후 0건이다.

프랜차이즈 추천(위 조건 위반율 0.0%)은 coffeereview 데이터에 의존하지 않는다 — 필터 대상이 프랜차이즈 메뉴(`menu_items`)이고 위반율은 그 메뉴에 대한 조건 필터 정확도를 재는 지표라, 원두 지식베이스의 소스 구성과 무관하게 같은 지표로 측정된다.

API 실행: `COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --reload` → http://localhost:8000/docs

## 빠른 시작

```bash
docker compose up -d db          # Postgres 17 + pgvector
uv sync
ollama pull qwen3.5:9b && ollama pull bge-m3
cp .env.example .env             # NVIDIA_API_KEY (평가 judge용)
# Kaggle 키: ~/.kaggle/kaggle.json

uv run python -m pipeline run                    # collect → normalize → enrich → embed → load
uv run python -m pipeline query "산미 밝은 에티오피아" --decaf
uv run python -m pipeline gold-sample && uv run python -m pipeline gold-label && uv run python -m pipeline gold-score
uv run pytest -q                                 # 114 tests (DB 테스트 포함)
```

`run --only <stage>`로 단계별 실행, `--limit N`으로 LLM 호출 수 제한, `--retry-failed`로 실패 행 재시도. enrich·embed는 캐시로 **중단 후 이어서** 실행된다(실제로 컴퓨터 재시작·메모리 부족으로 여러 번 끊겼지만 한 건도 잃지 않았다).

## 아키텍처

```
pipeline/
  collect/    robots.txt 준수 + 호스트별 1초 지연, 소스별 격리(하나가 실패해도 계속), 날짜별 원본 스냅샷
  normalize/  공통 스키마, 산지·가공·로스팅 표기 통일, 디카페인 판별, 3개 스크랩 데이터 병합
  enrich.py   규칙 우선 → 빈 칸만 로컬 LLM(qwen3.5:9b) → JSON 스키마 검증 → 재개 가능한 캐시
  embed.py    bge-m3 (1024차원), 텍스트 해시 캐시, 배치별 저장
  load.py     단일 트랜잭션 적재(실패 시 롤백)
  llm.py      Ollama·NVIDIA 공용 OpenAI 호환 클라이언트: 타임아웃·로컬 폴백·빈 응답 처리·429 백오프
  gold.py     정답셋 샘플링(디카페인 우선), judge 라벨링, 값 출처별 채점, judge 간 일치도
```

### 설계 결정
- **카페인은 절대 조건(필터), 산미·바디는 취향 점수.** 와인 v1의 "프랑스 제외 = 논리 필터"를 한 단계 발전시킨 구조.
- **데이터가 적은 도메인 대응:** 규칙으로 먼저 채우고 빈 칸만 LLM → 약 9천 건 중 5,035건만 LLM 호출(로컬, 비용 0원).
- **무료 모델 구성:** 대량 배치는 로컬 Ollama, 실시간·평가는 NVIDIA 무료 엔드포인트. 실측 결과 큰 모델 다수가 무료 등급에서 90초 이상 응답하지 않아, 모든 원격 호출에 타임아웃 + 로컬 폴백을 둔다.
- **RAG 원문은 사용자에게 그대로 보여주지 않는다.** 리뷰 원문은 LLM의 근거 입력이고, 화면에는 예측·근거 요약·추천 이유만 노출한다(2단계).

### 알려진 한계 / 다음 할 일
- 적재는 매번 전체 삭제 후 재적재(TRUNCATE)다. **2단계에서 사용자 기록 테이블이 `coffees`를 참조하기 전에 key 기반 upsert로 바꿔야 한다.**
- 규칙 태그의 부정 표현·일반어 오탐 개선, 원본 점수의 절대 척도 보정.
- 리뷰 코퍼스가 영어라 한국어 질의 검색 품질이 낮다 → 2단계에서 질의 번역 또는 한국어 요약 임베딩.
- 프랜차이즈 음료 단위 수집은 3개 브랜드뿐(나머지 7개는 브랜드 정보만).

## 데이터 출처

| 소스 | 내용 | 라이선스·비고 |
|---|---|---|
| [CQI 2018](https://github.com/jldbc/coffee-quality-database), [CQI 2023](https://github.com/fatih-boyar/coffee-quality-data-CQI) | 산지·가공·산미/바디 점수 | MIT |
| Kaggle: [patkle](https://www.kaggle.com/datasets/patkle/coffeereviewcom-over-7000-ratings-and-reviews), [hanifalirsyad](https://www.kaggle.com/datasets/hanifalirsyad/coffee-scrap-coffeereview), [schmoyote](https://www.kaggle.com/datasets/schmoyote/coffee-reviews-dataset) | coffeereview.com 리뷰 스크랩 | 원 저작권은 Coffee Review에 있음. 비상업 포트폴리오 용도로만 사용, 원본 데이터는 레포에 포함하지 않음 |
| [RoasterDB 샘플](https://github.com/RoasterDB/specialty-coffee-roasterdb) | 로스터리 원두 + SCA 노트 | CC BY-NC 4.0 |
| [SCA 플레이버 휠 JSON](https://github.com/fschlz/coffee-flavor-api) | 향미 분류 체계 | © SCA/WCR 2016, CC BY-NC-ND 4.0 (원본 수정 없이 별도 한국어 매핑) |
| 스타벅스·메가MGC·빽다방 공식 메뉴 | 음료, 카페인 mg | robots.txt 허용 범위, 1회 스냅샷 |
| 블루보틀 코리아 `products.json` | 원두 상품 설명 | Shopify 공개 엔드포인트 |
| `data/curated/brands.yaml` | 10개 브랜드 디카페인 정보 | 공식 페이지·뉴스 수기 정리(확인 수준 표기) |

coffeereview.com 원본 사이트, 투썸플레이스(봇 차단), 컴포즈커피(캡차)는 직접 수집하지 않았다.

## 와인 v1에서 배운 점

이 레포는 원래 LangChain + Pinecone 기반 와인 추천 RAG였다(태그 [`wine-v1`](../../tree/wine-v1)). 거기서 얻은 교훈을 이번에 반영했다.
- 사이드바 필터가 프롬프트 텍스트로만 전달돼 실제로는 걸러지지 않았다 → 이번엔 **DB 필터(SQL WHERE)**로 강제한다.
- 평가 체계가 없어 "정확도 100%" 같은 주장을 검증할 수 없었다 → 이번엔 **품질 리포트와 정답셋 수치를 먼저** 만들었다.
- README에는 "맛 태그 12개"라고 적었지만 실제 적재된 태그는 2개였다 → 이번엔 README의 모든 숫자를 리포트 파일에서 옮겼다.
