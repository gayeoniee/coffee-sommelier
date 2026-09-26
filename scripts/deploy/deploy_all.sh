#!/usr/bin/env bash
# Deploy everything after the one-time logins (see docs/deploy.md):
#   npx neonctl auth        # Neon (browser, GitHub sign-in)
#   npx vercel login        # Vercel (browser, GitHub sign-in)
#   Render: either create an API key (Account Settings > API Keys) and export RENDER_API_KEY,
#           or click the Blueprint link this script prints and paste the service URL back.
#
#   NVIDIA_API_KEY=... [RENDER_API_KEY=...] scripts/deploy/deploy_all.sh
#
# Idempotent: re-running reuses the Neon project, the Render service and the Vercel project it finds by name.
#
# Env (all optional except NVIDIA_API_KEY, which falls back to the repo's .env):
#   NEON_DATABASE_URL   skip Neon project lookup/creation and use this connection string
#   RENDER_API_KEY      create/update the Render service through the REST API (else: Blueprint link)
#   RENDER_URL          the Render service URL if it already exists (https://....onrender.com)
#   LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL   optional tracing
#   KEEP_WARM=1         also set the GitHub repo variable that turns on .github/workflows/keep-warm.yml (needs gh)
#   SKIP_MIGRATE=1      don't copy the local DB (e.g. Neon already has the data)
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
NAME=coffee-sommelier
RENDER_SERVICE=coffee-sommelier-api
REPO_URL=https://github.com/gayeoniee/wine-sommelier_rag
NEON_REGION=aws-ap-southeast-1           # Singapore, next to Render singapore and Vercel sin1
NEONCTL="npx -y neonctl@6"
VERCEL="npx -y vercel@60"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { echo "오류: $*" >&2; exit 1; }
# json '<js expression over o>' < input  -> prints the value (node is present wherever npx is)
json() { node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const o=JSON.parse(s);const v=new Function("o","return ("+process.argv[1]+")")(o);console.log(v==null?"":(typeof v==="object"?JSON.stringify(v):v))})' "$1"; }

if [ -z "${NVIDIA_API_KEY:-}" ] && [ -f .env ]; then
  NVIDIA_API_KEY=$(grep -E '^NVIDIA_API_KEY=' .env | head -n1 | cut -d= -f2- | tr -d '"\r' || true)
fi
[ -n "${NVIDIA_API_KEY:-}" ] || die "NVIDIA_API_KEY가 필요해요 (환경변수 또는 .env)"
command -v docker >/dev/null || die "docker가 필요해요 (로컬 DB를 옮길 때 사용)"
# Production has no Ollama: the query embedding (config/models.yaml tasks.embed) must come from a remote provider,
# and the DB vectors being copied must have been made by that same model.
embed_provider=$(sed -n '/^  embed:/,/^  [a-z_0-9]*:$/p' config/models.yaml | sed -n 's/^ *provider: *//p' | head -n1 | tr -d '\r ')
if [ "$embed_provider" = "ollama" ]; then
  echo "경고: config/models.yaml의 embed가 ollama예요. 운영엔 Ollama가 없어 원두 분석의 유사 원두 검색이 빠져요." >&2
  echo "      NVIDIA 임베딩으로 전환·재임베딩한 뒤 배포하는 걸 권장해요 (계속하려면 Enter, 중단은 Ctrl+C)" >&2
  [ "${YES:-0}" = "1" ] || read -r _
fi

