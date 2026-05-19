"""Multi-device fanout: one upload -> N connected devices.

Reuses the round-1 mDNS discovery contract (PR #2): clients announce
themselves to the same `_droidlan._tcp` advertised endpoint, then register
here to receive every subsequent broadcast.
"""

from .registry import ClientRegistry, FanoutPayload
from .server import FanoutHandler, serve

__all__ = ["ClientRegistry", "FanoutPayload", "FanoutHandler", "serve"]
