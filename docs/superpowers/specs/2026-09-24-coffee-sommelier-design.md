# Coffee Sommelier — 설계 문서 (전체 로드맵 + 1단계 상세)

- 작성일: 2026-09-24
- 상태: 사용자 리뷰 대기
- 범위: 전체 제품 구조와 4단계 로드맵을 정의하고, **1단계(데이터 기반)** 만 구현 가능한 수준으로 상세화한다. 2~4단계는 각자 별도의 스펙 → 계획 → 구현 사이클을 가진다.

---

## 1. 배경과 목표

### 1.1 출발점
이 레포는 와인 추천 RAG(v1)다. Streamlit + LangChain SelfQueryRetriever + Pinecone + GPT-4o-mini, Kaggle winemag-130k 데이터. 한계:
- 추천 결과가 "2017년 미국 가격 와인"이라 실제 구매·음용과 연결되지 않음
- 싱글턴(대화 기억 없음), 사이드바 필터가 프롬프트 텍스트로만 전달됨
- README의 "맛 태그 12개"와 달리 실제 적재된 태그는 `tag_oak`, `tag_acid` 2개
- 평가 체계 없음

### 1.2 새 방향
**"내 커피 취향을 알아주는 앱"** — 카페에서 음료를 고를 때, 사용자의 조건(카페인 등)은 반드시 지키고 취향(산미·바디·향미)에 맞는 선택지를 근거와 함께 추천하며, 마신 기록으로 점점 개인화된다.

- 대상 사용자: 카페에서 사 마시는 사람. 프랜차이즈와 개인/스페셜티 카페를 가리지 않고 다님.
- 대표 시나리오(작성자 본인): 건강 때문에 디카페인을 주로 마시지만 산미 있는 커피를 좋아함. 디카페인은 **여러 사용자 조건 중 하나**이며 제품 전체가 디카페인 전용은 아니다.
- 포트폴리오 목적: AI 개발자 지원. 앱은 실사용 가능한 PWA로 만들되 무게중심은 AI 파트(데이터·RAG 추론·Vision·개인화·평가).

### 1.3 제품 루프
```
찍는다/고른다 (원두카드·메뉴 사진 or 브랜드 메뉴)
  → 알려준다 (예상 향미 + 근거 요약 + 내 취향 적합도, 조건 위반 항목은 제외)
  → 마시고 기록한다 (별점 + 한 줄)
  → 취향 프로필 업데이트 → 다음 추천이 더 정확해짐
```

### 1.4 출력 원칙
RAG 검색 결과(리뷰 원문)는 **LLM의 근거 입력**이며 사용자 화면에 원문을 그대로 나열하지 않는다. 사용자에게는 예측(예: 산미 4/5, 시트러스·플로럴), 근거 요약(예: "유사 원두 12건 중 9건에서 시트러스 언급"), 추천 이유만 보여준다. 원문은 개발용 디버그 뷰와 평가 리포트에서만 확인한다.

---

## 2. 전체 아키텍처

```
[PWA: Next.js]      카메라/사진, 취향 온보딩, 추천, 기록, 대화
      │ REST
[API: FastAPI]      AI 로직 전부
      ├─ 추천 서비스      (절대조건 필터 → 취향 벡터 점수)
      ├─ 향미 추론 서비스  (RAG: 유사 원두 근거 → 향미 예측)
      ├─ Vision 추출 서비스 (사진 → 산지·가공·로스팅·디카페인)
      ├─ 에이전트          (위 서비스를 도구로 사용, 멀티턴)
      └─ 평가 러너         (정답셋 → 지표 리포트)
[Postgres + pgvector]  원두·리뷰·메뉴·벡터·사용자기록을 한 DB에
[pipeline/]            수집 → 정규화 → 구조화 → 임베딩 → 적재
```
모든 서비스는 `docker compose up`으로 기동한다. Ollama는 호스트에서 실행하고 컨테이너는 `host.docker.internal:11434`로 접근한다.

### 2.1 모델 구성 (무료)
| 용도 | 모델 | 비고 |
|---|---|---|
| 메인 LLM (태깅·번역·Vision·에이전트) | `qwen3.5:9b` (Ollama, 로컬) | 설치 확인됨. completion/vision/tools/thinking 지원, Q4_K_M 6.6GB, RTX 4060 8GB에서 구동 |
| 임베딩 | `bge-m3` (Ollama, 로컬) | **미설치 — `ollama pull bge-m3` 필요**. 다국어(한국어 포함) |
| 배포용 대체 | Gemini Flash 무료 등급 | 배포 서버는 로컬 GPU를 못 쓰므로. 한도·조건은 구현 시점에 재확인 |
| 평가 비교군 | `qwen3:8b`, `granite4.1:8b`, `command-r7b` | 설치 확인됨 |

