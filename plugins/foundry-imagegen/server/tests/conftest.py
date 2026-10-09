import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from foundry_imagegen.config import Settings


def png_bytes(size=(64, 48), mode="RGB", color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    fill = color if mode == "RGB" else (*color, 255)
    Image.new(mode, size, fill).save(buf, format="PNG")
    return buf.getvalue()


def png_b64(**kwargs) -> str:
    return base64.b64encode(png_bytes(**kwargs)).decode()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        endpoint="https://res.services.ai.azure.com",
        api_key="secret-key-123456",
        deployment="gpt-image-2.5-flare",
        extra_deployments=("gpt-image-2.5-sunburst",),
        rpm_limit=5,
        max_wait_seconds=30,
        output_dir=tmp_path / "out",
        api_version="2025-04-01-preview",
        project_dir=tmp_path,
        state_dir=tmp_path / "state",
    )
