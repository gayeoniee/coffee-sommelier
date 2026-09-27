# Spike: raising flavor-tag prediction quality for unknown beans

Read-only spike against the dev DB (`coffee`, 9,155 active beans with embeddings, 7,534 tagged). No product
code changed; no commits made. Scripts live in `.superpowers/spike/` in this worktree (`common.py` +
`exp_a.py`..`exp_e.py`, `exp_leak.py`, `exp_d_leak.py`, `sanity_check.py`); raw results in the sibling
`results_*.json` files.

**Sanity check** (`sanity_check.py`): reimplementing `predict_from_neighbors` + the same k=10/thr=0.3/lift=1.2
defaults with an in-memory exact-cosine kNN over the n=200/seed=42 sample reproduces the documented baseline
almost exactly: **P=0.3933, R=0.4015, F1=0.3974, category F1=0.6441** vs the documented 0.393/0.402/0.397/0.643.
The small residual gap is `repo.neighbors`' approximate HNSW index (`ef_search=200`, relaxed iterative scan)
vs our exact brute-force cosine — expected, not a bug. All experiments below build on this validated harness.

## Headline table

All rows score the SAME two target samples: **n=200** = `repo.random_coffee_ids_for_loo(200, seed=42,
exclude_sources=("roasters_kr",))` — byte-identical to what `app/eval.py loo_accuracy` draws by default — and
**n=1000** = 1,000 tagged, non-`roasters_kr` beans, `random.Random(7).sample(...)`, a larger/lower-noise
sample. "n" below is the number actually scored (targets with non-empty truth tags; matches `loo_accuracy`'s
`tag_n`).

| Method | Sample | P | R | F1 | category F1 | n |
|---|---|---|---|---|---|---|
| **Baseline** (k=10, thr=0.3, count, lift=1.2 — current production) | n=200 | 0.3933 | 0.4015 | **0.3974** | 0.6441 | 168 |
| Baseline | n=1000 | 0.3787 | 0.4028 | **0.3904** | 0.6154 | 1000 |
| **A. Best vote tuning** (k=30, thr=0.2, count, lift=1.0) | n=200 | 0.3893 | 0.4697 | **0.4258** | 0.6554 | 168 |
| A. Best vote tuning | n=1000 | 0.3716 | 0.4793 | **0.4186** | 0.6409 | 1000 |
| B. A + origin/process-aware neighbours | n=200 | 0.3918 | 0.4356 | **0.4126** | 0.6441 | 168 |
| B. A + origin/process-aware neighbours | n=1000 | 0.3518 | 0.4405 | **0.3912** | 0.6189 | 1000 |
| C. Text-only (rule tags from own notes text — **leaky, see caveat**) | n=200 | 1.0000 | 0.9621 | **0.9807** | 0.9853 | 168 |
| C. Text-only (leaky) | n=1000 | 0.9859 | 0.9661 | **0.9759** | 0.9845 | 1000 |
| C. Union(best-A, text) — leaky | n=200 | 0.5692 | 0.9735 | **0.7184** | 0.8185 | 168 |
| C. Union(best-A, text) — leaky | n=1000 | 0.5418 | 0.9723 | **0.6959** | 0.7975 | 1000 |
| D. Logistic regression, OvR, 5-fold CV, thr=0.3, all 7,534 tagged beans | CV | 0.8168 | 0.5979 | **0.6904** | 0.7847 | 7504 |
| D. Logistic regression, held out, thr=0.3 (train on the rest) | n=200 | 0.8416 | 0.6136 | **0.7097** | 0.8056 | 168 |
| D. Logistic regression, held out, thr=0.3 | n=1000 | 0.8265 | 0.6127 | **0.7037** | 0.7978 | 1000 |
| D. MLP (1x128), 5-fold CV, thr=0.35, all 7,534 tagged beans | CV | 0.8524 | 0.7886 | **0.8193** | 0.8880 | 7504 |
| **D. MLP, held out, thr=0.35 (train on the rest)** | n=200 | 0.8870 | 0.8030 | **0.8429** | 0.9023 | 168 |
| **D. MLP, held out, thr=0.35** | n=1000 | 0.8599 | 0.8022 | **0.8301** | 0.8996 | 1000 |
| D. MLP, held out, top-k=5 (no threshold) | n=200 | 0.5560 | 0.8845 | **0.6827** | 0.8217 | 168 |
| D. MLP, held out, top-k=5 | n=1000 | 0.5240 | 0.9059 | **0.6640** | 0.7881 | 1000 |
| E. Union(MLP, best-A) | n=200 | 0.5156 | 0.8447 | **0.6403** | 0.7820 | 168 |
| E. Union(MLP, best-A) | n=1000 | 0.4873 | 0.8385 | **0.6164** | 0.7591 | 1000 |
| E. Intersection(MLP, best-A) | n=200 | 0.9040 | 0.4280 | **0.5810** | 0.6628 | 168 |
| E. Intersection(MLP, best-A) | n=1000 | 0.8822 | 0.4429 | **0.5898** | 0.6729 | 1000 |

