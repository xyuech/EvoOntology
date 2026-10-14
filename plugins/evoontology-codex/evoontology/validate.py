"""Deterministic validation of a versioned ontology workspace.

Checks that a named or active version exists, its five record files are valid
JSON, every cross-record reference resolves, and the store can be loaded by
the runtime. A named inactive version can therefore be validated before
``active.json`` is changed.

When an evo-query catalog snapshot is available -- passed explicitly or
discovered through ``project.json`` -- every ``sql`` binding is additionally
checked against the real source/entity/field names, so a binding that names a
column the data does not have fails the gate instead of surfacing later as a
runtime ``TERM_NOT_BOUND``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from evoontology.ontology.models import BINDING_KINDS, FILTER_OPS
from evoontology.ontology.store import VERSION_FILES, SemanticStore
from evoontology.workspace import PathLike, resolve_workspace, validate_path_component


def _binding_errors(obj_id: str, item: Dict[str, Any]) -> List[str]:
    """Structural checks for a mapping's optional ``bindings`` list."""
    raw = item.get("bindings")
    if raw is None:
        return []
    label = f"mapping {obj_id}"
    if not isinstance(raw, list):
        return [f"{label}: bindings must be a list"]
    errors: List[str] = []
    for index, binding in enumerate(raw):
        where = f"{label}: bindings[{index}]"
        if not isinstance(binding, dict):
            errors.append(f"{where} must be an object")
            continue
        priority = binding.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            errors.append(f"{where}.priority must be a positive integer")
        if binding.get("kind") not in BINDING_KINDS:
            errors.append(f"{where}.kind must be one of {list(BINDING_KINDS)}")
        for key in ("source_id", "entity", "field"):
            value = binding.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{where}.{key} must be a non-empty string")
        conditions = binding.get("semantic_filter", [])
        if isinstance(conditions, dict):
            conditions = [conditions]
        if not isinstance(conditions, list):
            errors.append(f"{where}.semantic_filter must be an object or a list of objects")
            continue
        for c_index, condition in enumerate(conditions):
            c_where = f"{where}.semantic_filter[{c_index}]"
            if not isinstance(condition, dict):
                errors.append(f"{c_where} must be an object")
                continue
            if not isinstance(condition.get("field"), str) or not condition.get("field"):
                errors.append(f"{c_where}.field must be a non-empty string")
            op = condition.get("op")
            if op not in FILTER_OPS:
                errors.append(f"{c_where}.op must be one of {sorted(FILTER_OPS)}")
            elif op not in ("is_null", "not_null") and "value" not in condition:
                errors.append(f"{c_where}.value is required for op {op!r}")
    return errors


def load_catalog(path: PathLike) -> Dict[str, Any]:
    """Read and structurally check an evo-query catalog snapshot.

    The snapshot is produced by evo-query's ``SourceCatalog.snapshot()``:
    ``{"sources": [{"source_id", "kind", "entities": [{"name", "fields"}]}]}``
    where ``fields`` is a list of names.
    """
    catalog_path = Path(path).expanduser()
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid catalog JSON in {catalog_path}: {exc}") from exc
    if not isinstance(catalog, dict):
        raise ValueError(f"Catalog {catalog_path} must contain a JSON object")
    sources = catalog.get("sources")
    if not isinstance(sources, list):
        raise ValueError(f"Catalog {catalog_path} must contain a 'sources' list")
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            raise ValueError(f"Catalog {catalog_path}: sources[{index}] must be an object with source_id")
        entities = source.get("entities")
        if not isinstance(entities, list):
            raise ValueError(
                f"Catalog {catalog_path}: sources[{index}] must contain an 'entities' list"
            )
    return catalog


