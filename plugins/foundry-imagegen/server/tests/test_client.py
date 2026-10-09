import httpx
import pytest
import respx

from conftest import png_b64, png_bytes
from foundry_imagegen.client import (
    AuthError,
    ContentFilterError,
    DeploymentNotFoundError,
    FoundryImageClient,
    parse_duration,
)
from foundry_imagegen.images import inspect_input_image, validate_params
from foundry_imagegen.ratelimit import RateLimiter, RateLimitExceeded

BASE = "https://res.services.ai.azure.com"
V1_GEN = f"{BASE}/openai/v1/images/generations"
V1_EDIT = f"{BASE}/openai/v1/images/edits"
LEGACY_GEN = f"{BASE}/openai/deployments/gpt-image-2.5-flare/images/generations"


class Clock:
    def __init__(self):
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(settings, clock):
    limiter = lambda dep: RateLimiter(settings.state_dir, dep, settings.rpm_limit, sleep=clock.sleep)  # noqa: E731
    return FoundryImageClient(settings, limiter_factory=limiter, sleep=clock.sleep)


def ok_body(n=1):
    return {"created": 1, "data": [{"b64_json": png_b64()} for _ in range(n)], "usage": {"total_tokens": 10}}


PARAMS = validate_params(size="1024x1024", quality="low")[0]


@pytest.mark.parametrize(
    "raw,expected", [("20", 20), ("1.5", 1.5), ("500ms", 0.5), ("6s", 6), ("1m30s", 90), ("junk", None)]
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


@respx.mock
async def test_generate_v1(client):
    route = respx.post(V1_GEN).mock(return_value=httpx.Response(200, json=ok_body(2)))
    result = await client.generate("gpt-image-2.5-flare", "a cat", PARAMS, deadline=9e12)
    assert len(result.images) == 2 and result.route == "v1" and result.usage == {"total_tokens": 10}
    sent = route.calls.last.request
    assert sent.headers["api-key"] == "secret-key-123456"
    body = __import__("json").loads(sent.content)
    assert body == {"prompt": "a cat", "model": "gpt-image-2.5-flare", "size": "1024x1024", "quality": "low",
                    "n": 1, "output_format": "png"}


@respx.mock
async def test_falls_back_to_legacy_route(client):
    respx.post(V1_GEN).mock(return_value=httpx.Response(404, json={"error": {"code": "404", "message": "Resource not found"}}))
    legacy = respx.post(LEGACY_GEN).mock(return_value=httpx.Response(200, json=ok_body()))
    result = await client.generate("gpt-image-2.5-flare", "a cat", PARAMS, deadline=9e12)
    assert result.route == "legacy"
    assert legacy.calls.last.request.url.params["api-version"] == "2025-04-01-preview"
    assert "model" not in __import__("json").loads(legacy.calls.last.request.content)
    await client.generate("gpt-image-2.5-flare", "again", PARAMS, deadline=9e12)
    assert respx.calls.call_count == 3  # the v1 route is not retried once known to be missing


@respx.mock
async def test_deployment_not_found(client):
    respx.post(V1_GEN).mock(
        return_value=httpx.Response(404, json={"error": {"code": "DeploymentNotFound", "message": "nope"}})
    )
    with pytest.raises(DeploymentNotFoundError, match="gpt-image-2.5-sunburst"):
        await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=9e12)


@respx.mock
async def test_429_sets_cooldown_and_retries(client, clock):
    respx.post(V1_GEN).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after-ms": "2000"}, json={"error": {"code": "429"}}),
            httpx.Response(200, json=ok_body()),
        ]
    )
    result = await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=9e12)
    assert result.images
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(2, abs=0.2)


@respx.mock
async def test_429_beyond_deadline_raises(client):
    import time

    respx.post(V1_GEN).mock(return_value=httpx.Response(429, headers={"retry-after": "120"}))
    with pytest.raises(RateLimitExceeded):
        await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=time.time() + 10)


@respx.mock
async def test_5xx_retries_then_succeeds(client, clock):
    respx.post(V1_GEN).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=ok_body())])
    assert (await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=9e12)).images
    assert clock.sleeps == [2.0]


@respx.mock
@pytest.mark.parametrize(
    "status,body",
    [
        (400, {"error": {"code": "contentFilter", "message": "Your task failed as a result of our safety system."}}),
        (200, {"created": 1, "error": {"code": "contentFilter", "message": "Generated image was filtered as a result of our safety system."}}),
        (400, {"error": {"code": "content_policy_violation", "message": "blocked", "innererror": {"code": "ResponsibleAIPolicyViolation"}}}),
    ],
)
async def test_content_filter(client, status, body):
    respx.post(V1_GEN).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(ContentFilterError, match="safety system blocked"):
        await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=9e12)


@respx.mock
async def test_auth_error(client):
    respx.post(V1_GEN).mock(return_value=httpx.Response(401, json={"error": {"code": "401", "message": "bad key"}}))
    with pytest.raises(AuthError, match="API key"):
        await client.generate("gpt-image-2.5-flare", "x", PARAMS, deadline=9e12)


@respx.mock
async def test_edit_multipart(client, tmp_path):
    (tmp_path / "a.png").write_bytes(png_bytes())
    (tmp_path / "b.png").write_bytes(png_bytes())
    (tmp_path / "m.png").write_bytes(png_bytes(mode="RGBA"))
    route = respx.post(V1_EDIT).mock(return_value=httpx.Response(200, json=ok_body()))
    images = [inspect_input_image(tmp_path / "a.png"), inspect_input_image(tmp_path / "b.png")]
    await client.edit("gpt-image-2.5-flare", "combine", PARAMS, images, inspect_input_image(tmp_path / "m.png"), deadline=9e12)
    content = route.calls.last.request.content
    assert content.count(b'name="image[]"') == 2
    assert b'name="mask"' in content
    assert b'name="model"\r\n\r\ngpt-image-2.5-flare' in content
    assert b'name="n"\r\n\r\n1' in content


@respx.mock
async def test_probe(client):
    respx.post(V1_GEN).mock(
        return_value=httpx.Response(
            400, headers={"x-ratelimit-remaining-requests": "4"}, json={"error": {"code": "invalid_value", "message": "size"}}
        )
    )
    result = await client.probe("gpt-image-2.5-flare", deadline=9e12)
    assert result.status == "ok" and result.route == "v1"
    assert result.rate_headers == {"x-ratelimit-remaining-requests": "4"}
