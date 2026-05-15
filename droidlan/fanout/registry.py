"""In-process registry of connected fanout clients.

Each client gets a thread-safe queue; broadcasts enqueue the same payload
identity (bytes + filename + sha256) to every registered client. Clients
drain via long-poll; the hash is precomputed once and travels alongside the
bytes so receivers verify integrity without re-hashing the producer's copy.
"""

from __future__ import annotations

import hashlib
import queue
import threading
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class FanoutPayload:
    filename: str
    content: bytes
    sha256: str

    @classmethod
    def of(cls, filename: str, content: bytes) -> "FanoutPayload":
        return cls(
            filename=filename,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )


class ClientRegistry:
    def __init__(self, max_queue: int = 64) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, queue.Queue[FanoutPayload]] = {}
        self._max_queue = max_queue

    def register(self) -> str:
        client_id = uuid.uuid4().hex
        with self._lock:
            self._clients[client_id] = queue.Queue(maxsize=self._max_queue)
        return client_id

    def unregister(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def client_ids(self) -> list[str]:
        with self._lock:
            return list(self._clients)

    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def broadcast(self, payload: FanoutPayload) -> int:
        with self._lock:
            targets = list(self._clients.values())
        delivered = 0
        for q in targets:
            try:
                q.put_nowait(payload)
                delivered += 1
            except queue.Full:
                # Slow consumer: drop oldest, retry once. Fanout favours
                # liveness for fast clients over blocking the producer.
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                    delivered += 1
                except queue.Empty:
                    pass
        return delivered

    def pull(self, client_id: str, timeout: float) -> FanoutPayload | None:
        with self._lock:
            q = self._clients.get(client_id)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None
