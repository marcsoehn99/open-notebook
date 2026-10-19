"""The pytest process does not keep provider keys from the shell or from a .env file."""

import os
from pathlib import Path

import pytest
from conftest import _strip_provider_secrets
from dotenv import load_dotenv

_PROVIDER_NAMES = (
    "MISTRAL_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "EXAMPLE_API_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)


def test_dotenv_load_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MISTRAL_API_KEY=from-file\nOPEN_NOTEBOOK_CHUNK_SIZE=12345\n")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_CHUNK_SIZE", raising=False)

    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert load_dotenv(env_file) is False
    assert os.environ.get("MISTRAL_API_KEY") is None
    assert os.environ.get("OPEN_NOTEBOOK_CHUNK_SIZE") is None


def test_strip_removes_provider_keys_and_keeps_app_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _PROVIDER_NAMES:
        monkeypatch.setenv(name, "dummy")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "enc")
    monkeypatch.setenv("OPEN_NOTEBOOK_PASSWORD", "")

    _strip_provider_secrets(monkeypatch)

    for name in _PROVIDER_NAMES:
        assert name not in os.environ
    assert os.environ["OPEN_NOTEBOOK_ENCRYPTION_KEY"] == "enc"
    assert os.environ["OPEN_NOTEBOOK_PASSWORD"] == ""
