#!/usr/bin/env bash
# NEXARA installer — Hostinger KVM VPS / any Debian-Ubuntu host.
#
#   curl -fsSL <raw-url>/scripts/install.sh | bash
#   or: git clone <repo> && cd nexara && bash scripts/install.sh
#
# Idempotent: safe to re-run. Installs Docker if absent, starts the stack,
# waits for health, and prints the real state — never claims success blind.
set -euo pipefail

REPO_URL="${NEXARA_REPO:-https://github.com/Ajeetesh-Ranjan/nexara-p8-test.git}"
INSTALL_DIR="${NEXARA_HOME:-/opt/nexara}"
NODE_NAME="${NEXARA_NODE_NAME:-$(hostname)}"
NODE_ROLE="${NEXARA_NODE_ROLE:-production}"

log()  { echo "[nexara] $*"; }
die()  { echo "[nexara] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || SUDO="sudo" && SUDO="${SUDO:-}"

log "installing NEXARA to $INSTALL_DIR (node=$NODE_NAME role=$NODE_ROLE)"

# ---------- Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  log "Docker not found — installing"
  curl -fsSL https://get.docker.com | $SUDO sh || die "Docker install failed"
  $SUDO systemctl enable --now docker
else
  log "Docker present: $(docker --version)"
fi

docker compose version >/dev/null 2>&1 || die "docker compose v2 required"

# ---------- source ----------
if [ -d "$INSTALL_DIR/.git" ]; then
  log "updating existing checkout"
  git -C "$INSTALL_DIR" pull --ff-only || die "git pull failed"
else
  log "cloning $REPO_URL"
  $SUDO mkdir -p "$(dirname "$INSTALL_DIR")"
  $SUDO git clone "$REPO_URL" "$INSTALL_DIR" || die "clone failed"
  $SUDO chown -R "$(id -u):$(id -g)" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ---------- config ----------
if [ ! -f .env ]; then
  log "writing .env"
  cat > .env <<EOF
NEXARA_NODE_NAME=$NODE_NAME
NEXARA_NODE_ROLE=$NODE_ROLE
# Bind dashboard to localhost by default. Put a reverse proxy in front to expose.
NEXARA_BIND=127.0.0.1
NEXARA_DASHBOARD_PORT=8080
# Comma-separated peer brain URLs for multi-node replication, e.g.
# NEXARA_PEERS=http://10.0.0.5:8081,http://10.0.0.6:8081
NEXARA_PEERS=
NEXARA_SYNC_INTERVAL=300
NEXARA_BACKUP_INTERVAL=3600
NEXARA_BACKUP_KEEP=24
# Set to push knowledge offsite automatically:
NEXARA_GIT_REMOTE=
EOF
else
  log ".env exists — leaving it alone"
fi

# ---------- build and start ----------
log "building image"
docker compose build || die "build failed"

log "starting stack"
docker compose up -d || die "startup failed"

# ---------- wait for health ----------
log "waiting for services to become healthy"
DEADLINE=$(( $(date +%s) + 180 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  TOTAL=$(docker compose ps --services | wc -l)
  HEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"Health":"healthy"' || true)
  if [ "$HEALTHY" -ge "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    log "all $TOTAL services healthy"
    break
  fi
  sleep 5
done

echo
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo

UNHEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"Health":"unhealthy"' || true)
if [ "${UNHEALTHY:-0}" -gt 0 ]; then
  die "$UNHEALTHY service(s) unhealthy — check: docker compose logs"
fi

# ---------- register this node ----------
log "registering node with relay"
docker compose exec -T relay curl -fsS -X POST http://localhost:8082/nodes/register \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"$NODE_NAME\",\"role\":\"$NODE_ROLE\",\"capabilities\":[\"research\",\"knowledge\",\"infrastructure\"]}" \
  >/dev/null 2>&1 && log "node registered" || log "WARNING: node registration failed (relay may still be starting)"

echo
log "NEXARA is running."
log "  dashboard : http://127.0.0.1:${NEXARA_DASHBOARD_PORT:-8080}/  (bound to localhost)"
log "  status    : docker compose -f $INSTALL_DIR/docker-compose.yml ps"
log "  logs      : docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
log "  ops test  : bash $INSTALL_DIR/scripts/ops_test.sh"
log "  backup    : docker compose exec backup python /app/scripts/backup_daemon.py --once"
