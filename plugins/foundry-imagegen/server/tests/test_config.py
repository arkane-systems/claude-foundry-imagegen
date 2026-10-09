from pathlib import Path

import pytest

from foundry_imagegen.config import (
    DEFAULT_DESKTOP_DIR,
    PROJECT_SUBDIR,
    ConfigError,
    load_settings,
    normalize_endpoint,
)


@pytest.mark.parametrize(
    "raw",
    [
        "https://res.services.ai.azure.com",
        "https://res.services.ai.azure.com/",
        "res.services.ai.azure.com",
        "https://res.services.ai.azure.com/api/projects/myproj",
        "https://res.services.ai.azure.com/openai/v1/",
        "https://res.services.ai.azure.com/openai/deployments/gpt-image-2.5-flare/images/generations?api-version=x",
    ],
)
def test_normalize_endpoint(raw):
    assert normalize_endpoint(raw) == "https://res.services.ai.azure.com"


def test_normalize_endpoint_rejects_garbage():
    with pytest.raises(ConfigError):
        normalize_endpoint("ftp://")


def base_env(**extra):
    env = {"FOUNDRY_IMAGEGEN_ENDPOINT": "https://res.openai.azure.com/", "FOUNDRY_IMAGEGEN_API_KEY": "k" * 20}
    env.update(extra)
    return env


def test_env_settings_and_defaults(tmp_path):
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_EXTRA_DEPLOYMENTS="a, b,,"), config_path=tmp_path / "none.toml")
    assert s.endpoint == "https://res.openai.azure.com"
    assert s.deployments == ("gpt-image-2.5-flare", "a", "b")
    assert s.rpm_limit == 5
    assert s.sources["endpoint"] == "environment"
    assert s.sources["deployment"] == "default"
    assert s.output_dir == DEFAULT_DESKTOP_DIR


def test_unsubstituted_placeholders_are_ignored(tmp_path):
    s = load_settings(
        base_env(FOUNDRY_IMAGEGEN_DEPLOYMENT="${user_config.deployment}", FOUNDRY_IMAGEGEN_RPM_LIMIT=""),
        config_path=tmp_path / "none.toml",
    )
    assert s.deployment == "gpt-image-2.5-flare"
    assert s.rpm_limit == 5


def test_config_file_fallback(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('endpoint = "https://f.services.ai.azure.com"\napi_key = "filekey"\nrpm_limit = 3\n')
    s = load_settings({}, config_path=cfg)
    assert s.endpoint == "https://f.services.ai.azure.com"
    assert s.rpm_limit == 3
    assert s.sources["api_key"].startswith("config file")


def test_env_overrides_file(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('endpoint = "https://f.services.ai.azure.com"\napi_key = "filekey"\n')
    s = load_settings({"FOUNDRY_IMAGEGEN_API_KEY": "envkey"}, config_path=cfg)
    assert s.api_key == "envkey"


def test_missing_required(tmp_path):
    with pytest.raises(ConfigError, match="endpoint, api_key"):
        load_settings({}, config_path=tmp_path / "none.toml")


def test_project_dir_output(tmp_path):
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_PROJECT_DIR=str(tmp_path)), config_path=tmp_path / "none.toml")
    assert s.output_dir == tmp_path / PROJECT_SUBDIR


def test_home_is_not_a_project(tmp_path):
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_PROJECT_DIR=str(Path.home())), config_path=tmp_path / "none.toml")
    assert s.project_dir is None
    assert s.output_dir == DEFAULT_DESKTOP_DIR


def test_relative_output_dir_is_project_relative(tmp_path):
    s = load_settings(
        base_env(FOUNDRY_IMAGEGEN_PROJECT_DIR=str(tmp_path), FOUNDRY_IMAGEGEN_OUTPUT_DIR="art"),
        config_path=tmp_path / "none.toml",
    )
    assert s.output_dir == tmp_path / "art"


def test_resolve_deployment(settings):
    assert settings.resolve_deployment(None) == "gpt-image-2.5-flare"
    assert settings.resolve_deployment("gpt-image-2.5-sunburst") == "gpt-image-2.5-sunburst"
    with pytest.raises(ConfigError, match="not configured"):
        settings.resolve_deployment("nope")


def test_bad_number(tmp_path):
    with pytest.raises(ConfigError, match="rpm_limit"):
        load_settings(base_env(FOUNDRY_IMAGEGEN_RPM_LIMIT="lots"), config_path=tmp_path / "none.toml")


def test_output_dir_expands_mcpb_folder_variables_without_home_env(tmp_path, monkeypatch):
    # Claude Desktop on Windows passed "${HOME}/Pictures/Foundry Images" through unexpanded,
    # and Windows has no HOME variable, so expandvars alone left it literal.
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    s = load_settings(
        base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR="${HOME}/Art/Foundry Images"), config_path=tmp_path / "none.toml"
    )
    assert s.output_dir == tmp_path / "Art" / "Foundry Images"
    assert "${" not in str(s.output_dir)


def test_output_dir_expands_documents_and_tilde(tmp_path, monkeypatch):
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda: str(tmp_path / "Docs"))
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR="${documents}/art"), config_path=tmp_path / "none.toml")
    assert s.output_dir == tmp_path / "Docs" / "art"
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR="~/art"), config_path=tmp_path / "none.toml")
    assert s.output_dir == Path.home() / "art"


def test_output_dir_rejects_unknown_variable(tmp_path):
    with pytest.raises(ConfigError, match=r"\$\{NOPE_NOT_SET\}"):
        load_settings(base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR="${NOPE_NOT_SET}/x"), config_path=tmp_path / "none.toml")


@pytest.mark.parametrize(
    "legacy", ["${HOME}/Pictures/Foundry Images", "${HOME}\\Pictures\\Foundry Images\\", " ${home}/pictures/foundry images "]
)
def test_legacy_mcpb_default_uses_real_pictures_folder(tmp_path, legacy):
    # 0.1.0/0.1.1 shipped "${HOME}/Pictures/Foundry Images", which ignores a redirected Pictures folder.
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR=legacy), config_path=tmp_path / "none.toml")
    assert s.output_dir == DEFAULT_DESKTOP_DIR


def test_pictures_variable_follows_platform_pictures_dir(tmp_path, monkeypatch):
    import platformdirs

    share = tmp_path / "share" / "My Pictures"
    monkeypatch.setattr(platformdirs, "user_pictures_dir", lambda: str(share))
    s = load_settings(base_env(FOUNDRY_IMAGEGEN_OUTPUT_DIR="${PICTURES}/Foundry Images"), config_path=tmp_path / "none.toml")
    assert s.output_dir == share / "Foundry Images"
