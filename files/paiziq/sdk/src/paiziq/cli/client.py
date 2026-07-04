"""Backend API access for the CLI (PZ-040).

A thin wrapper over `SyncHTTPTransport` that unwraps the control-plane
envelope ({"success", "data", "error"}) and turns HTTP/transport
failures into `CliError` with a human-readable message.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..transport import RetryPolicy, SyncHTTPTransport, TransportError, TransportResponse
from .config import CliConfig

TransportFactory = Callable[[str, str], SyncHTTPTransport]


class CliError(RuntimeError):
    """User-facing CLI failure; message is printed to stderr."""


def default_transport_factory(endpoint: str, api_key: str) -> SyncHTTPTransport:
    return SyncHTTPTransport(endpoint, api_key=api_key, retry=RetryPolicy(max_attempts=2))


class ApiClient:
    def __init__(
        self,
        config: CliConfig,
        transport_factory: Optional[TransportFactory] = None,
    ) -> None:
        factory = transport_factory or default_transport_factory
        self._transport = factory(config.require_endpoint(), config.require_api_key())

    def _unwrap(self, response: TransportResponse) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise CliError(f"backend returned invalid JSON (HTTP {response.status})") from exc
        if isinstance(body, dict) and "success" in body:
            if not body["success"]:
                error = body.get("error") or {}
                raise CliError(
                    f"{error.get('code', 'error')}: {error.get('message', 'request failed')}"
                )
            return body["data"]
        if response.status >= 400:  # non-envelope endpoints (ingest plane)
            raise CliError(f"HTTP {response.status}: {body}")
        return body

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, json_body: Optional[dict[str, Any]] = None) -> Any:
        return self._request("POST", path, json_body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def _request(
        self, method: str, path: str, json_body: Optional[dict[str, Any]] = None
    ) -> Any:
        try:
            response = self._transport.request(method, path, json_body=json_body)
        except TransportError as exc:
            raise CliError(f"cannot reach backend: {exc}") from exc
        return self._unwrap(response)
