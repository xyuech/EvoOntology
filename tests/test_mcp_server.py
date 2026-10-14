"""Protocol checks for the dependency-free semantic MCP server."""

import json
import os
import subprocess
import sys
from pathlib import Path

from evoontology import SemanticStore
from evoontology.runtime.tools import OPERATIONS, TOOLS


def _empty_workspace(tmp_path):
    workspace = tmp_path / ".evoontology"
    SemanticStore.save_version(
        workspace,
        "ontology_v0",
        {family: [] for family in ("terms", "mappings", "relations", "constraints", "evidence")},
    )
    SemanticStore.set_active(workspace, "ontology_v0")
    return workspace


def _run_stdio(args, lines):
    return subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", *args],
        input="\n".join(lines + [""]),
        text=True,
        capture_output=True,
    )


def test_stdio_mcp_initialize_and_list_tools(tmp_path):
    workspace = tmp_path / ".evoontology"
    SemanticStore.save_version(
        workspace,
        "ontology_v0",
        {family: [] for family in ("terms", "mappings", "relations", "constraints", "evidence")},
    )
    SemanticStore.set_active(workspace, "ontology_v0")

    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            "",
        ]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evoontology.runtime.mcp_server",
            "--store",
            str(workspace),
        ],
        input=requests,
        text=True,
        capture_output=True,
        check=True,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    tool_names = [tool["name"] for tool in responses[1]["result"]["tools"]]
    assert tool_names[:2] == ["browse_semantics", "resolve_semantics"]
    assert "validate_semantics" in tool_names
    assert "accept_evolution" in tool_names


def test_resources_templates_list_is_empty(tmp_path):
    workspace = tmp_path / ".evoontology"
    requests = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"}) + "\n"
    completed = subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", "--store", str(workspace)],
        input=requests,
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(completed.stdout.splitlines()[0])
    assert response["result"] == {"resourceTemplates": []}


def test_parse_error_reports_null_id_and_keeps_serving(tmp_path):
    """A malformed line must never poison the id of a later request."""
    workspace = tmp_path / ".evoontology"
    lines = [
        "{this is not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
        "",
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", "--store", str(workspace)],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=True,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(responses) == 2
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32700
    assert responses[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_chinese_arguments_survive_non_utf8_host_locale(tmp_path):
    """Requests carrying UTF-8 CJK text must get a real reply even when the
    server inherits a legacy console code page (no PYTHONIOENCODING/PYTHONUTF8).

    Regression: Codex spawns plugin MCP servers with the ambient Windows
    environment, where stdin/stdout default to the console code page (GBK on
    zh-CN hosts). The garbled decode used to drop requests silently, leaving
    the client waiting until its 300s timeout.
    """
    workspace = tmp_path / "语义工作区" / ".evoontology"
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_versions", "arguments": {"workspace": str(workspace)}},
        },
        ensure_ascii=False,
    )
    # Run by file path (as the plugin launcher does): this must work from a
    # cwd that does not contain the evoontology package.
    server_script = Path(__file__).resolve().parents[1] / "evoontology" / "runtime" / "mcp_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = proc.communicate(
            (request + "\n").encode("utf-8"), timeout=30
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("server never answered a UTF-8 CJK request")
    responses = [json.loads(line) for line in stdout.decode("utf-8").splitlines() if line.strip()]
    assert len(responses) == 1, stderr.decode("utf-8", "replace")
    payload = responses[0]
    assert payload["id"] == 3
    assert "result" in payload, payload
    text = payload["result"]["content"][0]["text"]
    assert json.loads(text)["status"] == "ok"


def test_tools_allow_list_filters_list_and_call(tmp_path):
    workspace = _empty_workspace(tmp_path)
    completed = _run_stdio(
        ["--store", str(workspace), "--tools", "browse_semantics,resolve_semantics"],
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "validate_semantics", "arguments": {}},
                }
            ),
        ],
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [tool["name"] for tool in responses[0]["result"]["tools"]] == [
        "browse_semantics",
        "resolve_semantics",
    ]
    call = responses[1]["result"]
    assert call["isError"] is True
    assert "not exposed" in call["content"][0]["text"]


def test_unknown_tool_in_allow_list_fails_fast(tmp_path):
    workspace = _empty_workspace(tmp_path)
    completed = _run_stdio(["--store", str(workspace), "--tools", "browse_semantics,no_such_tool"], [])

    assert completed.returncode != 0
    assert "no_such_tool" in completed.stderr


def test_stdio_non_object_line_still_reports_internal_error(tmp_path):
    workspace = _empty_workspace(tmp_path)
    completed = _run_stdio(["--store", str(workspace)], ["[1, 2]"])

    response = json.loads(completed.stdout.splitlines()[0])
    assert response["id"] is None
    assert response["error"]["code"] == -32603


def test_handle_message_ignores_notifications(tmp_path):
    from evoontology.runtime.mcp_server import SemanticMCPServer

    server = SemanticMCPServer(str(_empty_workspace(tmp_path)))

    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {},
    }


def test_unknown_method_still_reports_method_not_found(tmp_path):
    from evoontology.runtime.mcp_server import SemanticMCPServer

    server = SemanticMCPServer(str(_empty_workspace(tmp_path)))
    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "no/such/method"})

    assert response["error"]["code"] == -32601
    assert "no/such/method" in response["error"]["message"]


def test_resolve_semantics_description_mentions_bindings():
    """Revision 4.2: the Data Agent must be told that mappings carry bindings.

    ``browse_semantics`` deliberately stays silent about bindings -- it projects
    records through ``SemanticLayer.browse``, which has no mapping detail.
    """
    by_name = {tool["name"]: tool for tool in TOOLS}

    assert "bindings" in by_name["resolve_semantics"]["description"]
    assert "priority" in by_name["resolve_semantics"]["description"]
    assert "bindings" not in by_name["browse_semantics"]["description"]


def test_resolve_semantics_input_schema_is_unchanged():
    """The description change must not widen or narrow the accepted arguments."""
    by_name = {tool["name"]: tool for tool in TOOLS}

    assert by_name["resolve_semantics"]["inputSchema"] == {
        "type": "object",
        "properties": {
            "mentions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "context": {"type": "string"},
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to an exact EvoOntology workspace, a .evoontology "
                    "container, or a project root containing .evoontology. A unique nested "
                    "workspace matching the requested version is discovered read-only; pass "
                    "the exact workspace when multiple databases match."
                ),
            },
            "version": {
                "type": "string",
                "description": "Semantic version to resolve; omit for the active version.",
            },
        },
        "required": ["mentions"],
        "additionalProperties": False,
    }


def test_validate_semantics_schema_exposes_optional_catalog():
    """Revision 4.1: the catalog snapshot path is an optional argument."""
    by_name = {tool["name"]: tool for tool in OPERATIONS}
    schema = by_name["validate_semantics"]["inputSchema"]

    assert schema["properties"]["catalog"]["type"] == "string"
    assert schema["required"] == ["workspace"]


def test_shutdown_still_answers_with_a_null_result(tmp_path):
    """stdio wire compatibility: ``shutdown`` has no payload but is not a notification.

    The response must stay ``{"result": null}`` with a real id, not be swallowed
    as a notification and not be reported as an error.
    """
    workspace = _empty_workspace(tmp_path)
    completed = _run_stdio(
        ["--store", str(workspace)],
        [json.dumps({"jsonrpc": "2.0", "id": 11, "method": "shutdown"})],
    )

    response = json.loads(completed.stdout.splitlines()[0])
    assert response == {"jsonrpc": "2.0", "id": 11, "result": None}
