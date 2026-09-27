# ADR 0008 — 미지 원두 향미 태그 예측을 학습형 모델로 교체

- 상태: 채택 (2026-09-27)

## 맥락
`predict_from_neighbors`의 향미 태그 절반은 이웃(k=10) 투표다(ADR 0007의 lift 게이트로 다듬었지만) — LOO
F1은 겨우 0.3974(n=168, [phase2_loo.json](../../data/eval/phase2_loo.json) 이전 값)로 README·
design-decisions.md 둘 다 "가장 약한 고리"로 적어 뒀다. 스파이크
([docs/spikes/2026-09-27-tag-model-spike.md](../spikes/2026-09-27-tag-model-spike.md))는 같은 1024차원
임베딩에 다층 퍼셉트론(1024→128→T, 시그모이드 다중 라벨)을 얹으면 F1이 0.84까지 뛴다는 것을 보였지만,
그 수치 자체에 **누수**가 있었다: `pipeline/embed.py`의 `embedding_text()`가 원두 자기 자신의
`flavor_tags`를 그 원두의 저장 임베딩 텍스트에 이미 포함시키고 있어서(`", ".join(c.flavor_tags)`),
LOO 채점도 학습도 "정답이 이미 들어 있는 벡터로 정답을 맞히는" 꼴이었다. 스파이크가 n=200 대상만
태그 없이 재임베딩해 확인한 누수 제거 후 추정치는 F1 0.71 근처(0.84에서 하락)였지만, 학습 데이터
7,534건 전체는 재임베딩하지 않아 확정값이 아니었다.

## 결정
운영이 실제로 보는 입력과 학습 입력의 분포를 맞춘 뒤(태그 프리 임베딩), MLP를 프로덕션에 올린다.

1. **`pipeline/embed.py`에 `embedding_text(c, review_text, include_tags=True)`**를 추가해 태그 세그먼트만
   뺄 수 있게 했다(다른 동작은 그대로).
2. **`scripts/train_tag_model.py`**가 활성 태그 보유 원두 7,327건(`roasters_kr` 제외, 고정 LOO 200개
   제외)의 태그 프리 텍스트를 NVIDIA 임베더로 **query 모드**로 재임베딩한다(238요청, 캐시:
   `data/embedded/<model>/tagfree_query.jsonl`, 키+해시로 재실행 시 무료). `app/graphs/analyze_bean.py`가
   `deps.embed(parsed.text)`로 만드는 질의도 태그가 없는 텍스트이므로, 이제 학습 입력과 운영 입력의
   분포가 같다.
3. `sklearn.neural_network.MLPClassifier(hidden_layer_sizes=(128,), ...)`를 `>=30`건 이상 양성인 태그
   54개(105개 중)에 대해 학습한다. scikit-learn은 `pipeline` 의존성 그룹에만 추가했다
   (`uv add --group pipeline scikit-learn`) — 배포 이미지(`uv sync --no-default-groups`)에는 들어가지
   않는다.
4. **`app/core/tagmodel.py`**가 numpy 없이 순수 파이썬으로 순전파(ReLU 은닉 → 시그모이드 출력)를 한다.
   `TagModel.load()`가 `config/tag_model.json`(없으면 `.json.gz`)을 읽고, 없으면 `None`을 반환해
   `app/graphs/analyze_bean.py`가 기존 이웃 투표로 폴백한다.
5. **`app/eval.py loo_accuracy`**가 태그 채점을 태그 프리 질의 임베딩 기본값으로 바꾸고(캐시:
   `data/eval/loo_tagfree_query_embeddings.jsonl`, 학습 스크립트가 고정 200개 대상만 만들어 둔다),
   같은 임베딩으로 `tags`(이웃 투표)와 `tags_model`(학습 모델)을 나란히 채점한다.

