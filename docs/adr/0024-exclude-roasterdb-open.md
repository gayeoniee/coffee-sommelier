# ADR 0024 — RoasterDB를 오픈·공모전 제출본에서 완전히 제외

- 상태: 채택 (2026-09-30)
- 범위: `coffee_open` DB(`scripts/competition/build_open_db.sh`), `config/feature_model_open.json`(유일한 작성자
  `scripts/train_feature_model.py`), `app/eval.py`의 `VARIANTS`(`compare3`·`loo_open`·`coverage_open`), 그리고
  RoasterDB를 이웃·학습·측정 풀에 넣던 나머지 스크립트(`scripts/ablate_open_labels.py`,
  `scripts/eval_tag_model_open.py`, `scripts/eval_en_aliases.py`). **전체(포트폴리오)판은 불변** — RoasterDB는
  `coffee`(전체) DB에는 그대로 남는다.

## 배경

RoasterDB 샘플(`github.com/RoasterDB/specialty-coffee-roasterdb`, 원두 100개·디카페인 5)은 **CC BY-NC 4.0**
(비영리 조건)으로 배포된다. 이 공모전의 참가 서약서 제6조는 제출 결과물이 게시되고 **생성형 AI 학습에
활용될 수 있다**고 정하는데, 이는 RoasterDB의 비영리 조건과 충돌할 여지가 있다 — coffeereview_kaggle을
이미 이런 이유로 제외한 것과 같은 논리다([`docs/adr/0012-official-brand-beans.md`](0012-official-brand-beans.md)
이전부터 coffeereview는 배제).

문제는 RoasterDB가 **포털 CSV에서만** 빠져 있었다는 점이다(`scripts/competition/export_csv.py`의
`coffees.source` 필터). `coffee_open` DB 자체에는 여전히 100건이 적재돼 있었고, 그 결과:

- **DB**: `coffee_open.coffees`에 `source = 'roasterdb'` 100행 (`build_open_db.sh`의 `EXCLUDE_SOURCES`는
  `coffeereview_kaggle`만 걸렀다).
- **이웃**: 오픈판 추천·특징 모델의 이웃 평균(`app.core.predict.predict_from_neighbors`)이 이 100건을
  후보로 썼다 — 디카페인+산미 페르소나 상위 5 예시(README)가 RoasterDB 원두로 채워졌던 것도 이 때문이다.
- **학습 라벨**: `scripts/ablate_open_labels.py`가 RoasterDB 원두의 향미 노트에서 규칙 기반 약한 라벨(weak
  label, "+B" 레시피)을 뽑아 `scripts/train_feature_model.py`의 산미 특징 모델 학습에 실제로 섞었다
  (재학습 후 `n_train`이 269 → 169로, 정확히 제외된 100건만큼 줄어든 것으로 확인).
- 그 밖에 `scripts/eval_tag_model_open.py`(측정 풀), `scripts/eval_en_aliases.py`(소스 목록),
  `app.eval.VARIANTS`(`compare3`/`loo_open`/`coverage_open`의 "open" 시뮬레이션)도 RoasterDB를 포함한 채
  오픈판 수치를 냈다.

`coffeereview_kaggle`은 이미 DB·이웃·학습 어디에도 없어(ADR 0009/0011 등) 일관됐지만, RoasterDB는 "포털에
안 올린다"만 지켜지고 "제출본 분석에 안 쓴다"는 지켜지지 않고 있었다.

## 결정

RoasterDB를 coffeereview_kaggle과 **완전히 같은 수준으로** 제외한다 — `coffee_open` DB에 적재하지 않고,
이웃으로도 쓰지 않고, 학습 라벨로도 쓰지 않는다.

### 구현

1. **`build_open_db.sh`**: `EXCLUDE_SOURCES="coffeereview_kaggle,roasterdb"`(정규화 단계), `DELETE FROM
   coffees WHERE source = 'roasterdb'`를 coffeereview용 DELETE 옆에 추가(적재 단계는 enriched 캐시를 그대로
   재사용하므로 이 DELETE가 실제 필터 — 스크립트 안 주석 참고).
