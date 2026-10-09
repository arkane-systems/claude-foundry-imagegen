"""MCP server: image tools, a connection check, and the gallery MCP App."""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from mcp.server.apps import Apps
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .client import FoundryError, FoundryImageClient, ImageResponse
from .config import ConfigError, Settings, config_file_path, load_settings
from .images import (
    ValidationError,
    append_index,
    indexed_paths,
    save_images,
    timestamp,
    validate_inputs,
    validate_params,
)
from .ratelimit import RateLimitExceeded

GALLERY_URI = "ui://foundry-imagegen/gallery.html"

# httpx logs every request at INFO; keep the MCP server log to what matters.
logging.getLogger("httpx").setLevel(logging.WARNING)

INSTRUCTIONS = """\
Generates and edits images with a GPT-image model deployed on Microsoft Foundry.
Before calling generate_image or edit_image, follow the `imagine` skill (if available) to turn the
user's idea into a structured prompt and pick parameters. The deployment allows only a few requests
per minute (often 2–5; check_config shows the limit): ask for variations with `n` in one call instead
of repeated calls; calls queue automatically when the limit is reached. Results are saved to disk; the tool result lists the
file paths and includes downscaled previews for you to review. Use check_config to diagnose setup."""

_state: dict[str, Any] = {}


def _settings() -> Settings:
    if "settings" not in _state:
        _state["settings"] = load_settings()
    return _state["settings"]


def _client() -> FoundryImageClient:
    if "client" not in _state:
        _state["client"] = FoundryImageClient(_settings())
    return _state["client"]


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _output_dir(override: str | None, settings: Settings) -> Path:
    if not override:
        return settings.output_dir
    path = Path(os.path.expandvars(override)).expanduser()
    if not path.is_absolute():
        path = (settings.project_dir or settings.output_dir) / path
    return path


def _known_output_dirs(settings: Settings) -> list[Path]:
    return list(dict.fromkeys([settings.output_dir, *_state.setdefault("extra_dirs", [])]))


async def _run(
    ctx: Context,
    *,
    operation: str,
    prompt: str,
    deployment: str | None,
    size: str,
    quality: str,
    n: int,
    output_format: str,
    output_compression: int | None,
    background: str,
    filename_hint: str | None,
    output_dir: str | None,
    images: list[str] | None = None,
    mask: str | None = None,
) -> CallToolResult:
    try:
        settings = _settings()
        dep = settings.resolve_deployment(deployment)
        params, warnings = validate_params(
            size=size,
            quality=quality,
            n=n,
            output_format=output_format,
            output_compression=output_compression,
            background=background,
        )
        out_dir = _output_dir(output_dir, settings)
        inputs, mask_input = ([], None)
        if operation == "edit":
            bases = [p for p in (settings.project_dir, settings.output_dir) if p is not None]
            inputs, mask_input = validate_inputs(images or [], mask, bases)
    except (ConfigError, ValidationError) as exc:
        return _error(str(exc))

    client = _client()
    started = time.time()
    deadline = started + settings.max_wait_seconds
    progress = {"step": 0}

    async def on_wait(seconds: float, reason: str) -> None:
        progress["step"] += 1
        await ctx.report_progress(progress["step"], None, f"Queued: rate limit ({reason}); about {seconds:.0f} s to go")

    try:
        await ctx.report_progress(0, None, f"Sending request to {dep}")
        if operation == "edit":
            response: ImageResponse = await client.edit(
                dep, prompt, params, inputs, mask_input, deadline=deadline, on_wait=on_wait
            )
        else:
            response = await client.generate(dep, prompt, params, deadline=deadline, on_wait=on_wait)
    except RateLimitExceeded as exc:
        return _error(f"{exc} Nothing was generated; try again then, or raise max_wait_seconds.")
    except FoundryError as exc:
        return _error(str(exc))

    elapsed = time.time() - started
    saved = save_images(
        response.images,
        output_dir=out_dir,
        output_format=params.output_format,
        name_hint=filename_hint or prompt,
    )
    if out_dir != settings.output_dir:
        _state.setdefault("extra_dirs", []).append(out_dir)

    base_record = {
        "time": timestamp(),
        "operation": operation,
        "prompt": prompt,
        "deployment": dep,
        "params": params.as_request(),
        "inputs": [str(i.path) for i in inputs],
        "mask": str(mask_input.path) if mask_input else None,
        "usage": response.usage,
        "duration_s": round(elapsed, 1),
    }
    append_index(
        out_dir,
        [
            {**base_record, "file": str(img.path), "revised_prompt": revised}
            for img, revised in zip(saved, response.revised_prompts, strict=False)
        ],
    )

    status = client.limiter(dep).status()
    lines = [f"{operation.capitalize()} complete with {dep} in {elapsed:.0f} s ({len(saved)} image(s)):"]
    for img in saved:
        lines.append(f"- {img.path} ({img.width}x{img.height}, {img.bytes / 1024:.0f} KB)")
    revised = [r for r in response.revised_prompts if r]
    if revised:
        lines.append(f"Revised prompt used by the service: {revised[0]}")
    if response.usage:
        lines.append(f"Usage: {response.usage}")
    lines.append(
        f"Quota: {status['requests_last_minute']}/{status['rpm_limit']} requests used in the last minute."
    )
    lines.extend(f"Note: {w}" for w in warnings)
    lines.append("Previews below are downscaled; the full-resolution files are at the paths above.")

    content: list[Any] = [TextContent(type="text", text="\n".join(lines))]
    content.extend(ImageContent(type="image", data=img.preview_b64, mime_type=img.preview_mime) for img in saved)
    structured = {
        "operation": operation,
        "prompt": prompt,
        "deployment": dep,
        "params": params.as_request(),
        "duration_s": round(elapsed, 1),
        "output_dir": str(out_dir),
        "images": [img.summary() for img in saved],
        "revised_prompt": revised[0] if revised else None,
    }
    return CallToolResult(content=content, structured_content=structured)


