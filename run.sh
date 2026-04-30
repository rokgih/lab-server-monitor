#!/usr/bin/env bash
# Collect a snapshot, commit, and push. Run by cron / systemd timer.
# Reads master_name from nodes.yaml so the same script works on every master.
set -u
cd "$(dirname "$0")"

MASTER=$(awk '/^master_name:/ {print $2; exit}' nodes.yaml)
if [ -z "$MASTER" ]; then
  echo "master_name missing from nodes.yaml" >&2
  exit 1
fi
OUT="data/${MASTER}.json"

python3 collect.py --config nodes.yaml --out "$OUT" || exit 1

git add "$OUT"
# `git diff --cached --quiet` exits 0 if nothing staged → skip commit silently
# (this happens when the JSON didn't change, e.g. all nodes still unreachable).
if ! git diff --cached --quiet; then
  git -c user.name=lsm-bot -c user.email=lsm@local \
      commit -m "snapshot $(date -u +%FT%TZ)" >/dev/null
  git push --quiet
fi
