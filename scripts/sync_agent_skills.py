"""Write the copies of FB Scout's skills that Cursor, Codex and GitHub Copilot read.

The skills are written for Claude Code, in plugins/fb-scout/skills/<name>/SKILL.md. Cursor, Codex and
Copilot all read .agents/skills/<name>/SKILL.md, which this script generates from them: it keeps only
the `name` and `description` frontmatter and replaces what works only in Claude Code ($ARGUMENTS,
/fb-scout:... commands, the background setup, Claude's tool names).

    uv run --no-project python scripts/sync_agent_skills.py           # write .agents/skills
    uv run --no-project python scripts/sync_agent_skills.py --check   # exit 1 if they are out of date

Edit the Claude skills, never the copies. tests/test_agent_files.py fails when the copies are stale.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "plugins" / "fb-scout" / "skills"
TARGET = ROOT / ".agents" / "skills"
MARKER = "<!-- Generated from "

KEEP_FIELDS = ("name", "description")

SETUP = (
    "If the fb-scout tools aren't available at all, the fb-scout MCP server isn't running. On a new\n"
    "computer it needs a one-time setup first (uv, Python and packages): see \"Setup\" in `AGENTS.md` at\n"
    "the root of this repository. Offer to run those commands, then ask the user to restart the fb-scout\n"
    "MCP server (or the agent) so the tools load. Don't search Facebook any other way."
)

# (pattern, replacement). Every pattern must match at least one skill: when a Claude skill changes so
# that one no longer does, the script stops instead of leaving Claude-only text in the copies.
RULES: list[tuple[str, str]] = [
    (r"`/fb-scout:(fb-[a-z]+)` (step \d+)", r"\2 of the `\1` skill"),
    (r"`/fb-scout:(fb-[a-z]+)`", r"the `\1` skill"),
    (r"If the fb-scout tools aren't available at all,.*?(?=\n\n)", SETUP),
    (r"(?<=\. )`Read`", "Read"),
    (r"`Read`", "read"),
    (r"set by Claude", "set by the agent"),
    (r"<plugin>([\\/])server", r"<repo>\1plugins\1fb-scout\1server"),
]

# Nothing of this may be left in a copy.
CLAUDE_ONLY = ("$ARGUMENTS", "/fb-scout:", "mcp__", "Claude", "/mcp ", "`Read`", "<plugin>")


class SyncError(Exception):
    pass


def split_frontmatter(text: str, source: str) -> tuple[dict[str, str], str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise SyncError(f"{source}: no frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(": ")
        if not sep or not re.fullmatch(r"[a-z-]+", key):
            raise SyncError(f"{source}: frontmatter line is not a one-line `key: value`: {line!r}")
        fields[key] = value
    return fields, text[match.end():]


def convert(name: str, text: str, used: set[str]) -> str:
    source = f"plugins/fb-scout/skills/{name}/SKILL.md"
    fields, body = split_frontmatter(text, source)
    if fields.get("name") != name:
        raise SyncError(f"{source}: `name` must be {name!r} (the folder name)")

    hint = fields.get("argument-hint", "")
    hint = json.loads(hint) if hint.startswith('"') else hint
    request = (
        f"The request is the user's message. Called by name (`/{name}` in Cursor and Copilot, `${name}` in\n"
        f"Codex), it takes this form:\n`{hint}`"
    )
    body, n = re.subn(r"^User request: \$ARGUMENTS$", lambda _: request, body, flags=re.MULTILINE)
    if n != 1:
        raise SyncError(f"{source}: expected one line 'User request: $ARGUMENTS'")

    for pattern, replacement in RULES:
        body, n = re.subn(pattern, replacement, body, flags=re.DOTALL)
        if n:
            used.add(pattern)

    left = [s for s in CLAUDE_ONLY if s in body]
    if left:
        raise SyncError(f"{source}: Claude-only text left after conversion: {left}; add a rule to RULES")

    frontmatter = "".join(f"{key}: {fields[key]}\n" for key in KEEP_FIELDS)
    note = f"{MARKER}{source} by scripts/sync_agent_skills.py: edit that file, then run the script. -->\n"
    return f"---\n{frontmatter}---\n{note}{body}"


def build() -> dict[Path, str | None]:
    """Every file under .agents/skills that should change: path -> new text (None = delete)."""
    used: set[str] = set()
    wanted = {}
    for skill in sorted(SOURCE.glob("*/SKILL.md")):
        name = skill.parent.name
        wanted[TARGET / name / "SKILL.md"] = convert(name, skill.read_text(encoding="utf-8"), used)
    unused = [p for p, _ in RULES if p not in used]
    if unused:
        raise SyncError(f"rules that match no skill any more (update RULES): {unused}")

    changes: dict[Path, str | None] = {}
    for path, text in wanted.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            changes[path] = text
    for path in TARGET.glob("*/SKILL.md"):   # copies of skills that were removed or renamed
        if path not in wanted and MARKER in path.read_text(encoding="utf-8"):
            changes[path] = None
    return changes


def main(argv: list[str]) -> int:
    try:
        changes = build()
    except SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for path, text in changes.items():
        rel = path.relative_to(ROOT).as_posix()
        if "--check" in argv:
            print(f"out of date: {rel}", file=sys.stderr)
        elif text is None:
            path.unlink()
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
            print(f"removed {rel}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print(f"wrote {rel}")
    if "--check" in argv and changes:
        print("run: uv run --no-project python scripts/sync_agent_skills.py", file=sys.stderr)
        return 1
    if not changes:
        print("agent skills are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
