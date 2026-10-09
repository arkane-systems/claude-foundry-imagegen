"""HTTP client for the Foundry / Azure OpenAI images API."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import anyio
import httpx

from . import __version__
from .config import Settings
from .images import ImageParams, InputImage
from .ratelimit import RateLimiter, RateLimitExceeded, WaitCallback

Route = Literal["v1", "legacy"]
Kind = Literal["generations", "edits"]

DEFAULT_COOLDOWN = 60.0
MAX_TRANSIENT_RETRIES = 2
TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)


class FoundryError(Exception):
    """An error from the images service, with a message written for the user."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class ContentFilterError(FoundryError):
    pass


class AuthError(FoundryError):
    pass


class DeploymentNotFoundError(FoundryError):
    pass


@dataclass
class ImageResponse:
    images: list[str]
    revised_prompts: list[str | None]
    usage: dict[str, Any] | None
    route: Route
    rate_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ProbeResult:
    deployment: str
    status: str
    detail: str
    route: Route | None
    rate_headers: dict[str, str]


def parse_duration(value: str) -> float | None:
    """Parse '20', '1.5', '500ms', '6s', '1m30s', or an HTTP date into seconds."""
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if parts and "".join(n + u for n, u in parts) == value.replace(" ", ""):
        factors = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
        return sum(float(n) * factors[u] for n, u in parts)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def retry_after_seconds(headers: httpx.Headers) -> float:
    if ms := headers.get("retry-after-ms"):
        seconds = parse_duration(ms)
        if seconds is not None:
            return seconds / 1000
    for name in ("retry-after", "x-ratelimit-reset-requests"):
        if raw := headers.get(name):
            seconds = parse_duration(raw)
            if seconds is not None:
                return seconds
    return DEFAULT_COOLDOWN


def rate_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.startswith("x-ratelimit") or k in ("retry-after", "retry-after-ms")}


def _error_info(response: httpx.Response) -> tuple[str | None, str, str | None]:
    """Return (code, message, inner code) from an error response body."""
    try:
        body = response.json()
    except ValueError:
        return None, response.text[:500] or response.reason_phrase, None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, str(body)[:500], None
    inner = error.get("innererror") or error.get("inner_error") or {}
    inner_code = inner.get("code") if isinstance(inner, dict) else None
    return error.get("code"), str(error.get("message") or ""), inner_code


_FILTER_CODES = {"contentfilter", "content_filter", "content_policy_violation", "responsibleaipolicyviolation"}


def _is_content_filter(code: str | None, inner: str | None, message: str) -> bool:
    codes = {c.lower() for c in (code, inner) if c}
    return bool(codes & _FILTER_CODES) or "safety system" in message.lower()


def _is_deployment_missing(code: str | None, message: str) -> bool:
    lowered = message.lower()
    return code == "DeploymentNotFound" or ("deployment" in lowered and "does not exist" in lowered)


class FoundryImageClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        limiter_factory: Callable[[str], RateLimiter] | None = None,
        sleep: Callable[[float], Any] = anyio.sleep,
    ):
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT)
        self._limiter_factory = limiter_factory or self._default_limiter
        self._limiters: dict[str, RateLimiter] = {}
        self._routes: dict[tuple[str, Kind], Route] = {}
        self.last_rate_headers: dict[str, dict[str, str]] = {}
        self._sleep = sleep

    def _default_limiter(self, deployment: str) -> RateLimiter:
        return RateLimiter(
            self.settings.state_dir,
            f"{self.settings.endpoint}|{deployment}",
            self.settings.rpm_limit,
        )

    def limiter(self, deployment: str) -> RateLimiter:
        if deployment not in self._limiters:
            self._limiters[deployment] = self._limiter_factory(deployment)
        return self._limiters[deployment]

    def route_for(self, deployment: str, kind: Kind = "generations") -> Route:
        return self._routes.get((deployment, kind), "v1")

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, route: Route, deployment: str, kind: Kind) -> str:
        base = self.settings.endpoint
        if route == "v1":
            return f"{base}/openai/v1/images/{kind}"
        return f"{base}/openai/deployments/{deployment}/images/{kind}?api-version={self.settings.api_version}"

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.settings.api_key, "user-agent": f"foundry-imagegen/{__version__}"}

    async def _send(self, route: Route, deployment: str, kind: Kind, payload: dict[str, Any], files: list | None) -> httpx.Response:
        url = self._url(route, deployment, kind)
        body = dict(payload)
        if route == "v1":
            body["model"] = deployment
        if files is None:
            return await self._http.post(url, json=body, headers=self._headers())
        data = {k: str(v) for k, v in body.items()}
        return await self._http.post(url, data=data, files=files, headers=self._headers())

    async def _request(
        self,
        deployment: str,
        kind: Kind,
        payload: dict[str, Any],
        files: list | None,
        deadline: float,
        on_wait: WaitCallback | None,
    ) -> tuple[dict[str, Any], Route, dict[str, str]]:
        limiter = self.limiter(deployment)
        transient_failures = 0
        while True:
            await limiter.acquire(deadline, on_wait)
            route = self.route_for(deployment, kind)
            try:
                response = await self._send(route, deployment, kind, payload, files)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                transient_failures += 1
                backoff = 2.0**transient_failures
                if transient_failures > MAX_TRANSIENT_RETRIES or time.time() + backoff > deadline:
                    raise FoundryError(f"Could not reach the images service at {self.settings.endpoint}: {exc!r}") from exc
                await self._sleep(backoff)
                continue

            headers = rate_headers(response.headers)
            self.last_rate_headers[deployment] = headers
            if limit := response.headers.get("x-ratelimit-limit-requests"):
                try:
                    limiter.observe_limit(int(float(limit)))
                except ValueError:
                    pass

            status = response.status_code
            if status == 429:
                wait = retry_after_seconds(response.headers)
                limiter.set_cooldown(wait)
                if time.time() + wait > deadline:
                    raise RateLimitExceeded(time.time() + wait, "the service returned 429 Too Many Requests")
                continue
            if status >= 500:
                transient_failures += 1
                backoff = 2.0**transient_failures
                if transient_failures > MAX_TRANSIENT_RETRIES or time.time() + backoff > deadline:
                    _, message, _ = _error_info(response)
                    raise FoundryError(f"The images service failed ({status}): {message}", status=status)
                await self._sleep(backoff)
                continue

            code, message, inner = _error_info(response) if status >= 400 else (None, "", None)
            if status == 404 and route == "v1" and not _is_deployment_missing(code, message):
                # This resource does not serve the v1 route; use the deployment-scoped route from now on.
                self._routes[(deployment, kind)] = "legacy"
                continue
            if status >= 400:
                raise self._map_error(status, code, message, inner, deployment)

            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                error = body["error"]
                raise self._map_error(status, error.get("code"), str(error.get("message") or ""), None, deployment)
            self._routes[(deployment, kind)] = route
            return body, route, headers

    def _map_error(self, status: int, code: str | None, message: str, inner: str | None, deployment: str) -> FoundryError:
        if _is_content_filter(code, inner, message):
            where = "generated image" if "generated image" in message.lower() else "prompt"
            return ContentFilterError(
                f"The service's safety system blocked the {where}. Rephrase the request (avoid the flagged "
                f"content) and try again. Service message: {message}",
                status=status,
                code=code,
            )
        if status in (401, 403):
            return AuthError(
                f"Authentication failed ({status}). Check the API key and endpoint in the plugin/extension "
                f"settings. Service message: {message}",
                status=status,
                code=code,
            )
        if status == 404 or _is_deployment_missing(code, message):
            return DeploymentNotFoundError(
                f"Deployment {deployment!r} was not found on {self.settings.endpoint}. Configured deployments: "
                f"{', '.join(self.settings.deployments)}. Check the deployment name in the Foundry portal. "
                f"Service message: {message}",
                status=status,
                code=code,
            )
        return FoundryError(f"The request was rejected ({status} {code or ''}): {message}".strip(), status=status, code=code)

    @staticmethod
    def _parse_images(body: dict[str, Any], route: Route, headers: dict[str, str]) -> ImageResponse:
        data = body.get("data") or []
        images = [item["b64_json"] for item in data if isinstance(item, dict) and item.get("b64_json")]
        if not images:
            raise FoundryError("The service returned no image data.")
        revised = [item.get("revised_prompt") for item in data if isinstance(item, dict) and item.get("b64_json")]
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
        return ImageResponse(images, revised, usage, route, headers)

    async def generate(
        self,
        deployment: str,
        prompt: str,
        params: ImageParams,
        *,
        deadline: float,
        on_wait: WaitCallback | None = None,
    ) -> ImageResponse:
        payload = {"prompt": prompt, **params.as_request()}
        body, route, headers = await self._request(deployment, "generations", payload, None, deadline, on_wait)
        return self._parse_images(body, route, headers)

    async def edit(
        self,
        deployment: str,
        prompt: str,
        params: ImageParams,
        images: list[InputImage],
        mask: InputImage | None,
        *,
        deadline: float,
        on_wait: WaitCallback | None = None,
    ) -> ImageResponse:
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("image[]", (img.path.name, img.path.read_bytes(), img.mime)) for img in images
        ]
        if mask is not None:
            files.append(("mask", (mask.path.name, mask.path.read_bytes(), mask.mime)))
        payload = {"prompt": prompt, **params.as_request()}
        body, route, headers = await self._request(deployment, "edits", payload, files, deadline, on_wait)
        return self._parse_images(body, route, headers)

    async def probe(self, deployment: str, *, deadline: float) -> ProbeResult:
        """Check key and deployment with a deliberately invalid (unbilled) generation request."""
        payload = {"prompt": "connection test", "size": "1x1", "n": 1}
        try:
            await self._request(deployment, "generations", payload, None, deadline, None)
        except AuthError as exc:
            return ProbeResult(deployment, "auth_failed", str(exc), None, self.last_rate_headers.get(deployment, {}))
        except DeploymentNotFoundError as exc:
            return ProbeResult(deployment, "deployment_not_found", str(exc), None, self.last_rate_headers.get(deployment, {}))
        except RateLimitExceeded as exc:
            return ProbeResult(deployment, "rate_limited", str(exc), None, {})
        except FoundryError as exc:
            headers = self.last_rate_headers.get(deployment, {})
            if exc.status == 400:
                detail = "Key and deployment accepted (the test request was rejected as invalid, as intended)."
                return ProbeResult(deployment, "ok", detail, self.route_for(deployment), headers)
            return ProbeResult(deployment, "error", str(exc), None, headers)
        detail = "The service accepted the test request."
        return ProbeResult(deployment, "ok", detail, self.route_for(deployment), self.last_rate_headers.get(deployment, {}))
