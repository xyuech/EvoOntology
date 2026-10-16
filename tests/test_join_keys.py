"""Relation join paths (spec §7): normalization, resolve output, browse, validation, display."""

import json

from evoontology import SemanticLayer, SemanticStore, ensure_workspace
from evoontology.ontology.models import Relation
from evoontology.validate import validate

SOURCE = "availabilityvaluedb"


def _end(entity, field, source_id=SOURCE):
    return {"source_id": source_id, "entity": entity, "field": field}


ALARM_ON_TRAIL = {
    "id": "r_alarm_on_trail",
    "source": "t_alarm",
    "target": "t_trail",
    "relation_type": "association",
    "description": "告警影响的业务",
    "inverse_description": "业务上的告警",
    "connection_condition": (
        "t_alarm_info.link_unique_id 对应 t_trail_link_relation.link_unique_id；"
        "t_trail_link_relation.trail_id 对应 t_trail_info.uuid"
    ),
    "join_keys": [
        {"left": _end("t_alarm_info", "link_unique_id"),
         "right": _end("t_trail_link_relation", "link_unique_id")},
        {"left": _end("t_trail_link_relation", "trail_id"),
         "right": _end("t_trail_info", "uuid")},
    ],
}

CATALOG = {
    "sources": [
        {
            "source_id": SOURCE,
            "kind": "sql",
            "entities": [
                {"name": "t_alarm_info", "fields": ["id", "unique_id", "link_unique_id"]},
                {"name": "t_trail_link_relation", "fields": ["id", "trail_id", "link_unique_id"]},
                {"name": "t_trail_info", "fields": ["id", "uuid", "name"]},
            ],
        }
    ]
}


def _copy(record):
    return json.loads(json.dumps(record, ensure_ascii=False))


def _records(relations):
    def binding(entity, field):
        return {"priority": 1, "kind": "sql", **_end(entity, field)}

    return {
        "terms": [
            {"id": "t_alarm", "name": "告警", "type": "entity", "definition": "告警记录"},
            {"id": "t_trail", "name": "业务", "type": "entity", "definition": "业务"},
        ],
        "mappings": [
            {"id": "m_alarm", "term_id": "t_alarm", "bindings": [binding("t_alarm_info", "unique_id")]},
            {"id": "m_trail", "term_id": "t_trail", "bindings": [binding("t_trail_info", "uuid")]},
        ],
        "relations": relations,
        "constraints": [],
        "evidence": [],
    }


def _workspace(tmp_path, relations):
    ensure_workspace(tmp_path)
    SemanticStore.save_version(tmp_path, "ontology_v1", _records(relations))
    SemanticStore.set_active(tmp_path, "ontology_v1")
    return tmp_path


# --- normalization ---------------------------------------------------------


def test_join_keys_keep_their_walking_order():
    relation = Relation.from_dict(ALARM_ON_TRAIL)

    assert [hop["left"]["entity"] for hop in relation.join_keys] == [
        "t_alarm_info", "t_trail_link_relation"]
    assert relation.join_keys[1]["right"] == _end("t_trail_info", "uuid")
    assert relation.join_keys[0]["transform"] is None
    assert relation.inverse_description == "业务上的告警"


def test_relation_without_join_keys_has_an_empty_path():
    relation = Relation.from_dict({"id": "r_old", "source": "a", "target": "b"})

    assert relation.join_keys == []
    assert relation.inverse_description == ""


def test_malformed_hops_are_skipped_not_fatal():
    relation = Relation.from_dict({"id": "r", "join_keys": ["oops", {"left": {"entity": "e"}}]})

    assert relation.join_keys == [
        {"left": {"source_id": "", "entity": "e", "field": ""},
         "right": {"source_id": "", "entity": "", "field": ""},
         "transform": None}
    ]
