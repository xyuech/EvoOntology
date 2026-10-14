"""Dependency-free MCP server for the active EvoOntology workspace.

Serves the semantic tools over either newline-delimited JSON-RPC on stdio (the
default, used by the plugins) or a minimal Streamable HTTP endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

if __package__:
    from ..workspace import resolve_workspace, resolve_workspace_for_version
    from . import ops
    from .runtime import SemanticLayer
    from .tools import OPERATIONS, TOOLS
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evoontology.runtime import ops
    from evoontology.runtime.runtime import SemanticLayer
    from evoontology.runtime.tools import OPERATIONS, TOOLS
    from evoontology.workspace import resolve_workspace, resolve_workspace_for_version

_RESOURCE_URI = "evo-semantic://session-manifest"

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def force_utf8_stdio() -> None:
    """Serve UTF-8 regardless of the host locale.

    MCP stdio traffic is always UTF-8, but a plugin server inherits the host
    process environment. On Windows that means the console code page (for
    example GBK) is used for stdin/stdout unless told otherwise. Incoming
    UTF-8 bytes then decode to mojibake that can corrupt JSON escapes, and
    outgoing non-ASCII text can fail to encode -- either way the client waits
    for a reply that never arrives. Reconfiguring the streams makes the
    server correct no matter how it was launched.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


class SemanticMCPServer:
    """Serve the semantic tools over newline-delimited JSON-RPC or HTTP.

    ``tools`` is an optional allow-list: when given, only those tools appear in
    ``tools/list`` and only those tools answer ``tools/call``. ``None`` exposes
    everything, which keeps existing stdio clients working unchanged.
    """

    def __init__(self, store_path: str, version: str = "", tools: Optional[Iterable[str]] = None):
        self.store_path = store_path
        self.version = version
        known = [tool["name"] for tool in TOOLS + OPERATIONS]
        if tools is None:
            self.exposed: Optional[frozenset] = None
        else:
            requested = [str(name).strip() for name in tools if str(name).strip()]
            unknown = sorted(set(requested) - set(known))
            if unknown:
                raise ValueError(f"Unknown tools in allow-list: {', '.join(unknown)}")
            self.exposed = frozenset(requested)

    @property
    def layer(self) -> SemanticLayer:
        """The layer a request would see right now (pinned version or active).

        Loading per access is deliberate: a long-running server picks up a newly
        activated version without a restart, and concurrent HTTP requests never
        share one layer, so a request pinned to one version cannot observe
        another request's version.
        """
        return SemanticLayer.load(self.store_path, version=self.version or None)

    def _exposed(self, name: str) -> bool:
        return self.exposed is None or name in self.exposed

    def dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "evo-semantic-mcp", "version": "1.0.0"},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool for tool in TOOLS + OPERATIONS if self._exposed(tool["name"])]}
        if method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments", {})
            if not self._exposed(name):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"status": "error", "message": f"Tool not exposed by this server: {name}"},
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": True,
                }
            if name in {"browse_semantics", "resolve_semantics"}:
                try:
                    semantic_args = dict(arguments or {})
                    requested_workspace = semantic_args.pop("workspace", None)
                    requested_version = str(semantic_args.pop("version", "") or "")
                    if requested_workspace:
                        semantic_workspace = resolve_workspace_for_version(
                            requested_workspace,
                            version=requested_version or "active",
                        )
                        semantic_version = requested_version or None
                    else:
                        semantic_workspace = Path(self.store_path)
                        semantic_version = requested_version or self.version or None
                    layer = SemanticLayer.load(str(semantic_workspace), version=semantic_version)
                    result = layer.execute(name, semantic_args)
                    result.setdefault("workspace", str(semantic_workspace.resolve()))
                    is_error = False
                except Exception as exc:
                    result = {"status": "error", "message": str(exc)}
                    is_error = True
            elif name in ops._HANDLERS:
                try:
                    result = ops.execute(name, arguments)
                    is_error = False
                except Exception as exc:
                    result = {"status": "error", "message": str(exc)}
                    is_error = True
            else:
                result = {"status": "error", "message": f"Unknown tool: {name}"}
                is_error = True
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, default=str),
                    }
                ],
                "isError": is_error,
            }
        if method == "resources/list":
            return {
                "resources": [
                    {
                        "uri": _RESOURCE_URI,
                        "name": "EvoOntology semantic session manifest",
                        "mimeType": "text/plain",
                    }
                ]
            }
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = str(params.get("uri", ""))
            if uri != _RESOURCE_URI:
                raise ValueError(f"Unknown resource: {uri}")
            layer = self.layer
            return {
                "contents": [
                    {
                        "uri": _RESOURCE_URI,
                        "mimeType": "text/plain",
                        "text": layer.manifest(),
                    }
                ]
            }
        if method == "shutdown":
            return None
        raise KeyError(method)

    def handle_message(self, message: Any) -> Optional[Dict[str, Any]]:
        """Answer one decoded JSON-RPC message; ``None`` means a notification."""
        if not isinstance(message, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": INTERNAL_ERROR, "message": "request must be a JSON object"},
            }
        request_id = message.get("id")
        if request_id is None:
            return None  # notification: no response by design
        try:
            result = self.dispatch(str(message.get("method", "")), message.get("params") or {})
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except KeyError as exc:
            missing = exc.args[0] if exc.args else ""
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {missing}"},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": INTERNAL_ERROR, "message": str(exc)},
            }

    @staticmethod
    def _write(response: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def run(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except UnicodeDecodeError as exc:
                # Undecodable bytes: report and keep serving; never crash the
                # transport, never reuse a previous request id.
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": PARSE_ERROR, "message": f"Undecodable input: {exc}"},
                    }
                )
                continue
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": PARSE_ERROR, "message": f"Parse error: {exc}"},
                    }
                )
                continue
            response = self.handle_message(message)
            if response is not None:
                self._write(response)


def main() -> None:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="EvoOntology semantic MCP server")
    parser.add_argument(
        "--store",
        default=None,
        help="Workspace root containing active.json (default: <cwd>/.evoontology)",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Explicit semantic version to serve (default: active version)",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="Transport to serve on (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (http transport)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (http transport)")
    parser.add_argument(
        "--tools",
        default="",
        help="Comma-separated tool names to expose (default: all tools)",
    )
    args = parser.parse_args()
    tools = [name for name in args.tools.split(",") if name.strip()] or None
    try:
        server = SemanticMCPServer(
            str(resolve_workspace(args.store)), version=args.version, tools=tools
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.transport == "http":
        # Imported here so the stdio path never depends on the HTTP module.
        if __package__:
            from .http_transport import make_http_server
        else:
            from evoontology.runtime.http_transport import make_http_server

        httpd = make_http_server(server.handle_message, args.host, args.port)
        print(
            f"evo-semantic-mcp listening on http://{args.host}:{httpd.server_address[1]}/mcp",
            file=sys.stderr,
            flush=True,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    else:
        server.run()


if __name__ == "__main__":
    main()
