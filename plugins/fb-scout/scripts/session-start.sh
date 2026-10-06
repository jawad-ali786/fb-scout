#!/usr/bin/env bash
# FB Scout setup, run by Claude Code when a session starts (hooks/hooks.json), in two parts:
#
#   check  quick (well under a second), before Claude's first reply: if FB Scout isn't installed
#          yet, tell the user and Claude that it is being set up in the background, so nobody waits.
#   setup  in the background (asyncRewake): install what is missing: uv (which brings Python),
#          the Python packages (about 100 MB), and a browser if neither Chrome nor Edge is there.
#          When a first-time setup ends, exit 2 wakes Claude to tell the user the tools are ready
#          (or what went wrong).
#
# Once everything is installed, both parts take about 0.1 s and print nothing. Never fails the
# session. FBSCOUT_NO_AUTO_INSTALL=1: don't install uv automatically, only say how to.

MODE="${1:-check}"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVER="$ROOT/server"
STATE="${CLAUDE_PLUGIN_DATA:-$HOME/.fbscout}"   # kept across plugin updates
LOG="$STATE/setup.log"
LOCK="$STATE/setup.lock"
DONE="$STATE/setup-done"                         # set up once on this computer (plugin updates are quick)
MARKER="$SERVER/.venv/.fbscout-synced"
LOCKSUM=$(cksum < "$SERVER/uv.lock" 2>/dev/null)

is_windows() { case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; esac; return 1; }

# uv on PATH, or where its installer puts it (not on PATH yet in a window opened before the install).
find_uv() {
  command -v uv 2>/dev/null && return 0
  local dir
  for dir in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -n "$dir" ] || continue
    dir=$(cygpath -u "$dir" 2>/dev/null || printf '%s' "$dir")
    for exe in "$dir/uv" "$dir/uv.exe"; do
      [ -f "$exe" ] && { printf '%s\n' "$exe"; return 0; }
    done
  done
  return 1
}

ready() { find_uv >/dev/null && [ -n "$LOCKSUM" ] && [ "$(cat "$MARKER" 2>/dev/null)" = "$LOCKSUM" ]; }

