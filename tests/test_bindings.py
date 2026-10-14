"""Structured mapping bindings: normalization, legacy conversion, validation."""

import json

from evoontology import SemanticLayer, SemanticStore, ensure_workspace
from evoontology.ontology.models import Mapping
from evoontology.validate import validate

CATALOG = {
    "catalog_version": "0.1",
    "generated_at": "2026-10-14T00:00:00Z",
    "sources": [
        {
            "source_id": "analyzerleasedlineservicedb",
            "kind": "sql",
            "entities": [
                {"name": "tbl_sla_event", "fields": ["id", "eventId", "severity", "occurTime"]}
            ],
        },
        {
            "source_id": "TBL_INV_TRAIL",
            "kind": "sql",
            "entities": [{"name": "TBL_INV_TRAIL", "fields": ["ID", "AMOUNT"]}],
        },
    ],
}


def _records(mappings):
    return {
        "terms": [{"id": "t_urgent", "name": "urgent event", "type": "entity", "definition": "Urgent SLA event"}],
        "mappings": mappings,
        "relations": [],
        "constraints": [],
        "evidence": [],
    }


def _sql_binding(**overrides):
    binding = {
        "priority": 1,
        "kind": "sql",
        "source_id": "analyzerleasedlineservicedb",
        "entity": "tbl_sla_event",
        "field": "severity",
    }
    binding.update(overrides)
    return binding


def _write_snapshot(path, catalog=CATALOG):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --- normalization -------------------------------------------------------


def test_structured_bindings_are_sorted_and_normalized():
    mapping = Mapping.from_dict(
        {
            "id": "m1",
            "term_id": "t_urgent",
            "bindings": [
                {"priority": 2, "kind": "sql", "source_id": "db2", "entity": "events", "field": "sev"},
                {
                    "priority": 1,
                    "kind": "sql",
                    "source_id": "db1",
                    "entity": "tbl_sla_event",
                    "field": "severity",
                    "semantic_filter": {"field": "severity", "op": "=", "value": 1},
                },
            ],
        }
    )

    assert [b["source_id"] for b in mapping.bindings] == ["db1", "db2"]
    assert mapping.bindings[0]["semantic_filter"] == [{"field": "severity", "op": "=", "value": 1}]
    assert mapping.bindings[1]["semantic_filter"] == []


def test_legacy_mapping_becomes_single_sql_binding():
    mapping = Mapping.from_dict(
        {
            "id": "m_customer",
            "term_id": "t_customer",
            "database_source": "amazon_crm.db",
            "table": "customers",
            "column": "customer_id",
            "semantic_filter": "status = 'active'",
        }
    )

    assert mapping.bindings == [
        {
            "priority": 1,
            "kind": "sql",
            "source_id": "amazon_crm.db",
            "entity": "customers",
            "field": "customer_id",
            "semantic_filter": [],
        }
    ]
    assert mapping.semantic_filter == "status = 'active'"


def test_mapping_without_location_has_no_bindings():
    assert Mapping.from_dict({"id": "m0", "term_id": "t0"}).bindings == []


def test_missing_priority_falls_back_to_position():
    mapping = Mapping.from_dict(
        {
            "id": "m1",
            "term_id": "t_urgent",
            "bindings": [
                {"kind": "sql", "source_id": "db1", "entity": "e", "field": "f"},
                {"kind": "sql", "source_id": "db2", "entity": "e", "field": "f"},
            ],
        }
    )

    assert [b["priority"] for b in mapping.bindings] == [1, 2]


# --- structural validation ----------------------------------------------


def test_validate_accepts_structured_bindings(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_urgent",
                    "term_id": "t_urgent",
                    "bindings": [
                        {
                            "priority": 1,
                            "kind": "sql",
                            "source_id": "analyzerleasedlineservicedb",
                            "entity": "tbl_sla_event",
                            "field": "severity",
                            "semantic_filter": [{"field": "severity", "op": "=", "value": 1}],
                        }
                    ],
                }
            ]
        ),
    )

    report = validate(str(tmp_path), version="ontology_v0")

    assert report["passed"] is True, report["errors"]


def test_validate_reports_malformed_bindings(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_bad",
                    "term_id": "t_urgent",
                    "bindings": [
                        {
                            "priority": 0,
                            "kind": "ftp",
                            "source_id": "db",
                            "entity": "",
                            "field": "severity",
                            "semantic_filter": [{"field": "severity", "op": "~"}, {"field": "x", "op": "="}],
                        }
                    ],
                }
            ]
        ),
    )

    report = validate(str(tmp_path), version="ontology_v0")
    errors = "\n".join(report["errors"])

    assert report["passed"] is False
    assert "bindings[0].priority must be a positive integer" in errors
    assert "bindings[0].kind must be one of" in errors
    assert "bindings[0].entity must be a non-empty string" in errors
    assert "semantic_filter[0].op must be one of" in errors
    assert "semantic_filter[1].value is required" in errors


