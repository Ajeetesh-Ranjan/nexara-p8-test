#!/usr/bin/env python3
"""
BACKUP DAEMON — periodic, verified, pruned snapshots of all NEXARA state.

Each run produces a single tar.gz containing knowledge, world model, relay
registry and infrastructure observations, plus a manifest with per-file sha256.
Every archive is verified by re-reading it after write; a snapshot that cannot
be read back is deleted rather than counted as a backup.

Optionally pushes to a git remote when NEXARA_GIT_REMOTE is set.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

BACKUP_DIR = os.environ.get("NEXARA_BACKUP_DIR", "/backups")
INTERVAL = int(os.environ.get("NEXARA_BACKUP_INTERVAL", "3600"))
KEEP = int(os.environ.get("NEXARA_BACKUP_KEEP", "24"))
GIT_REMOTE = os.environ.get("NEXARA_GIT_REMOTE", "").strip()

# (label, path) — read-only mounts included when present
SOURCES = [
    ("knowledge-base", os.path.join(os.environ.get("NEXARA_DATA", "/data"), "knowledge-base")),
    ("research", os.path.join(os.environ.get("NEXARA_DATA", "/data"), "research")),
    ("relay", "/data-relay/relay"),
    ("infrastructure", "/data-infra/infrastructure"),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def make_snapshot() -> dict:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"nexara-{stamp}.tar.gz"
    out = os.path.join(BACKUP_DIR, name)

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": os.environ.get("NEXARA_NODE_NAME", "unknown"),
        "sources": {},
        "files": {},
    }

    staged = tempfile.mkdtemp(prefix="nexara-backup-")
    try:
        total_files = 0
        for label, src in SOURCES:
            if not os.path.isdir(src):
                manifest["sources"][label] = {"present": False, "reason": "not mounted"}
                continue
            dest = os.path.join(staged, label)
            shutil.copytree(src, dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.lock", ".tmp-*"))
            count = 0
            for root, _dirs, files in os.walk(dest):
                for fn in files:
                    p = os.path.join(root, fn)
                    rel = os.path.relpath(p, staged)
                    manifest["files"][rel] = sha256_file(p)
                    count += 1
            manifest["sources"][label] = {"present": True, "files": count}
            total_files += count

        manifest["file_count"] = total_files
        with open(os.path.join(staged, "MANIFEST.json"), "w") as f:
            json.dump(manifest, f, indent=2)

        with tarfile.open(out, "w:gz") as tar:
            tar.add(staged, arcname="nexara")

        # verify: the archive must open and contain the manifest
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
            if "nexara/MANIFEST.json" not in names:
                raise RuntimeError("manifest missing from archive")
            members = len(names)

        return {
            "ok": True, "archive": out, "bytes": os.path.getsize(out),
            "files": total_files, "members": members, "created_at": manifest["created_at"],
        }
    except Exception as e:
        if os.path.exists(out):
            os.unlink(out)  # never keep an unverifiable backup
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def prune(keep: int) -> list[str]:
    archives = sorted(
        (f for f in os.listdir(BACKUP_DIR)
         if f.startswith("nexara-") and f.endswith(".tar.gz")),
        reverse=True,
    )
    removed = []
    for old in archives[keep:]:
        try:
            os.unlink(os.path.join(BACKUP_DIR, old))
            removed.append(old)
        except OSError:
            pass
    return removed


def git_push() -> dict:
    """Push knowledge to a git remote if configured. Never invents success."""
    if not GIT_REMOTE:
        return {"pushed": False, "reason": "NEXARA_GIT_REMOTE not set"}
    repo = os.path.join(os.environ.get("NEXARA_DATA", "/data"), "knowledge-base")
    if not os.path.isdir(repo):
        return {"pushed": False, "reason": "knowledge-base not present"}
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True,
                                        timeout=120, env=env)
        if not os.path.isdir(os.path.join(repo, ".git")):
            run("git", "init")
            run("git", "config", "user.name", "NEXARA Backup")
            run("git", "config", "user.email", "backup@nexara.local")
            run("git", "remote", "add", "origin", GIT_REMOTE)
        run("git", "add", "-A")
        c = run("git", "commit", "-m", f"backup {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        p = run("git", "push", "origin", "HEAD")
        return {
            "pushed": p.returncode == 0,
            "commit": c.returncode == 0,
            "stderr": (p.stderr or "").strip()[:300],
        }
    except Exception as e:
        return {"pushed": False, "error": str(e)}


def run_once() -> dict:
    snap = make_snapshot()
    result: dict[str, object] = {"snapshot": snap}
    if snap.get("ok"):
        result["pruned"] = prune(KEEP)
        result["git"] = git_push()
    with open(os.path.join(BACKUP_DIR, "last-backup.json"), "w") as f:
        json.dump(result, f, indent=2)
    return result


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if "--once" in sys.argv:
        r = run_once()
        print(json.dumps(r, indent=2))
        return 0 if r["snapshot"].get("ok") else 1

    print(f"[backup] daemon start: interval={INTERVAL}s keep={KEEP} dir={BACKUP_DIR}", flush=True)
    while True:
        alive = os.path.join(BACKUP_DIR, ".daemon-alive")
        with open(alive, "w") as f:
            f.write(str(time.time()))
        try:
            r = run_once()
            s = r["snapshot"]
            if s.get("ok"):
                print(f"[backup] ok: {os.path.basename(s['archive'])} "
                      f"({s['files']} files, {s['bytes']:,} B)", flush=True)
            else:
                print(f"[backup] FAILED: {s.get('error')}", flush=True)
        except Exception as e:
            print(f"[backup] error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
