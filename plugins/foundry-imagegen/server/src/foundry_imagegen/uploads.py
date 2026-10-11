"""Hand images from the user to the server without passing their bytes through the model.

In Claude Desktop, files attached to a chat land in Claude's sandbox, which the server (running on the
user's computer) can't read. Instead:

1. `upload_images` opens an upload request and returns at once; the gallery widget renders an upload
   panel from that result. (Desktop doesn't deliver a widget's input while its tool call is still
   running, so the panel can't come from a call that blocks.)
2. The user drops, picks, or pastes images; the widget sends them straight to the server
   (`stage_upload`), which stages them in a temporary cache and attaches them to the request.
3. `collect_uploads` waits (bounded, below host tool timeouts) for the request to be fulfilled or
   cancelled, and returns the staged paths.

Staged copies are temporary: removed when the server exits and, for anything left behind by a crash,
after STAGE_TTL. The index records each upload's original file name, size, and SHA-256 instead of the
staging path, and the original path when the host reveals one the server can read (then no copy is
made at all).
"""

from __future__ import annotations

import atexit
import base64
import binascii
import hashlib
import io
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import platformdirs
from PIL import Image

from .config import APP_NAME
from .images import INPUT_FORMATS, MAX_REFERENCE_BYTES, MAX_REFERENCE_IMAGES, ValidationError

STAGE_TTL = 24 * 3600
# How long an unfulfilled upload request stays open for the widget and collect_uploads.
REQUEST_TTL = 30 * 60


def default_stage_root() -> Path:
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)) / "uploads"


@dataclass(frozen=True)
class UploadedImage:
    path: Path
    name: str
    bytes: int
    sha256: str
    width: int
    height: int
    original_path: str | None = None
    staged: bool = True

    def provenance(self) -> dict[str, Any]:
        record: dict[str, Any] = {"uploaded": self.name, "bytes": self.bytes, "sha256": self.sha256}
        if self.original_path:
            record["original_path"] = self.original_path
        return record

    def summary(self) -> dict[str, Any]:
        return {"path": str(self.path), "name": self.name, "width": self.width, "height": self.height, "bytes": self.bytes}


@dataclass
class UploadRequest:
    id: str
    max_files: int
    purpose: str | None
    created: float
    done: anyio.Event = field(default_factory=anyio.Event)
    images: list[UploadedImage] | None = None
    cancelled: bool = False


class UploadCancelled(Exception):
    pass


class UploadTimedOut(Exception):
    pass


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip(" .") or "image"
    return stem[:80]