2. **`scripts/train_feature_model.py`**: 코드 변경 없음 — 산미 "+B"(약한 라벨) 레시피가
   `scripts/ablate_open_labels.py`를 거치므로, 거기서 `roasterdb`를 소스 목록에서 뺀 것만으로 학습 데이터에서
   자동으로 빠진다.
3. **`scripts/ablate_open_labels.py`**: 약한 라벨 후보 쿼리의 `source IN (...)` 목록에서 `roasterdb` 제거.
4. **`app/eval.py`**: `VARIANTS`에 `roasterdb` 추가 — `open = (coffeereview_kaggle, roasters_kr, roasterdb)`,
   `open_plus = (coffeereview_kaggle, roasterdb)`. `open_plus`가 실제 제출본 `coffee_open`과 같은 구성이므로,
   이걸 안 고치면 `compare3`/`loo_open`/`coverage_open`(디카페인 페르소나 상위 5, README 3가지 비교표 등)이
   실제 제출본과 다른(RoasterDB가 섞인) 수치를 계속 냈을 것이다.
5. **`scripts/eval_tag_model_open.py`**: 측정 풀(`MEASURE_SOURCES`)에서 `roasterdb` 제거 — 이제 측정 풀과
   탑재 풀(`SHIP_SOURCES`)이 같다.
6. **`scripts/eval_en_aliases.py`**: `OPEN_SOURCES`에서 `roasterdb` 제거.
7. **바꾸지 않은 것**: `scripts/build_tag_cooc.py`(`COOC_SOURCES`가 이미 `roasters_kr`/`shopify`/
   `shopify_gauged`뿐이라 RoasterDB를 애초에 안 썼음 — 재실행해도 `config/tag_cooc_open.json`은 바이트
   단위로 동일), `scripts/train_attr_model.py`(`--variant open`이 이미 RoasterDB를 뺐고, 현재 오픈판은 이
   스크립트가 아니라 `train_feature_model.py`를 씀), `pipeline/collect/datasets.py`·`normalize/datasets.py`
   (전체판이 RoasterDB를 계속 수집·정규화해야 하므로), `app/core/predict.py`의 `FILL_EXCLUDE_SOURCES`(이미
   RoasterDB 제외, 그대로 유지).

## 결과 — 재구축 전후

`bash scripts/competition/build_open_db.sh` (재실행 전은 2026-09-30 오전 스냅샷, 재실행 후는 이 ADR 반영 후):

| 항목 | 전 (RoasterDB 포함) | 후 (RoasterDB 제외) |
|---|---|---|
| `coffee_open.coffees` 총계 | 2,063 | **1,963** |
| `source = 'roasterdb'` 행 | 100 | **0** |
| 조건 위반 (`phase2_violations.json`) | 0/169 | 0/169 (변화 없음 — 메뉴 로직과 무관) |
| 디카페인 후보 (`phase2_coverage.json`) | 44 | **39** (RoasterDB의 디카페인 5건만큼 정확히 감소) |
| 디카페인 중 국내 구매 가능 (`compare3` `open_plus`) | 20 | **20** (RoasterDB 5건은 전부 해외라 변화 없음) |
| 산미 특징 모델 학습 표본 (`n_train`, "+B" 레시피) | 269 | **169** |
| E1(로스터리 단위 CV) 산미 ±1 · MAE (n=95) | 0.705 · 0.711 | 0.726 · 0.752 (잡음 범위 — 아래 참고) |
| E2(Zenodo 외부 패널) 산미 ±1 · MAE · ρ (n=196) | 85.2% · 0.57 · 0.71 | 86.2% · 0.57 · 0.72 |
| E1 바디 ±1 (n=77) | 0.792 | 0.792 (동일) |
| E2 바디 ±1 (n=195) | 84.6% | 85.1% |
| E1 단맛 답한 비율 · ±1(운영 규칙, 문턱 재조정 포함) | 51.4% · 0.676 (문턱 1.4) | **58.3%** · 0.667 (문턱 **1.3**로 재조정 — 아래 참고) |