LLM과 임베딩은 `LLMClient` / `Embedder` 인터페이스로 추상화하고 설정으로 공급자를 교체한다.

### 2.2 로드맵
| 단계 | 내용 | 완료 시 되는 것 |
|---|---|---|
| **1. 데이터 기반** (이 문서에서 상세화) | 수집 파이프라인, 스키마, SCA 태그 정규화, 임베딩 적재, 태깅 품질 측정 | DB에 지식베이스 적재, 재실행 가능, 품질 리포트 |
| 2. 추천 + 기록 | 취향 온보딩, 조건 필터 + 취향 점수 추천, 음용 기록, 취향 벡터 갱신, PWA 기본 화면 | 폰에서 실제 사용 가능 |
| 3. 스캔 + 추론 + 평가 | Vision 추출, 근거 기반 향미 예측, 정답셋, 모델 비교 리포트 | "예측 정확도 X% → Y%" 수치 |
| 4. 에이전트 | 대화형 인터페이스, 도구 호출, 멀티턴("아까 거보다 산미 센 걸로") | 대화형 추천 |

### 2.3 레포 전환
- 현재 커밋에 git 태그 `wine-v1`을 달아 와인 버전을 보존한다.
- 와인 코드(`app.py`, `wine_logic.py`, `data.ipynb`)는 1단계 구현 첫 작업에서 제거한다. README는 커피 프로젝트로 재작성하고 "와인 v1에서 배운 점"을 한 섹션으로 연결한다.
- 기존 Pinecone 인덱스 `wine-sommelier-agent`는 건드리지 않는다.

---

## 3. 1단계: 데이터 기반 (상세)

### 3.1 데이터 소스
사용자 결정: 라이선스가 명시되지 않은 데이터도 포트폴리오에 사용한다(리뷰 원문 포함). 출처는 README와 각 행의 `source`/`source_url`로 명시한다.

단, 다음은 **직접 수집하지 않는다**:
- coffeereview.com 원본 사이트 — robots.txt가 ClaudeBot/anthropic-ai를 명시적으로 차단. 해당 내용은 Kaggle 스크랩본으로 대체(2023년까지).
- 투썸플레이스(CloudFront 403 봇 차단), 컴포즈커피(캡차) — 차단 우회 없이 공개 정보를 보고 브랜드 행을 수기 정리.

| 층 | 소스 | 예상 규모 | 수집 방식 | 역할 |
|---|---|---|---|---|
| ① 점수형 사전지식 | CQI 2018 (jldbc/coffee-quality-database, MIT) | 1,340행 | GitHub raw CSV | 산지×가공 → 산미·바디 점수 |
| | CQI 2023 (fatih-boyar/coffee-quality-data-CQI, MIT) | 약 200행 (정확한 수 미확인) | GitHub raw CSV | 동일 |
| ② 리뷰 텍스트 | Kaggle patkle/coffeereviewcom-over-7000-ratings-and-reviews | 7,041건 | Kaggle API | RAG 근거 문서 |
| | Kaggle hanifalirsyad/coffee-scrap-coffeereview | 행 수 미확인(10.3MB) | Kaggle API | 동일, aroma/acidity/body 등 점수 |
| | Kaggle schmoyote/coffee-reviews-dataset | 1,267행(simplified) | Kaggle API | roast type, origin, 가격 |
| | RoasterDB 샘플 (GitHub, CC-BY-NC-4.0) | 미확인 | GitHub | process/roast + SCA 매핑 노트 |
| | 블루보틀 코리아 `kr.bluebottlecoffee.com/products.json` | 원두 상품 수십 건 | Shopify JSON | 한국 판매 원두 향미 노트 |
| | 국내 스페셜티 로스터리 상세페이지 | 조사 후 결정 | HTML, robots.txt 허용 사이트만 | 한국어 향미 노트 |
| ③ 택소노미 | SCA 플레이버 휠 JSON (fschlz/coffee-flavor-api, CC BY-NC-ND) | 약 100노드 | GitHub | 태그 정규화, 취향 벡터 축 |
| ④ 프랜차이즈 메뉴 | 스타벅스 `starbucks.co.kr/upload/json/menu/W0000003.js` 등 | 커피 약 150 | JSON | 음료, 카페인 mg |
| | 메가MGC `/menu/menu.php?...` | 약 120 | HTML 조각 | 디카페인 메뉴, 카페인 mg |
| | 빽다방 `/menu/menu_coffee/` | 약 230 | 정적 HTML | 카페인 mg |
| | 폴바셋, 할리스, 커피빈, 이디야 | 수십 | HTML | 메뉴, 원두 정보 |
| | 브랜드 정보(10~15개 브랜드) | 브랜드당 1행 | **수기 정리 YAML** | 디카페인 가능 여부·추가요금·원두·향미 |

Kaggle 다운로드에는 사용자의 `kaggle.json`(API 키)이 필요하다. 없으면 사용자가 수동 다운로드해 `data/raw/kaggle/`에 둔다.

