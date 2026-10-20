"""Checks bank records against the kit's schemas before anything is stored.

validate_product(record)  -> list of plain-language problems ([] means valid)
validate_evidence(entry)  -> same, for one evidence ledger entry

On top of the JSON Schemas in bank_data/, it enforces the rules that a schema
can't express on its own:
- A confirmed, from_photo or conflicting fact must point to at least one piece
  of evidence. Unknown facts carry no value.
- A from_photo fact is always 'inferred', and never sets material or origin.
- brand_wide evidence can't confirm a product-level claim on its own.
- When the evidence IDs are given, every one a fact points to must exist.
"""
import json
import os
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

from jsonschema import Draft202012Validator

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_data")

# Fields a photo can never establish.
NOT_FROM_PHOTO = ("materials.", "construction.country_of_assembly", "construction.factory",
                  "people_and_sourcing.", "animal_welfare.")


@lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _path(err) -> str:
    return ".".join(str(p) for p in err.absolute_path) or "(record)"


def _is_fact(node: Any) -> bool:
    return isinstance(node, dict) and "status" in node and node.get("status") in (
        "confirmed", "from_photo", "unknown", "conflicts")


def _walk_facts(node: Any, path: str = ""):
    """Yield (path, fact) for every Fact object anywhere in a record."""
    if _is_fact(node):
        yield path, node
        return
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_facts(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_facts(v, f"{path}[{i}]")


def validate_product(record: Dict[str, Any],
                     known_evidence_ids: Optional[Iterable[str]] = None) -> List[str]:
    problems = [f"{_path(e)}: {e.message}"
                for e in sorted(_validator("product_schema.json").iter_errors(record),
                                key=lambda e: list(e.absolute_path))]
    known = set(known_evidence_ids) if known_evidence_ids is not None else None
    for path, fact in _walk_facts(record):
        status = fact.get("status")
        ids = fact.get("evidence_ids") or []
        if status != "unknown" and not ids:
            problems.append(f"{path}: a {status} fact needs at least one piece of evidence")
        if status == "unknown" and fact.get("value") is not None:
            problems.append(f"{path}: an unknown fact can't have a value")
        if status == "from_photo":
            if fact.get("source_type") != "inferred":
                problems.append(f"{path}: a fact from a photo must be 'inferred'")
            if path.startswith(NOT_FROM_PHOTO):
                problems.append(f"{path}: material, origin and sourcing can't come from a photo")
        if status == "confirmed" and fact.get("scope") == "brand_wide":
            problems.append(f"{path}: brand-wide evidence can't confirm a product fact on its own")
        if known is not None:
            missing = [i for i in ids if i not in known]
            if missing:
                problems.append(f"{path}: evidence not found: {', '.join(missing)}")
    return problems


def validate_evidence(entry: Dict[str, Any]) -> List[str]:
    return [f"{_path(e)}: {e.message}"
            for e in sorted(_validator("evidence_ledger_schema.json").iter_errors(entry),
                            key=lambda e: list(e.absolute_path))]


def schemas_ok() -> bool:
    """True if both schema files load and are valid JSON Schemas."""
    _validator("product_schema.json")
    _validator("evidence_ledger_schema.json")
    return True
