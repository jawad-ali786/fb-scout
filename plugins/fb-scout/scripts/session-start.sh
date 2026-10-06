#!/usr/bin/env bash
# FB Scout setup check, run by Claude Code when a session starts (hooks/hooks.json).
#
# On a new computer the MCP server's first start has to download Python and the
# packages (about 100 MB), which takes longer than Claude Code waits for a server.
# Doing that here, before anyone searches, makes the tools ready on the first try.
# Prints a note for Claude only when something needs attention; otherwise silent.
# Never fails the session: always exits 0.

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVER="$ROOT/server"

if ! command -v uv >/dev/null 2>&1; then
  cat <<'EOF'
FB Scout setup: "uv" is not installed, so the fb-scout tools can't start. If the user wants to use
FB Scout, tell them to install uv once (it installs Python and everything else by itself), then
restart Claude Code:
- Windows (PowerShell): powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
- macOS / Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
EOF
  exit 0
fi

first_time=0
[ -d "$SERVER/.venv" ] || first_time=1

# Already set up for this exact lockfile (same plugin version): nothing to do.
MARKER="$SERVER/.venv/.fbscout-synced"
LOCKSUM=$(cksum < "$SERVER/uv.lock" 2>/dev/null)
if [ "$first_time" = 0 ] && [ -n "$LOCKSUM" ] && [ "$(cat "$MARKER" 2>/dev/null)" = "$LOCKSUM" ]; then
  exit 0
fi

# Inexact: keep optional extras the user installed; frozen: use the shipped uv.lock as is.
if ! out=$(uv sync --inexact --frozen --no-dev --quiet --directory "$SERVER" 2>&1); then
  echo "FB Scout setup: installing the Python packages failed, so the fb-scout tools may not start."
  echo "Error: $(printf '%s' "$out" | tail -n 3)"
  echo "Suggest checking the internet connection, then restarting Claude Code (or /mcp -> reconnect fb-scout)."
  exit 0
fi

printf '%s' "$LOCKSUM" > "$MARKER" 2>/dev/null

if [ "$first_time" = 1 ]; then
  echo "FB Scout setup: first-time install of Python and packages is done. If the fb-scout tools are not"
  echo "available yet in this session, the user can run /mcp and reconnect fb-scout (no restart needed)."
fi
exit 0