### 3.2 파이프라인
```
pipeline/
  collect/    소스별 수집기 → data/raw/{source}/{YYYY-MM-DD}/ (원본 스냅샷, git 제외)
  normalize/  공통 스키마 변환, 산지·가공 표기 통일, 디카페인 판별, 중복 병합
  enrich/     구조화: 산지·가공·로스팅·decaf_process·SCA 태그·산미/바디/단맛(1~5)
              규칙 기반 추출을 먼저 하고, 빈 칸만 LLM(qwen3.5:9b)이 채운다
  embed/      향미 설명 텍스트 → bge-m3 → pgvector
  load/       Postgres 적재 + 품질 리포트 출력
  __main__.py `python -m pipeline run [--only STAGE] [--source NAME]`
```
- 각 단계는 앞 단계 산출물만 읽으므로 독립 재실행이 가능하다. normalize 이후는 네트워크 없이 동작한다.
- 수집 소스는 `Collector` 인터페이스(`name`, `collect(out_dir) -> Manifest`) 하나로 통일한다. 새 소스 추가는 파일 하나.

### 3.3 스키마 (1단계 범위)
- `coffees` — 원두 1종. `id, name, roaster, origin_country, origin_region, process, roast_level, is_decaf, decaf_process, acidity, body, sweetness, flavor_tags text[], flavor_summary, embedding vector(1024), source, source_url, collected_at`
- `reviews` — 원두에 달린 리뷰. `id, coffee_id → coffees, text, rating, sub_scores jsonb, source, source_url, collected_at`. RAG 근거 전용, 사용자 화면 비노출.
- `brands` — `id, name, decaf_available, decaf_surcharge_krw, default_bean_coffee_id, decaf_bean_coffee_id, notes, source_url, verified_at`
- `menu_items` — `id, brand_id → brands, name, category, is_decaf, decaf_option, caffeine_mg, coffee_id → coffees (nullable), source_url, collected_at`
- `flavor_taxonomy` — `id, parent_id, level, name_en, name_ko`
- `enrich_log` — `row_ref, stage, status(ok|failed), error, model, updated_at` (재시작·실패 추적)

사용자·기록 테이블은 2단계 스펙에서 정의한다. `embedding` 차원(1024)은 bge-m3 기준이며 모델 교체 시 마이그레이션한다.

### 3.4 에러 처리
- **소스 격리**: 수집기 하나가 실패해도 나머지는 진행, 실패 소스는 리포트에 표기.
- **재시작**: enrich는 `enrich_log`로 처리 완료 행을 건너뛴다. 로컬 LLM으로 수천 건을 처리하므로 중단 후 재개가 필수.
- **크롤링 예절**: robots.txt 확인, 요청 간 지연(기본 1초), 스냅샷 1회 수집 후 재사용.
- **LLM 출력 검증**: Pydantic 스키마 검증 → 실패 시 1회 재시도 → 실패 시 `enrich_log.status=failed` 기록 후 진행.

### 3.5 테스트
- 수집기·정규화: 저장된 샘플 HTML/JSON/CSV fixture로 파싱 테스트(네트워크 없음).
- 정규화 규칙 단위 테스트: 디카페인 판별("decaf", "디카페인", "Swiss Water", "Sugarcane/EA", "CO2"), 산지 표기 통일, 중복 병합.
- 구조화 품질: 사람이 라벨링한 **50건 정답셋**과 LLM 태깅 비교 → 필드별 일치율. 3단계 평가 체계의 첫 조각.
- 적재 스모크 테스트: 테스트 DB에 소량 적재 후 벡터 유사도 질의가 결과를 반환하는지.

### 3.6 완료 기준
1. `docker compose up` 후 `python -m pipeline run` 한 번으로 DB 적재까지 완료.
2. 품질 리포트: 소스별 행 수, 필드별 결측률, 디카페인 행 수, enrich 실패 건수.
3. 50건 정답셋 대비 필드별 태깅 일치율 수치.
4. "에티오피아 워시드와 비슷한 원두"(+ `is_decaf = true` 필터) 같은 질의가 pgvector에서 동작.
5. 와인 코드 제거, `wine-v1` 태그 존재, README 초안 갱신.

---

## 4. 열린 사항 (구현 중 확인)
- CQI 2023 정확한 행 수, hanifalirsyad 데이터셋 행 수·컬럼, RoasterDB 샘플 크기.
- 국내 스페셜티 로스터리 후보 선정(robots.txt 허용 여부 확인 후).
- Gemini 무료 등급의 현재 한도·조건(배포 시점).
- SCA 휠 한국어 번역은 CC BY-NC-ND의 변경 금지 조건 때문에 **별도 매핑 테이블(`name_ko`)** 로 두고 원본 JSON은 수정·재배포하지 않는다.
