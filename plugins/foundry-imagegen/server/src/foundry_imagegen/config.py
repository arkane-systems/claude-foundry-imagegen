"""Configuration: environment variables, then a TOML config file, then defaults.

Every surface feeds the same `FOUNDRY_IMAGEGEN_*` variables: the Claude Code plugin
substitutes them from `userConfig`, the Desktop Extension from its `user_config`.
The config file is the fallback for hosts that start the server without prompting
for settings.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import platformdirs

APP_NAME = "foundry-imagegen"
ENV_PREFIX = "FOUNDRY_IMAGEGEN_"

DEFAULT_DEPLOYMENT = "gpt-image-2.5-flare"
DEFAULT_API_VERSION = "2025-04-01-preview"
DEFAULT_RPM = 5
DEFAULT_MAX_WAIT = 240
DEFAULT_DESKTOP_DIR = Path.home() / "Pictures" / "Foundry Images"
PROJECT_SUBDIR = "generated-images"

# Unsubstituted placeholders that a host may pass through verbatim when a value is unset.
_PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")


class ConfigError(Exception):
    """The configuration is missing or invalid; the message says how to fix it."""


@dataclass(frozen=True)
class Settings:
    endpoint: str
    api_key: str
    deployment: str
    extra_deployments: tuple[str, ...]
    rpm_limit: int
    max_wait_seconds: int
    output_dir: Path
    api_version: str
    project_dir: Path | None
    state_dir: Path
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def deployments(self) -> tuple[str, ...]:
        return (self.deployment, *(d for d in self.extra_deployments if d != self.deployment))

    def resolve_deployment(self, requested: str | None) -> str:
        if not requested:
            return self.deployment
        if requested not in self.deployments:
            raise ConfigError(
                f"Deployment {requested!r} is not configured. Configured deployments: {', '.join(self.deployments)}."
            )
        return requested

    def masked_key(self) -> str:
        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:4]}…{self.api_key[-4:]}"


def config_file_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / "config.toml"


def default_state_dir() -> Path:
    return Path(platformdirs.user_state_dir(APP_NAME, appauthor=False))


def normalize_endpoint(raw: str) -> str:
    """Reduce any Foundry / Azure OpenAI URL form to the resource base URL.

    Accepts e.g. `https://res.services.ai.azure.com/api/projects/p`,
    `https://res.openai.azure.com/openai/v1/`, or a full target URI copied from the
    portal, and returns `https://res.services.ai.azure.com`.
    """
    value = raw.strip()
    if not value:
        raise ConfigError("The endpoint is empty.")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ConfigError(f"The endpoint {raw!r} is not a valid URL.")
    path = parts.path
    for marker in ("/openai", "/api/projects"):
        idx = path.find(marker)
        if idx != -1:
            path = path[:idx]
    path = path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path}"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or _PLACEHOLDER.match(value):
        return None
    return value


def _read_config_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    return data


def _resolve_output_dir(configured: str | None, project_dir: Path | None) -> Path:
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if not path.is_absolute():
            path = (project_dir or Path.home()) / path
        return path
    if project_dir is not None:
        return project_dir / PROJECT_SUBDIR
    return DEFAULT_DESKTOP_DIR


def _resolve_project_dir(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        if not path.is_dir() or path.resolve() == Path.home().resolve():
            return None
    except OSError:
        return None
    return path


def load_settings(env: dict[str, str] | None = None, config_path: Path | None = None) -> Settings:
    """Resolve settings; raises ConfigError naming what is missing and where to set it."""
    env = dict(os.environ if env is None else env)
    path = config_path or Path(_clean(env.get(ENV_PREFIX + "CONFIG_FILE")) or config_file_path())
    file_values = _read_config_file(path)
    sources: dict[str, str] = {}

    def get(key: str, default: object = None) -> object:
        env_value = _clean(env.get(ENV_PREFIX + key.upper()))
        if env_value is not None:
            sources[key] = "environment"
            return env_value
        file_value = file_values.get(key)
        if isinstance(file_value, str):
            file_value = _clean(file_value)
        if file_value is not None:
            sources[key] = f"config file ({path})"
            return file_value
        sources[key] = "default"
        return default

    endpoint = get("endpoint")
    api_key = get("api_key")
    missing = [name for name, value in (("endpoint", endpoint), ("api_key", api_key)) if not value]
    if missing:
        raise ConfigError(
            f"Missing required setting(s): {', '.join(missing)}. Set them in the plugin's configuration "
            f"(Claude Code: /plugin → foundry-imagegen → Configure), in the Desktop Extension settings, "
            f"or in {path}."
        )

    extra = get("extra_deployments", "")
    if isinstance(extra, str):
        extra_list = tuple(d.strip() for d in extra.split(",") if d.strip())
    elif isinstance(extra, list):
        extra_list = tuple(str(d).strip() for d in extra if str(d).strip())
    else:
        raise ConfigError("extra_deployments must be a comma-separated string or a list.")

    def get_int(key: str, default: int, minimum: int) -> int:
        raw = get(key, default)
        try:
            value = int(float(raw))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be a number, got {raw!r}.") from exc
        if value < minimum:
            raise ConfigError(f"{key} must be at least {minimum}, got {value}.")
        return value

    project_dir = _resolve_project_dir(_clean(env.get(ENV_PREFIX + "PROJECT_DIR")))
    output_raw = get("output_dir")
    state_raw = _clean(env.get(ENV_PREFIX + "STATE_DIR"))

    return Settings(
        endpoint=normalize_endpoint(str(endpoint)),
        api_key=str(api_key),
        deployment=str(get("deployment", DEFAULT_DEPLOYMENT)),
        extra_deployments=extra_list,
        rpm_limit=get_int("rpm_limit", DEFAULT_RPM, 1),
        max_wait_seconds=get_int("max_wait_seconds", DEFAULT_MAX_WAIT, 0),
        output_dir=_resolve_output_dir(str(output_raw) if output_raw else None, project_dir),
        api_version=str(get("api_version", DEFAULT_API_VERSION)),
        project_dir=project_dir,
        state_dir=Path(state_raw) if state_raw else default_state_dir(),
        sources=sources,
    )
