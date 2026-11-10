#!/usr/bin/env bash
# One-off setup on the Dell for the VBI jobs. User space only (uv + Playwright Chromium).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x "$HOME/venvs/tools/bin/uv" ] || { python3 -m venv "$HOME/venvs/tools"; "$HOME/venvs/tools/bin/pip" install -q uv; }
export UV_PROJECT_ENVIRONMENT="$HOME/venvs/vbi"
"$HOME/venvs/tools/bin/uv" sync --frozen --python python3
"$HOME/venvs/vbi/bin/python" -m playwright install chromium
mkdir -p "$HOME/data/outbox/vbi"
chmod +x dell/*.sh
echo "VBI Dell setup done. If Chromium later complains about system libraries, run: sudo $HOME/venvs/vbi/bin/python -m playwright install-deps chromium"