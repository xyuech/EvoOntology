# M1-A report: structured bindings + HTTP transport

**Branch:** `feat/http-bindings` · **Baseline:** `8b67a43` (pre-M1-A checkpoint on `master`)
**Task spec:** `prompt-m1a.md` (this repo) + `~/D/EvoQuery/docs/superpowers/plans/2026-10-13-m1a-evoontology-http-bindings.md`

## Result

| Gate | Result |
|---|---|
| `python3 -m pytest -q` | **233 passed** (baseline was 195 passed, 1 failed) |
| `python3 scripts/sync_plugin_core.py --check` | in sync (both plugin bundles) |
| `ruff check` on every changed file | clean |
| Non-stdlib imports in `evoontology/` | none (D31 holds, `dependencies = []` unchanged) |
| Cross-repo acceptance (`evo_query.ontology_client.HttpOntologyClient`) | **passed** — see below |

The pre-existing single failure (`tests/test_plugin_sync.py`, caused by a Finder
`.DS_Store` inside `evoontology/`) is fixed by Task A4's `IGNORE_FILES`, and
`tests/test_preview.py` still passes (the original plan wrongly predicted it
would fail; it was not touched).

## What was built

- **A1** — `Mapping.bindings` (`evoontology/ontology/models.py`): structured
  `bindings` win; a legacy `table`+`column` mapping becomes one `sql` binding
  whose free-text `semantic_filter` is deliberately *not* converted. Normalized
  and sorted by `priority`. Structural checks in `validate.py::_binding_errors`.
- **A2** — `resolve_semantics` mapping views gained `database_source` and a
  deep-copied `bindings` list. The server gained a `--tools` allow-list
  (validated at startup via `parser.error`), `handle_message()` shared by both
  transports, and `layer` as a property that reloads per access.
- **A3** — `evoontology/runtime/http_transport.py`: stdlib `ThreadingHTTPServer`
  implementing the request/response subset of MCP Streamable HTTP on `POST /mcp`
  (202 for pure notifications, 405 for `GET`/`DELETE`, 404 off-endpoint, 400 for
  a parse error). CLI: `--transport/--host/--port`.
- **A4** — sync script ignores `.DS_Store`; plugin copies regenerated; `USAGE.md`
  / `USAGE.zh-CN.md` documented (`### Serving over HTTP` / `### 通过 HTTP 提供服务`).

### Revision 4.1 — catalog snapshot validation

