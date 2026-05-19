"""Multi-device fanout proof.

Central promise: ONE upload -> N connected devices all receive the same bytes.
Test: 3 clients register, one broadcast, all 3 pull, hashes match.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import urllib.request
import uuid

import pytest

# Make the repo root importable when invoked from any CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from droidlan.fanout import ClientRegistry, FanoutPayload, serve
from droidlan.fanout.registry import ClientRegistry as _Reg


@pytest.fixture
def fanout_server():
    server, _thread, registry = serve(host="127.0.0.1", port=0, pull_timeout=2.0)
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}", registry
    server.shutdown()
    server.server_close()


def _multipart(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----droidlan{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes = b"", content_type: str = "application/x-www-form-urlencoded"):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": content_type})
    return urllib.request.urlopen(req, timeout=5)


def _get(url: str):
    return urllib.request.urlopen(url, timeout=10)


# --- Central promise -----------------------------------------------------

def test_central_promise_one_upload_reaches_three_clients_with_identical_hash(fanout_server):
    base, _ = fanout_server

    # 3 clients register
    client_ids = []
    for _ in range(3):
        resp = _post(f"{base}/fanout/register")
        client_ids.append(eval_json(resp)["client_id"])
    assert len(set(client_ids)) == 3  # unique

    # Each client pulls on its own thread (long-poll-style)
    received: dict[str, tuple[str, bytes]] = {}
    errors: list[Exception] = []

    def pull(cid: str) -> None:
        try:
            resp = _get(f"{base}/fanout/pull?client_id={cid}")
            sha = resp.headers.get("X-SHA256", "")
            data = resp.read()
            received[cid] = (sha, data)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=pull, args=(cid,)) for cid in client_ids]
    for t in threads:
        t.start()

    # Give pullers a moment to enter the queue, then broadcast once.
    time.sleep(0.2)
    payload_bytes = b"FANOUT-PROOF-" + os.urandom(4096)  # non-trivial size + uniqueness
    expected_sha = hashlib.sha256(payload_bytes).hexdigest()
    body, ctype = _multipart("proof.bin", payload_bytes)
    resp = _post(f"{base}/fanout/broadcast", body=body, content_type=ctype)
    broadcast_result = eval_json(resp)

    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "client puller hung"

    assert not errors, f"client pull errors: {errors}"
    assert broadcast_result["delivered"] == 3
    assert broadcast_result["sha256"] == expected_sha
    assert broadcast_result["bytes"] == len(payload_bytes)

    # The central assertion: all 3 clients got the SAME hash, equal to producer's.
    assert len(received) == 3
    received_hashes = {sha for sha, _ in received.values()}
    assert received_hashes == {expected_sha}, f"hash mismatch across clients: {received_hashes}"

    # Bytes-level identity check (defence in depth — header could lie).
    for cid, (_sha, data) in received.items():
        assert hashlib.sha256(data).hexdigest() == expected_sha, f"corrupt payload for {cid}"
        assert data == payload_bytes


# --- Registry-level invariants -------------------------------------------

def test_registry_broadcast_returns_delivered_count_equal_to_registered():
    reg = _Reg()
    ids = [reg.register() for _ in range(5)]
    delivered = reg.broadcast(FanoutPayload.of("x.bin", b"hello"))
    assert delivered == 5
    # Each queue now has the payload waiting.
    for cid in ids:
        payload = reg.pull(cid, timeout=0.1)
        assert payload is not None
        assert payload.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_registry_unregister_removes_client_from_future_broadcasts():
    reg = _Reg()
    a = reg.register()
    b = reg.register()
    reg.unregister(a)
    delivered = reg.broadcast(FanoutPayload.of("x", b"y"))
    assert delivered == 1
    assert reg.pull(a, timeout=0.05) is None
    assert reg.pull(b, timeout=0.05) is not None


def test_pull_returns_none_on_timeout_with_no_broadcast(fanout_server):
    base, _ = fanout_server
    resp = _post(f"{base}/fanout/register")
    cid = eval_json(resp)["client_id"]
    # pull_timeout is 2s in the fixture; ask for it and expect 204.
    resp = _get(f"{base}/fanout/pull?client_id={cid}")
    assert resp.status == 204


def test_payload_sha256_is_computed_once_at_construction():
    p = FanoutPayload.of("file.bin", b"abc")
    assert p.sha256 == hashlib.sha256(b"abc").hexdigest()
    # frozen dataclass — sha doesn't drift if content is reused.
    p2 = FanoutPayload.of("other.bin", b"abc")
    assert p.sha256 == p2.sha256


# --- helpers -------------------------------------------------------------

def eval_json(resp) -> dict:
    import json
    return json.loads(resp.read().decode("utf-8"))
