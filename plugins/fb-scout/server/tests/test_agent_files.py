"""The files in the repository root that let Cursor, Codex and GitHub Copilot use FB Scout."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "fb-scout"

if not (ROOT / "AGENTS.md").exists():   # the plugin folder on its own (e.g. Claude Code's plugin cache)
    pytest.skip("needs the whole repository", allow_module_level=True)


def test_agent_skills_are_generated_from_the_claude_skills():
    spec = importlib.util.spec_from_file_location("sync_agent_skills", ROOT / "scripts" / "sync_agent_skills.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    stale = [p.relative_to(ROOT).as_posix() for p in sync.build()]
    assert not stale, f"{stale} out of date: run uv run --no-project python scripts/sync_agent_skills.py"


def _read_server(config: str) -> dict:
    path = ROOT / config
    if path.suffix == ".toml":
        tomllib = pytest.importorskip("tomllib")   # Python 3.11+
        return tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["fb-scout"]
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("mcpServers") or data["servers"])["fb-scout"]


def _resolve(value: str) -> Path:
    value = value.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)).replace("${workspaceFolder}", str(ROOT))
    return (ROOT / value).resolve()   # relative paths: the agent is started in the repository root


@pytest.mark.parametrize("config", [
    "plugins/fb-scout/.mcp.json", ".cursor/mcp.json", ".codex/config.toml", ".vscode/mcp.json", ".github/mcp.json",
])
def test_every_agent_starts_the_same_server(config):
    server = _read_server(config)
    args = server["args"]
    assert server["command"] == "uv"
    assert args[0] == "run" and args[-1] == "fbscout-mcp"
    assert {"--quiet", "--frozen", "--no-dev"} <= set(args)

    flag = "--directory" if "--directory" in args else "--project"
    assert _resolve(args[args.index(flag) + 1]) == (PLUGIN / "server").resolve()
    if config != "plugins/fb-scout/.mcp.json":
        # --directory would make the server folder the working directory, and the results would land
        # there; Claude Code alone passes the project folder (CLAUDE_PROJECT_DIR).
        assert flag == "--project"
    output_dir = server.get("env", {}).get("FBSCOUT_OUTPUT_DIR")
    if output_dir:
        assert _resolve(output_dir) == (ROOT / "fb-scout-output").resolve()


# The setup check that Cursor, Codex and Copilot run when a session starts. No quotes, so the same
# command works in sh, PowerShell and cmd.
SETUP_CHECK = "git -c alias.fb-scout-check=!sh fb-scout-check scripts/agent-setup-check.sh"
SYNC = "uv sync --inexact --frozen --no-dev --project plugins/fb-scout/server"


def _hook_commands(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("command", "bash", "powershell") and isinstance(value, str):
                yield value
            else:
                yield from _hook_commands(value)
    elif isinstance(node, list):
        for value in node:
            yield from _hook_commands(value)


@pytest.mark.parametrize("config, agent", [
    (".cursor/hooks.json", "cursor"), (".codex/hooks.json", "codex"), (".github/hooks/fb-scout.json", "copilot"),
])
def test_every_agent_runs_the_setup_check(config, agent):
    commands = list(_hook_commands(json.loads((ROOT / config).read_text(encoding="utf-8"))))
    assert commands and all(c == f"{SETUP_CHECK} {agent}" for c in commands)


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
@pytest.mark.parametrize("agent", ["cursor", "codex", "copilot"])
def test_setup_check_tells_the_agent_what_to_run(tmp_path, agent):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "agent-setup-check.sh", tmp_path / "scripts")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)   # a repository without FB Scout installed
    out = subprocess.run(SETUP_CHECK.split() + [agent], cwd=tmp_path, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    context = (data.get("additional_context")                        # Cursor
               or data.get("additionalContext")                      # Copilot CLI
               or data["hookSpecificOutput"]["additionalContext"])   # Codex
    assert SYNC in context and "show the user" in context
    if agent != "cursor":   # Cursor shows nothing to the user at session start
        assert SYNC in data["systemMessage"]


@pytest.mark.skipif(not shutil.which("git") or not shutil.which("uv"), reason="needs git and uv")
def test_setup_check_is_silent_once_installed():
    venv = PLUGIN / "server" / ".venv"
    if not ((venv / "Scripts" / "fbscout-mcp.exe").exists() or (venv / "bin" / "fbscout-mcp").exists()):
        pytest.skip("the packages aren't installed here")
    out = subprocess.run(SETUP_CHECK.split() + ["copilot"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout == ""
