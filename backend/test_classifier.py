from __future__ import annotations

import pytest

from classifier import get_catalog, resolve


def _column_number(name: str) -> int:
    result = 0
    for character in name:
        result = result * 26 + ord(character) - 64
    return result


def test_generated_catalog_preserves_representative_source_mapping() -> None:
    catalog = get_catalog()
    assert len(catalog["groups"]) == 24
    assert catalog["metadata"]["imported_records"] == 1283
    assert catalog["metadata"]["ambiguous_count"] == 0
    assert catalog["metadata"]["duplicate_code_count"] == 0
    assert catalog["metadata"]["schema_version"] == 3
    assert catalog["source"]["sha256"] == "1d907424ab96ae397cf4b67dfd0193aa94deda56fba2d2ca4f8df8f2c2585937"

    first = resolve("1010101", catalog["version"])
    assert first == {
        "id": "1010101",
        "group_id": "1",
        "features": ["на улице", "мусор", "открытое пламя"],
        "incident_type": "пожар: мусор",
        "main_service": "MCHS",
        "additional_details": "",
        "routing_cells": {
            "N": "пожар: мусор",
            "S": "пожар: мусор",
            "V": "пожар",
            "W": "пожар",
            "Y": "пожар",
            "Z": "нет реагирования",
            "AB": "пожар",
            "AD": "карточка-112",
            "AE": "карточка-112",
            "AF": "карточка-112",
            "AG": "карточка-112",
            "AO": "карточка-112",
            "BC": "пожар: мусор",
            "BG": "карточка-112",
            "BM": "карточка -112",
            "BW": "карточка-112",
            "BX": "карточка-112",
            "CD": "карточка-112",
            "CF": "карточка-112",
            "CG": "карточка-112",
            "CI": "пожар: мусор",
            "CM": "карточка-112",
            "CQ": "карточка-112",
        },
        "source_row": 5,
    }

    # A blank third feature and blank service are data, not values to fill from
    # neighbouring rows.
    blank_service = next(record for record in catalog["records"] if record["main_service"] == "")
    assert len(blank_service["features"]) == 3
    assert all(isinstance(feature, str) for feature in blank_service["features"])
    assert catalog["groups"][-1] == {"id": "24", "title": "БПЛА"}

    assert all(isinstance(record["additional_details"], str) for record in catalog["records"])
    assert all(
        all(14 <= _column_number(column) <= 99 for column in record["routing_cells"])
        for record in catalog["records"]
    )

    # CI contains same-row formulas in the workbook. The import stores the
    # resolved K value and never exposes executable formula text at runtime.
    assert first["routing_cells"]["CI"] == first["incident_type"]


def test_resolve_rejects_unknown_id_and_stale_version() -> None:
    catalog = get_catalog()
    with pytest.raises(ValueError, match="unknown"):
        resolve("missing", catalog["version"])
    with pytest.raises(ValueError, match="version"):
        resolve("1010101", "sha256:stale")


def test_get_catalog_returns_an_isolated_copy() -> None:
    catalog = get_catalog()
    catalog["records"][0]["features"][0] = "changed"
    assert get_catalog()["records"][0]["features"][0] == "на улице"
