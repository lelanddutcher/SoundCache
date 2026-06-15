"""Shared URL-safety checks (SSRF defense).

A submitted link travels relay -> paired desktop, where it's fetched by
yt-dlp / urllib / Playwright. A malicious link could try to make the desktop
hit internal/cloud-metadata endpoints. These checks reject non-web schemes,
over-long URLs, and hosts that are literal private/reserved IPs or known
internal hostnames.

The relay uses the cheap, DNS-free :func:`is_safe_public_url` at submit time.
The desktop (which actually fetches) additionally uses :func:`resolves_to_public`
to catch public hostnames that resolve to private IPs.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048

_BLOCKED_HOSTNAMES = {
    "localhost",
    "ip6-localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.google.com",
    "instance-data",
    "instance-data.ec2.internal",
}


def _ip_is_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    host = host.lower().strip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return True
    return _ip_is_blocked(host)


def is_safe_public_url(url: str) -> bool:
    """True only for an http(s) URL, of reasonable length, whose *literal* host
    is not a private/reserved IP or a known internal hostname. No DNS lookup."""
    if not url or len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    return not _host_is_blocked(host)


def resolves_to_public(url: str) -> bool:
    """True if every IP the URL's host resolves to is public. Does a DNS lookup,
    so use only where the URL is about to be fetched (the desktop), not in the
    serverless relay. Fails closed on resolution errors."""
    try:
        host = urlparse(url.strip()).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    # Literal-IP hosts are covered by is_safe_public_url; this adds DNS coverage.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    return all(not _ip_is_blocked(addr) for addr in addrs)


def is_safe_to_fetch(url: str) -> bool:
    """Full check for the fetch point: literal checks + DNS-resolution check."""
    return is_safe_public_url(url) and resolves_to_public(url)
