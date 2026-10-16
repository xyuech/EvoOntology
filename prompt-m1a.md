# M1-A 交接说明（EvoOntology：结构化 bindings + HTTP 传输）

> 版本：v1 · 2026-10-14 · 由 `~/D/EvoQuery` 会话整理
> **目标仓库**：`~/D/EvoOntology`（下文简称 ONTO）
> **本文的用法**：在一个能读写 ONTO 的新 workspace 里，把本文当作唯一任务说明。所有路径都是绝对路径或相对 ONTO 根。

---

## 0. 一句话任务

让 EvoOntology 的 mapping 能携带**结构化、按优先级排序的 bindings**（并在 `resolve_semantics` 里返回），让同一个 server 能以**最小 MCP Streamable HTTP** 方式对外提供服务、并支持工具白名单；再补上一条**对照 evo-query catalog 快照校验 binding** 的能力。

---

## 1. 背景与上下游

| 仓库 | 作用 | 本任务是否修改 |
|---|---|---|
| `~/D/EvoOntology` | 语义层：ontology 版本存储、browse/resolve 运行时、stdio MCP server、演进会话 | **只在这里写** |
| `~/D/EvoQuery` | evo-query 服务端（M1-C 已完成，108 单测全绿）。它是 **bindings 的消费方** | 不改，只读文档与源码 |
| `~/D/EvoQueryMockData` | 数据集仓库（M1-B，交接文档 `~/D/EvoQuery/prompt.md`）。它写出的种子 ontology 会带 `bindings` | 不改 |

### 必读文档（都在 `~/D/EvoQuery`）

| 文档 | 读什么 |
|---|---|
| `~/D/EvoQuery/docs/superpowers/plans/2026-10-13-m1a-evoontology-http-bindings.md` | **原始 M1-A 计划**（1104 行，含可直接抄的完整代码）。本文在它之上做了 4 处修订，见 §5 |
| `~/D/EvoQuery/docs/evo-query-contracts.zh-CN.md` §7.1、§7.3、§7.5 | 消费方对 resolve 输出、版本、HTTP 端点的契约要求 |
| `~/D/EvoQuery/docs/evo-query-design.zh-CN.md` §7 | EvoOntology 侧改动清单（M1 行） |
| `~/D/EvoQuery/docs/multi-source-query.zh-CN.md` | D31（核心零依赖）、D35（运行时调 resolve）、D41（种子 ontology）、D42（不提交） |
| `~/D/EvoQuery/evo_query/ontology_client.py` | **最重要的验收对象**：evo-query 怎么调用、怎么解析、什么情况算失败 |
| `~/D/EvoQuery/evo_query/data/engine.py` | bindings 怎么被消费（按 priority 回退、semantic_filter 合并） |
| `~/D/EvoQueryMockData/README.md` | 数据来源（M1-B 会据此写种子 bindings） |

### 消费方到底要什么（直接决定兼容性）

`~/D/EvoQuery/evo_query/ontology_client.py` 里 `HttpOntologyClient`：

1. 用 `mcp` Python SDK 的 streamable HTTP client，`mode="legacy"` 连 `http://host:port/mcp`。
2. 调 `resolve_semantics`，参数形如 `{"mentions": ["t_urgent_sla_event"], "version": "ontology_v0"}`。
3. **从 `content[0].text` 里 `json.loads` 出结果**（不是 `structuredContent`）。
4. `active_version()` 用**一个探测 mentions**（`__evo_query_version_probe__`）取值，只读 **顶层 `version`** 字段；`version` 为空或 `"uninitialized"` 就判 `ONTOLOGY_UNAVAILABLE`。
5. `parse_resolve_response` 只接受 `results[]` 里 **`query_type == "semantic_id"` 且 `term.id` 完全等于请求的 term** 的那一项；每个 `mappings[].bindings[]` 读 `priority / kind / source_id / entity / field / semantic_filter`，`semantic_filter` 里每一项用 evo-query 的 `Filter` 模型校验（`field/op/value`，op ∈ `= != > >= < <= in not_in between like is_null not_null`），**校验失败的 binding 会被静默跳过**。
6. `result.is_error` 为真、或 payload `status == "error"` → `ONTOLOGY_UNAVAILABLE`。

**由此得到的硬要求**：HTTP 必须支持 MCP 的 `initialize` 握手与 `tools/call`；`resolve_semantics` 的返回必须是 `content[0].text` 里的 JSON 字符串；顶层 `version` 必须是**实际使用的版本**；错误必须走 `isError: true`（而不是 HTTP 4xx/5xx，否则客户端看到的是传输错误，虽然也会归到 `ONTOLOGY_UNAVAILABLE`，但信息更差）。

---

## 2. 已核实的现状（2026-10-14，逐项对过源码）

| 项 | 位置 | 现状 |
|---|---|---|
| `Mapping` 模型 | `evoontology/ontology/models.py:82-120` | 只有 `database_source/table/column/semantic_filter: str/...`，**没有 `bindings`**；`from_dict` 是 `data.get(...)` 风格，**多余键被安全忽略** |
| `validate()` | `evoontology/validate.py:19` | 只查文件存在、JSON 合法、跨记录引用；**不碰 bindings**；签名 `validate(root=None, version=None) -> dict`，返回 `{passed, root, version, errors}` |
| `VERSION_FILES` / 族名 | `evoontology/ontology/store.py:31-37` | `terms, relations, mappings, constraints, evidence` → `terms.json/relations.json/mappings.json/constraints.json/evidence.json`；`set_active` 要求版本目录已存在 |
| resolve 输出 | `evoontology/runtime/runtime.py:318-329`（顶层）与 `_build_resolve_result` 的 mapping 视图 | 顶层已有 `version: self.version` ✅（M1-C 的 `active_version()` 就是靠它）；mapping 视图目前**没有** `database_source`（其实有）与 `bindings` |
| MCP server | `evoontology/runtime/mcp_server.py` | 纯 stdio；`__init__(store_path, version="")`；`self.layer` 是**实例属性**（在语义调用里会被重新赋值）；`dispatch(method, params)`；CLI 只有 `--store`、`--version` 两个 flag |
| 工具注册 | `evoontology/runtime/tools.py:31` `TOOLS`、`:79` `OPERATIONS` | `TOOLS` = browse/resolve；`OPERATIONS` = validate_semantics / visualize_ontology / 版本与演进工具；`validate_semantics` 的 inputSchema 只有 `workspace`、`version` |
| ops | `evoontology/runtime/ops.py:39` | `validate_semantics(arguments)` → `validate(str(_workspace(arguments)), version=_version(arguments))` |
| 项目上下文 | `evoontology/workspace.py:11-12` | `PROJECT_SCHEMA_VERSION = 1`；`project.json` 必填 `schema_version/mode/data_source/workload_source/evaluation/boundary`；`data_source` **既可以是字符串**（如 `str(db)`，见 `tests/test_workflow.py:21`）**也可以是 dict**（M1-B 会写 dict） |
| 插件镜像 | `scripts/sync_plugin_core.py:31` | `IGNORE_DIRS = {"__pycache__"}`，`source_files()`/`target_files()` 的跳过条件只查目录；`tests/test_plugin_sync.py` 用**自己的**过滤器（忽略 `.DS_Store`），所以两边不一致 |
| 导出 | `evoontology/__init__.py` | 已导出 `SemanticLayer`、`SemanticStore`、`ensure_workspace` ✅（测试里可直接 `from evoontology import ...`） |

### 2.1 基线（**与原始计划的说法不同，务必以实测为准**）

```
$ python3 -m pytest -q          # 在 ~/D/EvoOntology，用系统 Python 3.11
195 passed, 1 failed
```

- 唯一的失败是 `tests/test_plugin_sync.py::test_plugin_copies_mirror_root_core`，原因是 `evoontology/.DS_Store`（8196 字节，真实存在）被 sync 脚本当作源文件、而测试的过滤器忽略它 → `added=['.DS_Store']`。**Task A4 的 `IGNORE_FILES = {".DS_Store"}` 就是修这个**（比删文件更正确，因为 Finder 会再生成）。
- 原始计划说 `194 passed, 2 failed`、且 `tests/test_preview.py` 会失败 —— **现在 preview 测试是通过的**，别按计划去"修"它。
- **pyproject 的 `dependencies = []` 约束（D31）必须保持**：核心包只用标准库。

---

## 3. 使用原始计划的方式

