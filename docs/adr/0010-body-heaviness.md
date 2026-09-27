# ADR 0010 — 바디를 품질 서브스코어가 아닌 무게감으로 재라벨링

- 상태: 채택 (2026-09-27)

## 맥락
`pipeline/normalize/datasets.py::normalize_coffeereview`/`normalize_cqi`가 `coffees.body`를 만드는 방식은
coffeereview·CQI 원자료의 "body" 컬럼을 소스 내 백분위로 5분위화하는 것뿐이었다(`pipeline/rules.py::to_quintile`).
문제는 그 컬럼 자체가 **무게감(라이트~헤비)이 아니라 큐핑 품질 점수**라는 점이다 — "이 원두의 바디가 얼마나
좋은가"이지 "이 원두가 얼마나 무거운가"가 아니다. 그 결과 원산지별 평균이 실제 무게감과 반대로 나온다:

| 원산지 | 재라벨 전 바디 평균 | 원두 수 |
|---|---|---|
| 에티오피아 | 3.90 | 1,465 |
| 케냐 | 3.78 | 644 |
| 인도네시아 | 3.22 | 451 |
| 브라질 | 3.08 | 409 |

가벼운 워시드 에티오피아가 무거운 내추럴/세미워시드 인도네시아·브라질보다 "바디가 높게" 나온다 — 실제
무게감과 정반대다. 만델링(수마트라, 습식 탈각으로 유명한 헤비바디 원두) 315건의 평균도 catalog 중앙값
근처로 뭉개져 있었다. 산미는 같은 방식(acidity 서브스코어의 5분위화)인데도 케냐 3.8 > 브라질 2.8로
실제 강도와 방향이 맞아 그대로 뒀다 — coffeereview의 acidity 서브스코어는 "밝기/강도" 자체를 채점하는
반면 body 서브스코어는 "이 무게감이 그 원두 프로필에 맞게 잘 표현됐는가"를 채점해서, 후자만 방향이
어긋난다.

## 결정
`scripts/relabel_body.py`가 리뷰 원문에서 mouthfeel/body/texture를 언급하는 문장(정규식, 없으면 첫
400자로 폴백)을 뽑아 10건씩 묶어 `judge_explain2` 태스크(NVIDIA `openai/gpt-oss-20b`, `reasoning_effort:
low`, config/models.yaml)에 앵커링된 1-5 척도로 무게감을 다시 매기게 한다:

```
1 = light, tea-like, delicate      4 = full, syrupy, creamy
2 = light-medium                   5 = heavy, viscous, dense
3 = medium
```

텍스트가 무게감을 전혀 언급하지 않으면 null. 결과는 `data/enriched/body_heaviness.jsonl`에 원두 키 +
추출 문장의 해시로 캐시해(append, idempotent) 재실행 시 이미 처리한 원두는 다시 묻지 않는다. 요청은
`pipeline/embed.py`의 클라이언트 쪽 레이트 제한과 같은 방식(`scripts/relabel_body.py::Throttle`)으로
스로틀링하고, 스레드 풀(기본 8개 동시 요청)로 전체 대상(활성 coffeereview 원두 중 리뷰 원문이 있는
7,392건, 약 740 요청)을 처리한다.

`pipeline/enrich.py::apply_body_heaviness()`가 `run_enrich`의 마지막 단계로 이 결과를 병합한다:
- **coffeereview_kaggle**: 재라벨 결과의 `body` 값으로 교체한다. 리뷰 원문이 아예 없어 재라벨을 시도조차
  하지 않은 원두(원자료 결측)만 기존 5분위 값을 그대로 둔다 — 시도했지만 텍스트가 무게감을 언급하지
  않았거나(judged null) LLM 호출 자체가 실패한 원두는 `None`이 된다. 잘못된 값을 계속 들고 있는 것보다
  "근거 없음"이 정직하다.
