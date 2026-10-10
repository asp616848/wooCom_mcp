"""SSRF policy for upstream URLs (DESIGN §8.4, D43).

`check_url` runs when a server is created/updated/tested; `GuardedTransport` (upstream/transport.py) runs the
same checks on every outbound request and connects to the validated IP, so DNS rebinding can't slip through.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_HOST_CHARS = re.compile(r"^[a-z0-9.-]+$")
_IN_CLUSTER = re.compile(r"^([a-z0-9-]+)\.([a-z0-9-]+)\.svc(\.cluster\.local)?$")
_STORE_NS = re.compile(r"^store-[a-z0-9]+$")
UPSTREAM_NAMESPACES = frozenset({"mcp-upstreams", "mcp-hosted"})
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_BLOCKED_NAMES = frozenset({"localhost", "metadata.google.internal"})


class SsrfBlockedError(Exception):
    """The URL or one of its resolved addresses is not allowed; the message says why."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # lower-case; IPv6 without brackets
    port: int
    in_cluster: bool


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def parse(url: str) -> Target:
    """Static checks (rules 1-3): scheme, userinfo, hostname shape, blocked names, in-cluster allowlist."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SsrfBlockedError(f"invalid URL: {exc}") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise SsrfBlockedError("only http and https URLs are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SsrfBlockedError("credentials in the URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("URL has no host")
    bracketed = parts.netloc.split("@")[-1].startswith("[")
    if bracketed:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise SsrfBlockedError("invalid IPv6 literal") from None
    elif not _HOST_CHARS.match(host):
        raise SsrfBlockedError("hostname contains invalid characters")
    port = port or (443 if scheme == "https" else 80)

    # Checked before the `.local` suffix rule: `*.svc.cluster.local` names end in `.local` by design.
    in_cluster = _IN_CLUSTER.match(host)
    if in_cluster:
        service, namespace = in_cluster.group(1), in_cluster.group(2)
        if service == "kubernetes" or not (namespace in UPSTREAM_NAMESPACES or _STORE_NS.match(namespace)):
            raise SsrfBlockedError(f"in-cluster namespace '{namespace}' is not an upstream namespace")
        return Target(scheme, host, port, in_cluster=True)

    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise SsrfBlockedError(f"host '{host}' is not allowed")
    if scheme != "https":
        raise SsrfBlockedError("external servers must use https")
    return Target(scheme, host, port, in_cluster=False)


def is_public(address: IpAddress) -> bool:
    """Global unicast only; IPv6 forms that embed an IPv4 address must embed a public one too."""
    if not address.is_global or address.is_multicast:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded: list[ipaddress.IPv4Address | None] = [address.ipv4_mapped, address.sixtofour]
        if address.teredo:
            embedded.extend(address.teredo)
        return all(is_public(v4) for v4 in embedded if v4 is not None)
    return True


async def resolve_public(target: Target, resolver: Resolver = system_resolver) -> str:
    """Rule 4: every resolved address must be public. Returns the first one (the address we connect to)."""
    try:
        addresses = await resolver(target.host, target.port)
    except OSError as exc:
        raise SsrfBlockedError(f"cannot resolve '{target.host}': {exc}") from None
    if not addresses:
        raise SsrfBlockedError(f"'{target.host}' did not resolve")
    for raw in addresses:
        address = ipaddress.ip_address(raw.split("%")[0])
        if not is_public(address):
            raise SsrfBlockedError(f"'{target.host}' resolves to a non-public address ({address})")
    return addresses[0].split("%")[0]


async def check_url(url: str, resolver: Resolver = system_resolver) -> Target:
    """Full check for the API (create/update/test). Raises SsrfBlockedError."""
    target = parse(url)
    if not target.in_cluster:
        await resolve_public(target, resolver)
    return target
