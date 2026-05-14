"""mDNS / Zeroconf broadcast helper for droidlan servers.

Registers an LAN service so the phone can resolve a name like
`droidlan-ftp.local` instead of needing to type the host's IPv4 address.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

from zeroconf import IPVersion, ServiceInfo, Zeroconf

SERVICE_TYPES = {
    "http": "_http._tcp.local.",
    "ftp": "_ftp._tcp.local.",
}


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _normalize_hostname(name: str) -> str:
    name = name.strip()
    if not name.endswith(".local."):
        if name.endswith(".local"):
            name = name + "."
        else:
            name = name + ".local."
    return name


@dataclass
class MdnsBroadcast:
    """Handle for a live Zeroconf registration."""
    zeroconf: Zeroconf
    info: ServiceInfo

    def unregister(self) -> None:
        try:
            self.zeroconf.unregister_service(self.info)
        finally:
            self.zeroconf.close()


def register(
    hostname: str,
    port: int,
    service: str = "http",
    ip: Optional[str] = None,
    properties: Optional[dict] = None,
) -> MdnsBroadcast:
    """Register a service under `<hostname>.local` on the LAN.

    `hostname` may be supplied with or without a trailing `.local`.
    Returns an `MdnsBroadcast` handle; call `.unregister()` to clean up.
    """
    service_type = SERVICE_TYPES.get(service)
    if service_type is None:
        raise ValueError(f"unknown service kind {service!r}; expected one of {sorted(SERVICE_TYPES)}")

    fqdn = _normalize_hostname(hostname)
    instance = fqdn[: -len(".local.")]
    full_name = f"{instance}.{service_type}"

    resolved_ip = ip or _local_ip()

    info = ServiceInfo(
        type_=service_type,
        name=full_name,
        addresses=[socket.inet_aton(resolved_ip)],
        port=port,
        properties=properties or {},
        server=fqdn,
    )

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    zc.register_service(info)
    return MdnsBroadcast(zeroconf=zc, info=info)


def resolve(hostname: str, service: str = "http", timeout_ms: int = 3000) -> Optional[str]:
    """Resolve a `<hostname>.local` service to its IPv4 address.

    Returns the dotted-quad string or `None` if not found within `timeout_ms`.
    """
    service_type = SERVICE_TYPES.get(service)
    if service_type is None:
        raise ValueError(f"unknown service kind {service!r}; expected one of {sorted(SERVICE_TYPES)}")

    fqdn = _normalize_hostname(hostname)
    instance = fqdn[: -len(".local.")]
    full_name = f"{instance}.{service_type}"

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        info = zc.get_service_info(service_type, full_name, timeout=timeout_ms)
        if info is None:
            return None
        addrs = info.parsed_addresses()
        return addrs[0] if addrs else None
    finally:
        zc.close()