- **CQI**: `body`를 항상 `None`으로 비운다. CQI의 "Body" 컬럼도 coffeereview와 같은 결함(품질 점수이지
  무게감이 아님)을 갖고 있는데, CQI에는 재라벨에 쓸 리뷰 원문 자체가 없다 — 커핑 시트 점수 하나뿐이다.
  판단할 텍스트가 없으므로 값을 지어내지 않는다. `app/graphs/analyze_bean.py`의 이웃 평균/학습 속성
  모델이 대신 채운다.
- **roasters_kr**: 리뷰 원문이 없는 노트 단어뿐인 소스라 LLM 재라벨 대상이 아니다. 대신
  `pipeline/enrich.py::ko_body_cue()`가 기존 한국어 규칙 경로(`_apply_rules`)에서 노트 단어를 직접 본다
  — 묵직/무거운/풀바디 → 4, 가벼운/라이트/깔끔한 바디 → 2. 값이 이미 있으면 규칙은 건드리지 않는다.

## 스팟체크 — 20건
전체 결과(`data/enriched/body_heaviness.jsonl`, 판정된 6,527건)에서 무작위로 20건(seed 7)을 뽑아
문장과 판정을 직접 대조했다:

| 판정 | mouthfeel 문구 발췌 |
|---|---|
| 2 | Round, backgrounded but softly resonant acidity; lightly syrupy mouthfeel. |
| 2 | Sweet-tart structure with brisk acidity; delicate, silky mouthfeel. |
| 4 | Round, juicy acidity; plush mouthfeel. |
| 3 | Lightly syrupy in mouthfeel and bittersweet in structure. |
| 1 | Sweet-tart structure with citric acidity; dry, velvety mouthfeel. |
| 4 | Sweetly bright structure with vibrant, balanced acidity; lively, silky-smooth mouthfeel. |
| 3 | Rich, round, but authoritative acidity; lightly syrupy mouthfeel. |
| 2 | Sweetly tart with vibrant, malic (apple-like) acidity; delicately satiny mouthfeel. |
| 4 | Full, buttery mouthfeel; richly drying finish. |
| 3 | (긴 인용문) ...silky mouthfeel and solid finish... |
| 4 | Sweet-toned structure with gently brisk acidity; plush, syrupy mouthfeel. |
| 1 | Muted acidity; rather lean mouthfeel. |
| 3 | Sweet-tart-savory structure with soft acidity; satiny-smooth mouthfeel. |
| 4 | Sweet structure with brisk acidity; full, satiny mouthfeel. |
| 5 | Tart-sweet structure with deeply tangy, lactic acidity; very full, syrupy-smooth mouthfeel. |
| 2 | Rich, lyrical acidity; smooth, silky mouthfeel. |
| 4 | Pronounced but gentle acidity; plush, silky mouthfeel. |
| 2 | High-toned structure with sweetly bright acidity; silky, buoyant mouthfeel. |
| 4 | Crisply sweet-savory in structure; viscous, velvety mouthfeel. |
| 4 | Sweet-savory structure with gentle but lively acidity; creamy-smooth mouthfeel. |

20건 중 19건은 직접 읽고 판단한 값과 ±1 이내로 일치했다(**19/20 = 95%**). 큐핑 어휘를 일관되게 앵커에
맞춘 편이다 — "lightly syrupy"류는 대체로 2~3, "plush/full/syrupy"류는 4, "delicate/silky/satiny"류는
2, "lean/dry"류는 1로 갈렸다. 유일한 불일치는 "lively, silky-smooth mouthfeel"을 4(full)로 매긴
건이다 — "silky"는 이 데이터셋의 다른 문장 대부분에서 라이트~미디엄(2)의 신호로 쓰이는데, 이 건은
그보다 두 단계 높게 나왔다(경계 사례로 남겨둔다). "lightly syrupy"가 문장마다 2 또는 3으로 다르게
매겨진 것도 완벽히 일관되진 않지만 ±1 안이라 분류상 문제는 없다.

## 결과 — 재라벨 전후 원산지별 바디 평균

