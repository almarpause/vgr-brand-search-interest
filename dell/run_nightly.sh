#!/usr/bin/env bash
# VBI nightly staggered fetch (was Windows task "VBI-FetchNightly", daily 02:30).
# Extra args (e.g. --shard 3) are passed through to the fetcher.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
. dell/lib.sh
R="$PWD"; PY="$HOME/venvs/vbi/bin/python"
need_file "$PY" "run dell/setup_dell.sh first"
pull_repo "$R"
mkdir -p logs
"$PY" -m vgr_brand_index.fetch_nightly "$@"; rc=$?
publish vbi "$R/logs/nightly_status.json"
log "nightly fetch finished rc=$rc"
exit $rc