import base64
import io
import json

import pytest
from PIL import Image

from conftest import png_b64, png_bytes
from foundry_imagegen.images import (
    ValidationError,
    append_index,
    indexed_paths,
    save_images,
    slugify,
    validate_inputs,
    validate_params,
    validate_size,
)


@pytest.mark.parametrize("size", ["auto", "1024x1024", "1536x1024", "1024x1536", "2048x1152", "3840x2160"])
def test_valid_sizes(size):
    assert validate_size(size)[0] == size


@pytest.mark.parametrize(
    "size,msg",
    [
        ("1000x1000", "multiples of 16"),
        ("4096x2304", "exceed"),
        ("3072x768", "aspect ratio"),
        ("512x512", "total pixels"),
        ("big", "WIDTHxHEIGHT"),
    ],
)
def test_invalid_sizes(size, msg):
    with pytest.raises(ValidationError, match=msg):
        validate_size(size)


def test_experimental_size_warns():
    assert validate_size("3840x2160")[1]


def test_param_combinations():
    params, _ = validate_params(size="1024x1024", quality="HIGH", output_format="jpg", output_compression=80)
    assert params.as_request() == {
        "size": "1024x1024",
        "quality": "high",
        "n": 1,
        "output_format": "jpeg",
        "output_compression": 80,
    }
    with pytest.raises(ValidationError, match="transparent"):
        validate_params(background="transparent", output_format="jpeg")
    with pytest.raises(ValidationError, match="output_compression"):
        validate_params(output_compression=50)
    with pytest.raises(ValidationError, match="quality"):
        validate_params(quality="ultra")
    with pytest.raises(ValidationError, match="n must"):
        validate_params(n=11)


def test_background_only_sent_when_not_auto():
    params, _ = validate_params(background="transparent")
    assert params.as_request()["background"] == "transparent"
    assert "background" not in validate_params()[0].as_request()


def test_validate_inputs_and_mask(tmp_path):
    (tmp_path / "a.png").write_bytes(png_bytes(size=(64, 48)))
    (tmp_path / "mask.png").write_bytes(png_bytes(size=(64, 48), mode="RGBA"))
    (tmp_path / "opaque.png").write_bytes(png_bytes(size=(64, 48)))
    (tmp_path / "small.png").write_bytes(png_bytes(size=(32, 32), mode="RGBA"))
    inputs, mask = validate_inputs(["a.png"], "mask.png", [tmp_path])
    assert inputs[0].mime == "image/png" and mask is not None
    with pytest.raises(ValidationError, match="alpha"):
        validate_inputs(["a.png"], "opaque.png", [tmp_path])
    with pytest.raises(ValidationError, match="must match"):
        validate_inputs(["a.png"], "small.png", [tmp_path])
    with pytest.raises(ValidationError, match="not found"):
        validate_inputs(["missing.png"], None, [tmp_path])
    with pytest.raises(ValidationError, match="At most 16"):
        validate_inputs(["a.png"] * 17, None, [tmp_path])


def test_rejects_non_image(tmp_path):
    (tmp_path / "x.png").write_text("not an image")
    with pytest.raises(ValidationError, match="readable"):
        validate_inputs(["x.png"], None, [tmp_path])


def test_save_images_previews_and_index(tmp_path):
    big = io.BytesIO()
    Image.new("RGB", (2048, 1152), (10, 20, 30)).save(big, format="PNG")
    payloads = [base64.b64encode(big.getvalue()).decode(), png_b64(mode="RGBA")]
    saved = save_images(payloads, output_dir=tmp_path, output_format="png", name_hint="A Cozy Cabin!")
    assert [p.path.name.split("-", 2)[2] for p in saved] == ["a-cozy-cabin-1.png", "a-cozy-cabin-2.png"]
    assert saved[0].preview_mime == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(saved[0].preview_b64))) as preview:
        assert max(preview.size) == 1024
    assert saved[1].preview_mime == "image/png"  # alpha preserved
    again = save_images(payloads[:1], output_dir=tmp_path, output_format="png", name_hint="x")
    assert again[0].path.exists()

    append_index(tmp_path, [{"file": str(s.path)} for s in saved])
    assert indexed_paths([tmp_path]) == {s.path for s in saved}
    assert json.loads((tmp_path / "index.jsonl").read_text().splitlines()[0])["file"] == str(saved[0].path)


def test_unique_names(tmp_path):
    first = save_images([png_b64()], output_dir=tmp_path, output_format="png", name_hint="same")
    from datetime import datetime

    when = datetime.strptime(first[0].path.name[:15], "%Y%m%d-%H%M%S")
    second = save_images([png_b64()], output_dir=tmp_path, output_format="png", name_hint="same", when=when)
    assert first[0].path != second[0].path


def test_slugify():
    assert slugify("  Hello, World! ") == "hello-world"
    assert slugify("!!!") == "image"


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
    from foundry_imagegen.images import looks_like_sandbox_path

    assert looks_like_sandbox_path(raw, platform) is expected


def test_sandbox_path_error_explains(tmp_path):
    from foundry_imagegen.images import resolve_input_path

    with pytest.raises(ValidationError, match="Claude's sandbox.*path on their computer"):
        resolve_input_path("/mnt/user-data/uploads/cat.png", [tmp_path])
    with pytest.raises(ValidationError, match="Claude's sandbox"):
        resolve_input_path('"/mnt/user-data/uploads/cat.png"', [tmp_path])


@pytest.mark.parametrize(
    "wrapped",
    [
        '"{p}"', "'{p}'", "`{p}`", "\u201c{p}\u201d", "\u2018{p}\u2019", "\u00ab{p}\u00bb", "<{p}>",
        '  "{p}"\n', "`\"{p}\"`", "{p}",
    ],
)
def test_resolve_input_path_accepts_common_quoting(tmp_path, wrapped):
    from foundry_imagegen.images import resolve_input_path

    target = tmp_path / "my photo (1).png"
    target.write_bytes(png_bytes())
    assert resolve_input_path(wrapped.format(p=target), [tmp_path]) == target.resolve()


def test_resolve_input_path_prefers_literal_name(tmp_path):
    from foundry_imagegen.images import resolve_input_path

    plain = tmp_path / "pic.png"
    plain.write_bytes(png_bytes())
    quoted = tmp_path / "'pic.png'"  # a real (if odd) file whose name includes quotes
    quoted.write_bytes(png_bytes())
    assert resolve_input_path("'pic.png'", [tmp_path]) == quoted.resolve()
    assert resolve_input_path("pic.png", [tmp_path]) == plain.resolve()