# ---------------------------------------------------------------- 1. Neon
say "1/4 Neon 데이터베이스"
if [ -z "${NEON_DATABASE_URL:-}" ]; then
  $NEONCTL me -o json >/dev/null 2>&1 || die "Neon 로그인이 필요해요: npx neonctl auth"
  pid=$($NEONCTL projects list -o json | json "(Array.isArray(o)?o:(o.projects||[])).filter(p=>p.name==='$NAME').map(p=>p.id)[0]")
  if [ -z "$pid" ]; then
    echo "프로젝트 $NAME 생성 ($NEON_REGION, Postgres 17)"
    pid=$($NEONCTL projects create --name "$NAME" --region-id "$NEON_REGION" --pg-version 17 -o json | json "(o.project||o).id")
  else
    echo "기존 프로젝트 사용: $pid"
  fi
  # direct (non-pooled) connection: pg_restore needs a session, and the API's own pool is small (max 5)
  NEON_DATABASE_URL=$($NEONCTL connection-string --project-id "$pid" | tail -n1 | tr -d '\r')
fi
case "$NEON_DATABASE_URL" in postgres*://*) ;; *) die "Neon 연결 문자열을 얻지 못했어요: $NEON_DATABASE_URL" ;; esac
echo "연결 문자열 확보 (${NEON_DATABASE_URL%%@*}@...)" | sed -E 's#://([^:]+):[^@]+#://\1:****#'

if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
  src=${SRC_CONTAINER:-$(docker ps --filter ancestor=pgvector/pgvector:pg17 --filter publish=5432 --format '{{.Names}}' | head -n1)}
  [ -n "$src" ] || die "로컬 DB 컨테이너가 없어요: docker compose up -d db"
  has=$(docker exec -e TARGET_URL="$NEON_DATABASE_URL" "$src" sh -c 'psql "$TARGET_URL" -XqtAc "SELECT to_regclass('"'"'public.coffees'"'"') IS NOT NULL"')
  if [ "$has" = "t" ]; then
    echo "Neon에 이미 데이터가 있어요 — 옮기기 건너뜀 (다시 하려면 RESET=1 scripts/deploy/migrate_to_neon.sh)"
  else
    SRC_CONTAINER=$src NEON_DATABASE_URL=$NEON_DATABASE_URL bash scripts/deploy/migrate_to_neon.sh
  fi
fi

# ---------------------------------------------------------------- 2. Render
say "2/4 Render 백엔드"
env_json() {
  node -e 'const e=process.env;const keys=["DATABASE_URL","NVIDIA_API_KEY","LANGFUSE_PUBLIC_KEY","LANGFUSE_SECRET_KEY"];
    const out=keys.filter(k=>e[k]).map(k=>({key:k,value:e[k]}));
    out.push({key:"COOKIE_SECURE",value:"true"},{key:"COOKIE_SAMESITE",value:"lax"},
             {key:"LANGFUSE_BASE_URL",value:e.LANGFUSE_BASE_URL||"https://cloud.langfuse.com"});
    console.log(JSON.stringify(out))'
}
render() {  # render METHOD PATH [BODY]
  curl -fsS -X "$1" "https://api.render.com/v1$2" -H "Authorization: Bearer $RENDER_API_KEY" \
    -H "Accept: application/json" -H "Content-Type: application/json" ${3:+--data "$3"}
}
if [ -z "${RENDER_URL:-}" ] && [ -n "${RENDER_API_KEY:-}" ]; then
  export DATABASE_URL=$NEON_DATABASE_URL NVIDIA_API_KEY
  envs=$(env_json)
  sid=$(render GET "/services?name=$RENDER_SERVICE&limit=1" | json "(o[0]&&o[0].service.id)||''")
  if [ -z "$sid" ]; then
    owner=$(render GET "/owners?limit=1" | json "o[0].owner.id")
    body=$(ENVS="$envs" OWNER="$owner" node -e 'const e=process.env;console.log(JSON.stringify({
      type:"web_service", name:"'"$RENDER_SERVICE"'", ownerId:e.OWNER, repo:"'"$REPO_URL"'", branch:"main",
      autoDeploy:"yes", envVars:JSON.parse(e.ENVS),
      serviceDetails:{runtime:"docker", plan:"free", region:"singapore", healthCheckPath:"/health",
        envSpecificDetails:{dockerfilePath:"./Dockerfile", dockerContext:"."}}}))')
    sid=$(render POST /services "$body" | json "o.service.id")
    echo "서비스 생성: $sid"
  else
    echo "기존 서비스 사용: $sid — 환경변수 갱신 후 재배포"
    render PUT "/services/$sid/env-vars" "$envs" >/dev/null
    render POST "/services/$sid/deploys" '{}' >/dev/null
  fi
  RENDER_URL=$(render GET "/services/$sid" | json "o.serviceDetails.url")
