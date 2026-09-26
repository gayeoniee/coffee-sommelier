# ADR 0002 — AI 흐름 오케스트레이션: LangGraph (LangChain 미사용)

- 상태: 채택 (2026-09-26)

## 맥락
2단계의 AI 흐름에는 분기("DB에 있는 원두인가?"), 폴백(LLM 실패 → 템플릿, 임베딩 실패 → 산지·가공 평균), 병렬(설명 3개), 단계별 트레이싱·스트리밍이 있다.
4단계에서는 이 흐름들을 도구로 쓰는 멀티턴 에이전트가 필요하다.

## 대안
| 요구 | LangChain 체인(LCEL) | LangGraph | 직접 구현(asyncio) |
|---|---|---|---|
| 조건 분기 | 라우팅 체인으로 우회 | `add_conditional_edges` | if문 |
| 실패 폴백 | 예외 처리 별도 | 노드 안의 폴백 + 그래프에 표현 | try/except |
| 병렬 fan-out | 가능하나 번거로움 | `Send` 로 기본 지원 | `asyncio.gather` |
| 노드별 스트리밍·트레이싱 | 제한적 | `get_stream_writer`, 노드 단위 span | 직접 구현 |
| 구조 시각화 | 없음 | `draw_mermaid()` 자동 생성 | 없음 |
| 4단계 에이전트 확장 | LangGraph로 이전 권장 | 같은 그래프를 도구로 재사용 + checkpointer | 처음부터 |

## 결정
LangGraph 1.2만 쓴다. LLM 호출은 1단계 자체 클라이언트(`pipeline/llm.py`, `app/llm.py`)를 그대로 써서 LangChain 의존성을 들이지 않는다.
단순 조회(브랜드 목록·검색·내 정보)는 그래프 없이 엔드포인트에서 바로 처리한다 — 그래프는 분기·폴백·병렬이 있는 곳에만.

## 증거
- 구조: `docs/graphs.md` (자동 생성 다이어그램 3개)
- 폴백 동작: `tests/app/test_graph_recommend.py::test_one_failed_explanation_falls_back_others_stream`,
  `tests/app/test_graph_analyze.py::test_parse_failure_and_embedding_failure_degrade_to_low_confidence`
- 병렬 효과: ADR 0001 실측 표(순차 vs 병렬)
- 노드별 시간: Langfuse 트레이스(키 설정 시). 스크린샷은 배포 후 README에 추가.

## 결과
- 노드는 `app/core/` 순수 함수의 얇은 래퍼라 로직은 그래프 없이 테스트된다.
- 4단계에서 recommend / analyze_bean / log_tasting 을 에이전트의 도구로 노출하고 checkpointer로 대화 메모리를 붙인다.

### 모델 선택: 설명(explain) LLM이 nemotron으로 정착한 과정
그래프 노드(`explain`)가 호출하는 LLM은 처음엔 실측 TTFT(첫 토큰 지연)만으로 골랐다가, 실제 그래프 출력 품질을 보고
다시 바꿨다 — LangGraph 노드 선택도 벤치 숫자 하나로 끝나지 않고 그래프를 실제로 돌려봐야 드러나는 문제가 있다는 사례라 여기 기록한다.

- **1차 선정(실측 기준):** NVIDIA 엔드포인트 4개 모델을 자연스러운 한국어 생성 여부 + TTFT로 비교. `google/gemma-3-12b-it`는 HTTP 404로 탈락, 나머지 중
  `meta/llama-3.2-11b-vision-instruct`가 TTFT 0.8s로 가장 빨라(`nvidia/nemotron-3-super-120b-a12b`는 ~1.7s, 두 런 평균 1.8s·1.6s) `explain`/`parse_note`/`parse_bean`에 채택.
- **문제 발견(실서버 스모크 테스트, 2026-09-26):** `meta/llama-3.2-11b-vision-instruct`로 그래프를 실제로 돌려보니
  (1) 손님의 취향 프로필은 산미 4.5(강한 산미 선호)인데도 모든 추천 설명이 "손님은 산미가 강한 음료를 좋아하지 않아 … 취향에 맞지 않습니다"라고 정반대로 서술했고,
  (2) 후기 "너무 달고 무거웠어요"(단맛·바디에 대한 말)를 `parse_note`가 잘못 해석해 엉뚱하게 `acidity: lower` 신호를 뽑아내는 바람에 산미 선호만 0.5 낮아지고 정작 바디·단맛에는 신호가 반영되지 않았다.
  TTFT가 빠른 대신 내용 품질과 한국어 파싱 정확도가 떨어져, 사용자에게 잘못된 근거를 보여주는 문제로 이어졌다.
- **재선정(`01819d2`):** `explain`, `parse_note`, `parse_bean` 세 태스크 모두 `nvidia/nemotron-3-super-120b-a12b`로 되돌렸다.
  TTFT는 llama보다 느리지만(0.8s vs ~1.7s), 설명이 손님 취향 요약과 모순되지 않고 후기 파싱도 올바른 신호(예: 단맛·바디 하향)를 뽑아 실사용 품질을 우선했다.
- **결론:** 설명 모델 선정 기준은 "TTFT 최저"가 아니라 "TTFT + 취향 프로필과의 일관성 + 한국어 후기 파싱 정확도"다. LangGraph 노드는 순수 함수 래퍼라
  이런 모델 교체가 그래프 구조 변경 없이 `config/models.yaml` 값만 바꿔서 가능했다.
