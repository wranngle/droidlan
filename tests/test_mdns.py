"""Round-trip test for the mDNS broadcast helper.

Registers a service, then resolves the same hostname from a fresh
Zeroconf client and asserts the IP matches.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mdns import _local_ip, _normalize_hostname, register, resolve  # noqa: E402


def test_normalize_hostname_idempotent() -> None:
    assert _normalize_hostname("droidlan-ftp") == "droidlan-ftp.local."
    assert _normalize_hostname("droidlan-ftp.local") == "droidlan-ftp.local."
    assert _normalize_hostname("droidlan-ftp.local.") == "droidlan-ftp.local."


def test_register_rejects_unknown_service() -> None:
    with pytest.raises(ValueError):
        register("droidlan-x", 2121, service="gopher")


def test_resolve_rejects_unknown_service() -> None:
    with pytest.raises(ValueError):
        resolve("droidlan-x", service="gopher")


def test_register_then_resolve_round_trip() -> None:
    hostname = "droidlan-test-ftp"
    port = 2121
    expected_ip = _local_ip()

    broadcast = register(hostname, port, service="ftp", ip=expected_ip)
    try:
        addr = resolve(hostname, service="ftp", timeout_ms=4000)
        assert addr is not None, "service did not resolve over mDNS"
        try:
            packed = socket.inet_aton(addr)
        except OSError as exc:
            pytest.fail(f"resolved address {addr!r} is not valid IPv4: {exc}")
        assert socket.inet_aton(expected_ip) == packed
    finally:
        broadcast.unregister()


def test_resolve_missing_returns_none() -> None:
    assert resolve("droidlan-nonexistent-host", service="http", timeout_ms=500) is None
