"""TEST-U-06: SSRF table (TESTING §3.2) with a stub resolver, plus GuardedTransport pinning."""

import ipaddress

import httpx2
import pytest

from wmg_gateway.security import ssrf
from wmg_gateway.upstream.transport import GuardedTransport

PUBLIC_IP = "93.184.215.14"
STUB = {
    "example.com": [PUBLIC_IP],
    "private.example": ["10.1.2.3"],
    "mixed.example": [PUBLIC_IP, "192.168.0.5"],
}


async def stub_resolver(host: str, port: int) -> list[str]:
    if host in STUB:
        return STUB[host]
    # numeric forms (2130706433, 0x7f.1, IPv6 literals) resolve locally, without the network
    return await ssrf.system_resolver(host, port)


ALLOWED = [
    "http://gst.mcp-upstreams.svc.cluster.local:8080/mcp",
    "http://main-woo-mcp.store-main.svc:8080/mcp",
    "http://x.mcp-hosted.svc/mcp",
    "https://example.com/mcp",
]
BLOCKED = [
    "http://platform-postgres.mcp-platform.svc.cluster.local:5432",
    "http://kubernetes.default.svc",
    "http://kubernetes.store-main.svc",
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1:8080/mcp",
    "http://localhost/mcp",
    "http://gateway.localhost/mcp",
    "https://gateway.localhost/mcp",
    "https://metadata.google.internal/",
    "https://printer.local/",
    "https://router.home.arpa/",
    "https://2130706433/",
    "https://0x7f.1/",
    "https://[::ffff:127.0.0.1]/",
    "https://[::1]/",
    "http://10.43.0.1/",
    "https://192.168.1.10/",
    "https://100.64.0.1/",
    "http://example.com/mcp",
    "https://user:pass@example.com/",
    "ftp://example.com/",
    "https://private.example/",
    "https://mixed.example/",
    "https://exa_mple.com/",
    "https:///nohost",
]


@pytest.mark.anyio
@pytest.mark.parametrize("url", ALLOWED)
async def test_U06_allowed(url: str) -> None:
    await ssrf.check_url(url, stub_resolver)


@pytest.mark.anyio
@pytest.mark.parametrize("url", BLOCKED)
async def test_U06_blocked(url: str) -> None:
    with pytest.raises(ssrf.SsrfBlockedError):
        await ssrf.check_url(url, stub_resolver)


def test_U06_ipv6_embedded_v4() -> None:
    assert not ssrf.is_public(ipaddress.ip_address("2002:7f00:1::1"))  # 6to4 of 127.0.0.1
    assert not ssrf.is_public(ipaddress.ip_address("ff02::1"))
    assert ssrf.is_public(ipaddress.ip_address("2606:4700:4700::1111"))


class Recorder(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json={"ok": True})


@pytest.mark.anyio
async def test_U06_transport_pins_ip_and_keeps_host_and_sni() -> None:
    inner = Recorder()
    async with httpx2.AsyncClient(transport=GuardedTransport(inner, stub_resolver)) as client:
        r = await client.post("https://example.com/mcp", json={})
    assert r.status_code == 200
    sent = inner.requests[0]
    assert sent.url.host == PUBLIC_IP and sent.url.scheme == "https" and sent.url.path == "/mcp"
    assert sent.headers["Host"] == "example.com"
    assert sent.extensions["sni_hostname"] == "example.com"


@pytest.mark.anyio
async def test_U06_transport_in_cluster_untouched_and_rebinding_blocked() -> None:
    inner = Recorder()
    answers = iter([[PUBLIC_IP], ["10.0.0.7"]])

    async def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    async with httpx2.AsyncClient(transport=GuardedTransport(inner, rebinding)) as client:
        await client.get("http://gst.mcp-upstreams.svc.cluster.local:8080/mcp")
        assert inner.requests[0].url.host == "gst.mcp-upstreams.svc.cluster.local"
        await client.get("https://rebind.example/")
        with pytest.raises(ssrf.SsrfBlockedError, match="non-public"):
            await client.get("https://rebind.example/")
        with pytest.raises(ssrf.SsrfBlockedError):
            await client.get("http://platform-postgres.mcp-platform.svc:5432/")
    assert len(inner.requests) == 2
