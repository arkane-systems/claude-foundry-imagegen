#!/usr/bin/env python3
"""Set one version everywhere it appears: `scripts/bump-version.py 0.2.0`.

Without an argument, checks that every location agrees with pyproject.toml and exits non-zero if not.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "plugins/foundry-imagegen/server/pyproject.toml"
INIT = ROOT / "plugins/foundry-imagegen/server/src/foundry_imagegen/__init__.py"
JSON_FILES = [
    ROOT / "plugins/foundry-imagegen/.claude-plugin/plugin.json",
    ROOT / "mcpb/manifest.json",
    ROOT / "ui/package.json",
]
GALLERY_TS = ROOT / "ui/src/gallery.ts"


def current_versions() -> dict[Path, str]:
    found = {
        PYPROJECT: re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(), re.M).group(1),
        INIT: re.search(r'__version__ = "([^"]+)"', INIT.read_text()).group(1),
        GALLERY_TS: re.search(r'version: "([^"]+)"', GALLERY_TS.read_text()).group(1),
    }
    for path in JSON_FILES:
        found[path] = json.loads(path.read_text())["version"]
    return found


def set_version(version: str) -> None:
    PYPROJECT.write_text(re.sub(r'^version = "[^"]+"', f'version = "{version}"', PYPROJECT.read_text(), count=1, flags=re.M))
    INIT.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', INIT.read_text()))
    GALLERY_TS.write_text(re.sub(r'(version: )"[^"]+"', rf'\1"{version}"', GALLERY_TS.read_text(), count=1))
    for path in JSON_FILES:
        text = path.read_text()
        path.write_text(re.sub(r'("version":\s*)"[^"]+"', rf'\1"{version}"', text, count=1))


def main() -> int:
    if len(sys.argv) > 1:
        set_version(sys.argv[1])
        print(f"Set version {sys.argv[1]}. Run `uv lock` in the server dir and rebuild the UI.")
    versions = current_versions()
    if len(set(versions.values())) != 1:
        for path, version in versions.items():
            print(f"{version}\t{path.relative_to(ROOT)}")
        return 1
    print(f"All versions agree: {next(iter(versions.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
