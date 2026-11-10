#!/usr/bin/env bash
# Shared helpers for this project's Dell (Ubuntu) jobs. Sourced by run_*.sh.
# Same pattern as the Zara move: pull the code first; a failed pull is logged and
# the run continues with the code already on disk. Results go to the Drive outbox.
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 GIT_TERMINAL_PROMPT=0
export PATH="$HOME/.local/bin:$HOME/venvs/tools/bin:$PATH"

log() { echo "[$(date '+%F %T')] $*"; }
die() { echo "ERROR: $*" >&2; exit 2; }
need_file() { [ -e "$1" ] || die "missing $1 - $2"; }

# pull_repo DIR - latest code from the laptop push (or GitHub for GitHub repos).
pull_repo() {
  if git -C "$1" pull --rebase --autostash -q; then
    log "pulled $(basename "$1") @ $(git -C "$1" log -1 --format='%h %s')"
  else
    git -C "$1" rebase --abort >/dev/null 2>&1
    log "WARNING: could not pull $(basename "$1") - running with the code already on the Dell"
  fi
}

# publish OUTBOX_NAME FILE... - copy readable results to ~/data/outbox/<name>/
# (the Dell job drive-sync copies that folder to Google Drive every 5 min).
publish() {
  local d="$HOME/data/outbox/$1"; shift
  mkdir -p "$d"
  for f in "$@"; do [ -e "$f" ] && cp -f "$f" "$d/"; done
  log "outbox $d updated"
}