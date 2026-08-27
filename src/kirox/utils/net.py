"""Shared host classification used by every loopback-only boundary."""

from __future__ import annotations

import ipaddress

__all__ = ["is_loopback_host"]


def is_loopback_host(host: str) -> bool:
    """Report whether a host string syntactically denotes a loopback target.

    Accepts ``localhost``, any address in ``127.0.0.0/8``, ``::1``, an
    IPv4-mapped loopback such as ``::ffff:127.0.0.1``, and bracketed or
    zone-suffixed forms of those. Names are never resolved: resolution is
    attacker-influenced, so a hostname that is not literally ``localhost`` is
    rejected instead of being trusted.
    """
    if not isinstance(host, str):
        return False
    normalized = host.strip("[]").split("%", 1)[0].lower()
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback
