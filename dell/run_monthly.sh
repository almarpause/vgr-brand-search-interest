#!/usr/bin/env bash
# VBI monthly assemble + report + email (was Windows task "VBI-Monthly", day 1 06:00).
# Pass --dry-run to build everything without sending the email.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
. dell/lib.sh
R="$PWD"; PY="$HOME/venvs/vbi/bin/python"
need_file "$PY" "run dell/setup_dell.sh first"
need_file "$R/cache" "copy the laptop's brand-index/cache/ to the Dell once (see dell/README.md)"
# VBI_EXTRA_ARGS="--dry-run" lets a test run (vgr_node run) build everything without emailing.
if [ -n "${VBI_EXTRA_ARGS:-}" ]; then read -r -a _x <<< "$VBI_EXTRA_ARGS"; set -- "$@" "${_x[@]}"; fi
dry=0; for a in "$@"; do [ "$a" = "--dry-run" ] || [ "$a" = "--no-email" ] && dry=1; done
if [ "$dry" = 0 ]; then
  # The report is emailed with the SMTP login reused from the Zara parser (config path in config/settings.json).
  need_file "$HOME/projects/parser/russia/zara/config/email.ini" "SMTP login for the monthly email (reused from Zara)"
fi
pull_repo "$R"
mkdir -p logs
"$PY" -m vgr_brand_index.run_monthly "$@"; rc=$?
m=$(date +%Y-%m)
publish vbi "$R/Brand_Index_Dashboard.html" "$R/brand_index_data.json" "$R/output/$m/index_500.csv" "$R/output/$m/VGR_Brand_Index_$m.docx" "$R/output/latest/dashboard.html"
log "monthly finished rc=$rc"
exit $rc