elif [ -z "${RENDER_URL:-}" ]; then
  cat <<EOF
RENDER_API_KEY가 없어서 블루프린트 링크로 만들어요. 브라우저에서 열고 GitHub로 로그인한 뒤:
  https://render.com/deploy?repo=$REPO_URL
  - DATABASE_URL  = 아래 Neon 연결 문자열
  - NVIDIA_API_KEY = .env의 값
  - LANGFUSE_* 는 비워 둬도 됨 → Deploy Blueprint
Neon 연결 문자열:
  $NEON_DATABASE_URL
EOF
  read -r -p "배포가 시작되면 서비스 URL(https://....onrender.com)을 붙여 넣으세요: " RENDER_URL
fi
RENDER_URL=${RENDER_URL%/}
[ -n "$RENDER_URL" ] || die "Render URL이 없어요"
echo "백엔드: $RENDER_URL — /health 응답 대기 (첫 빌드는 5분 안팎)"
for i in $(seq 1 60); do
  if curl -fsS --max-time 60 "$RENDER_URL/health" >/dev/null 2>&1; then echo "백엔드 정상"; break; fi
  [ "$i" = 60 ] && die "백엔드가 15분 안에 뜨지 않았어요. Render 대시보드의 Logs를 확인하세요"
  sleep 15
done

# ---------------------------------------------------------------- 3. Vercel
say "3/4 Vercel 프론트엔드"
$VERCEL whoami >/dev/null 2>&1 || die "Vercel 로그인이 필요해요: npx vercel login"
# The project's Root Directory is web/, and it is linked from the repo root, so the same project also works
# with Vercel's Git integration (push to main -> deploy). .vercelignore uploads only web/ from the CLI.
$VERCEL project add "$NAME" >/dev/null 2>&1 || true            # already exists -> fine
$VERCEL project update "$NAME" --framework nextjs --root-directory web --yes >/dev/null
$VERCEL link --yes --project "$NAME" >/dev/null
$VERCEL env add API_URL production --value "$RENDER_URL" --force --yes >/dev/null
DEPLOY_URL=$($VERCEL deploy --prod --yes | tail -n1 | tr -d '\r')
# The per-deployment URL sits behind Vercel's deployment protection; the public one is the shortest alias.
alias=$($VERCEL inspect "$DEPLOY_URL" --json 2>/dev/null \
  | json "((o.alias||o.aliases||[]).slice().sort((a,b)=>a.length-b.length)[0])||''" || true)
WEB_URL=${alias:+https://${alias#https://}}
WEB_URL=${WEB_URL:-https://$NAME.vercel.app}
echo "배포 완료: $WEB_URL"

# ---------------------------------------------------------------- 4. check
say "4/4 확인"
curl -fsS --max-time 90 "$WEB_URL/api/health" && echo "  <- Vercel 프록시 → Render 연결 정상"
if [ "${KEEP_WARM:-0}" = "1" ] && command -v gh >/dev/null; then
  gh variable set KEEP_WARM_URL --repo "${REPO_URL#https://github.com/}" --body "$RENDER_URL/health"
  echo "keep-warm 켜짐 (끄기: gh variable delete KEEP_WARM_URL)"
fi
cat <<EOF

프론트: $WEB_URL   (Vercel 대시보드의 Domains에서 고정 주소 확인: https://$NAME.vercel.app 형태)
백엔드: $RENDER_URL
EOF
