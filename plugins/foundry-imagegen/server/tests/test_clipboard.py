from pathlib import Path

import pytest

from foundry_imagegen.clipboard import ClipboardError, build_command


def which_none(_):
    return None


def test_windows_image_uses_sta_powershell_and_env_path():
    cmd = build_command("image", Path("C:/x/it's here.png"), platform="win32", environ={})
    assert cmd.argv[:5] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command"]
    assert "SetDataObject" in cmd.argv[5] and "it's here" not in cmd.argv[5]  # path never in the script
    assert cmd.env["FOUNDRY_IMAGEGEN_CLIP_PATH"].endswith("it's here.png")


def test_windows_path_uses_set_clipboard():
    cmd = build_command("path", Path("C:/x/a.png"), platform="win32", environ={})
    assert "Set-Clipboard" in cmd.argv[-1]


def test_macos_commands(tmp_path):
    png = tmp_path / "a.png"
    cmd = build_command("image", png, platform="darwin", environ={})
    assert cmd.argv[0] == "osascript" and cmd.env["FOUNDRY_IMAGEGEN_CLIP_PNG"] == "1"
    assert build_command("path", png, platform="darwin", environ={}).stdin == str(png).encode()
    with pytest.raises(ClipboardError, match="WebP"):
        build_command("image", tmp_path / "a.webp", platform="darwin", environ={})


def test_linux_prefers_wl_copy_on_wayland(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG data")
    tools = {"wl-copy": "/usr/bin/wl-copy", "xclip": "/usr/bin/xclip"}
    cmd = build_command("image", png, platform="linux", environ={"WAYLAND_DISPLAY": "wayland-0"}, which=tools.get)
    assert cmd.argv == ["wl-copy", "--type", "image/png"] and cmd.stdin == b"\x89PNG data"
    cmd = build_command("path", png, platform="linux", environ={}, which=tools.get)
    assert cmd.argv[0] == "xclip" and cmd.stdin == str(png).encode()


def test_linux_without_tools(tmp_path):
    with pytest.raises(ClipboardError, match="xclip"):
        build_command("path", tmp_path / "a.png", platform="linux", environ={}, which=which_none)