原计划 `~/D/EvoQuery/docs/superpowers/plans/2026-10-13-m1a-evoontology-http-bindings.md` 的 **Task A0–A4 结构与代码可以直接照做**，它已逐行核实与现状一致。请按它的"先写失败测试 → 实现 → 跑绿"节奏推进：

| 任务 | 内容 | 原计划位置 |
|---|---|---|
| A0 | 建分支 `feat/http-bindings`（**不要 commit**） | 第 33–47 行 |
| A1 | `Mapping.bindings` 归一化（结构化优先，legacy `table`/`column` → 单条 sql binding，free-text `semantic_filter` **不转换**）+ `validate.py` 的 `_binding_errors` | 第 49–365 行 |
| A2 | `resolve` mapping 视图加 `database_source`/`bindings`；server 加 `tools` 白名单、`handle_message()`、`layer` 改成 **property**、`--tools` flag | 第 369–702 行 |
| A3 | `evoontology/runtime/http_transport.py`（stdlib `ThreadingHTTPServer`，`POST /mcp`）+ `--transport/--host/--port` | 第 706–1033 行 |
| A4 | sync 脚本忽略 `.DS_Store`、同步插件副本、补 USAGE 文档、全量测试 | 第 1037–1104 行 |

**原计划里我认为不需要改的关键设计**（已验证正确，别自作主张替换）：

- `handle_message` 里 `request_id = message.get("id")`，为 `None` 即当通知处理、返回 `None` → 这就是"batch 里只有通知返回 202"的实现基础。
- `layer` 改成 property（每次 `SemanticLayer.load`）是**对的**：`tests/test_workflow.py:200` 断言 `server.layer.version == "ontology_v1"`，property 让它天然成立；同时 HTTP 并发请求不再共享 layer，满足"不同版本互不串"。
- `_bindings()` 只做**结构**归一化（priority 缺省按顺序、`semantic_filter` 支持 object 或 list、按 priority 稳定排序），**不做**语义校验——语义校验在 `validate.py`。
- legacy 转换条件 `data.get("table") and data.get("column")`，且 **free-text `semantic_filter` 保留在 `Mapping.semantic_filter` 上、不塞进 binding**（因为它无法安全地结构化应用）。

---

## 4. 相对原计划的三处**新增/修订**（用户已确认）

### 4.1 ★ 新增：`validate` 支持对照 evo-query catalog 快照校验 binding

**为什么**：binding 里写的 `source_id/entity/field` 是**另一个仓库**（数据集）的名字。写错只会在运行时以 `TERM_NOT_BOUND` 暴露，很难排查。M1-B 的种子 ontology 正需要这个门禁。

**catalog 快照的确切格式**（由 evo-query `SourceCatalog.snapshot()` 生成，见 `~/D/EvoQuery/evo_query/data/catalog.py`）：

```json
{
  "catalog_version": "0.1",
  "generated_at": "2026-10-14T00:00:00Z",
  "sources": [
    {
      "source_id": "analyzerleasedlineservicedb",
      "kind": "sql",
      "entities": [
        {"name": "tbl_sla_event", "fields": ["id", "eventId", "severity", "occurTime", "..."]}
      ]
    }
  ]
}
```

注意：`entities[].fields` 是**字符串数组**（不是对象数组）。比较时 `source_id`/`entity`/`field` **大小写敏感、逐字相等**（物理名可能是 `TBL_INV_TRAIL` 这种全大写）。

**怎么做**（建议的最小实现，请自行配测试）：

