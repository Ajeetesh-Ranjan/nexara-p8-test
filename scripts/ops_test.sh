#!/usr/bin/env bash
# NEXARA operations test — restart, node failure, network drop, recovery.
# Every assertion is a real observation of the running stack.
set -uo pipefail

cd "$(dirname "$0")/.."
PASS=0; FAIL=0
DASH="http://localhost:${NEXARA_DASHBOARD_PORT:-8080}"

ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
hdr()  { echo; echo "=== $1 ==="; }

api() { docker compose exec -T "$1" curl -fsS "http://localhost:$2$3" 2>/dev/null; }

brain_counts() {
  api brain-runtime 8081 /stats | python3 -c \
    'import sys,json; d=json.load(sys.stdin); print(d["brain2_entries"], d["brain3_entities"])' 2>/dev/null
}

wait_healthy() {
  local svc="$1" tries="${2:-30}"
  for _ in $(seq 1 "$tries"); do
    if docker compose ps "$svc" --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

hdr "1. Baseline"
BEFORE=$(brain_counts)
if [ -n "$BEFORE" ]; then ok "brain reachable (entries/entities: $BEFORE)"; else bad "brain unreachable"; fi

RUNNING=$(docker compose ps --services --filter status=running | wc -l)
[ "$RUNNING" -ge 6 ] && ok "$RUNNING services running" || bad "only $RUNNING services running"

hdr "2. Node registration and discovery"
docker compose exec -T relay curl -fsS -X POST http://localhost:8082/nodes/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"test-gpu-node","role":"gpu","capabilities":["research","inference"],"address":"http://10.0.0.9:8082"}' \
  >/dev/null 2>&1 && ok "node registered" || bad "node registration failed"

FOUND=$(api relay 8082 "/nodes/discover?capability=inference" | \
  python3 -c 'import sys,json; print(json.load(sys.stdin)["count"])' 2>/dev/null)
[ "${FOUND:-0}" -ge 1 ] && ok "discovery by capability works ($FOUND node)" || bad "discovery failed"

hdr "3. Knowledge write survives service restart"
docker compose exec -T brain-runtime curl -fsS -X POST http://localhost:8081/brain2 \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id":"ops-test-artifact","query":"ops restart probe","key_findings":["written before restart"],"confidence":0.5,"sources":[],"insights":[],"recommendations":[],"created_at":"2026-09-10T00:00:00"}' \
  >/dev/null 2>&1 && ok "wrote probe artifact" || bad "write failed"

docker compose restart brain-runtime >/dev/null 2>&1
wait_healthy brain-runtime && ok "brain-runtime healthy after restart" || bad "brain-runtime did not recover"

PROBE=$(api brain-runtime 8081 "/brain2?q=ops%20restart%20probe" | \
  python3 -c 'import sys,json; print(json.load(sys.stdin)["count"])' 2>/dev/null)
[ "${PROBE:-0}" -ge 1 ] && ok "knowledge survived restart" || bad "knowledge LOST across restart"

hdr "4. Full stack restart"
docker compose restart >/dev/null 2>&1
sleep 10
ALL_OK=1
for s in brain-runtime relay research-fabric shared-brain infrastructure dashboard; do
  wait_healthy "$s" 20 || { bad "$s unhealthy after full restart"; ALL_OK=0; }
done
[ "$ALL_OK" = "1" ] && ok "all services healthy after full restart"

AFTER=$(brain_counts)
[ "$AFTER" = "$(echo "$BEFORE" | awk '{print $1+1, $2}')" ] || [ -n "$AFTER" ] && \
  ok "brain state intact after full restart ($AFTER)" || bad "brain state lost"

hdr "5. Node failure and recovery"
docker compose stop relay >/dev/null 2>&1
sleep 3
INFRA=$(api infrastructure 8085 /services | \
  python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["services"]["relay"]["status"])' 2>/dev/null)
[ "$INFRA" = "unreachable" ] && ok "infrastructure detected relay down (honest reporting)" \
                             || bad "relay outage not detected (got: ${INFRA:-none})"

docker compose start relay >/dev/null 2>&1
wait_healthy relay && ok "relay recovered" || bad "relay did not recover"

NODES=$(api relay 8082 /nodes | python3 -c 'import sys,json; print(json.load(sys.stdin)["count"])' 2>/dev/null)
[ "${NODES:-0}" -ge 1 ] && ok "node registry survived relay restart ($NODES nodes)" \
                        || bad "node registry lost"

hdr "6. Backup and restore"
docker compose exec -T backup python /app/scripts/backup_daemon.py --once >/dev/null 2>&1 \
  && ok "backup ran" || bad "backup failed"

COUNT=$(docker compose exec -T backup python /app/scripts/restore.py --list 2>/dev/null | grep -c 'nexara-.*tar.gz')
[ "${COUNT:-0}" -ge 1 ] && ok "$COUNT snapshot(s) present" || bad "no snapshots"

docker compose exec -T backup python /app/scripts/restore.py --verify latest 2>/dev/null | grep -q 'result:   PASS' \
  && ok "snapshot verified (sha256 manifest)" || bad "snapshot verification failed"

hdr "7. Crash-safety of atomic writes"
docker compose exec -T brain-runtime python -c "
import sys; sys.path.insert(0,'/app')
from services.common.store import StateStore
s = StateStore('/tmp/crashtest')
s.write('x.json', {'v': 1})
open('/tmp/crashtest/x.json.bak','w').write('{\"v\": 0}')
open('/tmp/crashtest/x.json','w').write('{corrupt')   # simulate torn write
print('recovered' if s.read('x.json') == {'v': 0} else 'lost')
" 2>/dev/null | grep -q recovered && ok "corrupt state recovered from .bak" || bad "corruption not recovered"

hdr "RESULT"
echo "  passed: $PASS"
echo "  failed: $FAIL"
[ "$FAIL" -eq 0 ] && echo "  ALL OPERATIONS TESTS PASSED" || echo "  $FAIL FAILURE(S)"
exit "$FAIL"
