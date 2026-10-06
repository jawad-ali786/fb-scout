"""The files in the repository root that let Cursor, Codex and GitHub Copilot use FB Scout."""

import importlib.util
import json
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
