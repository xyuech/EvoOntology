"""Minimal MCP Streamable HTTP transport for the dependency-free server.

Implements the request/response subset of the transport: ``POST /mcp``
carries one JSON-RPC message or a batch and is answered with
``application/json``; a message (or batch) with nothing to answer gets
``202 Accepted``. The server keeps no session state and offers no
server-to-client stream, so ``GET`` and ``DELETE`` answer ``405``.

Only the standard library is used, because the core package must stay
dependency-free (D31).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
MAX_BODY_BYTES = 4 * 1024 * 1024

MessageHandler = Callable[[Any], Optional[Dict[str, Any]]]


def _error(code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}


def make_http_server(
    handle_message: MessageHandler, host: str, port: int, path: str = "/mcp"
) -> ThreadingHTTPServer:
    """Bind a threading HTTP server that feeds decoded messages to ``handle_message``."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "evo-semantic-mcp"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return  # keep stdout clean; the stdio transport shares this module tree

        def _send(self, status: int, body: bytes = b"", headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _on_endpoint(self) -> bool:
            return self.path.split("?", 1)[0] == path

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self._send(405 if self._on_endpoint() else 404, headers={"Allow": "POST"})

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
            self._send(405 if self._on_endpoint() else 404, headers={"Allow": "POST"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            if not self._on_endpoint():
                self._send(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(400, _error(INVALID_REQUEST, "Missing or oversized request body"))
                return
            try:
                message = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(400, _error(PARSE_ERROR, f"Parse error: {exc}"))
                return
            if isinstance(message, list):
                responses = [r for r in (handle_message(item) for item in message) if r is not None]
                if responses:
                    self._send_json(200, responses)
                else:
                    self._send(202)
                return
            response = handle_message(message)
            if response is None:
                self._send(202)
            else:
                self._send_json(200, response)

    return ThreadingHTTPServer((host, port), _Handler)
