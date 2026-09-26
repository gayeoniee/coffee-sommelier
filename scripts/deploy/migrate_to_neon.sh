#!/usr/bin/env bash
# Copy the local Docker Postgres (schema + catalog data) into Neon, once.
#
#   NEON_DATABASE_URL='postgresql://...neon.tech/neondb?sslmode=require' scripts/deploy/migrate_to_neon.sh
#
# pg_dump / pg_restore / psql run INSIDE the local pgvector container, so no Postgres tools are needed on the
# host. Use Neon's *direct* connection string (not the "-pooler" host): pg_restore needs a session.
#
# What is copied: every table's schema; data of the catalog tables only. User tables (users, taste_profiles,
# tastings, profile_history) start empty in production; pipeline-only tables (reviews, enrich_log) are not
# read by the API and are left empty to save space (Neon free: 0.5 GB).
#
# Options (env):
#   SRC_CONTAINER   local DB container (default: the running pgvector/pgvector:pg17 container publishing 5432)
#   SRC_DB, SRC_USER  (default coffee / coffee). For the competition open-data variant, point this at the
#                     local open-data DB (SRC_DB=coffee_open, built by scripts/competition/build_open_db.sh)
#                     while NEON_DATABASE_URL targets Neon's own coffee_open database — deploy_all.sh
#                     VARIANT=open sets both automatically.
#   INCLUDE_REVIEWS=1  also copy reviews + enrich_log data
#   RESET=1         target already has tables: DROP and recreate them. Destroys production user data!
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'   # Git Bash on Windows: don't rewrite /tmp paths

: "${NEON_DATABASE_URL:?NEON_DATABASE_URL을 설정하세요 (Neon의 direct 연결 문자열)}"
SRC_DB=${SRC_DB:-coffee}
SRC_USER=${SRC_USER:-coffee}
DUMP=/tmp/coffee_neon.dump
LIST=/tmp/coffee_neon.list

if [ -z "${SRC_CONTAINER:-}" ]; then
  SRC_CONTAINER=$(docker ps --filter ancestor=pgvector/pgvector:pg17 --filter publish=5432 --format '{{.Names}}' | head -n1)
fi
[ -n "$SRC_CONTAINER" ] || { echo "로컬 DB 컨테이너를 찾지 못했어요. docker compose up -d db 후 다시 실행하거나 SRC_CONTAINER를 지정하세요" >&2; exit 1; }
echo "원본: 컨테이너 $SRC_CONTAINER / DB $SRC_DB"

EXCLUDE_DATA=(users taste_profiles tastings profile_history)
[ "${INCLUDE_REVIEWS:-0}" = "1" ] || EXCLUDE_DATA+=(reviews enrich_log)

# Run a command inside the container; the target URL travels as an env var, never on a command line.
in_db() { docker exec -i -e TARGET_URL="$NEON_DATABASE_URL" -e PGUSER="$SRC_USER" "$SRC_CONTAINER" "$@"; }
tgt_sql() { in_db sh -c 'psql "$TARGET_URL" -X -v ON_ERROR_STOP=1 -qtA -c "$1"' _ "$1"; }
src_sql() { in_db psql -d "$SRC_DB" -X -v ON_ERROR_STOP=1 -qtA -c "$1"; }

echo "1/5 대상 연결 확인"
tgt_sql "SELECT version()" | head -n1
existing=$(tgt_sql "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
CLEAN=()
if [ "$existing" != "0" ]; then
  if [ "${RESET:-0}" = "1" ]; then
    echo "   RESET=1: 기존 테이블 $existing개를 지우고 다시 만들어요 (운영 사용자 데이터도 삭제됨)"
    CLEAN=(--clean --if-exists)
  else
    echo "대상 DB에 이미 테이블이 $existing개 있어요. 처음부터 다시 옮기려면 RESET=1 (사용자 데이터 삭제)," >&2
    echo "카탈로그만 갱신하려면: DATABASE_URL=<neon> uv run python -m pipeline run --only load (upsert)" >&2
    exit 1
  fi
fi

echo "2/5 vector 확장 생성"
tgt_sql "CREATE EXTENSION IF NOT EXISTS vector"

echo "3/5 로컬 DB 덤프 (데이터 제외: ${EXCLUDE_DATA[*]})"
excl=()
for t in "${EXCLUDE_DATA[@]}"; do excl+=("--exclude-table-data=public.$t"); done
in_db pg_dump -d "$SRC_DB" -Fc --no-owner --no-privileges "${excl[@]}" -f "$DUMP"
# The extension already exists on the target (and COMMENT ON EXTENSION needs superuser there): skip both.
in_db sh -c "pg_restore -l $DUMP | grep -v ' EXTENSION ' > $LIST"
in_db ls -l "$DUMP" | awk '{print "   덤프 크기: " $5 " bytes"}'

echo "4/5 Neon에 복원 (한 트랜잭션, 실패 시 전부 롤백)"
in_db sh -c 'pg_restore -d "$TARGET_URL" --no-owner --no-privileges --exit-on-error --single-transaction "$@"' _ \
  ${CLEAN[@]+"${CLEAN[@]}"} -L "$LIST" "$DUMP"
in_db rm -f "$DUMP" "$LIST"

echo "5/5 검증 + ANALYZE"
tables=$(src_sql "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
tgt_sql "ANALYZE $(echo $tables | sed 's/ /, /g')"
fail=0
printf '   %-18s %10s %10s\n' table local neon
for t in $tables; do
  src=$(src_sql "SELECT count(*) FROM public.$t")
  dst=$(tgt_sql "SELECT count(*) FROM public.$t")
  want=$src
  for e in "${EXCLUDE_DATA[@]}"; do [ "$e" = "$t" ] && want=0; done
  mark=""; [ "$dst" = "$want" ] || { mark="  <-- 기대값 $want"; fail=1; }
  printf '   %-18s %10s %10s%s\n' "$t" "$src" "$dst" "$mark"
done
echo "   Neon DB 크기: $(tgt_sql "SELECT pg_size_pretty(pg_database_size(current_database()))") (무료 한도 0.5 GB)"
echo "   임베딩 차원: $(tgt_sql "SELECT coalesce(max(vector_dims(embedding))::text, '없음') FROM coffees")"
[ "$fail" = "0" ] && echo "완료" || { echo "행 수가 맞지 않아요" >&2; exit 1; }