1. `evoontology/validate.py`：新增模块级函数 `_catalog_errors(raw_mappings, catalog: dict) -> List[str]`，以及一个 `load_catalog(path) -> dict`（JSON 读取 + 基本结构校验：必须有 `sources` 列表，否则报错而不是静默跳过）。
2. `validate()` 签名扩展为 `validate(root=None, version=None, catalog=None)`：
   - `catalog` 可以是 **快照文件的路径（str/Path）** 或**已解析的 dict**（两种都支持，方便测试与 ops 调用）。
   - `catalog is None` 时**尝试自动发现**：读 `<root>/project.json`，若 `data_source` 是 dict 且有 `catalog` 键 → 用该路径（相对路径相对项目根解析）；若 `data_source` 是字符串或是普通 db 路径 → **不校验，静默跳过**（不要报错，`tests/test_workflow.py` 的 fixture 就是字符串）。
   - 校验规则：`bindings[]` 里每条 `kind == "sql"` 的 binding，`source_id` 必须在 `sources[].source_id` 里、`entity` 必须在那个 source 的 `entities[].name` 里、`field` 必须在那个 entity 的 `fields` 里。错误文案要能一眼定位，建议格式：
     `mapping m_urgent: bindings[0].source_id 'analyzerleased' not found in catalog (known: analyzerleasedlineservicedb, ...)`
     `mapping m_urgent: bindings[0].entity 'tbl_sla_events' not found in source 'analyzerleasedlineservicedb'`
     `mapping m_urgent: bindings[0].field 'sevrity' not found in entity 'analyzerleasedlineservicedb.tbl_sla_event'`
   - `kind == "api"` 的 binding 现在**跳过**（catalog 的 API 源不在本次范围）。
   - 返回值保持向后兼容：新增 `"catalog": {"path": ..., "checked": N}` 之类的信息字段是允许的，但**不要改动 `passed`/`errors` 的语义**。
3. `evoontology/runtime/ops.py`：`validate_semantics(arguments)` 读可选 `arguments.get("catalog")`（字符串），透传给 `validate(..., catalog=...)`。
4. `evoontology/runtime/tools.py`：`validate_semantics` 的 inputSchema 加可选 `catalog`：
   ```python
   "catalog": {
       "type": "string",
       "description": (
           "Optional path to an evo-query catalog.json snapshot. When given (or "
           "discovered from project.json), every sql binding's source_id/entity/field "
           "is checked against the real data catalog."
       ),
   },
   ```
5. **不要**把 catalog 校验塞进 `validate()` 的必填路径——没有 catalog 的旧工作区必须继续 `passed: true`。

### 4.2 ★ 新增：更新 `resolve_semantics` 的工具描述

`resolve_semantics` 的返回里多了 `database_source` 与 `bindings`，Data Agent 需要知道可以读它。在 `evoontology/runtime/tools.py` 里改它的 `description`（**`inputSchema` 不要动**）：

> 明确"每个 mapping 会带上 `bindings`（按 `priority` 升序），每条 binding 给出 `kind`/`source_id`/`entity`/`field` 与结构化 `semantic_filter`；`database_source`/`table`/`column` 是等价的历史字段，**优先按 `bindings` 执行**"。

**`browse_semantics` 不要改描述**——已核实它走的是另一条投影路径：`SemanticLayer.browse()`（`runtime.py:155`）对每个记录用 `_browse_view()`（`runtime.py:222`）生成 `{id, name, type, scope, availability}`，**不含 mapping 的绑定信息**，与 `_build_resolve_result()` 无关。所以不要在 browse 的描述里承诺 `bindings`，否则描述与实现不符。

⚠️ 这处 description 是**对外可见文案**：改完要在 `tests/test_mcp_server.py`（或新测试）里断言 `resolve_semantics` 的 description 含 `bindings` 字样，免得以后被回退。

### 4.3 ★ 新增：HTTP 端点要用 `evoontology-mock` 做联动验证

`~/D/EvoQuery` 会话里挂了一个 **`evoontology-mock` MCP server**（工具：`browse_semantics`、`resolve_semantics`、`save_version`、`set_active_version`、`validate_semantics`、`configure_ontology_project`、`visualize_ontology`、演进会话工具等），它跑的就是 ONTO 这份代码。M1-A 完成后请**实际起一次 HTTP 服务并打通**，作为端到端证据：

```bash
# 1) 起 HTTP 服务（注意：URL 路径是 /mcp）
cd ~/D/EvoOntology
python3 -m evoontology.runtime.mcp_server --transport http --host 127.0.0.1 --port 8765 \
    --store <某个已有 ontology_v0 的 workspace> \
    --tools browse_semantics,resolve_semantics

# 2) 三步握手 + 工具列表（urllib/curl 都行；注意 initialize 之后才 tools/list）
curl -s -X POST http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-06","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
curl -s -X POST http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
curl -s -X POST http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"resolve_semantics","arguments":{"mentions":["t_urgent_sla_event"]}}}'
```

**最终验收（最有价值的一条）**：用 evo-query 的**真实客户端**打这个端点。