def test_validate_rejects_non_list_bindings(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_bad", "term_id": "t_urgent", "bindings": {"kind": "sql"}}])
    )

    report = validate(str(tmp_path), version="ontology_v0")

    assert "mapping m_bad: bindings must be a list" in report["errors"]


# --- catalog snapshot validation (revision 4.1) --------------------------


def test_catalog_accepts_bindings_that_hit(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_urgent", "term_id": "t_urgent", "bindings": [_sql_binding()]}])
    )
    snapshot = _write_snapshot(tmp_path / "catalog.json")

    report = validate(str(tmp_path), version="ontology_v0", catalog=str(snapshot))

    assert report["passed"] is True, report["errors"]
    assert report["catalog"]["checked"] == 1


def test_catalog_accepts_a_parsed_dict(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_urgent", "term_id": "t_urgent", "bindings": [_sql_binding()]}])
    )

    report = validate(str(tmp_path), version="ontology_v0", catalog=CATALOG)

    assert report["passed"] is True, report["errors"]


def test_catalog_is_case_sensitive(tmp_path):
    """Physical names may be all-caps; comparison stays literal."""
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_inv",
                    "term_id": "t_urgent",
                    "bindings": [
                        _sql_binding(source_id="TBL_INV_TRAIL", entity="TBL_INV_TRAIL", field="AMOUNT")
                    ],
                }
            ]
        ),
    )

    report = validate(str(tmp_path), version="ontology_v0", catalog=CATALOG)

    assert report["passed"] is True, report["errors"]


def test_catalog_reports_every_missed_level(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_urgent",
                    "term_id": "t_urgent",
                    "bindings": [
                        _sql_binding(source_id="analyzerleased"),
                        _sql_binding(priority=2, entity="tbl_sla_events"),
                        _sql_binding(priority=3, field="sevrity"),
                    ],
                }
            ]
        ),
    )
    snapshot = _write_snapshot(tmp_path / "catalog.json")

    report = validate(str(tmp_path), version="ontology_v0", catalog=str(snapshot))
    errors = "\n".join(report["errors"])

    assert report["passed"] is False
    assert "mapping m_urgent: bindings[0].source_id 'analyzerleased' not found in catalog" in errors
    assert "analyzerleasedlineservicedb" in errors
    assert (
        "mapping m_urgent: bindings[1].entity 'tbl_sla_events' not found in source "
        "'analyzerleasedlineservicedb'" in errors
    )
    assert (
        "mapping m_urgent: bindings[2].field 'sevrity' not found in entity "
        "'analyzerleasedlineservicedb.tbl_sla_event'" in errors
    )
    assert "not found" in errors


def test_catalog_skips_api_bindings(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_api",
                    "term_id": "t_urgent",
                    "bindings": [
                        {
                            "priority": 1,
                            "kind": "api",
                            "source_id": "inventory_api",
                            "entity": "StockLevel",
                            "field": "on_hand",
                        }
                    ],
                }
            ]
        ),
    )

    report = validate(str(tmp_path), version="ontology_v0", catalog=CATALOG)

    assert report["passed"] is True, report["errors"]
    # `api` sources are out of scope for this catalog; nothing was checked.
    assert report["catalog"]["checked"] == 0


def test_catalog_without_sources_is_an_error_not_a_silent_pass(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_urgent", "term_id": "t_urgent", "bindings": [_sql_binding()]}])
    )
    snapshot = _write_snapshot(tmp_path / "catalog.json", {"catalog_version": "0.1"})

    report = validate(str(tmp_path), version="ontology_v0", catalog=str(snapshot))

    assert report["passed"] is False
    assert "sources" in "\n".join(report["errors"])


def test_missing_catalog_file_is_not_an_error(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_urgent", "term_id": "t_urgent", "bindings": [_sql_binding()]}])
    )

    report = validate(str(tmp_path), version="ontology_v0", catalog=str(tmp_path / "absent.json"))

    assert report["passed"] is True, report["errors"]


# --- catalog discovery from project.json (revision 4.1) ------------------


def _project(data_source):
    return {
        "schema_version": 1,
        "mode": "fixed_split",
        "data_source": data_source,
        "workload_source": {"type": "project_workload"},
        "evaluation": {"type": "llm_judge"},
        "boundary": {"strategy": "fixed_split"},
    }


