#!/usr/bin/env python3
"""
RESTORE — verify and restore a NEXARA backup archive.

  restore.py --list                 show available snapshots
  restore.py --verify <archive>     check every sha256 in the manifest
  restore.py --restore <archive>    verify, then restore into the target volume

Restore refuses to run unless verification passes. Existing data is moved aside
to <target>.pre-restore-<stamp> rather than deleted.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

BACKUP_DIR = os.environ.get("NEXARA_BACKUP_DIR", "/backups")
DATA = os.environ.get("NEXARA_DATA", "/data")

# label -> restore target
TARGETS = {
    "knowledge-base": os.path.join(DATA, "knowledge-base"),
    "research": os.path.join(DATA, "research"),
    "relay": os.path.join(DATA, "relay"),
    "infrastructure": os.path.join(DATA, "infrastructure"),
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        print(f"no backup dir at {BACKUP_DIR}")
        return 1
    archives = sorted(
        (f for f in os.listdir(BACKUP_DIR)
         if f.startswith("nexara-") and f.endswith(".tar.gz")),
        reverse=True,
    )
    if not archives:
        print("no snapshots found")
        return 1
    print(f"{len(archives)} snapshot(s) in {BACKUP_DIR}:\n")
    for a in archives:
        p = os.path.join(BACKUP_DIR, a)
        print(f"  {a}  {os.path.getsize(p):>12,} B")
    return 0


def _extract(archive: str, dest: str):
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest, filter="data")
    return os.path.join(dest, "nexara")


def verify(archive: str, quiet: bool = False) -> bool:
    if not os.path.exists(archive):
        print(f"ERROR: {archive} not found")
        return False
    tmp = tempfile.mkdtemp(prefix="nexara-verify-")
    try:
        root = _extract(archive, tmp)
        mpath = os.path.join(root, "MANIFEST.json")
        if not os.path.exists(mpath):
            print("ERROR: MANIFEST.json missing")
            return False
        manifest = json.load(open(mpath))

        checked = mismatched = missing = 0
        for rel, expected in manifest.get("files", {}).items():
            p = os.path.join(root, rel)
            if not os.path.exists(p):
                missing += 1
                continue
            checked += 1
            if sha256_file(p) != expected:
                mismatched += 1
                print(f"  MISMATCH: {rel}")

        ok = mismatched == 0 and missing == 0
        if not quiet:
            print(f"archive:  {os.path.basename(archive)}")
            print(f"created:  {manifest.get('created_at')}")
            print(f"node:     {manifest.get('node')}")
            print(f"files:    {checked} verified, {missing} missing, {mismatched} mismatched")
            print(f"result:   {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restore(archive: str, force: bool = False) -> int:
    print(f"verifying {os.path.basename(archive)} ...")
    if not verify(archive, quiet=True):
        print("VERIFICATION FAILED — refusing to restore")
        return 1
    print("verification passed\n")

    tmp = tempfile.mkdtemp(prefix="nexara-restore-")
    try:
        root = _extract(archive, tmp)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        restored = []
        for label, target in TARGETS.items():
            src = os.path.join(root, label)
            if not os.path.isdir(src):
                continue
            if os.path.exists(target) and os.listdir(target):
                aside = f"{target}.pre-restore-{stamp}"
                shutil.move(target, aside)
                print(f"  moved existing {label} -> {os.path.basename(aside)}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copytree(src, target, dirs_exist_ok=True)
            n = sum(len(f) for _r, _d, f in os.walk(target))
            restored.append((label, n))
            print(f"  restored {label}: {n} files -> {target}")

        if not restored:
            print("nothing to restore (no matching sections in archive)")
            return 1
        print(f"\nrestored {len(restored)} section(s)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="NEXARA backup restore")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--verify", metavar="ARCHIVE")
    g.add_argument("--restore", metavar="ARCHIVE")
    ap.add_argument("--latest", action="store_true",
                    help="with --verify/--restore, use the newest snapshot")
    args = ap.parse_args()

    def resolve(a):
        if args.latest or a in ("latest", "-"):
            archives = sorted(
                (f for f in os.listdir(BACKUP_DIR)
                 if f.startswith("nexara-") and f.endswith(".tar.gz")), reverse=True)
            if not archives:
                print("no snapshots available")
                sys.exit(1)
            return os.path.join(BACKUP_DIR, archives[0])
        return a if os.path.isabs(a) else os.path.join(BACKUP_DIR, a)

    if args.list:
        return list_backups()
    if args.verify:
        return 0 if verify(resolve(args.verify)) else 1
    return restore(resolve(args.restore))


if __name__ == "__main__":
    sys.exit(main())
