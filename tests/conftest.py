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

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def _block_external_network() -> Iterator[None]:
    _install_network_guard()
    yield
    _install_network_guard()
