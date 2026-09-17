"""Read-only access to the normalized incident classifier catalog."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("data") / "classifier.json"
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_GROUPS = 256
MAX_RECORDS = 10_000


def _column_name(number: int) -> str:
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


ROUTING_COLUMNS = {_column_name(column) for column in range(14, 100)}


@lru_cache(maxsize=1)
def _loaded() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if CATALOG_PATH.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError("classifier catalog exceeds the runtime size limit")
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    if (
        not isinstance(catalog, dict)
        or not isinstance(catalog.get("version"), str)
        or not catalog["version"]
    ):
        raise ValueError("invalid classifier catalog header")
    if not isinstance(catalog.get("source"), dict):
        raise ValueError("invalid classifier source metadata")
    if not isinstance(catalog.get("metadata"), dict):
        raise ValueError("invalid classifier import metadata")
    groups = catalog.get("groups")
    records = catalog.get("records")
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        raise ValueError("invalid classifier groups")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("invalid classifier records")

    group_ids: set[str] = set()
    for group in groups:
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("id"), str)
            or not isinstance(group.get("title"), str)
            or group["id"] in group_ids
        ):
            raise ValueError("invalid or duplicate classifier group")
        group_ids.add(group["id"])

    index: dict[str, dict[str, Any]] = {}
    required = {
        "id",
        "group_id",
        "features",
        "incident_type",
        "main_service",
        "additional_details",
        "routing_cells",
        "source_row",
    }
    for record in records:
        if not isinstance(record, dict) or not required.issubset(record):
            raise ValueError("invalid classifier record")
        record_id = record["id"]
        features = record["features"]
        routing_cells = record["routing_cells"]
        if (
            not isinstance(record_id, str)
            or not record_id
            or record_id in index
            or not isinstance(record["group_id"], str)
            or record["group_id"] not in group_ids
            or not isinstance(features, list)
            or len(features) != 3
            or not all(isinstance(item, str) for item in features)
            or not isinstance(record["incident_type"], str)
            or not isinstance(record["main_service"], str)
            or not isinstance(record["additional_details"], str)
            or not isinstance(routing_cells, dict)
            or not all(
                isinstance(column, str)
                and column in ROUTING_COLUMNS
                and isinstance(value, str)
                for column, value in routing_cells.items()
            )
            or isinstance(record["source_row"], bool)
            or not isinstance(record["source_row"], int)
        ):
            raise ValueError("invalid or duplicate classifier record")
        index[record_id] = record
    return catalog, index


def get_catalog() -> dict[str, Any]:
    """Return an isolated copy so callers cannot mutate the cached catalog."""
    catalog, _ = _loaded()
    return copy.deepcopy(catalog)


def resolve(record_id: str, version: str) -> dict[str, Any]:
    """Resolve a record only against the exact catalog version supplied by UI."""
    catalog, index = _loaded()
    if not isinstance(version, str) or version != catalog["version"]:
        raise ValueError("classifier version mismatch")
    if not isinstance(record_id, str) or record_id not in index:
        raise ValueError("unknown classifier record")
    return copy.deepcopy(index[record_id])
