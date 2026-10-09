# Development notes

This repository is a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`) that holds one
plugin, `plugins/foundry-imagegen`. The plugin's Python MCP server also ships as a Claude Desktop
extension (`.mcpb`).

## Layout

- `plugins/foundry-imagegen/`: the plugin. Only this directory is installed for users.
  - `.claude-plugin/plugin.json`: manifest, `userConfig`, and the MCP server launch (`uv run`).
  - `skills/imagine/`: the prompting skill and its reference docs.
  - `server/`: the Python package `foundry_imagegen`, built on the mcp 2.x SDK and managed with uv.
    - `config.py`: settings. Precedence is env vars (`FOUNDRY_IMAGEGEN_*`), then the `config.toml` fallback, then defaults.
    - `client.py`: Foundry HTTP client. Tries the v1 route first and falls back to the legacy route; handles retries, 429 cooldowns, and error mapping.
    - `ratelimit.py`: cross-process RPM limiter (file lock plus JSON state in the user state dir).
    - `images.py`: parameter and input validation, saving, previews, and `index.jsonl`.
    - `server.py`: tools, MCP Apps registration, and the app-only `fetch_image`/`reveal_image` tools.
    - `ui/gallery.html`: **build output** of `ui/`. It is committed so users never need Node.
- `ui/`: source for the gallery MCP App (TypeScript, `@modelcontextprotocol/ext-apps`, esbuild).
- `mcpb/`: the desktop extension manifest and icon. `scripts/build-mcpb.sh` stages the server and packs it.
- `scripts/bump-version.py`: sets or checks the version across pyproject, `__init__`, plugin.json, the mcpb manifest, and the UI.

## Commands

```bash
cd plugins/foundry-imagegen/server && uv run pytest                  # unit tests
cd ui && npm install && npm run typecheck && npm run build           # rebuild gallery.html
claude plugin validate plugins/foundry-imagegen && claude plugin validate .
./scripts/build-mcpb.sh                                              # dist/foundry-imagegen-<ver>.mcpb
python3 scripts/bump-version.py [new-version]                        # check / set the version
claude --plugin-dir plugins/foundry-imagegen                         # try the plugin locally
```

## Conventions

- Keep the plugin and the `.mcpb` on the same server code and the same `FOUNDRY_IMAGEGEN_*` variables.
  When you add a setting, add it to `plugin.json` `userConfig`, `mcpb/manifest.json` `user_config`, both
  `env` maps, `config.py`, and the README settings table.
- Validate locally before calling the service. Quota is scarce: about 5 requests per minute.
- Tool errors are returned as `CallToolResult(is_error=True)` with a message the user can act on.
- After changing `ui/src/*`, rebuild and commit `server/src/foundry_imagegen/ui/gallery.html`.
