"""GuardedTransport (DESIGN §8.4 rule 5): every outbound upstream request is SSRF-checked and IP-pinned.

External hosts: resolve → validate every address → connect to the first validated IP, keeping the original
name in the `Host` header and as TLS SNI (`sni_hostname`), so certificates are still checked against the name
and a DNS answer can't change between the check and the connect. In-cluster service names are allowlisted by
namespace and connect through cluster DNS as usual.
"""

import httpx2

from wmg_gateway.security import ssrf


class GuardedTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx2.AsyncBaseTransport | None = None,
        resolver: ssrf.Resolver = ssrf.system_resolver,
    ) -> None:
        self._inner = inner or httpx2.AsyncHTTPTransport()
        self._resolver = resolver

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        target = ssrf.parse(str(request.url))
        if not target.in_cluster:
            address = await ssrf.resolve_public(target, self._resolver)
            request.headers["Host"] = request.url.netloc.decode("ascii")
            request.extensions = {**request.extensions, "sni_hostname": target.host}
            request.url = request.url.copy_with(host=address)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