| 원산지 | 재라벨 전 | 재라벨 후 | n (전 → 후) |
|---|---|---|---|
| 인도네시아 | 3.22 | **3.21** | 451 → 379 |
| 케냐 | 3.78 | **3.14** | 644 → 548 |
| 에티오피아 | 3.90 | **3.09** | 1,465 → 1,311 |
| 브라질 | 3.08 | **2.97** | 409 → 238 |
| (참고) 만델링(수마트라) | 2.40 | **3.50** | 47 → 42 |

정확히 뒤집히진 않았다 — 인도네시아(3.21)가 이제 에티오피아(3.09)보다 높아 핵심 결함(가벼운 에티오피아가
무거운 인도네시아보다 "바디가 높게" 나오던 것)은 고쳐졌고, 만델링(습식 탈각 특유의 헤비바디로 유명한
프로필)은 2.40 → 3.50으로 뚜렷이 올라갔다. 다만 브라질(2.97)은 여전히 에티오피아보다 낮게 나온다 —
원산지 평균은 그 원산지의 모든 가공·로스팅이 섞인 값이라(브라질은 워시드·펄프드내추럴 같은 가벼운
가공도 많다), 원산지 단위 스테레오타입보다 원두별 실제 리뷰 문구를 그대로 반영하는 지금 값이 더
정직하다고 본다. n이 전반적으로 줄어든 것은 정직한 결과다 — 텍스트가 무게감을 언급하지 않은 원두는
`None`이 되어 평균에서 빠진다(아래 참고).

## LOO 대상 게이트도 함께 고쳤다 — "산미 AND 바디" → "산미만"
바디가 재라벨로 sparse해지는 것은 설계상 당연한 결과다(CQI는 판단할 리뷰 원문이 없어 통째로 결측,
coffeereview도 텍스트가 무게감을 언급하지 않으면 결측). 그런데 `app.repo.Repo.random_coffee_ids_for_loo`
(LOO·compare3가 공유하는 대상 추첨 함수)는 원래 후보를 **"acidity AND body 둘 다 NOT NULL"**로
걸렀다 — 바디를 sparse하게 만든 바로 그 변경이 대상 후보 자체를 걸러내 버려서, 이번엔 손대지 않은
**산미** 비교까지 망가뜨렸다(CQI가 대상에서 통째로 빠지며 `compare3`는 대상 0개, 전체판 `loo`는
대상 모집단이 전부 coffeereview로 치우쳤다).

바로잡은 게이트는 **"acidity NOT NULL"만** 요구한다(`app/repo.py::random_coffee_ids_for_loo`). 바디·
단맛은 원래도 `app.eval.loo_accuracy`가 각 속성의 truth가 있는 대상만 따로 세어 자기 몫의 `n`을
보고하므로(`if t is None or v is None: continue`), 대상 게이트를 산미만으로 좁혀도 바디·단맛 채점
로직은 손댈 필요가 없다 — sparse한 속성은 sparse한 채로, 있는 그대로 재면 된다. 이 변경의 핵심
효과: **acidity는 원래 한 번도 결측이 된 적이 없으므로**, 같은 시드(42)로 뽑은 대상 200개(전체판) /
CQI 200개(compare3)가 **재라벨 전과 정확히 같은 집합으로 복원된다** — 산미 지표가 재라벨 전 숫자와
사실상 같아지는 것으로 이를 확인했다(아래 표).

## 지표 변화 — LOO 산미/바디 MAE·±1 (학습 모델 vs 이웃 평균)

