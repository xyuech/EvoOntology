"""Streamable HTTP subset: POST JSON-RPC, 202 for notifications, 405 for streams."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from evoontology import SemanticStore, ensure_workspace
from evoontology.runtime.http_transport import make_http_server
from evoontology.runtime.mcp_server import SemanticMCPServer


def _version(term_id: str):
    return {
        "terms": [{"id": term_id, "name": term_id, "type": "entity", "definition": term_id}],
        "mappings": [],
        "relations": [],
        "constraints": [],
        "evidence": [],
    }


@pytest.fixture
def endpoint(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(tmp_path, "ontology_v0", _version("t_zero"))
    SemanticStore.save_version(tmp_path, "ontology_v1", _version("t_one"))
    SemanticStore.set_active(tmp_path, "ontology_v1")
    server = SemanticMCPServer(str(tmp_path), tools=["browse_semantics", "resolve_semantics"])
    httpd = make_http_server(server.handle_message, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _request(url, method="POST", payload=None, raw=None):
    body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


def test_initialize_returns_json(endpoint):
    status, content_type, body = _request(
        endpoint + "/mcp",
        payload={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
    )

    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body)["result"]["protocolVersion"] == "2025-11-25"


def test_notification_returns_202(endpoint):
    status, _, body = _request(endpoint + "/mcp", payload={"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert status == 202
    assert body == b""


def test_batch_of_notifications_returns_202(endpoint):
    status, _, body = _request(
        endpoint + "/mcp",
        payload=[{"jsonrpc": "2.0", "method": "notifications/initialized"}],
    )

    assert status == 202
    assert body == b""


def test_batch_answers_requests_only(endpoint):
    status, _, body = _request(
        endpoint + "/mcp",
        payload=[
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )

    responses = json.loads(body)
    assert status == 200
    assert [r["id"] for r in responses] == [2]
    assert [t["name"] for t in responses[0]["result"]["tools"]] == ["browse_semantics", "resolve_semantics"]


def test_streams_are_not_offered(endpoint):
    assert _request(endpoint + "/mcp", method="GET")[0] == 405
    assert _request(endpoint + "/mcp", method="DELETE")[0] == 405


def test_unknown_path_is_404(endpoint):
    assert _request(endpoint + "/other", payload={"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 404


def test_invalid_json_is_400_parse_error(endpoint):
    status, _, body = _request(endpoint + "/mcp", raw=b"{not json")

    assert status == 400
    assert json.loads(body)["error"]["code"] == -32700


def _resolve_version(endpoint, version):
    _, _, body = _request(
        endpoint + "/mcp",
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "resolve_semantics", "arguments": {"mentions": ["anything"], "version": version}},
        },
    )
    return json.loads(json.loads(body)["result"]["content"][0]["text"])["version"]


def test_concurrent_resolves_keep_their_versions(endpoint):
    mismatches = []

    def worker(version):
        for _ in range(20):
            seen = _resolve_version(endpoint, version)
            if seen != version:
                mismatches.append((version, seen))

    threads = [
        threading.Thread(target=worker, args=("ontology_v0" if i % 2 else "ontology_v1",)) for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert mismatches == []