## 임계값 선택 — 5-fold CV, 학습 풀 7,327건 (`data/eval/phase2_tag_model.json`)
| 임계값 | 정밀도 | 재현율 | F1 | 카테고리 F1 |
|---|---|---|---|---|
| 0.25 | 0.6761 | 0.6833 | 0.6797 | 0.7874 |
| **0.30** | 0.7134 | 0.6528 | **0.6817** | 0.7837 |
| 0.35 | 0.7457 | 0.6241 | 0.6795 | 0.7746 |
| 0.40 | 0.7715 | 0.5952 | 0.672 | 0.763 |
| 0.45 | 0.7926 | 0.5682 | 0.6619 | 0.7511 |
| 0.50 | 0.8143 | 0.5419 | 0.6507 | 0.7361 |

CV F1이 가장 높은 **0.30**을 채택했다(held-out F1을 미리 보지 않고, CV만으로 골랐다).

## held-out 결과 — 고정 200개 대상 중 태그 보유 168개, **같은 태그 프리 질의 임베딩**으로 채점
| 방법 | 정밀도 | 재현율 | F1 | 카테고리 F1 |
|---|---|---|---|---|
| 이웃 투표 (k=10, thr=0.3, lift=1.2 — 현재 운영 기본값) | 0.3677 | 0.3712 | **0.3695** | 0.6298 |
| **학습 모델 (MLP, thr=0.3)** | 0.7799 | 0.6913 | **0.7329** | 0.8292 |

**+0.363 F1 / +0.199 카테고리 F1** — 스파이크가 추정한 "누수 제거 후" 값(~0.71)과 거의 일치한다. 같은
비교를 `app/eval.py loo_accuracy`(운영 코드 경로: `config/tag_model.json`을 읽어 순수 파이썬으로
순전파)로 다시 돌리면 F1 0.7286/카테고리 F1 0.8249로 나온다 — sklearn이 낸 원래 부동소수와 5자리로
반올림해 저장한 가중치 사이의 미세한 차이가 임계값 0.3 경계의 태그 몇 개를 뒤집는 정도이고(1e-4
단위), 결론은 바뀌지 않는다.

### 한국 로스터리 데이터로 독립 검증 — coffeereview 파생이 아닌 태그
학습에서 제외된 `roasters_kr` 활성 원두 중 향미 태그가 있는 39건(태그는 한국어 노트 단어를 규칙
매핑한 것, `pipeline/enrich.py ko_rule_tags` — coffeereview 규칙/LLM 태그와 다른 출처)에도 같은
방식(태그 프리 질의 임베딩, 학습 안 본 데이터)으로 채점했다:

| 방법 | 정밀도 | 재현율 | F1 | 카테고리 F1 |
|---|---|---|---|---|
| 이웃 투표 | 0.2302 | 0.2710 | 0.2489 | 0.4972 |
| **학습 모델** | 0.4381 | 0.4299 | **0.4340** | 0.6892 |

168개 대상보다 절대치는 낮지만(라벨 출처·도메인이 다른 진짜 분포 이동), 여전히 이웃 투표를 F1
+0.185/카테고리 F1 +0.192로 이긴다 — 모델의 우위가 coffeereview 라벨 스타일에 대한 과적합만은
아니라는 근거다(`data/eval/phase2_tag_model.json`의 `korean_roasters`).

## 크기·지연
- `config/tag_model.json`: **1,266 KiB**(1.24MB) — 2MB 예산 이내라 gzip하지 않았다(초과 시
  `.json.gz`로 자동 전환하는 코드는 `scripts/train_tag_model.py::write_config`에 있다).
- 순수 파이썬 순전파 지연: 실제 학습된 1024→128→54 가중치로 200회 반복 측정, **평균 7.1ms/호출**
  (`m.tags(vec)`) — 50ms 예산의 15% 이하. numpy 없이도 충분히 빠르다.
- Docker 이미지: `docker build -q -t coffee-api:check .`로 만든 이미지에서
  `docker run --rm coffee-api:check python -c "from app.core.tagmodel import TagModel; m=TagModel.load(); print(m and len(m.labels))"` → `54` — 가중치가 이미지 안에 들어 있다(`config/`는
  `.dockerignore` 허용목록에 이미 있었다, ADR로 이전에 옮긴 `tag_ko_extra.yaml`과 같은 이유).
  이 커밋 전(065b1bf) 이미지와 비교하면 **356MB → 358MB(+~2MB)** — 가중치 파일(1.24MB)이 대부분이고
  scikit-learn/numpy는 `pipeline` 그룹 전용이라 이미지에 들어가지 않는다(`uv sync --no-default-groups`).

