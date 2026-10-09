"""Put an image or text on the OS clipboard from the server process.

The gallery widget runs in a sandboxed frame that hosts may not grant clipboard access, but the
server runs as an ordinary local process, so it can use the platform's own clipboard tools.
The file path is passed through the environment, never interpolated into a script.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anyio

Kind = Literal["image", "path"]

# Windows: STA is required for the clipboard. Offer both a bitmap (for every app) and the original
# PNG bytes under the "PNG" format (keeps transparency for apps that read it).
_PS_IMAGE = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$p = $env:FOUNDRY_IMAGEGEN_CLIP_PATH
$bytes = [System.IO.File]::ReadAllBytes($p)
$img = [System.Drawing.Image]::FromStream((New-Object System.IO.MemoryStream(,$bytes)))
$data = New-Object System.Windows.Forms.DataObject
$data.SetImage($img)
if ($p.ToLower().EndsWith('.png')) { $data.SetData('PNG', (New-Object System.IO.MemoryStream(,$bytes))) }
[System.Windows.Forms.Clipboard]::SetDataObject($data, $true)
"""
_PS_TEXT = "Set-Clipboard -Value $env:FOUNDRY_IMAGEGEN_CLIP_PATH"

_OSA_IMAGE = (
    'set p to POSIX file (system attribute "FOUNDRY_IMAGEGEN_CLIP_PATH")\n'
    'if (system attribute "FOUNDRY_IMAGEGEN_CLIP_PNG") is "1" then\n'
    "  set the clipboard to (read p as «class PNGf»)\n"
    "else\n"
    "  set the clipboard to (read p as JPEG picture)\n"
    "end if"
)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class ClipboardError(Exception):
    pass


@dataclass(frozen=True)
class ClipboardCommand:
    argv: list[str]
    env: dict[str, str]
    stdin: bytes | None = None


def build_command(
    kind: Kind,
    path: Path,
    *,
    platform: str = sys.platform,
    environ: dict[str, str] | None = None,
    which=shutil.which,
) -> ClipboardCommand:
    environ = dict(os.environ if environ is None else environ)
    env = {**environ, "FOUNDRY_IMAGEGEN_CLIP_PATH": str(path)}
    mime = _MIME.get(path.suffix.lower(), "image/png")

    if platform == "win32":
        script = _PS_IMAGE if kind == "image" else _PS_TEXT
        return ClipboardCommand(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", script], env)

    if platform == "darwin":
        if kind == "path":
            return ClipboardCommand(["pbcopy"], env, str(path).encode())
        if mime not in ("image/png", "image/jpeg"):
            raise ClipboardError("Copying WebP images is not supported on macOS; use Download instead.")
        env["FOUNDRY_IMAGEGEN_CLIP_PNG"] = "1" if mime == "image/png" else "0"
        return ClipboardCommand(["osascript", "-e", _OSA_IMAGE], env)

    data = str(path).encode() if kind == "path" else path.read_bytes()
    content_type = "text/plain" if kind == "path" else mime
    if environ.get("WAYLAND_DISPLAY") and which("wl-copy"):
        return ClipboardCommand(["wl-copy", "--type", content_type], env, data)
    if which("xclip"):
        return ClipboardCommand(["xclip", "-selection", "clipboard", "-t", content_type, "-i"], env, data)
    raise ClipboardError("No clipboard tool found: install wl-clipboard (Wayland) or xclip (X11).")


async def copy_to_clipboard(kind: Kind, path: Path) -> None:
    command = build_command(kind, path)
    try:
        with anyio.fail_after(20):
            result = await anyio.run_process(command.argv, input=command.stdin, env=command.env, check=False)
    except FileNotFoundError as exc:
        raise ClipboardError(f"Clipboard tool not available: {command.argv[0]}") from exc
    except TimeoutError as exc:
        raise ClipboardError("Timed out while copying to the clipboard.") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode(errors="replace").strip().splitlines()
        raise ClipboardError(f"Copy failed ({command.argv[0]} exited {result.returncode}): {detail[-1] if detail else ''}")
