"""External sockets fail before a packet leaves. Loopback and Unix sockets stay open."""

import asyncio
import os
import shutil
import socket
import tempfile

import pytest
from conftest import ExternalNetworkDenied

_EXTERNAL_V4 = ("1.1.1.1", 443)
_EXTERNAL_V6 = ("2001:db8::1", 9, 0, 0)


def test_token_count_stays_offline() -> None:
    from open_notebook.utils.token_utils import token_count

    assert token_count("one two three four five") > 0


def test_external_create_connection_raises() -> None:
    with pytest.raises(ExternalNetworkDenied) as denied:
        socket.create_connection(_EXTERNAL_V4, timeout=0.2)
    assert denied.value.address == _EXTERNAL_V4


def test_external_connect_and_connect_ex_raise() -> None:
    sock = socket.socket()
    sock.settimeout(0.2)
    try:
        with pytest.raises(ExternalNetworkDenied) as denied:
            sock.connect(_EXTERNAL_V4)
        assert denied.value.address == _EXTERNAL_V4
        with pytest.raises(ExternalNetworkDenied) as denied_ex:
            sock.connect_ex(_EXTERNAL_V4)
        assert denied_ex.value.address == _EXTERNAL_V4
    finally:
        sock.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 is unavailable")
def test_external_ipv6_connect_raises() -> None:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        with pytest.raises(ExternalNetworkDenied) as denied:
            sock.connect(_EXTERNAL_V6)
        assert denied.value.address == _EXTERNAL_V6
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_asyncio_external_connect_raises() -> None:
    with pytest.raises(ExternalNetworkDenied) as denied:
        await asyncio.wait_for(asyncio.open_connection("1.1.1.1", 443), timeout=2)
    assert denied.value.address[0] == "1.1.1.1"


def test_loopback_and_localhost_connect() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.socket()
    try:
        client.connect(("127.0.0.1", port))
        assert client.getpeername()[0] == "127.0.0.1"
        client.close()
        client = socket.socket()
        client.connect(("localhost", port))
        assert client.getpeername()[0] == "127.0.0.1"
    finally:
        client.close()
        listener.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 is unavailable")
def test_ipv6_loopback_connects() -> None:
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        listener.bind(("::1", 0))
    except OSError:
        listener.close()
        pytest.skip("::1 is unavailable")
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        client.connect(("::1", port, 0, 0))
        assert client.getpeername()[0] == "::1"
    finally:
        client.close()
        listener.close()


def test_unix_socket_connects() -> None:
    directory = tempfile.mkdtemp(prefix="nb", dir="/tmp")
    path = os.path.join(directory, "p.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    accepted: socket.socket | None = None
    try:
        client.connect(path)
        accepted, _ = listener.accept()
        accepted.close()
        accepted = None
        client.close()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(os.fsencode(path))
        accepted, _ = listener.accept()
    finally:
        client.close()
        if accepted is not None:
            accepted.close()
        listener.close()
        shutil.rmtree(directory, ignore_errors=True)