apps = Apps()

PromptArg = Annotated[str, Field(description="The complete, refined image prompt (see the imagine skill).")]
DeploymentArg = Annotated[
    str | None, Field(description="Deployment name; omit for the configured default. Must be a configured deployment.")
]
SizeArg = Annotated[
    str,
    Field(
        description="'auto' or WIDTHxHEIGHT. Edges multiples of 16, max 3840, ratio ≤ 3:1, "
        "655,360–8,294,400 total pixels. E.g. 1024x1024, 1536x1024, 1024x1536, 2048x1152."
    ),
]
QualityArg = Annotated[str, Field(description="auto, low, medium, high, xhigh, or max.")]
NArg = Annotated[int, Field(ge=1, le=10, description="Number of variations (1–10) in one request.")]
FormatArg = Annotated[str, Field(description="png (default), jpeg, or webp.")]
CompressionArg = Annotated[int | None, Field(description="0–100; jpeg/webp only.")]
BackgroundArg = Annotated[str, Field(description="auto, opaque, or transparent (transparent needs png).")]
HintArg = Annotated[str | None, Field(description="Short name used in the saved file names.")]
OutDirArg = Annotated[str | None, Field(description="Override the save directory for this call.")]


@apps.tool(
    resource_uri=GALLERY_URI,
    description=(
        "Generate images from a text prompt with the Foundry image model. Saves full-resolution files and "
        "returns their paths plus previews. Use the imagine skill first to refine the prompt."
    ),
    annotations=ToolAnnotations(title="Generate image", open_world_hint=True),
)
async def generate_image(
    prompt: PromptArg,
    ctx: Context,
    deployment: DeploymentArg = None,
    size: SizeArg = "auto",
    quality: QualityArg = "auto",
    n: NArg = 1,
    output_format: FormatArg = "png",
    output_compression: CompressionArg = None,
    background: BackgroundArg = "auto",
    filename_hint: HintArg = None,
    output_dir: OutDirArg = None,
) -> CallToolResult:
    return await _run(
        ctx,
        operation="generate",
        prompt=prompt,
        deployment=deployment,
        size=size,
        quality=quality,
        n=n,
        output_format=output_format,
        output_compression=output_compression,
        background=background,
        filename_hint=filename_hint,
        output_dir=output_dir,
    )