# Set up on this computer before (this or another plugin version): then everything is cached.
set_up_before() {
  [ -f "$DONE" ] || [ -d "$SERVER/.venv" ] || ls -d "$ROOT"/../*/server/.venv >/dev/null 2>&1
}

UV_COMMANDS='Windows (PowerShell): powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"  /  macOS or Linux: curl -LsSf https://astral.sh/uv/install.sh | sh'

json() {   # {"systemMessage": $1 (shown to the user), additionalContext: $2 (for Claude)}
  local user=${1//\"/\\\"} claude=${2//\"/\\\"}
  printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' \
    "$user" "$claude"
}

# ---- check: before Claude's first reply ------------------------------------
if [ "$MODE" = "check" ]; then
  ready && exit 0
  if ! find_uv >/dev/null && [ -n "${FBSCOUT_NO_AUTO_INSTALL:-}" ]; then
    json "FB Scout needs uv (it installs Python and everything else). Install it once, then restart Claude Code. $UV_COMMANDS" \
      "FB Scout can't start: uv is not installed and automatic installation is switched off (FBSCOUT_NO_AUTO_INSTALL). If the user wants to use FB Scout, give them the command to install uv, then restart Claude Code. $UV_COMMANDS"
    exit 0
  fi
  if set_up_before && find_uv >/dev/null; then
    exit 0   # a plugin update: everything is cached, the tools start by themselves
  fi
  json "FB Scout is being set up in the background (first start on this computer: uv, Python and packages, 1-2 minutes). You can start chatting now; Claude will tell you when searches are ready." \
    "FB Scout is being installed in the background (first start on this computer: uv, Python and about 100 MB of packages, 1-2 minutes). Until it finishes the fb-scout tools are not available. You will get a note when it is done. If the user asks for a search before that, say FB Scout is still installing and will be ready in a minute or two; don't try to install anything yourself."
  exit 0
fi

# ---- setup: in the background ----------------------------------------------
mkdir -p "$STATE" 2>/dev/null
if ready; then
  [ -f "$DONE" ] || date '+%Y-%m-%d %H:%M:%S' > "$DONE" 2>/dev/null   # set up before this script existed
  exit 0
fi
if ! mkdir "$LOCK" 2>/dev/null; then   # another session is already setting up
  [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ] || exit 0   # unless that one died
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rm -rf "$LOCK"' EXIT
trap 'exit 1' INT TERM HUP

first_time=0
set_up_before || first_time=1
started=$(date +%s)
installed=""
echo "== $(date '+%Y-%m-%d %H:%M:%S') FB Scout setup ($ROOT)" >> "$LOG"

fail() {
  echo "FB Scout setup failed: $1 Last lines of the log ($LOG): $(tail -n 3 "$LOG" 2>/dev/null | tr '\n' ' ')" >&2
  echo "Tell the user in plain words. Suggest checking the internet connection; setup runs again by itself at the next session start." >&2
  exit 2
}

UV=$(find_uv)
if [ -z "$UV" ]; then
  [ -n "${FBSCOUT_NO_AUTO_INSTALL:-}" ] && exit 0   # the check already said how to install it
  echo "-- installing uv" >> "$LOG"
  if is_windows; then
    powershell.exe -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" >> "$LOG" 2>&1
  elif command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >> "$LOG" 2>&1
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh >> "$LOG" 2>&1
  else
    fail "uv is not installed, and neither curl nor wget is there to download it. Install uv by hand: $UV_COMMANDS."
  fi
  UV=$(find_uv) || fail "Installing uv did not work. It can be installed by hand: $UV_COMMANDS."
  installed="uv, "
  first_time=1
fi

# Inexact: keep optional extras the user installed; frozen: use the shipped uv.lock as is.
echo "-- installing Python and packages" >> "$LOG"
"$UV" sync --inexact --frozen --no-dev --directory "$SERVER" >> "$LOG" 2>&1 \
  || fail "Installing Python and the packages did not work."
printf '%s' "$LOCKSUM" > "$MARKER" 2>/dev/null
installed="${installed}Python and the packages"

# A plugin update usually takes seconds (all cached), and the tools start by themselves. Tell Claude
# only after a first setup, or one slow enough that the tools may have given up starting (~30 s).
if [ "$first_time" = 0 ]; then
  [ $(( $(date +%s) - started )) -gt 20 ] || exit 0
  echo "FB Scout updated its packages in the background. If the fb-scout tools (mcp__plugin_fb-scout_fb-scout__*) are not available to you, tell the user to type /mcp reconnect all (no restart needed)." >&2
  exit 2
fi

# Searches use the installed Chrome or Edge; without either, Playwright's own Chromium.
if ! "$UV" run --quiet --frozen --no-dev --directory "$SERVER" python -c \
    "import sys; from fbscout.login import find_browser_executable as f; sys.exit(0 if f('chrome') or f('msedge') else 1)" \
    >> "$LOG" 2>&1; then
  echo "-- no Chrome or Edge: installing Chromium" >> "$LOG"
  if "$UV" run --quiet --frozen --no-dev --directory "$SERVER" playwright install chromium >> "$LOG" 2>&1; then
    installed="$installed, and the Chromium browser (Google Chrome is still recommended for the login window)"
  else
    installed="$installed (no browser: neither Chrome nor Edge is installed and Chromium could not be downloaded; the user should install Google Chrome)"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  echo "FB Scout setup finished in the background: installed $installed. If the fb-scout tools (mcp__plugin_fb-scout_fb-scout__*) are already available to you, tell the user FB Scout is ready. If not, tell the user to type /mcp reconnect all (no restart needed); after that, searches work." >&2
else
  echo "FB Scout setup finished in the background: installed $installed. uv was just installed, and this Claude Code window can't see it yet, so the fb-scout tools can't start in this window. Tell the user to restart Claude Code once (quit and start it again); after that, searches work." >&2
fi
date '+%Y-%m-%d %H:%M:%S' > "$DONE"
echo "-- done" >> "$LOG"
exit 2
