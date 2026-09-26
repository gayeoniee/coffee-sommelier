# 설계 결정 — 면접에서 물을 만한 질문 13개

각 답은 "무엇을 골랐나 → 왜 → 수치 → 원자료" 순서다. 수치는 모두 `data/eval/*.json` 또는 ADR에서 옮겼다. 구조 그림은 [architecture.md](architecture.md), 결정 기록 원문은 [docs/adr/](adr/)에 있다.

| # | 질문 | 근거 |
|---|---|---|
| 1 | [왜 FastAPI인가](#1-왜-fastapi인가) | [ADR 0001](adr/0001-fastapi.md) |
| 2 | [왜 LangGraph인가, LangChain은 왜 안 썼나](#2-왜-langgraph인가-langchain은-왜-안-썼나) | [ADR 0002](adr/0002-langgraph.md) |
| 3 | [왜 별도 벡터 DB 대신 pgvector + HNSW인가](#3-왜-별도-벡터-db-대신-pgvector--hnsw인가) | [coverage](../data/eval/phase2_coverage.json), [ADR 0006](adr/0006-deterministic-neighbors.md) |
| 4 | [왜 조건은 필터, 취향은 점수인가](#4-왜-조건은-필터-취향은-점수인가) | [violations](../data/eval/phase2_violations.json) |
| 5 | [왜 규칙을 먼저 쓰고 빈 칸만 LLM에 맡기나](#5-왜-규칙을-먼저-쓰고-빈-칸만-llm에-맡기나) | [gold scores](../data/eval/gold_enrich_judge2_scores.json) |
| 6 | [왜 이웃 예측에 신뢰도와 근거를 붙이나](#6-왜-이웃-예측에-신뢰도와-근거를-붙이나) | [loo](../data/eval/phase2_loo.json) |
| 7 | [왜 템플릿 폴백과 12초 마감을 두나](#7-왜-템플릿-폴백과-12초-마감을-두나) | [ADR 0004](adr/0004-explain-thinking.md) |
| 8 | [왜 설명 모델의 thinking을 껐나](#8-왜-설명-모델의-thinking을-껐나) | [ADR 0004](adr/0004-explain-thinking.md) |
| 9 | [왜 임베딩 모델을 바꿨나](#9-왜-임베딩-모델을-바꿨나) | [ADR 0003](adr/0003-embedding-model.md) |
| 10 | [왜 이웃 정렬을 결정적으로 만들었나](#10-왜-이웃-정렬을-결정적으로-만들었나) | [ADR 0006](adr/0006-deterministic-neighbors.md) |
| 11 | [왜 설명 품질을 판정자 두 명으로 재나](#11-왜-설명-품질을-판정자-두-명으로-재나) | [ADR 0005](adr/0005-explain-quality-eval.md) |
| 12 | [데이터 라이선스는 어떻게 정했나 (전체판과 오픈판)](#12-데이터-라이선스는-어떻게-정했나-전체판과-오픈판) | [loo_open](../data/eval/phase2_loo_open.json), [compare3](../data/eval/phase2_compare3.json) |
| 13 | [무엇이 안 됐나](#13-무엇이-안-됐나) | 아래 각 링크 |

---

### 1. 왜 FastAPI인가

추천 API는 카드를 먼저 보내고 설명 3개를 토큰 단위로 스트리밍(SSE)해야 하며, 설명 LLM 호출 3개를 동시에 돌려야 한다. FastAPI는 네이티브 async, Pydantic 입출력 검증(1단계 데이터 모델이 이미 Pydantic), `StreamingResponse`를 기본으로 갖고 있어 Flask(async가 부가 기능)나 DRF(이 규모엔 무거움)보다 요구에 바로 맞았다. 설명 3개를 병렬로 돌리자 순차 4.59초가 2.03초로 줄었다. 원자료: [ADR 0001](adr/0001-fastapi.md), [phase2_bench.json](../data/eval/phase2_bench.json).

### 2. 왜 LangGraph인가, LangChain은 왜 안 썼나

AI 흐름에는 분기("DB에 있는 원두인가"), 폴백(LLM 실패 → 템플릿, 임베딩 실패 → 산지·가공 평균), 병렬(`Send`로 설명 3개)이 있고 LangGraph는 이 셋을 그래프로 그대로 표현한다. LLM 호출은 1단계에서 만든 자체 클라이언트를 쓰므로 LangChain 의존성은 들이지 않았다. 그래프는 3개(`recommend` 3노드, `analyze_bean` 5노드, `log_tasting` 4노드)이고, 노드는 `app/core/`의 순수 함수를 감싼 얇은 래퍼라 로직은 그래프 없이 테스트된다. 이 구조 덕분에 설명 모델을 TTFT 0.8초짜리 llama에서 nemotron으로 되돌릴 때(llama가 산미 4.5 선호 손님에게 "산미를 좋아하지 않아"라고 정반대 설명을 씀) 그래프는 건드리지 않고 `config/models.yaml`만 바꿨다. 원자료: [ADR 0002](adr/0002-langgraph.md), [graphs.md](graphs.md).

### 3. 왜 별도 벡터 DB 대신 pgvector + HNSW인가

추천의 절반은 SQL 필터(카페인·우유·브랜드)이고 나머지 절반이 벡터 유사도라, 둘을 한 쿼리에서 거는 게 가장 단순하다. 와인 v1은 Pinecone + 프롬프트 속 필터였고 필터가 실제로는 걸리지 않았다. 문제는 필터가 좁을 때였다 — 디카페인은 9,155개 중 168개(1.8%)뿐이라 HNSW가 필터 뒤에 k개를 못 채웠고, `hnsw.iterative_scan = relaxed_order`로 해결했다. 무료 등급 Neon 하나로 사용자 기록·프로필까지 같은 DB에 두고, 이웃 쿼리는 인덱스를 탄 채 1.5ms(EXPLAIN ANALYZE)다. 원자료: [phase2_coverage.json](../data/eval/phase2_coverage.json), [ADR 0006](adr/0006-deterministic-neighbors.md).

### 4. 왜 조건은 필터, 취향은 점수인가

카페인·우유는 어기면 안 되는 조건이라 점수에 섞으면 "취향 점수가 아주 높은 일반 커피"가 디카페인 손님에게 올라올 수 있다. 그래서 `passes`(하드 필터)를 먼저 통과시키고, 산미·바디·향미는 `score_item`(속성 0.6 + 향미 0.4)로 순위만 매긴다. 평가는 추천 코드와 독립인 라벨(메뉴 이름 427종 수기 우유 라벨, 원본 메뉴의 디카페인 필드)로 하는데, 필터를 고치기 전 판정 로직은 3/78건(마키아또·플랫 화이트를 우유 불가 손님에게)을 어겼고 지금은 페르소나 4 × 브랜드 10에서 0/102건이다. 원자료: [phase2_violations.json](../data/eval/phase2_violations.json).

### 5. 왜 규칙을 먼저 쓰고 빈 칸만 LLM에 맡기나

원두 약 9천 건을 전부 LLM에 돌리면 시간이 배로 들고, 원본 점수·키워드로 확실히 채울 수 있는 칸까지 모델의 흔들림에 맡기게 된다. 규칙으로 못 채운 5,035건만 로컬 `qwen3.5:9b`로 처리했고 실패는 0건, 비용은 0원이었다. 실버 정답(다른 벤더 판정자) 대비 LLM이 채운 산미는 ±1 이내 95.65%(n=23)로, 원본 점수를 5분위로 바꾼 값(62.5%, n=16)보다 오히려 정확했다. 판정자 두 명끼리의 산미 ±1 일치는 100%라 이 실버 라벨을 기준으로 쓸 만하다. 원자료: [gold_enrich_judge2_scores.json](../data/eval/gold_enrich_judge2_scores.json), [gold_agreement.json](../data/eval/gold_agreement.json).

### 6. 왜 이웃 예측에 신뢰도와 근거를 붙이나

처음 보는 원두는 유사 원두 10개의 가중 평균으로 산미·바디를 추정하는데, 이웃이 서로 엇갈리면 평균은 그럴듯해 보여도 틀리기 쉽다. 그래서 이웃 분산으로 신뢰도(낮음/보통/높음)를 매기고 근거 원두를 함께 보여준다. leave-one-out에서 신뢰도는 실제 정확도와 순서가 맞았다 — 산미 ±1 이내가 낮음 0.4(n=10), 보통 0.7077(n=130), 높음 0.85(n=60)로, 전체 0.735(n=200)를 사용자가 "얼마나 믿을지"로 쪼개 준다. 임베딩 API가 실패하면 산지·가공 평균으로 떨어지고 신뢰도는 강제로 "낮음"이 된다. 원자료: [phase2_loo.json](../data/eval/phase2_loo.json).

### 7. 왜 템플릿 폴백과 12초 마감을 두나

설명 LLM은 무료 NVIDIA 엔드포인트라 가끔 수십 초씩 멈추고, 설명이 안 와도 추천 자체는 유효하다. 그래서 카드는 템플릿 설명과 함께 먼저 보내고(`cards` 이벤트), LLM 설명이 실패하거나 12초 마감을 넘기면 `explain_fallback`으로 템플릿을 확정한다. 운영에서 thinking을 끄기 전 폴백은 3/8(37%), 끈 뒤 6개 브랜드 카드 18장에서 2/18(11%)였고, 설명 품질 평가 2차에서는 0/24였다. 남은 폴백은 NVIDIA 쪽 꼬리 지연이라, 첫 토큰이 3초(평소 p95 1.37초의 두 배 이상) 안에 안 오면 같은 요청을 하나 더 보내 먼저 오는 쪽을 쓰는 헤지 요청을 더했다(ADR 0004 "헤지 요청" 절; 카드당 추가 요청은 최대 1개). 템플릿도 평가 대상이라 1차 평가에서 "+200원 환각"(추가요금이 페이로드에 없었음)을 잡아 고쳤다. 원자료: [ADR 0004](adr/0004-explain-thinking.md), [ADR 0005](adr/0005-explain-quality-eval.md).

### 8. 왜 설명 모델의 thinking을 껐나

`nemotron-3-super-120b`는 기본으로 생각(chain-of-thought)을 한 뒤 답해서 첫 토큰까지 10.6초가 걸렸고, `<think>` 내용이 스트리밍 텍스트에 섞여 나왔다. `chat_template_kwargs: {enable_thinking: false}` 한 줄로 첫 토큰이 0.67초가 됐다. 대가를 재기 위해 파싱 태스크의 자기일관성을 on/off로 비교했고 96.29% → 94.21%(−2.08%p)로, 미리 정한 허용 기준(−5%p) 안이었다. 코드 변경 없이 설정만 바꿨으므로 한 줄을 지우면 되돌아간다. 원자료: [ADR 0004](adr/0004-explain-thinking.md), [phase2_thinking.json](../data/eval/phase2_thinking.json).

### 9. 왜 임베딩 모델을 바꿨나

저장된 벡터와 실행 중 질의 벡터는 같은 모델이어야 하는데, 로컬 `bge-m3`(1.2GB)는 Render 무료 512MB에 안 올라가고 호스팅 `baai/bge-m3`는 지원 종료(HTTP 410)였다. 그래서 9,155건 전체를 `nvidia/nemotron-3-embed-1b`로 다시 임베딩했고(287요청, 약 12분 30초, 실패 0), 2048차원을 Matryoshka 성질대로 앞 1024개만 잘라 스키마·HNSW 인덱스를 그대로 뒀다. LOO 산미 ±1은 0.75 → 0.735로 동점 잡음(±1~3%p) 안이었고, 한국어 질의 "고소하고 초콜릿 같은 브라질"은 오히려 top5가 모두 초콜릿·견과 노트 원두로 나아졌다. 원자료: [ADR 0003](adr/0003-embedding-model.md), [embedding_sanity_ko.json](../data/eval/embedding_sanity_ko.json).

### 10. 왜 이웃 정렬을 결정적으로 만들었나

같은 데이터·같은 대상인데 재적재할 때마다 LOO가 1~3%p씩 움직였다. 원인은 CQI 1,546개 중 744개가 임베딩이 완전히 같은 쌍둥이라 동점이 많고, 동점 안의 순서가 행의 물리적 저장 순서에 좌우됐기 때문이다. `ORDER BY embedding <=> v, id`로 id를 2차 키로 넣자 두 번 돌린 LOO 결과 JSON의 sha256이 같아졌다. HNSW 인덱스는 그대로 타고(`Incremental Sort`만 추가), 지연은 47.97 → 48.33ms(+0.7%)로 잡음 안이다. 원자료: [ADR 0006](adr/0006-deterministic-neighbors.md), [phase2_loo_repro.json](../data/eval/phase2_loo_repro.json).

### 11. 왜 설명 품질을 판정자 두 명으로 재나

지연만 재서는 "설명이 사실과 맞는가"를 알 수 없어, 손으로 쓴 24케이스에 결정적 규칙 검사(외국어·길이·숫자 근거·조건 언급·극성)와 LLM 판정자 두 명을 붙였다. 판정자가 하나면 그 모델의 성향이 곧 점수가 되고, 1차의 judge2는 생성 모델과 같은 nemotron이라 진짜 모순(산미 3 원두를 산미 4.5 손님에게 "매우 잘 맞는다")을 놓쳤다. 그래서 합격은 두 판정자가 모두 문제없다고 할 때만 세고, 2차에서 judge2를 다른 벤더(`gpt-oss-20b`)로 바꿨다. 2차 결과는 규칙 통과 10/22 → 21/24, 모순 없음 두 판정자 합의 17/22, 폴백 0/24다. 원자료: [ADR 0005](adr/0005-explain-quality-eval.md), [phase2_explain_quality.json](../data/eval/phase2_explain_quality.json).

### 12. 데이터 라이선스는 어떻게 정했나 (전체판과 오픈판)

가장 큰 소스인 coffeereview(Kaggle 스크랩, 약 7.4천 건)는 원 저작권이 Coffee Review에 있어 비상업 포트폴리오 용도로만 쓰고 원본은 레포에 넣지 않았다. 대신 그 데이터를 뺀 오픈 라이선스판을 평가(`exclude_sources`)와 배포(https://coffee-sommelier-open.vercel.app) 양쪽에 따로 만들어, 공모전처럼 라이선스가 엄격한 곳에 낼 수 있게 했다. 대가는 수치로 드러난다: 원두 9,155 → 1,655개, LOO 산미 ±1 0.735 → 0.5202(n=198), 디카페인 168 → 6개. 국내 로스터리 5곳의 사실 정보(설명 문구 미저장, robots.txt 준수)를 더하면 LOO는 그대로지만 디카페인이 6 → 13개로 늘어, 한국에서 살 수 있는 디카페인 후보가 처음 생긴다. 프랜차이즈 추천(조건 위반 0/102)은 메뉴 데이터만 쓰므로 두 판이 같다. 원자료: [phase2_loo_open.json](../data/eval/phase2_loo_open.json), [phase2_compare3.json](../data/eval/phase2_compare3.json).

### 13. 무엇이 안 됐나

- **규칙 기반 향미 태그가 가장 약한 고리다.** 정답 대비 Jaccard 0.40(n=48)이고, 이웃 예측 태그 F1도 0.4082(n=168)다. 부정 표현("no bitterness")·일반어("fresh") 오탐이 원인으로 보이는데 아직 고치지 않았다([gold_enrich_judge2_scores.json](../data/eval/gold_enrich_judge2_scores.json), [phase2_loo.json](../data/eval/phase2_loo.json)).
- **국내 로스터리 데이터는 예측에 기여하지 못했다.** CQI 대상 200개의 이웃 중 로스터리 원두는 0%라 compare3의 LOO가 세 판 모두 0.495로 같다. 기여는 커버리지(디카페인 6 → 13)뿐이다([phase2_compare3.json](../data/eval/phase2_compare3.json)).
- **설명의 환각은 절반만 줄었다.** 규칙 통과는 21/24지만 두 판정자가 모두 환각 없음으로 본 건 14/22다. 남은 환각은 "균형이 좋아요" 같은 평가적 수사와 1~5 수치를 "보통"으로 뭉개는 버릇이다([ADR 0005](adr/0005-explain-quality-eval.md)).
- **메뉴는 10개 브랜드 중 7개만 실측이다.** 투썸(봇 차단)·이디야(robots.txt 차단 경로)·블루보틀(음료 메뉴 미공개)은 브랜드 원두 추정 카드만 나온다.
- **운영 통계는 아직 비어 있다.** 텔레메트리와 집계 스크립트는 붙였지만 첫 집계 구간의 요청이 0건이라 운영 폴백 비율은 수동 스모크(2/18)밖에 없다([prod_stats_2026-09-26.json](../data/eval/prod_stats_2026-09-26.json)).
- **3단계(사진 스캔·Vision)와 4단계(멀티턴 에이전트)는 착수하지 않았다.** Lighthouse 12는 PWA 항목을 없애 설치 가능성은 수동으로만 확인했다([lighthouse.md](screenshots/lighthouse.md)).