| 지표 | 재라벨 전 (ADR 0009) | 재라벨 후 |
|---|---|---|
| LOO 산미 ±1 (이웃 투표) | 0.735 (n=200) | **0.735 (n=200)** — 대상 동일 |
| LOO 바디 ±1 (이웃 투표) | 0.64 (n=200) | **0.6405 (n=153/200)** |
| 학습 모델 산미 MAE (모델/이웃) | 0.6231 / 0.7336 | **0.6274 / 0.7307** (n=200/198, 대상 동일) |
| 학습 모델 바디 MAE (모델/이웃) | 0.7649 / 0.8831 | **0.6706 / 0.8709** (n=153/153) |
| 학습 모델 바디 ±1 (모델/이웃) | 0.725 / 0.645 | **0.7778 / 0.634** |
| 학습 모델 단맛 MAE (모델/이웃) | 0.4393 / 0.5618 | **0.4406 / 0.5653** (n=99/95, 대상 동일) |
| 바디 학습 표본 | 8,798 | **6,436** |
| 태그 F1 (이웃 투표 / 학습 모델) | 0.3695 / 0.7286 (n=168) | **0.3683 / 0.7308** (n=168, 대상 동일) |

**산미·단맛·태그 F1은 사실상 그대로다** — 대상 200개가 재라벨 전과 같은 집합으로 복원됐으니 당연하다
(작은 반올림 차이는 attr 모델을 다시 학습하며 생긴 부동소수점 수준 잡음). **바디는 표본이 8,798 →
6,436건으로 줄었지만(CQI 결측 + 무게감 언급 없는 리뷰 855건 + 실패 10건) MAE·±1 둘 다 오히려
좋아졌다** — 품질 점수보다 무게감이 텍스트에서 더 배우기 쉬운 신호였다는 뜻으로 읽힌다. held-out
채점도 200개 중 실제로 바디가 있는 153개로만 이뤄진다(n=153) — 이게 "몰래 줄인" 표본이 아니라 저
153개가 곧 "이 원두들의 진짜 무게감 정답이 존재하는 대상"이라는 뜻이다.

### compare3(`python -m app.eval compare3`) — 산미는 복원, 바디는 구조적으로 못 잰다
`compare3`의 LOO 대상은 "사람이 매긴 CQI 커핑 점수"라는 이유로 CQI 200개로 고정한다
(`LOO_TARGET_SOURCES = ("cqi",)`). 게이트를 산미만 요구하도록 고친 뒤에는 CQI 200개가 재라벨 전과
동일하게 뽑히고, **산미 ±1은 세 변형(전체/오픈/오픈+로스터리) 모두 0.495로 재라벨 전과 완전히 같다**
(`target_ids_sha1`도 동일). 반면 **바디는 여전히 `n: 0`, `within1: null`이다** — 이건 버그가 아니라
CQI라는 소스 자체의 한계다: CQI 200개 "전원"의 바디가 결측이라(다른 소스처럼 일부만 sparse한 게
아니라 CQI 전체가 100% 결측), 대상을 몇 개를 뽑든 그 안에서 바디 truth를 가진 원두가 하나도 없다.
`decaf_probe`(디카페인 후보 비교)는 대상 추첨과 무관해 원래부터 그대로 동작한다.

**결론: `compare3`로 바디 정확도를 재려면 대상 소스 자체를 바꿔야 한다** — 사람이 매긴 무게감 라벨을
가진 다른 오픈 라이선스 소스가 지금은 없다(로스터리 원두는 노트 단어 규칙/LLM 추정이지 사람 평가가
아니다). 이 ADR 범위에서 새로 설계하지 않고 후속 과제로 남긴다.

### 오픈판 DB(`coffee_open`) 직접 LOO — 대상은 200으로 복원, 바디만 sparse
`coffee_open` DB에서 그냥 `python -m app.eval loo`(고정 소스 제한 없음)를 돌리면 대상이 요청한
200개 그대로 나온다(`data/eval/open/phase2_loo.json`, `target_ids_sha1`도 메인 DB의 `loo_open`과
동일) — 게이트를 산미만 요구하도록 고친 덕분이다. 산미 ±1은 0.5126(n=199, 재라벨 전 0.5202/n=198과
거의 같다), 바디는 그 200개 중 실제로 무게감이 있는 5개뿐이라 n=5(±1 1.0) — 오픈판(CQI가 87.7%)은
바디 정답을 가진 원두 자체가 원래도 희소했다는 뜻이다. 메인 DB에서 `loo_open`(exclude_sources 방식)
으로 재면 산미 n=199/0.5126, 바디 n=3/1.0으로 아주 비슷하지만 완전히 같지는 않다 — 이웃 후보 풀
구성이 `coffee_open` 직접 조회와 `exclude_sources` 방식 사이에 미묘하게 다르기 때문이다(로스터리
원두가 전자에서만 이웃 후보에 낀다).

