"""WooCommerce REST client: OAuth 1.0a one-legged signing (HMAC-SHA256) over plain HTTP + error mapping.

Signing rules were verified against WooCommerce 11.1.2 in P3 (TRACKER "P3 evidence"):
- params = query params + oauth_* params (JSON bodies are not signed)
- sort by key, encode key and value (RFC 3986), encode "k=v" again, join with "%26"
- base string = METHOD & enc("http://<WP_HOME host>/wp-json/wc/v3<path>") & normalized params
- key = consumer_secret + "&"
"""

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp.server.mcpserver.exceptions import ToolError

from wmg_common.logging import get_logger
from woo_mcp.settings import WooMcpSettings

API_PREFIX = "/wp-json/wc/v3"
QueryValue = str | int | bool

log = get_logger(__name__)


def _enc(value: str) -> str:
    """PHP rawurlencode(): RFC 3986, everything but unreserved characters is percent-encoded."""
    return urllib.parse.quote(value, safe="")


def _as_str(value: QueryValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def oauth_params(
    method: str,
    path: str,
    query: dict[str, str],
    *,
    consumer_key: str,
    consumer_secret: str,
    public_host: str,
    nonce: str,
    timestamp: int,
) -> dict[str, str]:
    """Return query + oauth params + `oauth_signature` for a request to `API_PREFIX + path`."""
    params = {
        **query,
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(timestamp),
    }
    normalized = "%26".join(_enc(f"{_enc(k)}={_enc(v)}") for k, v in sorted(params.items()))
    base = f"{method.upper()}&{_enc(f'http://{public_host}{API_PREFIX}{path}')}&{normalized}"
    digest = hmac.new(f"{consumer_secret}&".encode(), base.encode(), hashlib.sha256).digest()
    return {**params, "oauth_signature": base64.b64encode(digest).decode()}


@dataclass(frozen=True)
class WcResponse:
    data: Any
    total: int | None = None
    total_pages: int | None = None


def _header_int(response: httpx2.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    return int(raw) if raw and raw.isdigit() else None


def _wc_message(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return str(body["message"])
    return f"HTTP {response.status_code}"


class WooClient:
    """One pooled HTTP client per process; every call is signed fresh (new nonce + timestamp)."""

    def __init__(self, settings: WooMcpSettings, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._ck = settings.wc_consumer_key.get_secret_value()
        self._cs = settings.wc_consumer_secret.get_secret_value()
        self._public_host = settings.wc_public_host
        self.timeout_s = settings.store_timeout_ms / 1000
        self._http = httpx2.AsyncClient(
            base_url=settings.wc_internal_url,
            headers={"Host": settings.wc_public_host, "Accept": "application/json"},
            timeout=self.timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, QueryValue | None] | None = None,
        json_body: dict[str, Any] | None = None,
        entity: str = "resource",
        entity_id: int | None = None,
    ) -> WcResponse:
        """Signed call to `/wp-json/wc/v3<path>`. Failures raise `ToolError` with a model-readable message."""
        clean = {k: _as_str(v) for k, v in (query or {}).items() if v is not None}
        params = oauth_params(
            method,
            path,
            clean,
            consumer_key=self._ck,
            consumer_secret=self._cs,
            public_host=self._public_host,
            nonce=secrets.token_hex(16),
            timestamp=int(time.time()),
        )
        response = await self._send(method, API_PREFIX + path, params=params, json_body=json_body)
        if response.is_success:
            return WcResponse(
                data=response.json(),
                total=_header_int(response, "X-WP-Total"),
                total_pages=_header_int(response, "X-WP-TotalPages"),
            )
        raise self._map_error(response, method, path, entity, entity_id)

    async def get_public(self, path: str) -> Any:
        """Unsigned GET of a public WordPress endpoint (e.g. `/wp-json/` for the site timezone)."""
        response = await self._send("GET", path)
        if response.is_success:
            return response.json()
        raise self._map_error(response, "GET", path, "resource", None)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx2.Response:
        try:
            return await self._http.request(method, url, params=params, json=json_body)
        except httpx2.TimeoutException:
            log.warning("store timeout", method=method, path=url)
            raise ToolError(f"Store did not respond within {self.timeout_s:g}s") from None
        except httpx2.TransportError as exc:
            log.warning("store unavailable", method=method, path=url, error=type(exc).__name__)
            raise ToolError("Store unavailable") from None

    @staticmethod
    def _map_error(
        response: httpx2.Response, method: str, path: str, entity: str, entity_id: int | None
    ) -> ToolError:
        status = response.status_code
        # path only: the full URL carries the signature and consumer key
        log.info("store error", method=method, path=path, status=status)
        if status == 400:
            return ToolError(f"Invalid request: {_wc_message(response)}")
        if status in (401, 403):
            return ToolError("Store rejected the credentials (check setup job / REST key)")
        if status == 404:
            return ToolError(
                f"Not found: {entity} {entity_id}" if entity_id is not None else f"Not found: {entity}"
            )
        return ToolError(f"Store error (HTTP {status}): {_wc_message(response)}")
