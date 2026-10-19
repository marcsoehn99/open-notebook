"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# python-dotenv skips load_dotenv while this is set, including api/main.py.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
# Vendored encoding. Set before open_notebook.config reads this name.
os.environ["TIKTOKEN_CACHE_DIR"] = str(
    Path(__file__).resolve().parent / "fixtures" / "tiktoken-cache"
)
# Empty string, not unset, so a later load cannot turn password auth back on.
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_REAL_SOCKET = socket.socket


class ExternalNetworkDenied(Exception):
    """Raised before the operating system connects to a non-local address."""

    def __init__(self, address: object) -> None:
        self.address = address
        super().__init__(f"blocked socket connect to {address!r}")


def _destination_allowed(address: object) -> bool:
    if isinstance(address, (str, bytes)):
        return True
    if (
        isinstance(address, tuple)
        and len(address) in (2, 4)
        and isinstance(address[0], str)
    ):
        return address[0].casefold() in _ALLOWED_HOSTS
    return False


class _GuardedSocket(_REAL_SOCKET):
    def connect(self, address: object) -> None:
        if not _destination_allowed(address):
            raise ExternalNetworkDenied(address)
        super().connect(address)  # type: ignore[arg-type]

    def connect_ex(self, address: object) -> int:
        if not _destination_allowed(address):
            raise ExternalNetworkDenied(address)
        return super().connect_ex(address)  # type: ignore[arg-type]


def _install_network_guard() -> None:
    if socket.socket is not _GuardedSocket:
        socket.socket = _GuardedSocket  # type: ignore[misc]


_install_network_guard()

_SECRET_SUFFIXES = ("_API_KEY", "_API_TOKEN")
_SECRET_NAMES = frozenset(
    {
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    }
)

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def _block_tiktoken_download() -> None:
    """A cache miss must not fetch the encoding. The vendored file is the normal path."""
    import tiktoken.load as tiktoken_load

    real_read_file = tiktoken_load.read_file

    def read_file(blobpath: str) -> bytes:
        if str(blobpath).startswith(("http://", "https://")):
            raise OSError("tiktoken encoding download is blocked in tests")
        return real_read_file(blobpath)

    tiktoken_load.read_file = read_file


_block_tiktoken_download()


def _is_provider_secret(name: str) -> bool:
    return name.endswith(_SECRET_SUFFIXES) or name in _SECRET_NAMES


def _strip_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if _is_provider_secret(name):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _block_external_network() -> Iterator[None]:
    _install_network_guard()
    yield
    _install_network_guard()


@pytest.fixture(autouse=True)
def _clear_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _strip_provider_secrets(monkeypatch)