## 오픈판에 미친 영향 — `config/attr_model_open.json`이 이제 아무 속성도 싣지 않는다
오픈판 바디 학습 표본이 CQI(약 1,370건, 대부분)를 잃으면서 **1,379건 → 31건**(`roasters_kr`/`shopify`만
남음)으로 무너졌다 — `MIN_OPEN_LABELS`(300건) 미달이라 바디 헤드는 탑재하지 않는다. acidity는 라벨 수
자체(1,373건)는 게이트를 통과하고, 이번엔 게이트 수정 덕에 `scripts/train_attr_model.py`의 held-out도
**진짜 200개**로 복원됐다(재라벨 전과 동일 대상) — 그 결과 모델 MAE 1.14/±1 0.45, 이웃 MAE 1.1004/±1
0.48로 **모델이 MAE·±1 둘 다 이웃 평균보다 나쁘다는 걸 신뢰할 수 있는 표본으로 확인했다**(ADR 0009가
n=200으로 처음 내린 판단과 사실상 같은 수치 — 정책을 뒤집을 이유가 없다). `config/attr_model_open.json`은
탑재할 속성이 하나도 없어 파일 자체를 지웠다(`scripts/train_attr_model.py`가 `attrs_out`이 비면 파일을
안 쓰는 것과 같은 규칙) — **오픈판(공모전 제출본)은 이제 산미·바디·단맛 셋 다 학습 모델 없이 이웃
평균만 쓴다.** 자세한 수치는 `data/eval/phase2_attr_model_open.json`.

