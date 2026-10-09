#!/usr/bin/env bash
# Build the Claude Desktop Extension (.mcpb) from the shared server package.
# Output: dist/foundry-imagegen-<version>.mcpb
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server="$root/plugins/foundry-imagegen/server"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$root/mcpb/manifest.json")"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

cp "$root/mcpb/manifest.json" "$root/mcpb/.mcpbignore" "$root/mcpb/icon.png" "$stage/"
cp "$server/pyproject.toml" "$server/uv.lock" "$stage/"
cp -r "$server/src" "$stage/src"
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +

mkdir -p "$root/dist"
out="$root/dist/foundry-imagegen-$version.mcpb"
npx --yes @anthropic-ai/mcpb@2.1.2 validate "$stage/manifest.json"
npx --yes @anthropic-ai/mcpb@2.1.2 pack "$stage" "$out"
echo "Built $out"
