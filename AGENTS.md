# FB Scout: notes for coding agents

FB Scout is a research tool. Given a keyword, it searches Facebook (posts, groups, comments,
Marketplace), keeps only results that really contain the keyword, screenshots each one with the keyword
highlighted, saves `results.json` with metadata, and merges every run into one SQLite dataset. The
work is done by the **`fb-scout` MCP server** (Python, in `plugins/fb-scout/server`). The skills
describe how to use its tools.

This file is for Cursor, OpenAI Codex, GitHub Copilot and other agents that read `AGENTS.md`. Claude
Code uses the plugin instead (see `README.md`, "Install").

## Where each agent finds FB Scout

| Agent | MCP server config | Skills | These instructions |
|---|---|---|---|
| Cursor (editor and CLI) | `.cursor/mcp.json` | `.agents/skills/` | `AGENTS.md` |
| OpenAI Codex (CLI, IDE extension, app) | `.codex/config.toml` | `.agents/skills/` | `AGENTS.md` |
| GitHub Copilot in VS Code | `.vscode/mcp.json` | `.agents/skills/` | `AGENTS.md` |
| GitHub Copilot CLI | `.github/mcp.json` | `.agents/skills/` | `AGENTS.md` |
| Claude Code | the plugin's `plugins/fb-scout/.mcp.json` | `plugins/fb-scout/skills/` | the skills |

Skills: `fb-search` (one keyword), `fb-batch` (a study: several keywords × groups) and `fb-dataset`
(counts, browsing, labels, exports). Call them by name (`/fb-search "solar panel" max=15` in Cursor
and Copilot, `$fb-search` in Codex), or just ask ("search facebook for 'Brand X', 20 posts").

## Setup (once per computer)

Run these in the repository root:

1. **uv**, which brings Python. Skip this if `uv --version` works.
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

   Then restart the agent (or the editor) so it finds `uv`.
2. **Python and the packages** (about 100 MB, 1–2 minutes):
   `uv sync --inexact --frozen --no-dev --project plugins/fb-scout/server`
3. **A browser**: Google Chrome (recommended) or Microsoft Edge. If neither is installed:
   `uv run --project plugins/fb-scout/server playwright install chromium`
4. **Turn on the `fb-scout` server** in the agent:
   - **Cursor**: approve the project's `fb-scout` MCP server when asked, or switch it on in the MCP settings.
   - **Codex**: start Codex in the repository root and trust the folder. Codex ignores
     `.codex/config.toml` in folders that aren't trusted. `/mcp` shows whether `fb-scout` is running.
   - **Copilot in VS Code**: open this folder, trust the `fb-scout` server when asked (or press
     *Start* above it in `.vscode/mcp.json`), and use the chat in Agent mode.
   - **Copilot CLI**: start `copilot` in the repository root and trust the folder.

The first search opens a normal Chrome window for a one-time Facebook login (use a dedicated research
account). After that, searches run in a hidden browser. Results go to `fb-scout-output/` in the
repository root (git-ignored): `<keyword>/<timestamp>/results.json`, `screenshots/` and the dataset
`fbscout.sqlite`.

If the fb-scout tools are missing in a session, the server isn't running. Check steps 1–4. Don't
search Facebook any other way (no browser automation, no web fetching).

## Rules for using the tools

- Run **one** search or study at a time, never in parallel. A search takes 1–2 minutes, a study can take
  an hour, and the login waits until the user closes the window. Don't cancel these calls early.
- On `checkpoint` or `blocked`, stop. The user resolves it by hand in the browser and waits (hours for
  `blocked`). Never retry in a loop.
- Keep volumes small (default 20 results, at most about 50). This protects the research account.
- Report only what the tools return. Never invent posts, counts, URLs or labels.
- Screenshots and results contain personal data. Share them only in anonymized form
  (`fb_export` with `anonymize: true`, `blur_names` for screenshots).

## Working on the code

- Layout: `README.md`, "Repository layout". All knowledge of Facebook's pages is in
  `plugins/fb-scout/server/src/fbscout/extract.py`.
- Tests: `cd plugins/fb-scout/server` then `uv run pytest`. These are offline browser tests on fake pages
  and use the installed Chrome, headless.
- **Skills**: edit the Claude Code skills in `plugins/fb-scout/skills/*/SKILL.md`, then run
  `uv run --no-project python scripts/sync_agent_skills.py`, which writes `.agents/skills/` for the other
  agents. Never edit `.agents/skills/` by hand; a test fails when it is out of date.
- **Server launch**: the command that starts the server is in five files: `plugins/fb-scout/.mcp.json`,
  `.cursor/mcp.json`, `.codex/config.toml`, `.vscode/mcp.json` and `.github/mcp.json`. Change them together.
- **Version**: bump it together in `plugins/fb-scout/.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, `plugins/fb-scout/server/pyproject.toml` and
  `plugins/fb-scout/server/src/fbscout/__init__.py`.
- **This repository is public.** Never commit `fb-scout-output/`, cookie files, browser profiles, or
  real names, profile ids or quotes from collected posts. Use made-up values in tests and examples.