**MLP (Experiment D) wins outright** — +0.44 F1 / +0.26 category-F1 over baseline on n=200 — but see the
**leakage caveat** below before trusting that number: a real re-embedding check on n=200 shows the true,
leak-free gain is smaller (D still wins, roughly +0.31 F1, not +0.44).

## A. Vote-tuning grid (72 combos: k ∈ {10,20,30} × thr ∈ {0.2,0.3,0.4} × weighting ∈ {count, similarity,
softmax(τ=0.05), softmax(τ=0.1)} × lift ∈ {1.0,1.2})

Neighbours fetched once per target at k=30 and sliced, so the whole grid costs one kNN pass per target
(72 combos over n=200 in 2.1s, over n=1000 in 10.9s — full grid in `results_a.json`).

Top of the n=200 grid:
| k | thr | weighting | lift | P | R | F1 | cat F1 |
|---|---|---|---|---|---|---|---|
| 20 | 0.2 | similarity | 1.0 | 0.3955 | 0.4659 | **0.4278** | 0.6709 |
| 30 | 0.2 | count | 1.0 | 0.3893 | 0.4697 | **0.4258** | 0.6554 |
| 20 | 0.2 | count | 1.0 | 0.3766 | 0.4886 | **0.4254** | 0.6714 |
| 20 | 0.3 | count | 1.0 | 0.4739 | 0.3788 | **0.4211** | 0.6020 |

Top of the n=1000 grid (less noisy — used to pick the final "best A"):
| k | thr | weighting | lift | P | R | F1 | cat F1 |
|---|---|---|---|---|---|---|---|
| 30 | 0.2 | count | 1.0 | 0.3716 | 0.4793 | **0.4186** | 0.6409 |
| 30 | 0.2 | similarity | 1.0 | 0.3846 | 0.4582 | **0.4182** | 0.6288 |
| 30 | 0.2 | softmax(τ=0.1) | 1.0 | 0.3688 | 0.4623 | **0.4103** | 0.6361 |

**Picked A\* = k=30, threshold=0.2, weighting=count, lift=1.0** (top-2 on both samples, and the single most
stable pick between them; `similarity`/`softmax` weighting barely move the needle over plain `count`).
Pattern: **lift=1.0 (i.e. no lift gate) beats lift=1.2 almost everywhere**, and a **lower threshold (0.2) +
bigger k (20-30) trades some precision for a lot more recall**, netting +0.03 F1 over the current production
defaults. Similarity-based weighting schemes are basically a wash — the neighbour *count* share the current
code already computes carries almost all the signal; a fancier vote is not where the ceiling is.

## B. Origin/process-aware neighbours