def _workspace_with_mapping(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path, "ontology_v0", _records([{"id": "m_urgent", "term_id": "t_urgent", "bindings": [_sql_binding()]}])
    )
    SemanticStore.set_active(tmp_path, "ontology_v0")


def test_project_json_dict_data_source_discovers_the_catalog(tmp_path):
    _workspace_with_mapping(tmp_path)
    _write_snapshot(tmp_path / "evoquery" / "catalog.json")
    (tmp_path / "project.json").write_text(
        json.dumps(_project({"database": "analyzer.db", "catalog": "evoquery/catalog.json"})),
        encoding="utf-8",
    )

    report = validate(str(tmp_path))

    assert report["passed"] is True, report["errors"]
    assert report["catalog"]["checked"] == 1


def test_string_data_source_skips_discovery_without_failing(tmp_path):
    """A legacy project.json whose data_source is a plain string must not fail."""
    _workspace_with_mapping(tmp_path)
    (tmp_path / "project.json").write_text(
        json.dumps(_project(str(tmp_path / "sales.sqlite"))), encoding="utf-8"
    )

    report = validate(str(tmp_path))

    assert report["passed"] is True, report["errors"]
    assert not report.get("catalog")


def test_missing_project_json_skips_discovery_without_failing(tmp_path):
    _workspace_with_mapping(tmp_path)

    report = validate(str(tmp_path))

    assert report["passed"] is True, report["errors"]
    assert not report.get("catalog")


def test_discovered_catalog_with_a_missed_binding_fails(tmp_path):
    _workspace_with_mapping(tmp_path)
    _write_snapshot(tmp_path / "catalog.json")
    (tmp_path / "versions" / "ontology_v0" / "mappings.json").write_text(
        json.dumps(
            [
                {
                    "id": "m_urgent",
                    "term_id": "t_urgent",
                    "bindings": [_sql_binding(field="sevrity")],
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "project.json").write_text(
        json.dumps(_project({"database": "analyzer.db", "catalog": "catalog.json"})), encoding="utf-8"
    )

    report = validate(str(tmp_path))

    assert report["passed"] is False
    assert "not found" in "\n".join(report["errors"])


# --- resolve returns bindings (task A2) ---------------------------------


def test_resolve_by_term_id_returns_bindings_and_version(tmp_path):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(
        tmp_path,
        "ontology_v0",
        _records(
            [
                {
                    "id": "m_urgent",
                    "term_id": "t_urgent",
                    "bindings": [
                        {
                            "priority": 1,
                            "kind": "sql",
                            "source_id": "analyzerleasedlineservicedb",
                            "entity": "tbl_sla_event",
                            "field": "severity",
                            "semantic_filter": {"field": "severity", "op": "=", "value": 1},
                        }
                    ],
                }
            ]
        ),
    )
    SemanticStore.set_active(tmp_path, "ontology_v0")

    response = SemanticLayer.load(str(tmp_path)).resolve(mentions=["t_urgent"])

    assert response["version"] == "ontology_v0"
    result = response["results"][0]
    assert result["query_type"] == "semantic_id"
    assert result["term"]["id"] == "t_urgent"
    assert result["mappings"][0]["database_source"] == ""
    assert result["mappings"][0]["bindings"] == [
        {
            "priority": 1,
            "kind": "sql",
            "source_id": "analyzerleasedlineservicedb",
            "entity": "tbl_sla_event",
            "field": "severity",
            "semantic_filter": [{"field": "severity", "op": "=", "value": 1}],
        }
    ]


def test_resolve_legacy_mapping_exposes_one_sql_binding(tmp_path):
    ensure_workspace(tmp_path)
    records = _records(
        [
            {
                "id": "m_customer",
                "term_id": "t_customer",
                "database_source": "amazon_crm.db",
                "table": "customers",
                "column": "customer_id",
            }
        ]
    )
    records["terms"].append(
        {"id": "t_customer", "name": "customer", "type": "entity", "definition": "A customer"}
    )
    SemanticStore.save_version(tmp_path, "ontology_v0", records)
    SemanticStore.set_active(tmp_path, "ontology_v0")

    response = SemanticLayer.load(str(tmp_path)).resolve(mentions=["t_customer"])

    mapping = response["results"][0]["mappings"][0]
    assert mapping["database_source"] == "amazon_crm.db"
    assert mapping["bindings"][0]["source_id"] == "amazon_crm.db"
    assert mapping["bindings"][0]["kind"] == "sql"
