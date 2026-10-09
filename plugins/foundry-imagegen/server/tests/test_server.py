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


@respx.mock
async def test_copy_to_clipboard_tool(settings, monkeypatch):
    calls = []

    async def fake_copy(kind, path):
        calls.append((kind, path))

    monkeypatch.setattr(server, "copy_to_clipboard", fake_copy)
    respx.post(f"{V1}/generations").mock(return_value=ok())
    async with Client(server.mcp) as client:
        result = await client.call_tool("generate_image", {"prompt": "x"})
        path = result.structured_content["images"][0]["path"]
        copied = await client.call_tool("copy_image_to_clipboard", {"path": path, "content": "image"})
        assert not copied.is_error and calls == [("image", Path(path))]
        denied = await client.call_tool("copy_image_to_clipboard", {"path": "/etc/passwd", "content": "path"})
        assert denied.is_error


@pytest.fixture
def upload_broker(tmp_path, monkeypatch):
    from foundry_imagegen import uploads

    b = uploads.UploadBroker(tmp_path / "stage")
    monkeypatch.setattr(uploads, "_broker", b)
    return b


@respx.mock
async def test_upload_then_edit_records_provenance(settings, upload_broker):
    import base64

    import anyio

    edit_route = respx.post(f"{V1}/edits").mock(return_value=ok())
    data = png_bytes()
    async with Client(server.mcp) as client:
        results = {}

        async def ask():
            results["upload"] = await client.call_tool("upload_images", {"purpose": "photo to edit"})

        async with anyio.create_task_group() as tg:
            tg.start_soon(ask)
            await anyio.sleep(0.2)
            staged = await client.call_tool(
                "stage_upload", {"files": [{"name": "holiday.png", "data": base64.b64encode(data).decode()}]}
            )
            assert not staged.is_error and staged.structured_content["delivered"]

        upload = results["upload"]
        assert upload.structured_content["status"] == "received"
        path = upload.structured_content["images"][0]["path"]
        edited = await client.call_tool("edit_image", {"prompt": "make it night", "images": [path]})
        assert not edited.is_error, edited.content[0].text
    assert edit_route.called
    record = json.loads((settings.output_dir / "index.jsonl").read_text().splitlines()[-1])
    assert record["inputs"][0]["uploaded"] == "holiday.png"
    assert "path" not in record["inputs"][0] and record["inputs"][0]["sha256"]


async def test_upload_cancel(upload_broker):
    import anyio

    async with Client(server.mcp) as client:
        results = {}

        async def ask():
            results["upload"] = await client.call_tool("upload_images", {})

        async with anyio.create_task_group() as tg:
            tg.start_soon(ask)
            await anyio.sleep(0.2)
            await client.call_tool("cancel_upload", {})
    result = results["upload"]
    assert not result.is_error and result.structured_content["status"] == "cancelled"


async def test_edit_with_sandbox_path_explains(upload_broker):
    async with Client(server.mcp) as client:
        result = await client.call_tool("edit_image", {"prompt": "x", "images": ["/mnt/user-data/uploads/cat.png"]})
    assert result.is_error and "upload_images" in result.content[0].text
