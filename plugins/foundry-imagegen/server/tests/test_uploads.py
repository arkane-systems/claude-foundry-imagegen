import base64
import hashlib
import os

import anyio
import pytest

from conftest import png_bytes
from foundry_imagegen.images import ValidationError, looks_like_sandbox_path, resolve_input_path
from foundry_imagegen.uploads import REQUEST_TTL, STAGE_TTL, UploadBroker, UploadCancelled, UploadTimedOut


def item(data: bytes, name="photo.png", **extra):
    return {"name": name, "data": base64.b64encode(data).decode(), **extra}


def test_stage_validates_and_records_provenance(tmp_path):
    b = UploadBroker(tmp_path / "stage")
    data = png_bytes(size=(40, 30))
    [img] = b.stage([item(data, name="../../evil name?.png")])
    assert img.staged and img.path.parent.parent == (tmp_path / "stage").resolve()
    assert img.path.name == "evil name_.png" and (img.width, img.height) == (40, 30)
    assert b.provenance(img.path) == {"uploaded": "evil name_.png", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with pytest.raises(ValidationError, match="not a readable image"):
        b.stage([item(b"nope", name="x.png")])
    with pytest.raises(ValidationError, match="corrupted"):
        b.stage([{"name": "x.png", "data": "!!!"}])


def test_original_path_used_in_place_when_identical(tmp_path):
    b = UploadBroker(tmp_path / "stage")
    original = tmp_path / "mine.png"
    original.write_bytes(png_bytes())
    [img] = b.stage([item(original.read_bytes(), name="mine.png", original_path=str(original))])
    assert not img.staged and img.path == original.resolve()
    assert b.provenance(original)["original_path"] == str(original.resolve())
    assert not (tmp_path / "stage").exists() or not any((tmp_path / "stage").iterdir())
    # A path that doesn't match the bytes is recorded but not trusted.
    [other] = b.stage([item(png_bytes(color=(1, 2, 3)), original_path=str(original))])
    assert other.staged and other.original_path == str(original)


def test_session_cleanup_and_ttl_sweep(tmp_path):
    root = tmp_path / "stage"
    b = UploadBroker(root)
    [img] = b.stage([item(png_bytes())])
    b.cleanup_session()
    assert not img.path.exists()
    stale = root / "old"
    stale.mkdir(parents=True)
    old = stale.stat().st_mtime - STAGE_TTL - 10
    os.utime(stale, (old, old))
    fresh = root / "fresh"
    fresh.mkdir()
    UploadBroker(root)
    assert not stale.exists() and fresh.exists()


async def test_request_deliver_collect_cancel_timeout(tmp_path):
    clock = {"t": 1000.0}
    b = UploadBroker(tmp_path / "stage", clock=lambda: clock["t"])
    images = b.stage([item(png_bytes()), item(png_bytes(), name="b.png")])

    request = b.open_request(max_files=1, purpose="photo")
    assert b.get(None) is request and b.get(request.id) is request
    assert b.deliver(request.id, images) is request
    assert await b.collect(request, 5) == images[:1]
    assert b.get(request.id) is None  # collected requests close

    request = b.open_request(1)
    assert b.cancel(None)  # no id → newest open request
    with pytest.raises(UploadCancelled):
        await b.collect(request, 5)
    assert b.deliver(request.id, images) is None

    request = b.open_request(1)
    with pytest.raises(UploadTimedOut):
        await b.collect(request, 0.05, tick=0.01)
    # A timed-out collect leaves the request open; a later delivery is still collected.
    assert b.deliver(request.id, images) is request
    assert await b.collect(request, 5) == images[:1]


def test_requests_expire(tmp_path):
    clock = {"t": 1000.0}
    b = UploadBroker(tmp_path / "stage", clock=lambda: clock["t"])
    request = b.open_request(1)
    clock["t"] += REQUEST_TTL + 1
    assert b.get(request.id) is None and b.get(None) is None


@pytest.mark.parametrize(
    "raw,platform,expected",
    [
        ("/mnt/user-data/uploads/cat.png", "linux", True),
        ("/home/claude/x.png", "darwin", True),
        ("/home/me/x.png", "linux", False),
        ("/home/me/x.png", "win32", True),
        ("\\\\server\\share\\x.png", "win32", False),
        ("C:\\Users\\me\\x.png", "win32", False),
    ],
)
def test_sandbox_detection(raw, platform, expected):
    assert looks_like_sandbox_path(raw, platform) is expected


def test_sandbox_path_error_explains(tmp_path):
    with pytest.raises(ValidationError, match="Claude's sandbox.*upload_images"):
        resolve_input_path("/mnt/user-data/uploads/cat.png", [tmp_path])