def _catalog_errors(
    raw_mappings: Dict[str, dict], catalog: Dict[str, Any]
) -> tuple[List[str], int]:
    """Check every ``sql`` binding against the catalog's real names.

    Returns ``(errors, checked)``; ``api`` bindings are out of scope and are
    counted as neither. Comparison is case-sensitive and literal, because
    physical names are (``TBL_INV_TRAIL``).
    """
    sources: Dict[str, Dict[str, set]] = {}
    for source in catalog.get("sources", []):
        entities: Dict[str, set] = {}
        for entity in source.get("entities", []):
            if not isinstance(entity, dict):
                continue
            fields = entity.get("fields")
            entities[str(entity.get("name", ""))] = {
                str(name) for name in (fields if isinstance(fields, list) else [])
            }
        sources[str(source.get("source_id", ""))] = entities

    known_sources = sorted(sources)
    errors: List[str] = []
    checked = 0
    for obj_id, item in raw_mappings.items():
        bindings = item.get("bindings")
        if not isinstance(bindings, list):
            continue
        for index, binding in enumerate(bindings):
            if not isinstance(binding, dict) or binding.get("kind") != "sql":
                continue
            checked += 1
            source_id = str(binding.get("source_id", ""))
            entity = str(binding.get("entity", ""))
            field = str(binding.get("field", ""))
            where = f"mapping {obj_id}: bindings[{index}]"
            if source_id not in sources:
                errors.append(
                    f"{where}.source_id {source_id!r} not found in catalog "
                    f"(known: {', '.join(known_sources)})"
                )
                continue
            entities = sources[source_id]
            if entity not in entities:
                errors.append(
                    f"{where}.entity {entity!r} not found in source {source_id!r} "
                    f"(known: {', '.join(sorted(entities))})"
                )
                continue
            if field not in entities[entity]:
                errors.append(
                    f"{where}.field {field!r} not found in entity "
                    f"'{source_id}.{entity}' (known: {', '.join(sorted(entities[entity]))})"
                )
    return errors, checked


