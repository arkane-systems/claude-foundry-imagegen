import json
from pathlib import Path

import httpx
import pytest
import respx
from mcp.client import Client

from conftest import png_b64, png_bytes
from foundry_imagegen import server

V1 = "https://res.services.ai.azure.com/openai/v1/images"


@pytest.fixture(autouse=True)
def configured(monkeypatch, settings):
    monkeypatch.setattr(server, "_state", {"settings": settings})
    yield
    server._state.clear()


def ok(n=1):
    return httpx.Response(200, json={"data": [{"b64_json": png_b64(), "revised_prompt": "rp"} for _ in range(n)]})


@respx.mock
async def test_generate_end_to_end(settings):
    respx.post(f"{V1}/generations").mock(return_value=ok(2))
    async with Client(server.mcp) as client:
        result = await client.call_tool("generate_image", {"prompt": "red square", "n": 2, "size": "1024x1024"})
    assert not result.is_error
    kinds = [c.type for c in result.content]
    assert kinds == ["text", "image", "image"]
    assert "Revised prompt used by the service: rp" in result.content[0].text
    images = result.structured_content["images"]
    assert len(images) == 2
    index = [json.loads(line) for line in (settings.output_dir / "index.jsonl").read_text().splitlines()]
    assert {r["file"] for r in index} == {i["path"] for i in images}


async def test_validation_error_is_tool_error():
    async with Client(server.mcp) as client:
        result = await client.call_tool("generate_image", {"prompt": "x", "size": "1000x1000"})
    assert result.is_error
    assert "multiples of 16" in result.content[0].text


async def test_unknown_deployment():
    async with Client(server.mcp) as client:
        result = await client.call_tool("generate_image", {"prompt": "x", "deployment": "dall-e-3"})
    assert result.is_error and "not configured" in result.content[0].text


@respx.mock
async def test_edit_and_fetch(settings, tmp_path):
    (tmp_path / "src.png").write_bytes(png_bytes())
    respx.post(f"{V1}/edits").mock(return_value=ok())
    async with Client(server.mcp) as client:
        result = await client.call_tool("edit_image", {"prompt": "make it blue", "images": ["src.png"]})
        assert not result.is_error, result.content[0].text
        path = result.structured_content["images"][0]["path"]
        fetched = await client.call_tool("fetch_image", {"path": path})
        assert fetched.structured_content["mime"] == "image/png"
        denied = await client.call_tool("fetch_image", {"path": str(tmp_path / "src.png")})
        assert denied.is_error


async def test_check_config_without_probe():
    async with Client(server.mcp) as client:
        result = await client.call_tool("check_config", {"probe": False})
    text = result.content[0].text
    assert "secr…3456" in text and "secret-key-123456" not in text


@respx.mock
async def test_per_call_output_dir_expands_variables(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    respx.post(f"{V1}/generations").mock(return_value=ok())
    async with Client(server.mcp) as client:
        result = await client.call_tool("generate_image", {"prompt": "x", "output_dir": "${HOME}/custom"})
    assert not result.is_error, result.content[0].text
    assert result.structured_content["output_dir"] == str(tmp_path / "custom")


async def test_gallery_requests_clipboard_permission():
    async with Client(server.mcp) as client:
        resources = await client.list_resources()
        gallery = next(r for r in resources.resources if r.uri == server.GALLERY_URI)
        content = await client.read_resource(gallery.uri)
    meta = content.contents[0].meta or gallery.meta
    assert meta["ui"]["permissions"] == {"clipboardWrite": {}}
