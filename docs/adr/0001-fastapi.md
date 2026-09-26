# ADR 0001 — API 프레임워크: FastAPI

- 상태: 채택 (2026-09-26)

## 맥락
추천 API는 (1) 결과 카드를 즉시 보내고 LLM 설명을 토큰 단위로 스트리밍(SSE)해야 하고, (2) 카드 3장의 설명 LLM 호출을 동시에 돌려야 하며,
(3) 1단계 데이터 모델이 이미 Pydantic이고, (4) LangGraph 그래프가 async다. "REST API"는 설계 방식이고, 여기서의 선택지는 그 REST API를 구현할 파이썬 프레임워크다.

## 대안
| 대안 | 장점 | 단점 |
|---|---|---|
| **FastAPI** | 네이티브 async, Pydantic 입출력 검증, `/docs` 자동 문서, StreamingResponse로 SSE | 비교적 젊은 생태계 |
| Flask | 단순, 익숙함 | async가 부가 기능, 검증·문서를 별도 라이브러리로 |
| Django REST Framework | 인증·어드민 등 풀스택 | API 서버만 필요한 이 프로젝트엔 무겁고 async 스트리밍이 번거로움 |

## 결정
FastAPI. 요청/응답 모델은 Pydantic(`app/api.py`의 `ProfileIn`, `TastingIn` 등), 스트리밍은 `StreamingResponse(media_type="text/event-stream")`.

## 실측 (data/eval/phase2_bench.json)
| 항목 | 값 |
|---|---|
| 설명 모델 | nvidia/nemotron-3-super-120b-a12b |
| 설명 3개 순차 생성 총 시간 | 13.95 초 |
| 설명 3개 병렬 생성 총 시간 | 11.59 초 |
| 첫 토큰까지(스트리밍) | 3.46 초 (2.6 / 2.94 / 4.84초 평균) |
| 전체 답변까지(비스트리밍이었다면 이만큼 기다림) | 4.65 초 (3.1 / 3.47 / 7.38초 평균) |

## 결과
- 병렬 fan-out과 스트리밍으로 사용자가 기다리는 체감 시간이 줄었다(위 표).
- 동기 psycopg를 스레드로 호출(Windows 이벤트 루프 제약) — DB 호출이 짧아 병목이 아님.