```bash
cd ~/D/EvoQuery
UV_CACHE_DIR=$PWD/.uvcache uv run python -c "
import asyncio
from evo_query.ontology_client import HttpOntologyClient
c = HttpOntologyClient('http://127.0.0.1:8765/mcp', timeout_s=5)
print('active_version ->', asyncio.run(c.active_version()))
print('resolve ->', asyncio.run(c.resolve_term('t_urgent_sla_event', 'ontology_v0')))
"
```

期望：`active_version()` 返回真实版本号（不是 `ONTOLOGY_UNAVAILABLE`）；`resolve_term` 返回的 `TermResolution.bindings` 里能看到 `source_id/entity/field/semantic_filter`，且 `kind == "sql"`。**这条一旦通过，M1-C 的 Phase 2（按 term 查询）就真正活了。**

> 环境提示：若该 workspace 是本机且系统代理劫持了 `127.0.0.1`，给 evo-query 那次调用加 `NO_PROXY=127.0.0.1,localhost`。

---

## 5. 与原计划的其余偏差 / 注意事项

1. **基线不同**（§2.1）：`195 passed, 1 failed`，`test_preview.py` 是通过的；唯一失败由 `.DS_Store` 引起，Task A4 修。
2. **测试命令**：原计划用 `uv run --no-project --python 3.10 --with "pytest>=8" python -m pytest ...`。**已实测可行**（`python3.10` 与 `uv` 都在），但前提是 `uv` 缓存可写 —— 在受限沙箱里需要 `UV_CACHE_DIR=<可写目录>`。若该环境不便，可退化为**系统 Python 3.11 + 已装 pytest**：`python3 -m pytest -q`（基线就是这么跑出来的）。核心包本来就是零依赖，两种方式等价。
3. **`tests/test_workflow.py:200`**：断言 `server.layer.version == "ontology_v1"`。把 `layer` 从实例属性改成 property 后**仍然成立**，但请把这条测试跑一遍确认（原计划 Step 5 已包含 `tests/test_workflow.py`）。
4. **`USAGE.md` 的插入点**（原计划只说"section 6 末尾"）：
   - `USAGE.md`：`## 6. MCP integration` 从第 127 行开始，**在第 141 行的 `---` 之前**插入新的 `### Serving over HTTP` 小节。
   - `USAGE.zh-CN.md`：`## 6. MCP 接入` 从第 159 行开始，**在第 177 行的 `---` 之前**插入 `### 通过 HTTP 提供服务`。
   （行号是 2026-10-14 的实测值，改动前请再确认一次。）
5. **`--tools` 是启动期校验**：白名单里出现不存在的工具要在**启动时**报错退出（`parser.error`），不要静默暴露空列表。
6. **会话/协议**：不加 `Mcp-Session-Id`（无状态）。evo-query 用 `mode="legacy"` 连，**不要**要求 `MCP-Protocol-Version` 之类的头；`initialize` 必须按请求里的 `protocolVersion` 原样回（现有 `dispatch` 已这么做，保留）。
7. **不要 commit**（D42）。每个任务结束只 `git status --short`。
8. **同步插件副本**：任何 `evoontology/` 下的改动之后都要跑 `python3 scripts/sync_plugin_core.py`，否则 `tests/test_plugin_sync.py` 会失败。新文件 `http_transport.py` 也会被同步过去（预期）。
9. `pyproject.toml` 的 `dependencies = []` 不能变（D31）。新增的 HTTP 传输必须只用标准库（`http.server`）。

---

## 6. 验收标准

### 6.1 必跑命令

```bash
cd ~/D/EvoOntology
git switch -c feat/http-bindings            # A0

# 每个任务后
python3 -m pytest tests/test_bindings.py tests/test_validate.py tests/test_store.py -q          # A1
python3 -m pytest tests/test_bindings.py tests/test_mcp_server.py tests/test_runtime.py tests/test_workflow.py -q   # A2
python3 -m pytest tests/test_mcp_http.py tests/test_mcp_server.py -q                            # A3

# A4
python3 scripts/sync_plugin_core.py
python3 -m pytest -q                        # 期望 全部通过（含 test_plugin_sync.py）
```

### 6.2 新增测试（对应 §4 的三处修订）

