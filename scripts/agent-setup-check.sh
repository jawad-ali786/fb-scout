#!/bin/sh
# FB Scout setup check for Cursor, OpenAI Codex and GitHub Copilot. Claude Code has its own check, which
# also installs everything: plugins/fb-scout/scripts/session-start.sh.
#
# .cursor/hooks.json, .codex/hooks.json and .github/hooks/fb-scout.json run it when a session starts:
#     git -c alias.fb-scout-check=!sh fb-scout-check scripts/agent-setup-check.sh <cursor|codex|copilot>
# Going through git makes one command work in every shell, also on Windows without sh on PATH (git
# brings its own), and runs it in the repository root.
#
# When uv or the Python packages are missing, it prints the setup commands as JSON for that agent: a
# message for the user (Codex, Copilot in VS Code) and context for the agent, which is told to show
# them to the user first (the only way in Cursor and Copilot CLI). Prints nothing when FB Scout is
# installed, and never fails the session.

AGENT=${1:-copilot}
SERVER=plugins/fb-scout/server

case $AGENT in
  cursor) APP=Cursor ;;
  codex)  APP=Codex ;;
  *)      APP="VS Code (or Copilot CLI)" ;;
esac

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    UV_INSTALL='powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    UV_EXE=uv.exe ;;
  *)
    UV_INSTALL='curl -LsSf https://astral.sh/uv/install.sh | sh'
    UV_EXE=uv ;;
esac

uv_on_path() { command -v uv >/dev/null 2>&1; }

uv_installed() {   # on PATH, or installed after this window was opened (its PATH doesn't have it yet)
  uv_on_path && return 0
  for dir in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -n "$dir" ] && [ -f "$dir/$UV_EXE" ] && return 0
  done
  return 1
}

packages_installed() { [ -f "$SERVER/.venv/Scripts/fbscout-mcp.exe" ] || [ -f "$SERVER/.venv/bin/fbscout-mcp" ]; }

uv_on_path && packages_installed && exit 0

STEPS=""
N=0
step() { N=$((N + 1)); STEPS="$STEPS
$N. $1"; }

uv_installed || step "Install uv, which brings Python: $UV_INSTALL"
if ! packages_installed; then
  where="In this repository folder"
  uv_on_path || where="In a new terminal, in this repository folder"
  step "$where (about 100 MB, 1-2 minutes): uv sync --inexact --frozen --no-dev --project $SERVER"
fi
if uv_on_path; then
  step "Restart the fb-scout MCP server (or $APP)."
else
  step "Restart $APP, so that it finds uv."
fi
packages_installed || STEPS="$STEPS
If neither Google Chrome nor Microsoft Edge is installed, also run: uv run --project $SERVER playwright install chromium"

MESSAGE="FB Scout isn't set up on this computer yet. Do this once:$STEPS"
CONTEXT="FB Scout is not set up on this computer yet, so the fb-scout MCP tools are missing or can't start. \
Before anything else, show the user these setup steps (also in AGENTS.md, \"Setup\") and offer to run the \
commands for them:$STEPS
Until then, don't search Facebook any other way."

json() {   # a JSON string (the text has no backslashes or tabs)
  printf '"'
  printf '%s\n' "$1" | sed 's/"/\\"/g' | awk 'BEGIN { ORS = "" } NR > 1 { print "\\n" } { print }'
  printf '"'
}

user=$(json "$MESSAGE")
context=$(json "$CONTEXT")
case $AGENT in
  cursor)
    printf '{"additional_context": %s, "user_message": %s}\n' "$context" "$user" ;;
  codex)
    printf '{"systemMessage": %s, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": %s}}\n' \
      "$user" "$context" ;;
  *)   # Copilot CLI reads additionalContext, VS Code hookSpecificOutput and systemMessage
    printf '{"systemMessage": %s, "additionalContext": %s, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": %s}}\n' \
      "$user" "$context" "$context" ;;
esac
exit 0