`repo.neighbors(vec, k, origin, process, ...)` does not re-rank by origin/process — it re-runs the kNN query
with an added `WHERE origin_country = %(o)s AND process = %(p)s` filter and only keeps that filtered result if
it has ≥5 hits (`MIN_FILTERED_NEIGHBORS`), else falls back to the plain kNN. Passing the target's own
origin/process this way (on top of A*'s k=30/thr=0.2/count/lift=1.0):

| Sample | variant | P | R | F1 | cat F1 | filter engaged |
|---|---|---|---|---|---|---|---|
| n=200 | A* (no filter) | 0.3893 | 0.4697 | 0.4258 | 0.6554 | — |
| n=200 | A* + origin/process | 0.3918 | 0.4356 | **0.4126** | 0.6441 | 181/185 |
| n=1000 | A* (no filter) | 0.3716 | 0.4793 | 0.4186 | 0.6409 | — |
| n=1000 | A* + origin/process | 0.3518 | 0.4405 | **0.3912** | 0.6189 | 849/860 |

**Origin/process filtering makes tag prediction worse**, on both samples (-0.013 to -0.027 F1). It almost
always engages (origin_country + process match narrows the pool to ≥5 beans in 96-98% of cases), but an
exact-match filter mostly discards genuinely closer embedding neighbours in favor of same-origin/process
beans that are not necessarily flavor-similar — and origin/process are already folded into the embedding text
(`pipeline/embed.py embedding_text`), so exact-matching them again is partly redundant with what cosine
similarity already captures, while losing beans that share flavor without sharing origin/process exactly.
Not recommended.

## C. Text-tag extraction from the target's own notes (leakage bound)

Ran the existing rule mapper (`pipeline/enrich.py rule_tags` + `ko_rule_tags`) over each target's own
name/origin/process/roast/decaf-note + `flavor_summary` + review text (everything `pipeline/embed.py
embedding_text()` folds in, **except** the joined `flavor_tags` string itself — joining that in would make
this a pure tautology). Result: **F1 ≈ 0.98** on both samples.

**This is not a fair "unknown bean" measurement and is reported as such.** For most of these beans (7,393 of
9,155 are `coffeereview_kaggle`), `flavor_tags` were originally produced by `pipeline/enrich.py`'s
`_apply_rules`/`_merge_llm` running the *same* rule mapper (plus an LLM fallback) over this *same*
`flavor_summary`/review text. Scoring rule-extraction against those tags is close to grading the rule mapper
against its own output. It is a genuine **upper bound for what happens when a user pastes in equivalent free
text for a bean the system has never tagged before** — `app/graphs/analyze_bean.py` does take user-typed text
of exactly this shape through `parse_bean_text` — but it is optimistic relative to a bean identified only by
name, and it says nothing new about the embedding-based methods. Unioning it with the best-A neighbour vote
(P=0.57/R=0.97/F1=0.72) mostly just inherits the text signal's near-perfect recall at a real precision cost;
not a method to ship, but confirms that **if the analyze flow ever captures a roaster's own tasting-note text
for a bean, running the existing rule mapper over it directly is close to free accuracy** compared to waiting
on kNN.

## D. Learned model (scikit-learn, `uv add --dev scikit-learn`, spike-only dependency)

54 of 105 distinct flavor tags have ≥30 positive examples among the 7,534 tagged beans; multi-label
classification restricted to those 54. Two models on the raw 1024-d embedding: one-vs-rest logistic
regression, and a single 1-hidden-layer (128-unit) MLP with sigmoid multi-label outputs (native
`MLPClassifier` multilabel support — much cheaper than wrapping 54 independent per-tag MLPs).

- **5-fold CV** (plain shuffled `KFold`, not iterative-stratified for multilabel — noted, not fixed) over all
  7,534 tagged beans, threshold swept over {0.3, 0.35, 0.4, 0.45, 0.5}, plus a top-k=5 (no threshold) variant.
  Logreg CV: ~10s total; MLP CV: ~125s total (5 folds, ~25s/fold on a 1024→128→54 network, ~6,000 train rows/fold).
- **Held out**: same model, trained on every OTHER tagged bean (target ids excluded from training), scored on
  the n=200/n=1000 target samples — directly comparable to the neighbour-vote rows above.
- Best threshold picked from the CV sweep alone (not by peeking at held-out F1): **0.30 for logreg, 0.35 for
  MLP** — both then applied unchanged to the held-out evaluation.

MLP beats logistic regression by a wide margin (F1 0.82-0.84 vs 0.69-0.71) and both dominate every neighbour-
vote variant. Top-k=5 (no threshold) trades precision for recall (F1 ~0.66-0.68) — useful if the product wants
a fixed-length "top 5 predicted flavors" UI slot regardless of confidence, matching the existing `MAX_TAGS=5` cap.

**But see the leakage caveat next — the numbers in this section are inflated versus real deployment.**

## Leakage caveat (found while sanity-checking D's implausibly large jump)

`pipeline/embed.py embedding_text()` folds a coffee's own **already-known** `flavor_tags` into the text used
to build its stored `coffees.embedding`:
```python
parts = [c.name, c.origin_country, c.process, c.roast_level, ...,
         ", ".join(c.flavor_tags) or None, c.flavor_summary, (review_text or "")[:REVIEW_CHARS] or None]
```
This means every LOO query in `app/eval.py loo_accuracy` (`repo.coffee_embedding(cid)`) — and every training/
scoring row in Experiment D — uses an embedding that was built from text **already containing the bean's own
ground-truth tags**. That is not how a genuinely new/unknown bean is embedded: `app/graphs/analyze_bean.py`
calls `deps.embed(parsed.text)` on the user-typed text alone, which has no tag list because the tags don't
exist yet. This pre-dates and is independent of this spike — it affects the *existing* production baseline,
not something introduced here — but it inflates every embedding-based number above (baseline, A, B, D, E) by
an unknown amount, so it was measured directly (`exp_leak.py`, `exp_d_leak.py`): re-embedded each n=200
target's own notes-only text (no tags) via the real NVIDIA embedder in query mode (matching
`analyze_bean.py`'s call exactly), kept the neighbour pool/training set unchanged (correct: those are existing
catalog beans whose tags really are already known), and re-scored:

| Method | query embedding | F1 | cat F1 |
|---|---|---|---|
| Production defaults (k=10,thr=0.3,count,lift=1.2) | stored (tags leaked) | 0.3974 | 0.6441 |
| Production defaults | **re-embedded, no leak** | **0.3672** | 0.6335 |
| Best-A (k=30,thr=0.2,count,lift=1.0) | stored (tags leaked) | 0.4258 | 0.6554 |
| Best-A | **re-embedded, no leak** | **0.3896** | 0.6403 |
| D. Logistic regression, thr=0.3 | stored (tags leaked) | 0.7097 | 0.8056 |
| D. Logistic regression | **re-embedded, no leak** | **0.6295** | 0.7227 |
| D. MLP, thr=0.35 | stored (tags leaked) | 0.8429 | 0.9023 |
| D. MLP | **re-embedded, no leak** | **0.7089** | 0.8349 |

Two findings:
1. **The neighbour-vote methods (baseline, A, B) are only mildly inflated** (~0.03 F1) — the tag words are a
   small fraction of the embedded text next to name/origin/process/notes, so kNN retrieval isn't hugely
   dependent on them.
2. **The learned model is more inflated in absolute terms** (MLP: -0.13 F1) but **still hugely beats kNN even
   with the leak removed** (MLP clean 0.7089 vs best-A clean 0.3896 — nearly 2x). This is the load-bearing
   result: the MLP's advantage is mostly genuine generalization from the embedding space, not an artifact of
   tags leaking into text.

Caveat on the caveat: only the n=200 sample's *query* embeddings were re-embedded (7 NVIDIA API calls); the
7,534-bean *training* set for D still used stored (tag-including) embeddings, since re-embedding all of it
(~235 more batched calls, several more minutes) was out of scope for this spike. A production rollout of the
learned model should retrain on embeddings of tag-free text to close this gap fully — expect the real
deployed MLP F1 to land closer to the 0.71 "clean query" number than the 0.84 "stored" number, though the
exact value after a from-scratch retrain on tag-free text (untested here) could land somewhat above or below
that estimate.