산미 E1의 ±1이 0.705→0.726으로 다소 오른 건 표본이 줄면 릿지 계수가 바뀌기 때문으로, ADR 0021이 이미 문서화한
"±1~3%p는 이웃 재선정(동점 처리) 잡음 범위" 수준이다 — MAE가 같이 오른 것도 같은 재적합 때문이며, 레시피
선택("+B") 자체는 이 재학습에서도 그대로 이긴다(`ablation`/`+B`가 여전히 게이지 전용보다 나음).

## 단맛 기권 문턱 재조정 (1.4 → 1.3)

RoasterDB 제외로 이웃 풀이 더 얇아지면서, ADR 0021이 정한 "유사도 가중 이웃 수 ≥ 1.4" 규칙의 E1 답한 비율이
48.6%로 떨어져 ADR 0016의 50% 문턱 아래로 다시 내려갔다(`scripts/eval_open_sweetness_abstain.py` →
`data/eval/open/phase10_open_sweetness.json`의 `weight_sweep`):

| 문턱 | E1 답한 비율 · ±1 · MAE (n=72) |
|---|---|
| 1.0 ~ 1.3 | 58.3% · 0.667 · 0.913 (네 값 모두 동일 — 해당 구간엔 원두가 없음) |
| **1.4 (기존, ADR 0021)** | **48.6%** · 0.714 · 0.871 |
| 1.5 ~ 1.6 | 41.7% · 0.767 · 0.795 |

1.0~1.3 구간은 값이 완전히 같아(그 사이에 가중합이 있는 원두가 없음), 문턱을 낮출수록 얻는 게 없다 — 그래서
**해당 구간에서 가장 엄격한 값인 1.3**을 새 문턱으로 쓴다(더 낮출 이유가 없고, 1.4는 더 이상 50% 문턱을
못 넘긴다). `ABSTAIN_MIN_WEIGHT = {"sweetness": 1.3}`(`scripts/train_feature_model.py`)로 반영하고
`config/feature_model_open.json`을 재학습해 `abstain.min_weight.sweetness`도 1.3으로 갱신했다. E1 답한
비율은 58.3%로 RoasterDB 제외 전(51.4%, 문턱 1.4)보다 오히려 늘고, ±1(0.667)도 그때(0.676) 대비 −0.009로
잡음 범위 안 — ADR 0021의 "커버리지·정확도 두 기준을 함께 넘기는 가장 낮은(가장 엄격한) 문턱" 원칙을 그대로
따른 것이다.

## 재확인하고 바꾸지 않은 것

- **산미·바디·단맛 SHIPPED_RECIPES**: 재학습 후에도 산미 "+B", 바디 "+A wA=1", 단맛 "+C"가 그대로 최선이다
  (`scripts/eval_open_recipes.py` 재실행 결과 — 바디는 이웃 평균 대비 E1 ±1 동률(0.792=0.792)·MAE 개선
  (0.650 vs 0.720)으로 기존 결정 유지, ADR 0016 규칙: "E1·E2 둘 다 이길 때만 교체"를 만족하는 후보가 없었음).
- **`config/tag_cooc_open.json`**: 재실행해도 바이트 단위로 동일 (RoasterDB를 애초에 안 썼음).
- **향미 태그 채우기·공기 채우기 규칙**(ADR 0017/0018): 재실행 결과 수치가 소폭(잡음 범위) 움직였을 뿐 순위는
  그대로 — 교체하지 않음.

## 영향받은 문서

README.md(지식베이스 원두 수, 3가지 비교표, 디카페인 페르소나 예시), `docs/competition/data-recipe-draft.md`
(수치 블록 + A-5/A-6/B-4/3장 라이선스 표), `docs/competition/presentation.md`(Q3/Q4/Q5), `checklist.md`
(K-DATA 질문 해소, DB 재구축 기록), `docs/competition/images/03_decaf_coverage.png`·
`04_external_validation.png`(재생성).
