# fbscout (server)

Python core of the FB Scout plugin: a Playwright-based Facebook keyword
collector, exposed as an MCP server (`fbscout-mcp`) and a CLI (`fbscout`).

```
uv run fbscout status
uv run fbscout login
uv run fbscout search "keyword" --max 20
uv run fbscout runs
uv run pytest
```

See the repository README and `docs/` for the full documentation.
