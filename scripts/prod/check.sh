#!/usr/bin/env bash
# Post-deploy smoke check: every service healthy, web + API answering, database migrated.
#
#   scripts/prod/check.sh
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_stack_config
failures=0
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
ok() { echo "ok    $*"; }

for service in db api worker web browser; do
    cid="$(compose ps -q "$service" 2>/dev/null || true)"
    if [ -z "$cid" ]; then fail "$service is not running"; continue; fi
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
    if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then ok "$service: $state"; else fail "$service: $state"; fi
done

web_port="$(env_value RMA_WEB_PORT 8480)"
if curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/healthz" >/dev/null; then ok "web /healthz"; else fail "web /healthz"; fi
if curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/api/v1/health" | grep -q '"ok"'; then ok "api through the proxy"; else fail "api through the proxy"; fi

count="$(compose exec -T db psql -U rma -d rma -tAc "select count(*) from workflows" 2>/dev/null | tr -d '[:space:]' || true)"
if [ "${count:-0}" -ge 21 ] 2>/dev/null; then ok "workflow catalog seeded ($count workflows)"; else fail "workflow catalog not seeded (got '${count:-}')"; fi

session="$(compose exec -T browser python -m rma_poc status 2>/dev/null | head -n 1 || true)"
echo "info  captured session: ${session:-unknown}"

if [ "$failures" -gt 0 ]; then echo "$failures check(s) failed"; exit 1; fi
echo "all checks passed"
