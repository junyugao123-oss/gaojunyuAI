#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
SERVICES="${DEPLOY_SERVICES:-server analyzer}"
API_PORT="${API_PORT:-8000}"

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
export COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"

log() {
  printf '\n==> %s\n' "$*"
}

log "Preparing git network settings"
git config --global http.version HTTP/1.1 || true
git config --global http.lowSpeedLimit 1000 || true
git config --global http.lowSpeedTime 60 || true

log "Pulling latest ${REMOTE}/${BRANCH}"
git fetch "$REMOTE" "$BRANCH"
git reset --hard "$REMOTE/$BRANCH"
CURRENT_HEAD="$(git rev-parse --short HEAD)"
printf 'Current HEAD: %s\n' "$CURRENT_HEAD"

log "Preparing persistent directories"
mkdir -p data logs reports longbridge_tokens

log "Building Docker image with dependency cache"
# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" build $SERVICES

log "Recreating services"
# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" up -d --no-deps --force-recreate $SERVICES

log "Waiting for API health"
for attempt in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null 2>&1 \
    || curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    printf 'API healthy after %s checks.\n' "$attempt"
    break
  fi
  if [ "$attempt" -eq 40 ]; then
    echo "API health check failed."
    docker compose -f "$COMPOSE_FILE" logs --tail=80 server
    exit 1
  fi
  sleep 2
done

log "Verifying company identity guardrail"
docker compose -f "$COMPOSE_FILE" exec -T server python - <<'PY'
from api.v1.endpoints import commercial_analysis as ca

profile = ca._verified_company_profile("600519.SH", "贵州茅台")
category = ca._infer_company_business_category(
    {"business": "贵州茅台酒及系列酒生产销售，同时提到品牌授权、实景体验等延展场景。"},
    [{"name": "品牌授权"}, {"name": "实景娱乐"}],
    {"name": "贵州茅台", "code": "600519.SH"},
)
print("moutai_industry=", profile.get("industry") if profile else "")
print("moutai_category=", category)
if not profile or profile.get("industry") != "高端白酒/食品饮料" or category != "baijiu":
    raise SystemExit("company identity guardrail failed")
PY

log "Verifying public API response"
curl -fsS "http://127.0.0.1:${API_PORT}/api/v1/commercial-analysis/600519.SH" \
  | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
texts = [
    item.get("description", "")
    for item in payload.get("decision_reasons", [])
    if item.get("title") == "公司基本面"
]
text = texts[0] if texts else ""
print(text[:220])
if "内容IP" in text or "影视娱乐" in text or "文旅消费链条" in text:
    raise SystemExit("public API still contains wrong media/content identity")
if "白酒" not in text and "茅台酒" not in text:
    raise SystemExit("public API did not return baijiu identity")
'

log "Deployment completed"
docker compose -f "$COMPOSE_FILE" ps