class UploadBroker:
    def __init__(self, stage_root: Path | None = None, *, clock=time.time):
        self.stage_root = stage_root or default_stage_root()
        self._clock = clock
        self._requests: dict[str, UploadRequest] = {}
        self._by_path: dict[Path, UploadedImage] = {}
        self._session_dirs: list[Path] = []
        self.sweep()

    # ---- staging -------------------------------------------------------------------------------

    def sweep(self) -> None:
        """Delete staging directories older than STAGE_TTL (left behind by crashed servers)."""
        if not self.stage_root.is_dir():
            return
        horizon = self._clock() - STAGE_TTL
        for child in self.stage_root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < horizon:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue

    def cleanup_session(self) -> None:
        for directory in self._session_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        self._session_dirs.clear()

    def stage(self, files: list[dict[str, Any]]) -> list[UploadedImage]:
        """Validate and stage files sent by the widget: [{name, data (base64), original_path?}]."""
        if not files:
            raise ValidationError("No files were received.")
        if len(files) > MAX_REFERENCE_IMAGES:
            raise ValidationError(f"At most {MAX_REFERENCE_IMAGES} images can be uploaded at once.")
        directory: Path | None = None
        result: list[UploadedImage] = []
        for item in files:
            name = _safe_name(str(item.get("name") or "pasted-image"))
            try:
                data = base64.b64decode(str(item.get("data") or ""), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValidationError(f"{name}: the file data was corrupted in transfer.") from exc
            if not data:
                raise ValidationError(f"{name} is empty.")
            if len(data) > MAX_REFERENCE_BYTES:
                raise ValidationError(f"{name} is {len(data) / 1_048_576:.1f} MB; images must be under 50 MB.")
            try:
                with Image.open(io.BytesIO(data)) as img:
                    fmt, (width, height) = img.format, img.size
            except Exception as exc:
                raise ValidationError(f"{name} is not a readable image.") from exc
            if fmt not in INPUT_FORMATS:
                raise ValidationError(f"{name} is {fmt}; images must be PNG, JPEG, or WebP.")
            digest = hashlib.sha256(data).hexdigest()

            original = self._usable_original(item.get("original_path"), digest, len(data))
            if original is not None:
                image = UploadedImage(original, name, len(data), digest, width, height, str(original), staged=False)
            else:
                if directory is None:
                    directory = self.stage_root / f"{int(self._clock())}-{uuid.uuid4().hex[:8]}"
                    directory.mkdir(parents=True, exist_ok=True)
                    self._session_dirs.append(directory)
                ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
                target = directory / (Path(name).stem + ext)
                counter = 2
                while target.exists():
                    target = directory / f"{Path(name).stem}-{counter}{ext}"
                    counter += 1
                target.write_bytes(data)
                path = target.resolve()
                image = UploadedImage(path, name, len(data), digest, width, height, item.get("original_path") or None)
            self._by_path[image.path.resolve()] = image
            result.append(image)
        return result

    @staticmethod
    def _usable_original(raw: Any, digest: str, size: int) -> Path | None:
        """Use the user's own file in place when the host revealed its path and it is the same file."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = Path(raw)
        try:
            if not path.is_file() or path.stat().st_size != size:
                return None
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return None
        except OSError:
            return None
        return path.resolve()

    def provenance(self, path: Path) -> dict[str, Any] | None:
        image = self._by_path.get(path.resolve())
        return image.provenance() if image else None

    # ---- requests ------------------------------------------------------------------------------

    def _prune(self) -> None:
        horizon = self._clock() - REQUEST_TTL
        for request_id in [r.id for r in self._requests.values() if r.created < horizon]:
            del self._requests[request_id]

    def open_request(self, max_files: int, purpose: str | None = None) -> UploadRequest:
        self._prune()
        request = UploadRequest(uuid.uuid4().hex[:12], max_files, purpose, self._clock())
        self._requests[request.id] = request
        return request

    def get(self, request_id: str | None) -> UploadRequest | None:
        """The named request, or the newest open one when no id is given."""
        self._prune()
        if request_id:
            return self._requests.get(request_id)
        return next(reversed(self._requests.values()), None)

    def deliver(self, request_id: str | None, images: list[UploadedImage]) -> UploadRequest | None:
        request = self.get(request_id)
        if request is None or request.cancelled:
            return None
        request.images = images[: request.max_files]
        request.done.set()
        return request

    def cancel(self, request_id: str | None) -> bool:
        request = self.get(request_id)
        if request is None:
            return False
        request.cancelled = True
        request.done.set()
        return True

    async def collect(self, request: UploadRequest, timeout: float, on_tick=None, tick: float = 10.0) -> list[UploadedImage]:
        """Wait for the request to be fulfilled. On timeout the request stays open for another try."""
        deadline = anyio.current_time() + timeout
        while not request.done.is_set():
            remaining = deadline - anyio.current_time()
            if remaining <= 0:
                raise UploadTimedOut()
            with anyio.move_on_after(min(tick, remaining)):
                await request.done.wait()
            if not request.done.is_set() and on_tick is not None:
                await on_tick(max(0.0, deadline - anyio.current_time()))
        self._requests.pop(request.id, None)
        if request.cancelled:
            raise UploadCancelled()
        return request.images or []


_broker: UploadBroker | None = None


def broker() -> UploadBroker:
    global _broker
    if _broker is None:
        _broker = UploadBroker()
        atexit.register(_broker.cleanup_session)
    return _broker
