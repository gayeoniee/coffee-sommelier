#!/usr/bin/env bash
# Builds/refreshes the "open data" variant DB (coffee_open on the local docker Postgres) with no
# coffeereview_kaggle source, then runs the non-LLM evals into data/eval/open/.
#
# Safe to re-run: the end state (coffee_open has zero coffeereview_kaggle rows) is the same every
# time. It is NOT a no-op on repeat runs, though - step 2's `load` re-upserts every source's coffees
# from the shared (full-variant) enriched cache, coffeereview_kaggle included, so step 3's DELETE
# finds and removes real rows (currently ~7,393) on every run, not just the first one. RESET=1 drops
# and recreates the database first.
#
# Usage:
#   bash scripts/competition/build_open_db.sh
#   RESET=1 bash scripts/competition/build_open_db.sh
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"  # repo root

DB_CONTAINER="${DB_CONTAINER:-hawksbill-db-1}"
PG_USER="${PG_USER:-coffee}"
PG_ADMIN_DB="${PG_ADMIN_DB:-coffee}"
DB_NAME="coffee_open"

export NORMALIZED_DIR="$(pwd)/data/normalized_open"
export EVAL_DIR="$(pwd)/data/eval/open"
export EXCLUDE_SOURCES="coffeereview_kaggle"
export DATABASE_URL="postgresql://${PG_USER}:coffee@localhost:5432/${DB_NAME}"

psql_admin() { docker exec "${DB_CONTAINER}" psql -U "${PG_USER}" -d "${PG_ADMIN_DB}" -v ON_ERROR_STOP=1 "$@"; }
psql_open() { docker exec "${DB_CONTAINER}" psql -U "${PG_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 "$@"; }

echo "== 1/4 database (${DB_NAME}) =="
if [ "${RESET:-0}" = "1" ]; then
  echo "RESET=1: dropping ${DB_NAME} if it exists"
  psql_admin -c "DROP DATABASE IF EXISTS ${DB_NAME}"
fi
EXISTS="$(psql_admin -tAc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")"
if [ "${EXISTS}" = "1" ]; then
  echo "${DB_NAME} already exists, skipping CREATE DATABASE"
else
  echo "creating ${DB_NAME}"
  psql_admin -c "CREATE DATABASE ${DB_NAME}"
fi

echo "== 2/4 pipeline: normalize (excluding ${EXCLUDE_SOURCES}) + load =="
# No embed stage here: run_load reads coffee embeddings from the per-model cache
# (data/embedded/<model>/embeddings.jsonl) by key, and every key this variant needs (cqi,
# roasterdb, roasters_kr, shopify = 1,762 coffees) is already in that cache from the full-variant
# pipeline run, so embed would make 0 new API requests for THIS variant's own coffees.
#
# We deliberately do NOT pass --only embed, though: pipeline.embed.run_embed rebuilds its embedding
# text from NORMALIZED_DIR's reviews.jsonl, which this variant's normalize excludes for
# coffeereview_kaggle. That changes the embedding-text hash for every coffeereview coffee (their
# review excerpt disappears), so an embed run here would NOT be a no-op cache hit for them - it
# would force ~7,393 real re-embedding API calls and rewrite the shared
# data/embedded/<model>/embeddings.jsonl cache that data/embedded is junctioned to. Since none of
# that is needed (those coffeereview rows never load into coffee_open - see step 3), embed stays
# skipped. If a future coffee variant genuinely introduces new keys, run_load will fail with a
# clear "run embed first" error and this script should stop there rather than silently embedding.
uv run python -m pipeline run --only normalize --only load

echo "== 3/4 cleanup: drop any coffeereview rows that reached coffees via the enriched cache =="
# run_load reads the coffees table's source rows from data/enriched/coffees.jsonl (ENRICHED_DIR),
# not from NORMALIZED_DIR - so --exclude-source at the normalize stage keeps coffeereview_kaggle
# out of reviews/menu_items/brands (those come from NORMALIZED_DIR) but NOT out of coffees by
# itself, since the enriched cache is the full-variant cache and still has every source.
# pipeline/settings.py has no ENRICHED_DIR env override (only NORMALIZED_DIR and EVAL_DIR do), so
# there is no cheap way to pre-filter step 2's load input the way EXCLUDE_SOURCES pre-filters
# normalize; this DELETE is the actual filter for the coffees table, not a defensive no-op. It runs
# (and deletes real rows - currently ~7,393) on every invocation of this script, including repeats,
# because step 2 re-upserts every source's coffees from that same shared enriched cache each time.
#
# Safety: this DELETE can only ever conflict with a foreign key if some row's default_bean_coffee_id
# / decaf_bean_coffee_id (brands) or coffee_id (menu_items) points at a coffeereview_kaggle coffee -
# both are nullable FKs with no ON DELETE action (RESTRICT), so such a reference would abort the
# DELETE with an FK-violation error rather than silently corrupting data. In practice neither column
# is populated by this pipeline (menu_items link to brands, not directly to coffees; brands.*_bean_*
# are legacy/unused), so no row ever references a coffeereview_kaggle coffee and the DELETE succeeds.
# reviews.coffee_id is NOT NULL with ON DELETE CASCADE, but that's moot here too: normalize already
# excluded coffeereview's own reviews from NORMALIZED_DIR, so none were loaded for them.
psql_open -c "DELETE FROM coffees WHERE source = 'coffeereview_kaggle'"
# Brand bean profiles: brands.yaml carries both the full-variant values (bean/decaf_bean, partly derived with
# models trained on coffeereview labels) and the licence-clean open ones (bean_open/decaf_bean_open; ADR 0012).
# The app reads only the *_open columns under DATA_VARIANT=open (app/repo.py brand_bean_columns); overwrite the
# full columns with them too so this DB -- which is what gets migrated to the submission deploy -- holds no
# coffeereview-derived brand value at all.
psql_open -c "UPDATE brands SET bean = bean_open, decaf_bean = decaf_bean_open"

echo "== 4/4 verification =="
echo "-- total coffees --"
psql_open -c "SELECT count(*) AS total_coffees FROM coffees"
echo "-- coffees by source --"
psql_open -c "SELECT source, count(*) FROM coffees GROUP BY source ORDER BY source"
echo "-- coffeereview rows (must be 0) --"
psql_open -c "SELECT count(*) AS coffeereview_rows FROM coffees WHERE source = 'coffeereview_kaggle'"
echo "-- reviews --"
psql_open -c "SELECT count(*) AS reviews FROM reviews"
echo "-- menu_items --"
psql_open -c "SELECT count(*) AS menu_items FROM menu_items"

echo "== eval: violations loo coverage convergence -> ${EVAL_DIR} =="
mkdir -p "${EVAL_DIR}"
uv run python -m app.eval violations loo coverage convergence

echo "done."