@apps.tool(
    resource_uri=GALLERY_URI,
    description=(
        "Edit an image or compose from reference images with the Foundry image model. images[0] is the image "
        "to edit; further images are references whose roles the prompt must state. Optional mask: PNG of the "
        "same size where fully transparent pixels mark the area to change."
    ),
    annotations=ToolAnnotations(title="Edit image", open_world_hint=True),
)
async def edit_image(
    prompt: PromptArg,
    images: Annotated[
        list[str],
        Field(description="1–16 image paths (PNG/JPEG/WebP, < 50 MB). Relative paths resolve against the project."),
    ],
    ctx: Context,
    mask: Annotated[str | None, Field(description="Optional PNG mask path (alpha 0 = area to edit).")] = None,
    deployment: DeploymentArg = None,
    size: SizeArg = "auto",
    quality: QualityArg = "auto",
    n: NArg = 1,
    output_format: FormatArg = "png",
    output_compression: CompressionArg = None,
    background: BackgroundArg = "auto",
    filename_hint: HintArg = None,
    output_dir: OutDirArg = None,
) -> CallToolResult:
    return await _run(
        ctx,
        operation="edit",
        prompt=prompt,
        deployment=deployment,
        size=size,
        quality=quality,
        n=n,
        output_format=output_format,
        output_compression=output_compression,
        background=background,
        filename_hint=filename_hint,
        output_dir=output_dir,
        images=images,
        mask=mask,
    )


def _check_path(path: str) -> Path:
    settings = _settings()
    resolved = Path(path).expanduser().resolve()
    if resolved not in indexed_paths(_known_output_dirs(settings)) or not resolved.is_file():
        raise ValidationError("Only images generated by this server can be opened.")
    return resolved


@apps.tool(resource_uri=GALLERY_URI, visibility=["app"], description="Return a generated image file for download.")
def fetch_image(path: str) -> CallToolResult:
    try:
        resolved = _check_path(path)
    except (ConfigError, ValidationError) as exc:
        return _error(str(exc))
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(resolved.suffix, "image/png")
    data = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return CallToolResult(
        content=[TextContent(type="text", text=f"{resolved.name} ({mime})")],
        structured_content={"file_name": resolved.name, "mime": mime, "data": data},
    )


@apps.tool(resource_uri=GALLERY_URI, visibility=["app"], description="Show a generated image in the file manager.")
def reveal_image(path: str) -> CallToolResult:
    try:
        resolved = _check_path(path)
    except (ConfigError, ValidationError) as exc:
        return _error(str(exc))
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(resolved)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(resolved)])
        else:
            subprocess.Popen(["xdg-open", str(resolved.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return _error(f"Could not open the file manager: {exc}")
    return CallToolResult(content=[TextContent(type="text", text=f"Opened {resolved.parent}")])


apps.add_html_resource(
    GALLERY_URI,
    resources.files("foundry_imagegen").joinpath("ui/gallery.html").read_text(encoding="utf-8"),
    title="Foundry image gallery",
    prefers_border=True,
)

mcp = MCPServer(
    "foundry-imagegen",
    title="Foundry Image Generation",
    instructions=INSTRUCTIONS,
    version=__version__,
    extensions=[apps],
)


@mcp.tool(
    description=(
        "Check the image-generation setup: resolved settings (key masked) and where each came from, the API "
        "route, output directory, rate-limit state, and (probe=true) a test request per deployment."
    ),
    annotations=ToolAnnotations(title="Check image generation config", read_only_hint=True, open_world_hint=True),
)
async def check_config(
    probe: Annotated[bool, Field(description="Send a deliberately invalid, unbilled request per deployment.")] = True,
) -> str:
    try:
        settings = _settings()
    except ConfigError as exc:
        return f"Configuration problem: {exc}\nConfig file location: {config_file_path()}"
    client = _client()
    lines = [
        f"foundry-imagegen {__version__}",
        f"Endpoint: {settings.endpoint}",
        f"API key: {settings.masked_key()}",
        f"Deployments: {', '.join(settings.deployments)} (default: {settings.deployment})",
        f"Output directory: {settings.output_dir}",
        f"Project directory: {settings.project_dir or '(none)'}",
        f"Max wait for a rate-limit slot: {settings.max_wait_seconds} s",
        "Setting sources: " + ", ".join(f"{k}={v}" for k, v in sorted(settings.sources.items())),
        f"Config file (fallback): {config_file_path()}",
    ]
    for dep in settings.deployments:
        if probe:
            result = await client.probe(dep, deadline=time.time() + 30)
            lines.append(f"[{dep}] {result.status}: {result.detail}")
            if result.route:
                lines.append(f"[{dep}] API route: {result.route}")
            if result.rate_headers:
                lines.append(f"[{dep}] rate-limit headers: {result.rate_headers}")
        lines.append(f"[{dep}] limiter: {client.limiter(dep).status()}")
    return "\n".join(lines)


def run() -> None:
    mcp.run("stdio")
