"""Parameter validation, input-image checks, and saving/previewing results."""

from __future__ import annotations

import base64
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .config import unquote_path

QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")
FORMATS = ("png", "jpeg", "webp")
BACKGROUNDS = ("auto", "opaque", "transparent")
MIME_BY_FORMAT = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
EXT_BY_FORMAT = {"png": "png", "jpeg": "jpg", "webp": "webp"}

MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
EXPERIMENTAL_PIXELS = 2560 * 1440
MAX_REFERENCE_IMAGES = 16
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
INPUT_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

INDEX_FILE = "index.jsonl"


class ValidationError(Exception):
    """A request parameter or input file is invalid; nothing was sent to the service."""


@dataclass(frozen=True)
class ImageParams:
    size: str
    quality: str
    n: int
    output_format: str
    output_compression: int | None
    background: str

    def as_request(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "size": self.size,
            "quality": self.quality,
            "n": self.n,
            "output_format": self.output_format,
        }
        if self.background != "auto":
            body["background"] = self.background
        if self.output_compression is not None:
            body["output_compression"] = self.output_compression
        return body

    @property
    def megapixels(self) -> float | None:
        if self.size == "auto":
            return None
        w, h = parse_size(self.size)
        return w * h / 1_000_000


def parse_size(size: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", size)
    if not match:
        raise ValidationError(f"size must be 'auto' or WIDTHxHEIGHT (e.g. 1536x1024), got {size!r}.")
    return int(match.group(1)), int(match.group(2))


def validate_size(size: str) -> tuple[str, list[str]]:
    """Return the canonical size string and any warnings."""
    if size.strip().lower() == "auto":
        return "auto", []
    w, h = parse_size(size)
    problems = []
    if w % 16 or h % 16:
        problems.append("both edges must be multiples of 16")
    if max(w, h) > MAX_EDGE:
        problems.append(f"neither edge may exceed {MAX_EDGE}px")
    if max(w, h) > 3 * min(w, h):
        problems.append("the aspect ratio must be between 1:3 and 3:1")
    if not MIN_PIXELS <= w * h <= MAX_PIXELS:
        problems.append(f"total pixels must be between {MIN_PIXELS:,} and {MAX_PIXELS:,} (got {w * h:,})")
    if problems:
        raise ValidationError(f"Invalid size {w}x{h}: " + "; ".join(problems) + ".")
    warnings = []
    if w * h > EXPERIMENTAL_PIXELS:
        warnings.append(f"{w}x{h} is above 2560x1440; the service treats this as experimental.")
    return f"{w}x{h}", warnings


def validate_params(
    *,
    size: str = "auto",
    quality: str = "auto",
    n: int = 1,
    output_format: str = "png",
    output_compression: int | None = None,
    background: str = "auto",
) -> tuple[ImageParams, list[str]]:
    canonical_size, warnings = validate_size(size)
    quality = quality.strip().lower()
    if quality not in QUALITIES:
        raise ValidationError(f"quality must be one of {', '.join(QUALITIES)}; got {quality!r}.")
    if not 1 <= n <= 10:
        raise ValidationError(f"n must be between 1 and 10; got {n}.")
    output_format = output_format.strip().lower()
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in FORMATS:
        raise ValidationError(f"output_format must be one of {', '.join(FORMATS)}; got {output_format!r}.")
    background = background.strip().lower()
    if background not in BACKGROUNDS:
        raise ValidationError(f"background must be one of {', '.join(BACKGROUNDS)}; got {background!r}.")
    if background == "transparent" and output_format == "jpeg":
        raise ValidationError("A transparent background needs output_format 'png' (JPEG has no alpha channel).")
    if output_compression is not None:
        if output_format == "png":
            raise ValidationError("output_compression applies only to jpeg or webp output; omit it for png.")
        if not 0 <= output_compression <= 100:
            raise ValidationError(f"output_compression must be 0–100; got {output_compression}.")
    if output_format == "webp":
        warnings.append("WebP output is not documented for Azure deployments; use png or jpeg if the request fails.")
    return (
        ImageParams(canonical_size, quality, n, output_format, output_compression, background),
        warnings,
    )


@dataclass(frozen=True)
class InputImage:
    path: Path
    mime: str
    width: int
    height: int


# Where Claude's own sandbox keeps chat attachments and scratch files. The image server runs on the
# user's computer, so these paths never exist for it.
SANDBOX_PREFIXES = ("/mnt/user-data/", "/mnt/data/", "/mnt/outputs/", "/home/claude/")

SANDBOX_HINT = (
    "{raw!r} is in Claude's sandbox (where files attached to a chat are stored), so the image server on "
    "the user's computer can't read it. Ask the user for the image's path on their computer instead. On "
    "Windows: select the file in Explorer and press Ctrl+Shift+C (or right-click > Copy as path). On "
    "macOS: select it in Finder and press Option-Command-C. Then paste the path into the chat; quotes "
    "around it are fine."
)


def looks_like_sandbox_path(raw: str, platform: str = sys.platform) -> bool:
    normalized = raw.strip().replace("\\", "/")
    if normalized.startswith(SANDBOX_PREFIXES):
        return True
    # A POSIX absolute path can't be a local file on Windows.
    return platform == "win32" and normalized.startswith("/") and not normalized.startswith("//")


def resolve_input_path(raw: str, bases: list[Path]) -> Path:
    # Try the path exactly as given first, then with copy/paste wrapping removed (quotes, file:// URLs,
    # shell escapes), so a real file whose name contains such characters still wins.
    candidates: list[Path] = []
    for form in dict.fromkeys([raw, unquote_path(raw)]):
        path = Path(form).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates += [base / path for base in bases] + [Path.cwd() / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if looks_like_sandbox_path(unquote_path(raw)):
        raise ValidationError(SANDBOX_HINT.format(raw=raw))
    tried = ", ".join(str(c) for c in dict.fromkeys(candidates))
    raise ValidationError(f"Image file not found: {raw!r} (looked in: {tried}).")


def inspect_input_image(path: Path) -> InputImage:
    size = path.stat().st_size
    if size > MAX_REFERENCE_BYTES:
        raise ValidationError(f"{path.name} is {size / 1_048_576:.1f} MB; input images must be under 50 MB.")
    try:
        with Image.open(path) as img:
            fmt = img.format
            width, height = img.size
    except Exception as exc:  # Pillow raises several types for unreadable files
        raise ValidationError(f"{path.name} is not a readable image: {exc}") from exc
    if fmt not in INPUT_FORMATS:
        raise ValidationError(f"{path.name} is {fmt}; input images must be PNG, JPEG, or WebP.")
    return InputImage(path, INPUT_FORMATS[fmt], width, height)


def validate_inputs(images: list[str], mask: str | None, bases: list[Path]) -> tuple[list[InputImage], InputImage | None]:
    if not images:
        raise ValidationError("edit_image needs at least one input image.")
    if len(images) > MAX_REFERENCE_IMAGES:
        raise ValidationError(f"At most {MAX_REFERENCE_IMAGES} input images are allowed; got {len(images)}.")
    inputs = [inspect_input_image(resolve_input_path(p, bases)) for p in images]
    mask_input = None
    if mask:
        mask_input = inspect_input_image(resolve_input_path(mask, bases))
        if mask_input.mime != "image/png":
            raise ValidationError("The mask must be a PNG with an alpha channel.")
        with Image.open(mask_input.path) as img:
            has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        if not has_alpha:
            raise ValidationError(
                "The mask has no alpha channel. Make the area to edit fully transparent (alpha 0) "
                "and leave everything else opaque, then save as PNG."
            )
        target = inputs[0]
        if (mask_input.width, mask_input.height) != (target.width, target.height):
            raise ValidationError(
                f"The mask is {mask_input.width}x{mask_input.height} but the first image is "
                f"{target.width}x{target.height}; they must match."
            )
    return inputs, mask_input


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "image"


@dataclass(frozen=True)
class SavedImage:
    path: Path
    mime: str
    width: int
    height: int
    bytes: int
    preview_b64: str
    preview_mime: str

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "file_name": self.path.name,
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
        }


def _unique_path(directory: Path, stem: str, ext: str) -> Path:
    candidate = directory / f"{stem}.{ext}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}.{ext}"
        counter += 1
    return candidate


def make_preview(data: bytes, max_edge: int) -> tuple[str, str]:
    """Downscale for the model's inline view; keeps alpha as PNG, otherwise JPEG."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        preview = img.copy()
    preview.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if has_alpha:
        preview.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        preview.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    return base64.b64encode(buf.getvalue()).decode("ascii"), mime


def save_images(
    b64_images: list[str],
    *,
    output_dir: Path,
    output_format: str,
    name_hint: str,
    when: datetime | None = None,
) -> list[SavedImage]:
    output_dir.mkdir(parents=True, exist_ok=True)
    when = when or datetime.now()
    stem = f"{when:%Y%m%d-%H%M%S}-{slugify(name_hint)}"
    preview_edge = 1024 if len(b64_images) <= 4 else 768
    saved = []
    for i, b64 in enumerate(b64_images, start=1):
        data = base64.b64decode(b64)
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            actual = (img.format or output_format).lower()
        fmt = "jpeg" if actual in ("jpeg", "jpg", "mpo") else actual if actual in FORMATS else output_format
        suffix = f"-{i}" if len(b64_images) > 1 else ""
        path = _unique_path(output_dir, stem + suffix, EXT_BY_FORMAT[fmt])
        path.write_bytes(data)
        preview_b64, preview_mime = make_preview(data, preview_edge)
        saved.append(SavedImage(path.resolve(), MIME_BY_FORMAT[fmt], width, height, len(data), preview_b64, preview_mime))
    return saved


def append_index(output_dir: Path, records: list[dict[str, Any]]) -> None:
    with (output_dir / INDEX_FILE).open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def indexed_paths(output_dirs: list[Path]) -> set[Path]:
    """Every image file recorded in the index of the given output directories."""
    known: set[Path] = set()
    for directory in output_dirs:
        index = directory / INDEX_FILE
        if not index.is_file():
            continue
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            file = record.get("file")
            if isinstance(file, str):
                known.add(Path(file).resolve())
    return known


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

