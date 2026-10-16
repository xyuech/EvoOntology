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


# --- resolve and browse ----------------------------------------------------


def test_resolve_returns_the_join_path_from_either_end(tmp_path):
    layer = SemanticLayer.load(str(_workspace(tmp_path, [ALARM_ON_TRAIL])))

    for term_id in ("t_alarm", "t_trail"):
        (relation,) = layer.resolve(mentions=[term_id])["results"][0]["relations"]
        assert relation["description"] == "告警影响的业务"
        assert relation["inverse_description"] == "业务上的告警"
        assert relation["join_keys"][0]["left"] == _end("t_alarm_info", "link_unique_id")
        assert relation["join_keys"][1]["right"] == _end("t_trail_info", "uuid")


def test_browse_finds_a_relation_by_its_inverse_reading(tmp_path):
    layer = SemanticLayer.load(str(_workspace(tmp_path, [ALARM_ON_TRAIL])))

    (item,) = layer.browse(query="业务上的告警", kind="relation")["items"]

    assert item["id"] == "r_alarm_on_trail"
    assert item["name"] == "告警影响的业务"


def test_resolve_tool_description_explains_the_join_path():
    from evoontology.runtime.tools import TOOLS

    tool = next(tool for tool in TOOLS if tool["name"] == "resolve_semantics")

    assert "join_keys" in tool["description"]
    assert "inverse_description" in tool["description"]


# --- validation ------------------------------------------------------------


def _errors(tmp_path, relation):
    report = validate(str(_workspace(tmp_path, [relation])), version="ontology_v1", catalog=CATALOG)
    return report, "\n".join(report["errors"])


def test_validate_accepts_a_two_hop_path(tmp_path):
    report, errors = _errors(tmp_path, ALARM_ON_TRAIL)

    assert report["passed"] is True, errors
    assert report["catalog"]["checked_join_keys"] == 4


def test_validate_reports_a_path_that_breaks(tmp_path):
    relation = _copy(ALARM_ON_TRAIL)
    relation["join_keys"][1]["left"] = _end("t_ne_info", "uuid")

    _, errors = _errors(tmp_path, relation)

    assert (
        "relation r_alarm_on_trail: join_keys[1].left.entity 't_ne_info' does not continue "
        "from join_keys[0].right.entity 't_trail_link_relation'" in errors
    )


def test_validate_reports_an_end_that_misses_the_catalog(tmp_path):
    relation = _copy(ALARM_ON_TRAIL)
    relation["join_keys"][1]["right"] = _end("t_trail_info", "uid")

    _, errors = _errors(tmp_path, relation)

    assert (
        "relation r_alarm_on_trail: join_keys[1].right.field 'uid' not found in entity "
        "'availabilityvaluedb.t_trail_info'" in errors
    )


def test_validate_anchors_the_path_to_both_terms(tmp_path):
    relation = _copy(ALARM_ON_TRAIL)
    relation["source"], relation["target"] = "t_trail", "t_alarm"

    _, errors = _errors(tmp_path, relation)

    assert (
        "relation r_alarm_on_trail: join_keys[0].left 'availabilityvaluedb.t_alarm_info' "
        "is not a table of source term 't_trail'" in errors
    )
    assert (
        "relation r_alarm_on_trail: join_keys[1].right 'availabilityvaluedb.t_trail_info' "
        "is not a table of target term 't_alarm'" in errors
    )


def test_validate_rejects_a_transform(tmp_path):
    relation = _copy(ALARM_ON_TRAIL)
    relation["join_keys"][0]["transform"] = "name_to_id"

    _, errors = _errors(tmp_path, relation)

    assert (
        "relation r_alarm_on_trail: join_keys[0].transform is not supported yet; "
        "omit it or use null" in errors
    )


def test_validate_rejects_malformed_join_keys(tmp_path):
    not_a_list = _copy(ALARM_ON_TRAIL)
    not_a_list["join_keys"] = {"left": {}}
    _, errors = _errors(tmp_path / "a", not_a_list)
    assert "relation r_alarm_on_trail: join_keys must be a list" in errors

    bad_ends = _copy(ALARM_ON_TRAIL)
    bad_ends["join_keys"] = [
        {"left": _end("t_alarm_info", "link_unique_id", source_id=""), "right": "x"}
    ]
    _, errors = _errors(tmp_path / "b", bad_ends)
    assert "relation r_alarm_on_trail: join_keys[0].left.source_id must be a non-empty string" in errors
    assert "relation r_alarm_on_trail: join_keys[0].right must be an object" in errors


def test_relations_without_join_keys_still_pass(tmp_path):
    legacy = {"id": "r_old", "source": "t_alarm", "target": "t_trail",
              "relation_type": "association", "connection_condition": "text only"}

    report, errors = _errors(tmp_path, legacy)

    assert report["passed"] is True, errors
    assert report["catalog"]["checked_join_keys"] == 0