def _discover_catalog(root: Path) -> Path | None:
    """Return the catalog snapshot named by ``project.json``, if it names one.

    Only a dict ``data_source`` carrying a ``catalog`` key opts in. A plain
    string ``data_source`` (the pre-M1-B shape) or a missing ``project.json``
    simply means "no catalog", never an error.
    """
    project_path = root / "project.json"
    if not project_path.is_file():
        return None
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(project, dict):
        return None
    data_source = project.get("data_source")
    if not isinstance(data_source, dict):
        return None
    raw = str(data_source.get("catalog") or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else (root / candidate)


def validate(
    root: PathLike | None = None,
    version: str | None = None,
    catalog: PathLike | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root = resolve_workspace(root)
    errors: List[str] = []

    selected_version = str(version or "").strip()
    if not selected_version:
        active_file = root / "active.json"
        if not active_file.is_file():
            return {
                "passed": False,
                "root": str(root),
                "errors": [f"Missing {active_file}"],
            }
        try:
            active = json.loads(active_file.read_text(encoding="utf-8"))
            selected_version = str(
                active.get("active_version") or active.get("version") or ""
            ).strip()
        except json.JSONDecodeError as exc:
            return {
                "passed": False,
                "root": str(root),
                "errors": [f"Invalid active.json: {exc}"],
            }
        if not selected_version:
            errors.append("active.json has no 'active_version'")
            return {
                "passed": False,
                "root": str(root),
                "version": "",
                "errors": errors,
            }

    try:
        selected_version = validate_path_component(selected_version, label="version")
    except ValueError as exc:
        return {
            "passed": False,
            "root": str(root),
            "version": selected_version,
            "errors": [str(exc)],
        }

    version_dir = root / "versions" / selected_version
    if not version_dir.is_dir():
        errors.append(f"Version directory missing: {version_dir}")
        return {
            "passed": False,
            "root": str(root),
            "version": selected_version,
            "errors": errors,
        }

    # Load raw records for reference checking.
    raw: Dict[str, Dict[str, dict]] = {}
    for filename in VERSION_FILES:
        path = version_dir / filename
        if not path.is_file():
            errors.append(f"Missing file: {path}")
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON in {filename}: {exc}")
            continue
        if not isinstance(records, list):
            errors.append(f"{filename} must be a JSON list")
            continue
        indexed = {}
        for index, item in enumerate(records):
            if not isinstance(item, dict) or not str(item.get("id", "")).strip():
                errors.append(f"{filename}[{index}] must be an object with an id")
                continue
            item_id = str(item["id"])
            if item_id in indexed:
                errors.append(f"{filename}: duplicate id {item_id!r}")
                continue
            indexed[item_id] = item
        raw[filename] = indexed

    term_ids = set(raw.get("terms.json", {}))
    mapping_ids = set(raw.get("mappings.json", {}))
    evidence_ids = set(raw.get("evidence.json", {}))

    def _check_refs(field_value, *, label: str, allowed: set):
        refs = field_value
        if isinstance(refs, dict):
            refs = refs.get("source_refs", [])
        if not isinstance(refs, list):
            return
        for ref in refs:
            if str(ref) not in allowed:
                errors.append(f"{label}: unresolved evidence ref {ref!r}")

    for obj_id, item in raw.get("terms.json", {}).items():
        _check_refs(item.get("evidence"), label=f"term {obj_id}", allowed=evidence_ids)
    for obj_id, item in raw.get("relations.json", {}).items():
        for end in ("source", "target"):
            ref = item.get(end)
            if ref and ref not in term_ids:
                errors.append(f"relation {obj_id}: unresolved {end} {ref!r}")
        _check_refs(item.get("evidence"), label=f"relation {obj_id}", allowed=evidence_ids)
    def _mapping_evidence(item: dict):
        refs = item.get("evidence_refs", [])
        if refs:
            return refs
        validation = item.get("validation", {})
        if isinstance(validation, dict):
            val_evidence = validation.get("evidence", [])
            if isinstance(val_evidence, list):
                return val_evidence
        return item.get("evidence", [])

    for obj_id, item in raw.get("mappings.json", {}).items():
        term_ref = item.get("term_id")
        if term_ref and term_ref not in term_ids:
            errors.append(f"mapping {obj_id}: unresolved term_id {term_ref!r}")
        _check_refs(_mapping_evidence(item), label=f"mapping {obj_id}", allowed=evidence_ids)
        errors.extend(_binding_errors(obj_id, item))
    for obj_id, item in raw.get("constraints.json", {}).items():
        target = item.get("target")
        if target and target not in term_ids and target not in mapping_ids:
            errors.append(f"constraint {obj_id}: unresolved target {target!r}")
        _check_refs(item.get("evidence"), label=f"constraint {obj_id}", allowed=evidence_ids)

    # Optional: check bindings against the real data catalog. Absence of a
    # catalog is never an error -- only a catalog that exists and disagrees is.
    catalog_info: Dict[str, Any] = {}
    resolved_catalog = catalog if catalog is not None else _discover_catalog(root)
    if resolved_catalog is not None:
        if isinstance(resolved_catalog, dict):
            loaded = resolved_catalog
            catalog_path = "<mapping>"
        else:
            catalog_path = str(resolved_catalog)
            try:
                loaded = load_catalog(resolved_catalog)
            except FileNotFoundError:
                loaded = None
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
                loaded = None
        if loaded is not None:
            catalog_errors, checked = _catalog_errors(raw.get("mappings.json", {}), loaded)
            errors.extend(catalog_errors)
            catalog_info = {"path": catalog_path, "checked": checked}

    # The runtime must be able to load the store.
    if not errors:
        try:
            SemanticStore.load_version(root, selected_version)
        except Exception as exc:
            errors.append(f"Store load failed: {exc}")

    report = {
        "passed": not errors,
        "root": str(root),
        "version": selected_version,
        "errors": errors,
    }
    if catalog_info:
        report["catalog"] = catalog_info
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an ontology workspace")
    parser.add_argument(
        "--root",
        default=None,
        help="Workspace root (default: <cwd>/.evoontology)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Named version to validate without changing active.json",
    )
    args = parser.parse_args()
    report = validate(args.root, version=args.version)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