| 测试 | 断言 |
|---|---|
| `tests/test_validate.py`（追加）| 带 `catalog=` 且 binding 全部命中 → `passed: True`；`source_id`/`entity`/`field` 任一写错 → `passed: False` 且错误文案含写错的那个名字与"not found" |
| 同上 | `catalog=None` 且工作区没有 `project.json` → 行为与改动前**完全一致**（不因缺 catalog 而失败） |
| 同上 | `project.json` 的 `data_source` 是**字符串**（如 `tests/test_workflow.py` 的 fixture）→ 自动发现跳过、不报错 |
| `tests/test_validate.py`（追加）| `kind: "api"` 的 binding 在 catalog 校验下被跳过 |
| `tests/test_mcp_server.py`（追加）| `resolve_semantics` 的 description 含 `bindings`（防回退）；`inputSchema` 与改动前一致 |
| `tests/test_mcp_http.py`（沿用计划）| 含 §4.3 的握手用例：`initialize` → `tools/list` → `tools/call resolve_semantics`，且 `isError` 为 false |

### 6.3 跨仓库验收（最终证据）

按 §4.3 的最后一段，用 `evo_query.ontology_client.HttpOntologyClient` 打通 `active_version()` 与 `resolve_term()`。**这是 M1-A 唯一真正重要的验收**：M1-C 的 term 查询链路依赖它。

### 6.4 不要破坏的东西

- stdio 行为对已有客户端（Claude Code / Codex 插件、dsh 的 `evoontology-mcp`）**逐字节兼容**：不认识的 method 仍回 `-32601`、非对象 JSON 行仍回 `-32603` 且 `id: null`、通知仍不回包。
- `python3 -m evoontology.runtime.mcp_server`（不带任何新 flag）行为不变。
- `tests/` 现有 196 个用例中，除 `.DS_Store` 那条外全部继续通过。

---

## 7. 需要向用户确认 / 待补的信息

1. **catalog 快照从哪来**：M1-B 会在 `~/D/EvoQueryMockData/evoquery/catalog.json` 生成它（`generator/catalog.py` 的职责）。在该文件出现之前，M1-A 的 catalog 校验只能用**测试里的临时快照**验证。请确认这是可接受的顺序。
2. **`kind: "api"` 的 binding 何时需要校验**：M1-B 会生成 `evoquery/sources-api/*.yaml`（`kind: api`，统一存量的 3 个 class），但 evo-query 的 catalog 快照**目前不包含 API 源**（M1-C 只支持 `kind: sql`）。所以本次 API binding 一律跳过校验。若你希望 API 也纳入，需要先让 evo-query 支持 `kind: api`（M2）。
3. **探测 terms 的约定**：evo-query 的 `active_version()` 会发一个 `mentions=["__evo_query_version_probe__"]` 的请求。**这个 term 永远不会存在**，所以它会走到 `unresolved` 分支——只要顶层 `version` 照常返回就没问题。请确认"未初始化的 workspace"（没有 `active.json`）返回的是**错误**（⇒ `ONTOLOGY_UNAVAILABLE`，M1-C 会记为 `ontology_version: null` 并给 warning，下次 `start_turn` 再试），而不是把 `version` 填成 `"uninitialized"`。当前 `mcp_server.dispatch` 在 store 加载失败时返回 `{"status":"error"}` + `isError` —— 保持这个行为即可。
4. **`--tools` 的默认值**：默认（不带该 flag）**暴露全部工具**，以保持对现有插件的兼容；M1-D 的 dsh profile 会显式传 `--tools browse_semantics,resolve_semantics`。请确认这个默认值符合你的预期。
5. **端口/路径**：默认 `127.0.0.1:8765`、路径 `/mcp`（D42 与契约 §7.5）。若与你的环境冲突请提前说。

---

## 8. 工作纪律

- **不要 commit**（D42）；每步只 `git status --short`。
- 改完 `evoontology/**` **必须**跑 `python3 scripts/sync_plugin_core.py`。
- 不要引入第三方依赖（D31）。
- 不要改用户资产：`*.xml`、`*.md` 字典、`~/D/EvoQuery` 与 `~/D/EvoQueryMockData` 里的任何文件。
- 完成后把结果写进 `~/D/EvoQuery/PROGRESS.md` 的 M1-A 一行（或在本仓库写报告并告知），说明与本文/原计划的偏差。
