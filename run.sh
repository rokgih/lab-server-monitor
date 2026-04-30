#!/usr/bin/env bash
# Collect a snapshot, commit, and push. Run by cron / systemd timer.
# Reads master_name from nodes.yaml so the same script works on every master.
# Handles concurrent pushes from other masters via pull --rebase + retry.
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
# Skip silently if nothing changed since last run.
if git diff --cached --quiet; then
  exit 0
fi

git -c user.name=lsm-bot -c user.email=lsm@local \
    commit -m "snapshot ${MASTER} $(date -u +%FT%TZ)" >/dev/null

# Push with retry. Each master only writes to data/<master>.json so rebase
# can never conflict — it always fast-forwards the other masters' commits.
for attempt in 1 2 3; do
  if git push --quiet 2>/dev/null; then
    exit 0
  fi
  git pull --rebase --quiet || {
    echo "rebase failed (unexpected — check for manual edits)" >&2
    exit 1
  }
done
echo "push failed after 3 attempts" >&2
exit 1