## Docker 확인
`docker build -q -t coffee-api:check .`로 만든 이미지에서: 전체판 `AttrModel.load()` →
acidity/body/sweetness 3개 그대로(가중치 릿지+MLP 조합이 바뀌었을 뿐 개수는 동일), `TagModel.load()` →
54개 태그(변화 없음). 오픈판(`-e DATA_VARIANT=open`): `AttrModel.load()` → `None`(로그: "no learned
attribute model found ... attr_model_open.json") — 파일 자체가 없으므로 기대한 대로 이웃 평균 폴백.
이미지 크기 360MB(ADR 0009 때 359MB에서 +1MB, `config/attr_model.json`이 gzip 없이 1.2MB로 커진 만큼).

## 재현 방법
```bash
uv run python scripts/relabel_body.py                        # ~740 요청, 배치 10, 동시 8, idempotent
uv run python -m pipeline run --only load                    # enriched/coffees.jsonl 재적재
# app/repo.py::random_coffee_ids_for_loo -- LOO 대상 게이트는 acidity NOT NULL만 요구한다(바디까지
# 요구하면 바디를 sparse하게 만든 이 재라벨 자체가 산미 대상 후보까지 걸러내 버린다). 코드 변경일 뿐
# 재적재로 반영되는 게 아니다.
uv run python scripts/train_attr_model.py --variant full
uv run python scripts/train_attr_model.py --variant open
uv run python -m app.eval loo loo_open compare3
EVAL_DIR=data/eval/open DATABASE_URL=postgresql://coffee:coffee@localhost:5432/coffee_open \
  DATA_VARIANT=open uv run python -m app.eval violations loo coverage convergence
uv run python scripts/check_readme_numbers.py                # README 수치 대조, 0이어야 통과
```

## 결정하지 않은 것 / 알려진 한계
- **`compare3`는 CQI가 대상 소스인 한 바디를 절대 잴 수 없다(가장 중요한 후속 과제).** LOO 대상
  게이트를 산미만 요구하도록 고쳐서 산미 비교(0.495, 세 변형 동일)는 재라벨 전과 완전히 복원됐지만,
  CQI 200개 "전원"의 바디가 결측이라 대상을 아무리 뽑아도 바디 truth가 하나도 없다 — `n: 0`은
  구조적 한계지 버그가 아니다. 대안(사람이 매긴 무게감 라벨을 가진 다른 오픈 소스를 찾기, 바디 전용
  대상 풀을 CQI 밖에서 뽑기 등)은 이 ADR 범위 밖에 남겨 둔다.
- **오픈판(공모전 제출본) 속성 모델이 통째로 사라졌다.** 바디는 학습 표본이 300건 미만(31건)으로
  떨어져 게이트 미달, acidity는 이제 진짜 200개짜리 held-out(재라벨 전과 동일 대상)에서 이웃 평균보다
  MAE·±1 둘 다 나쁘다고 재확인해(1.14/0.45 vs 1.1004/0.48) 정책상 계속 뺀다 —
  `config/attr_model_open.json` 자체가 없다. 오픈판은 이제 세 속성 모두 이웃 평균에만 의존한다.
- CQI는 이제 바디가 전혀 없다 — 이웃 평균/속성 모델이 커버하지만, CQI가 학습 풀의 큰 비중(1,546건)을
  차지했던 오픈판 바디 헤드는 표본이 거의 다 사라졌다(위 항목 참고).
- 리뷰 원문이 없는 극소수 coffeereview 원두(7,393건 중 1건)는 재라벨 대상이 아니어서 기존 5분위 값을
  그대로 유지한다 — 이 한 건만 여전히 품질 서브스코어 기반이다.
- gpt-oss-20b 앵커 스케일 판정 자체의 정확도는 20건 스팟체크(무작위 seed 7, 19/20 = 95% ±1 일치)로만
  확인했다 — 사람 다중 판정자 골드셋(ADR 0009 문서의 "judge 간 일치도" 같은 형식)은 이번 범위에
  포함하지 않았다.
- acidity는 그대로 뒀다(맥락 문단 참고) — coffeereview의 acidity 서브스코어는 이미 강도를 재는 축이라
  같은 결함이 없다고 판단했다; 별도로 검증하지는 않았다.
- 실패한 재라벨 요청 10건(7,392건 중 0.14%, 배치 JSON 파싱 오류)은 재시도로도 계속 실패해 `body =
  None`으로 남겼다 — 값을 지어내지 않는 쪽을 택했다.
- **작업 환경 주의 (바디 재라벨과 무관, 재현 시 참고).** 이 워크트리의 `data/raw`에는 `roasters_kr`
  스냅샷만 있고 coffeereview·CQI·RoasterDB·프랜차이즈 메뉴·SCA 휠 원자료가 없다(원본 스크랩은 무겁고
  이미 정규화/보강까지 끝났으면 지우는 편이라 보인다). `scripts/competition/build_open_db.sh`가
  내부적으로 돌리는 `pipeline run --only normalize`가 이 raw 부재 때문에 `data/normalized_open`의
  brands/menu_items/taxonomy를 한 번 비웠다가(메뉴 471→0, 태그 분류 121→0) 발견 즉시
  `data/normalized/*.jsonl`(완전한 값을 담은 정규 위치)을 그대로 복사해 다시 적재해 바로잡았다 —
  coffeereview 제외는 `coffees`/`reviews`가 아니라 이후의 `DELETE ... WHERE source =
  'coffeereview_kaggle'`(리뷰는 FK cascade)로 이뤄지므로 이 복사는 안전하다. 최종 `coffee_open`은
  `menu_items=471`, `flavor_taxonomy=121`로 정상이다 — 하지만 원자료 스냅샷이 없는 환경에서
  `build_open_db.sh`를 그대로 돌리면 똑같이 깨질 수 있다는 걸 기록해 둔다.
