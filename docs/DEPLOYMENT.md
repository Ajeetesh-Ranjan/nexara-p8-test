# NEXARA Deployment

Production deployment, update, backup, and recovery for the NEXARA stack.
Everything here has been executed against a live stack, not just written.

---

## 1. What runs

| Service | Port | Role | State |
|---|---|---|---|
| `brain-runtime` | 8081 | Owns Brain 2 (knowledge) + Brain 3 (world model) | `brain-data` |
| `relay` | 8082 | Node registry, discovery, heartbeat, messaging | `relay-data` |
| `research-fabric` | 8083 | Research pipeline, wiki generation | `brain-data` (shared) |
| `shared-brain` | 8084 | Cross-node replication | `shared-data` |
| `infrastructure` | 8085 | Host + fabric observation | `infra-data` |
| `dashboard` | 8080 | Read-only visibility | — |
| `backup` | — | Hourly verified snapshots | `backup-data` |

Only `dashboard` publishes a host port, bound to `127.0.0.1` by default.
Internal services communicate on the private `nexara` bridge network.

---

## 2. Install (Hostinger KVM VPS)

```bash
# On a fresh Debian/Ubuntu VPS
curl -fsSL https://raw.githubusercontent.com/Ajeetesh-Ranjan/nexara-p8-test/master/scripts/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/Ajeetesh-Ranjan/nexara-p8-test.git /opt/nexara
cd /opt/nexara
NEXARA_NODE_NAME=hostinger-prod NEXARA_NODE_ROLE=production bash scripts/install.sh
```

The installer is idempotent: installs Docker if absent, clones or pulls,
writes `.env` only if missing, builds, starts, waits for health, registers the
node, and **fails loudly** if any service is unhealthy.

### Exposing the dashboard

The dashboard binds to localhost. To reach it from outside, use an SSH tunnel
(safest) or put a TLS reverse proxy in front:

```bash
# SSH tunnel from your laptop
ssh -L 8080:127.0.0.1:8080 user@vps-ip
# then open http://localhost:8080
```

Do not set `NEXARA_BIND=0.0.0.0` without a proxy and auth in front — the
dashboard has no authentication by design.

---

## 3. Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `NEXARA_NODE_NAME` | hostname | Identity in the fabric |
| `NEXARA_NODE_ROLE` | `production` | `control` / `gpu` / `production` / `overflow` |
| `NEXARA_BIND` | `127.0.0.1` | Dashboard bind address |
| `NEXARA_DASHBOARD_PORT` | `8080` | Dashboard host port |
| `NEXARA_PEERS` | empty | Comma-separated peer brain URLs for replication |
| `NEXARA_SYNC_INTERVAL` | `300` | Replication interval (seconds) |
| `NEXARA_BACKUP_INTERVAL` | `3600` | Snapshot interval (seconds) |
| `NEXARA_BACKUP_KEEP` | `24` | Snapshots retained before pruning |
| `NEXARA_GIT_REMOTE` | empty | Optional git remote for offsite knowledge push |

Apply changes with `docker compose up -d`.

---

## 4. Update

```bash
cd /opt/nexara
docker compose exec backup python /app/scripts/backup_daemon.py --once   # snapshot first
git pull --ff-only
docker compose build
docker compose up -d
docker compose ps          # verify health before walking away
```

Volumes are not touched by updates. If an update misbehaves, roll back:

```bash
git checkout <previous-sha>
docker compose build && docker compose up -d
```

---

## 5. Backup

Automatic: the `backup` service snapshots hourly, verifies each archive by
re-reading it, prunes to `NEXARA_BACKUP_KEEP`, and optionally pushes knowledge
to `NEXARA_GIT_REMOTE`.

Manual snapshot:

```bash
docker compose exec backup python /app/scripts/backup_daemon.py --once
```

List and verify:

```bash
docker compose exec backup python /app/scripts/restore.py --list
docker compose exec backup python /app/scripts/restore.py --verify latest
```

Each archive carries `MANIFEST.json` with a sha256 per file. Verification
checks every hash; an archive that fails is never counted as a backup.

### Copy snapshots offsite

A backup that only exists on the same host is not a backup:

```bash
SNAP=$(docker compose exec -T backup sh -c 'ls -t /backups/nexara-*.tar.gz | head -1' | tr -d '\r')
docker compose cp "backup:$SNAP" ./
scp "$(basename "$SNAP")" you@another-host:/backups/
```

---

## 6. Recovery

### Restore knowledge into a running stack

```bash
docker compose cp ./nexara-<stamp>.tar.gz backup:/backups/
docker compose exec backup python /app/scripts/restore.py --restore nexara-<stamp>.tar.gz
```

Restore refuses to run if verification fails, and moves existing data aside to
`<target>.pre-restore-<stamp>` rather than deleting it.

### Full rebuild from Git (total loss of host)

```bash
git clone https://github.com/Ajeetesh-Ranjan/nexara-p8-test.git /opt/nexara
cd /opt/nexara && bash scripts/install.sh
# then restore the newest offsite snapshot
docker compose cp ./nexara-<stamp>.tar.gz backup:/backups/
docker compose exec backup python /app/scripts/restore.py --restore nexara-<stamp>.tar.gz
```

**This path is tested.** See §8.

---

## 7. Multi-node

Each node runs the same stack with its own identity. Replication is pull-based:
`shared-brain` fetches peer brain state and merges it, idempotent on
`artifact_id`, so repeated syncs converge rather than duplicate.

```bash
# on hostinger-prod
NEXARA_PEERS=http://10.0.0.5:8081,http://10.0.0.6:8081
docker compose up -d shared-brain
docker compose exec shared-brain curl -X POST http://localhost:8084/sync
```

Node roles: `control` (laptop), `gpu` (GPU laptop), `production` (Hostinger),
`overflow` (Oracle), `proxmox`. Registration:

```bash
docker compose exec relay curl -X POST http://localhost:8082/nodes/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"gpu-laptop","role":"gpu","capabilities":["inference","research"]}'
```

Nodes must heartbeat within `NEXARA_HEARTBEAT_TIMEOUT` (90s) or they are marked
`stale`, then `down` at 2×. Status is computed from real heartbeat age.

---

## 8. Operations testing

```bash
bash scripts/ops_test.sh
```

Covers: baseline health, node registration and capability discovery, knowledge
survival across single-service restart, full-stack restart, node failure
detection, relay recovery, backup + snapshot verification, and torn-write
recovery from `.bak`.

**Result on this stack: 16/16 passed.**

### Verified disaster recovery

The following was executed end to end:

1. Baseline: 8 Brain-2 entries (16,135 B), 19 Brain-3 entities, 5 wiki pages
2. Snapshot taken, copied off the stack
3. `docker compose down -v` — **all containers and all volumes destroyed**
4. `docker compose up -d` — rebuilt from Git; brain correctly empty (0 entries)
5. Snapshot restored after sha256 verification (43/43 files verified)
6. Final state: **8 entries, 16,135 B, 19 entities, 5 wiki pages — byte-identical**

---

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| Service unhealthy | `docker compose logs <service> --tail 50` |
| Backup crash-loop | Volume ownership — `/backups` must be writable by uid 10001 |
| Research returns 0 sources | Network egress to arxiv.org / news.ycombinator.com |
| Dashboard shows `unreachable` | That service is genuinely down — not a display bug |
| Restore refuses to run | Verification failed; the archive is corrupt — use an older one |

Health of everything at once:

```bash
docker compose exec infrastructure curl -s http://localhost:8085/services | python3 -m json.tool
```