## 라이선스 게이트
학습 라벨은 전량 `coffees.flavor_tags`에서 뽑았고, 태그 보유 원두의 대다수(7,393/9,155)가
`coffeereview_kaggle` 출처다 — 즉 이 모델은 라이선스가 제한적인 데이터에서 나온 라벨로 학습됐다.
공모전 제출용 오픈 데이터판(`DATA_VARIANT=open`)은 coffeereview를 아예 빼고 배포하므로, 그 배포에서
이 모델을 적재하는 것은 라이선스 취지에 어긋난다. `app/graphs/__init__.py::_tag_model_enabled()`가
`DATA_VARIANT == "open"`이면(또는 운영자가 `TAG_MODEL=off`로 강제하면) `TagModel.load()`를 아예 호출하지
않는다 — 오픈판은 이전처럼 이웃 투표만 쓴다. `app/eval.py`의 `loo_open`도 같은 이유로 `tags_model`을
채점하지 않는다(`loo`만 채점). 상세: [docs/competition/data-recipe-draft.md](../competition/data-recipe-draft.md) 5장.

## 유지한 것
- **속성 예측(산미/바디/단맛)·신뢰도·이웃 근거는 그대로다.** `predict_from_neighbors`는 계속
  attribute 절반을 담당하고, 이번 모델은 그 함수가 내던 `tags`/`evidence`만 교체한다
  (`app/core/predict.py::with_model_tags`).
- **임베딩 실패 시 폴백도 그대로다.** `deps.embed()`가 실패하면(LLMError/HTTPError) 여전히
  `repo.fallback_neighbors` + 이웃 투표로 저신뢰 답을 내고, 학습 모델은 아예 보지 않는다 — 임베딩이 없으면
  모델에 넣을 벡터도 없기 때문이다.
- **`config/tag_model.json`이 없으면**(다른 배포, 재학습 전) `TagModel.load()`가 `None`을 반환하고
  경고를 한 번만 로그로 남긴 뒤 이웃 투표로 돈다 — 오늘 이전과 동작이 같다.

## 재학습 방법
```bash
uv run python scripts/train_tag_model.py             # 재임베딩(캐시 활용) + 학습 + config/tag_model.json 갱신
uv run python scripts/train_tag_model.py --no-embed  # 캐시된 임베딩만 재사용(API 호출 없음)
uv run python -m app.eval loo                         # tags_model을 반영해 phase2_loo.json 갱신
```
캐시(`data/embedded/<model>/tagfree_query.jsonl`)는 원두 키+텍스트 해시로 키가 잡혀 있어, 데이터가
바뀌지 않은 원두는 재실행해도 다시 임베딩하지 않는다. 임베딩 모델을 바꾸면(`EMBED_TASK`) 캐시 디렉터리도
자동으로 바뀐다(`pipeline/settings.py::embedded_dir`).

## 결정하지 않은 것 / 알려진 한계
- 5-fold CV가 다중 라벨 계층화(iterative stratification)가 아니라 일반 `KFold`다 — 스파이크와 같은 한계,
  드문 태그가 폴드 사이에 불균등하게 나뉠 수 있지만 임계값 순위를 바꿀 정도는 아니었다.
- `app/eval.py loo_accuracy`의 태그 프리 캐시는 고정 200개(seed=42, `roasters_kr` 제외) 대상만 있다 —
  `loo_open`처럼 다른 소스 제외 조합으로 뽑은 대상은 캐시에 없는 id가 대부분이라 저장(누수) 임베딩으로
  폴백한다(`tags_query_embeddings.stored_fallback`로 커버리지를 남긴다). 오픈판은 애초에 이 모델을 안
  쓰므로 실질적 영향은 없다.
- 태그 54개는 학습 풀에서 30건 이상 양성인 것만 — 그보다 드문 태그(51개)는 모델이 아예 예측하지
  못한다(이웃 투표는 여전히 어떤 태그든 낼 수 있지만, 이번 변경으로 그 51개는 운영에서도 나오지 않게
  됐다 — 트레이드오프로 기록).