## E. Combination (best learned model ∪/∩ best neighbour vote)

MLP (thr=0.35) so dominates best-A that combining hurts rather than helps:

| Sample | variant | P | R | F1 | cat F1 |
|---|---|---|---|---|---|
| n=200 | MLP only | 0.8870 | 0.8030 | **0.8429** | 0.9023 |
| n=200 | Union(MLP, A) | 0.5156 | 0.8447 | 0.6403 | 0.7820 |
| n=200 | Intersection(MLP, A) | 0.9040 | 0.4280 | 0.5810 | 0.6628 |
| n=1000 | MLP only | 0.8599 | 0.8022 | **0.8301** | 0.8996 |
| n=1000 | Union(MLP, A) | 0.4873 | 0.8385 | 0.6164 | 0.7591 |
| n=1000 | Intersection(MLP, A) | 0.8822 | 0.4429 | 0.5898 | 0.6729 |

Union adds best-A's noisier, lower-precision tags on top of MLP's already-good recall (net loss). Intersection
throws away MLP's correct-but-not-neighbour-corroborated tags (net recall loss). **No combination beats MLP
alone** in this dataset — not surprising given how much stronger D already is than A/B.

## Caveats (methodology, all experiments)

- **Exact brute-force cosine vs production's approximate HNSW** (`ef_search=200`, relaxed iterative order):
  validated as a close match (sanity check above) but not bit-identical; grid rankings among near-tied combos
  could shuffle slightly under the real index.
- **`roasters_kr` excluded from all target samples** (facts-only beans, no human-verified tags), matching
  `app/eval.py`'s `NEVER_LOO_TARGETS` convention.
- **5-fold CV in Experiment D uses plain shuffled `KFold`**, not multilabel-stratified — a rarer tag could be
  unevenly split across folds; did not materially change the ranking between logreg/MLP/kNN, given the gap size.
- **The embedding-text tag leak** (see above) affects the baseline and every embedding-based method here, not
  just this spike's new experiments — it is a pre-existing property of `pipeline/embed.py`.
- Category F1 uses the same `tag_to_cat` mapping and micro-averaging as `app/eval.py loo_accuracy`.

## Recommendation

**Ship the MLP learned model (Experiment D)**, not the vote-tuning grid: even after removing the
embedding-text leak, it clear-wins the neighbour vote by a wide margin (F1 0.71 vs 0.39 clean; 0.84 vs 0.43
without correcting for the leak) at negligible extra runtime cost — a single forward pass through a
1024→128→54 network (~0.15M parameters, well under 1MB as a pickled/ONNX artifact) adds low-single-digit
milliseconds per prediction, similar to or cheaper than the current SQL kNN query. The one real product
dependency is `scikit-learn` (or reimplementing the tiny network at inference time without it) plus a trained
weights file checked into the repo/deploy image and reloaded at API start-up (`app/repo.py`/`app/core/predict.py`
would need a `predict_from_model(embedding)` path alongside or instead of `predict_from_neighbors`). Before
productionizing: (1) retrain on embeddings built from tag-free text (re-embed all 7,534 tagged beans' notes
without their own flavor_tags — ~235 NVIDIA API batches, a few minutes, one-time cost) to close the leak gap
measured above, since production inference will only ever see tag-free query text; (2) re-validate the CV/held
-out numbers on that retrained model before trusting the ~0.71-0.84 F1 range; (3) keep `predict_from_neighbors`
as a fallback for the acidity/body/sweetness attribute predictions, which this spike did not touch — Experiment
D only replaces the *tag* half of `predict_from_neighbors`, not the numeric attribute averaging. Expect a
realistic shipped gain over the current 0.397 F1 baseline of roughly **+0.3 F1 absolute** (to ~0.70, matching
the clean-query MLP number) rather than the uncorrected +0.44 — still the single biggest lever found in this
spike, well ahead of vote-tuning (+0.03), origin/process filtering (negative), or text-tag extraction (leaky,
not deployable as a general "unknown bean" method since most beans arrive with no notes text at all).
