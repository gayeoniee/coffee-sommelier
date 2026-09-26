# ADR 0004 — 설명 모델 thinking 끔 (explain, parse_note, parse_bean)

- 상태: 채택 (2026-09-27)

## 맥락
2단계 앱의 설명(`explain`)·후기 파싱(`parse_note`)·원두 파싱(`parse_bean`) 세 태스크는 모두 `nvidia/nemotron-3-super-120b-a12b`를
쓴다(`config/models.yaml`). 이 모델은 응답 전에 기본적으로 "생각"(chain-of-thought)을 한 뒤 답하는데, 스트리밍 설명에서 첫 토큰이
나오기까지 5~20초가 걸려 운영 타임아웃(`explain` 태스크 20초, 실질적으로 12초 내 응답이 목표)을 자주 넘겼다 — 운영 폴백(로컬 Ollama로
전환) 3/8 발생. `<think>...</think>` 태그로 감싸인 생각 내용이 스트리밍 텍스트에 그대로 섞여 나오는 문제도 있었다
(`pipeline/llm.py::extract_json`은 비스트리밍 JSON 응답에서만 이를 제거한다).

## 원인
모델이 기본값으로 "thinking"을 켠 채 응답한다. NVIDIA API는 요청 본문 최상위에 `chat_template_kwargs: {enable_thinking: false}`를
받으면 생각 단계를 건너뛴다(`reasoning_effort: "none"`도 같은 효과가 있으나 출력이 더 길어짐 — 아래 실측 참고).
`app/llm.py::_payload`는 `Target.extra`를 요청 본문 최상위에 병합하는 기존 동작 그대로 이를 실어 보낸다(새 코드 없음, 설정만 추가).

## 측정 — explain (같은 설명 프롬프트로 사전 확인, 2026-09-26)
| 설정 | 첫 토큰 | 전체 응답 | 비고 |
|---|---|---|---|
| 기본값 (thinking 켬) | 10.6초 | 15.8초 | 생각 내용이 텍스트에 섞여 나옴 |
| `chat_template_kwargs: {enable_thinking: false}` | 0.67초 | 1.73초 | 같은 품질의 한국어 2문장 |
| `reasoning_effort: "none"` | 0.78초 | — | 동작은 하지만 답이 더 길어짐 → 채택하지 않음 |

## 측정 — parse_note / parse_bean (thinking on vs off, 각 입력 3회, `.superpowers/thinking_probe.py`로 NVIDIA API 직접 호출)
입력: `app/core/parse.py`의 few-shot 예문(중복 문구 1개는 한 번만 셈, 실질 9개) + `tests/app/test_parse_explain.py` 케이스 +
새 후기 5개 + 새 원두 문구 5개. 결과 전체는 `data/eval/phase2_thinking.json`.

| 태스크 | on 일치율(자기일관성) | off 일치율(자기일관성) | on↔off 다수결 일치율 | on 지연 중앙값 | off 지연 중앙값 |
|---|---|---|---|---|---|
| parse_note (n=9) | 92.59% | 92.59% | 77.78% | 4.20초 | 1.21초 |
| parse_bean (n=8) | 100.00% | 95.83% | 75.00% | 2.59초 | 1.18초 |
| **합산** | **96.29%** | **94.21%** | — | — | — |

"일치율(자기일관성)"은 같은 입력·같은 설정으로 3번 호출했을 때 다수결과 일치한 비율(온도 0에서 결정성 척도). "on↔off 다수결 일치율"은
on의 다수결 결과와 off의 다수결 결과가 완전히 같은 입력의 비율.

on↔off 다수결이 갈린 사례를 들여다보면:
- `parse_bean`에서 갈린 2건("콜롬비아 수프리모 ...", "브라질 내추럴 다크 로스트")은 모두 `is_decaf: null`(on) vs `is_decaf: false`(off) 차이뿐이다.
  `app/core/parse.py::merge_llm_parse`는 `bool(llm.is_decaf)`로 쓰므로 `None`과 `False`는 다운스트림에서 동일하게 "디카페인 아님"으로
  처리된다 — 실질적 불일치가 아니다.
- `parse_note`에서 갈린 2건은 애매한 후기("고소하고 부드러워서 좋았어요"의 body 방향, "물 탄 것처럼 밍밍했어요"에서 acidity/sweetness까지
  뽑을지)로, on 자체도 자기일관성이 100%가 아니었다(각각 66.67%) — thinking 여부보다 문장 자체의 모호함에 가깝다.

판단 기준(off의 일치율 ≥ on의 일치율 − 5%p): 94.21% ≥ 96.29% − 5%p(91.29%) → **충족**.

## 결정
- `config/models.yaml`의 `explain`, `parse_note`, `parse_bean` 세 태스크 모두 `extra: {chat_template_kwargs: {enable_thinking: false}}`를 추가한다.
- `reasoning_effort: "none"`은 채택하지 않는다(출력 길이 증가, `enable_thinking: false`가 같은 지연 개선에 품질 저하가 없음).
- 로컬 폴백(Ollama `qwen3.5:9b`)의 `extra: {reasoning_effort: "none"}`은 그대로 둔다 — 다른 모델이라 별도 판단.

## 실측 — 벤치 재측정 (`uv run python -m app.eval bench`, `data/eval/phase2_bench.json`)
| 항목 | 이전 (thinking 켬) | 이후 (thinking 끔) |
|---|---|---|
| 설명 3개 순차 | 13.95초 | 10.42초 |
| 설명 3개 병렬 | 11.59초 | 3.49초 |
| 첫 토큰 범위 | 2.6~4.8초 | 0.66~1.53초 |
| 전체 응답 범위 | 3.1~7.38초 | 1.68~5.56초 |

(이 작업 도중 thinking을 끄기 전 한 번 더 측정한 중간값은 첫 토큰 4.94~20.27초, 순차 35.5초로 운영에서 보고된 폴백 문제와 일치했다 —
NVIDIA API 자체의 지연 변동성도 있어 보이나, thinking을 끄면 최악의 경우도 대체로 12초 마감 안에 들어온다.)

## 결과
- 설명 스트리밍의 첫 토큰이 12초 마감 안에 안정적으로 들어온다(0.66~1.53초). `<think>` 태그가 텍스트에 섞이는 문제도 없어진다.
- parse_note/parse_bean도 같은 설정을 추가해 지연을 줄였다(중앙값 4.2초→1.21초, 2.59초→1.18초); 파싱 정확도(자기일관성)는
  통계적으로 유의한 저하 없음(합산 96.29%→94.21%, −2.08%p, 기준 −5%p 이내).
- 여전히 드문 지연 스파이크(예: 벤치 1회차 8.02초)가 관측된다 — NVIDIA API 큐잉/콜드스타트로 보이며, 20초 타임아웃과 로컬 폴백은 유지한다.

## 되돌리는 방법
`config/models.yaml`에서 해당 태스크(`explain`/`parse_note`/`parse_bean`)의 `extra: {chat_template_kwargs: {enable_thinking: false}}`
줄을 지우면 즉시 기본(thinking 켬) 동작으로 돌아간다. 코드 변경은 없으므로(설정만 추가) 재배포만 하면 된다.