`validate(root, version, catalog)` where `catalog` may be a path **or** a parsed
dict. Every `kind == "sql"` binding is checked against the snapshot's real
`source_id` / `entity` / `field`, case-sensitively and literally. `kind == "api"`
bindings are skipped (evo-query's catalog has no API sources yet). A
`project.json` whose `data_source` is a dict carrying a `catalog` key is
discovered automatically, with relative paths resolved against the workspace; a
string `data_source` or a missing `project.json` means "no catalog" and never
fails. A snapshot that exists but has no `sources` list is an error rather than
a silent pass; a snapshot that simply is not there is not an error. The report
gained an additive `catalog: {path, checked}` field — `passed`/`errors` semantics
are unchanged, so no workspace without a catalog changes behaviour.

### Revision 4.2 — tool descriptions

`resolve_semantics`'s description now states that mappings carry `bindings`
sorted by ascending `priority` (with `kind`/`source_id`/`entity`/`field`/
`semantic_filter`) and that `database_source`/`table`/`column` are equivalent
historical fields to be preferred *against* `bindings`. Its `inputSchema` is
byte-identical to before, asserted by a test. `browse_semantics` was left alone:
it projects through `SemanticLayer.browse()`/`_browse_view()`, which carries no
mapping binding detail, so promising `bindings` there would have been a lie.
`validate_semantics` also documents its new optional `catalog` argument.

## Cross-repo acceptance (revision 4.3) — passed

Served the real M1-B seed workspace over HTTP, then called it with evo-query's
**real** client:

```bash
cd ~/D/EvoOntology
python3 -m evoontology.runtime.mcp_server --transport http --host 127.0.0.1 --port 8765 \
    --store ~/D/EvoQueryMockData/ontology/.evoontology \
    --tools browse_semantics,resolve_semantics

cd ~/D/EvoQuery
UV_CACHE_DIR=<writable> NO_PROXY=127.0.0.1,localhost uv run python -c "…HttpOntologyClient…"
```

```
active_version -> ontology_v0
term_id        -> t_urgent_sla_event
version        -> ontology_v0
bindings       -> 1
    Binding(mapping_id='m_urgent_sla_event', priority=1, kind='sql',
            source_id='analyzerleasedlineservicedb', entity='tbl_sla_event',
            field='severity', semantic_filter=(Filter(field='severity', op='=', value=1),))
```

`active_version()` returned the real version (not `ONTOLOGY_UNAVAILABLE`) and
`resolve_term()` produced a fully populated `TermResolution.bindings` with
`kind == "sql"`, so M1-C's per-term query path is live.

The three-step handshake (`initialize` → `tools/list` → `tools/call`) was also
verified against the real CLI, including the `--tools` allow-list filtering both
`tools/list` and `tools/call`, and startup failing (exit 2) on an unknown tool.

### Catalog gate exercised against real M1-B data

```
validate(~/D/EvoQueryMockData/ontology/.evoontology)
  → passed: true, catalog: {path: …/evoquery/catalog.json, checked: 23}
```

All 23 seed `sql` bindings resolve against the real 144-entity catalog. Injecting
a typo (`field: "sevirty_typo"`) into a **throwaway copy** flipped the report to
`passed: false` with a message naming the mapping, binding index, the bad name
and the known fields — the intended M1-B gate. The same call through a real
`validate_semantics` MCP tool call returns identical output.

## Deviations from `prompt-m1a.md` / the original plan

1. **New `.gitignore`** (added in commit `f28ed3c`, before any M1-A code): the
   prompt asked for git management, but the working tree was full of untracked
   `__pycache__/` and `.DS_Store` noise. This is additive and does not affect
   the sync script (which still needs its own `IGNORE_FILES`, because it must
   ignore files that are *present*, not merely untracked).
2. **`.gitignore` commit was extra to Task A4's step list**, which mentions only
   the sync script. Both mechanisms are needed and both exist.
3. **Test suite edits were leaner than the plan's**: the plan's route to the
   HTTP transport was to publish the exact code from A1–A3; the tests here cover
   every behaviour the plan and the prompt list (including two cases the plan did
   not: the `shutdown`-returns-`null` wire shape, and `--tools` schema
   assertions), but not via the plan's literal file contents.
4. **`tests/test_bindings.py` and `tests/test_mcp_http.py` are new**, as planned.
   `tests/test_validate.py` was **not** modified: the prompt allowed the catalog
   assertions to live in either file, and keeping all binding/catalog coverage in
   one new module avoided duplicating the `SAMPLE` fixtures.
5. **`USAGE.zh-CN.md` inserted at the line the prompt specified**; a sentence
   documenting the catalog gate was added to both docs beyond the plan's snippet,
   because the plan predates revision 4.1.
6. **`prompt-m1a.md` is left untracked** — it is the user's input document, not
   project source.
7. **Reporting**: §8 offered "write into `~/D/EvoQuery/PROGRESS.md` *or* write a
   report in this repo and say so". The second option was taken, and the report
   lives at this path — no file outside `~/D/EvoOntology` was modified.

## Open items for the user

- **The `evoontology-mock` MCP server in the current dsh session is still
  running the pre-change code** (it was started before these edits). Reload/restart
  that MCP server for `validate_semantics` to accept a `catalog` argument and for
  `resolve_semantics` to return `bindings`.
- Nothing has been pushed and no remote branch exists; the work is local commits
  on `feat/http-bindings`.
