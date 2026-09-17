from __future__ import annotations

import pytest

from classifier import get_catalog
from routing import FLAG_NAMES, SERVICE_COLUMNS, preview, service_catalog


def _card(record_id: str = "1010101", **flags: bool) -> dict:
    catalog = get_catalog()
    record = next(item for item in catalog["records"] if item["id"] == record_id)
    return {
        "classifier_id": record["id"],
        "classifier_version": catalog["version"],
        "classifier_group": record["group_id"],
        "classifier_features": record["features"],
        **flags,
    }


def _service(result: dict, name: str) -> dict | None:
    return next((item for item in result["suggestions"] if item["service"] == name), None)


def test_base_routing_uses_only_default_branches() -> None:
    result = preview(_card())
    assert result["rules_version"] == "full-v2"
    assert result["source_row"] == 5
    assert set(result["flags"]) == set(FLAG_NAMES)
    assert not any(result["flags"].values())
    assert _service(result, "Служба 101")["mappings"] == [
        {"cell": "N5", "incident_type": "пожар: мусор"}
    ]
    assert _service(result, "Служба 104") is None
    assert {item["cell"] for item in result["excluded"]} >= {"P5", "T5", "U5", "X5", "AA5"}
    assert any("BW:BY" in warning for warning in result["warnings"])


def test_conditional_branches_and_offsite_no_response() -> None:
    result = preview(
        _card(
            no_access=True,
            injured=True,
            threat_to_people=True,
            injured_offsite=True,
            gasification=True,
        )
    )
    assert _service(result, "Служба 101") is None
    assert _service(result, "ОДС ПСЦ")["mappings"] == [
        {"cell": "S5", "incident_type": "пожар: мусор"}
    ]
    assert _service(result, "Служба 103") is None
    assert _service(result, "Служба 104")["mappings"] == [
        {"cell": "AB5", "incident_type": "пожар"}
    ]
    exclusions = {item["cell"]: item["reason"] for item in result["excluded"]}
    assert exclusions["Q5"] == "Нет соответствия в источнике"
    assert exclusions["R5"] == "Нет соответствия в источнике"
    assert exclusions["Z5"] == "Нет реагирования"


def test_ods_retains_all_selected_conditional_cells() -> None:
    result = preview(
        _card("5200200", no_access=True, injured=True, threat_to_people=True)
    )
    expected_type = "Происшествие на стройке: обрушения котлована "
    assert _service(result, "ОДС ПСЦ")["mappings"] == [
        {"cell": "Q436", "incident_type": expected_type},
        {"cell": "R436", "incident_type": expected_type},
        {"cell": "S436", "incident_type": expected_type},
    ]


def test_police_retains_both_matching_branches_and_warns_on_conflict() -> None:
    catalog = get_catalog()
    record = next(
        item
        for item in catalog["records"]
        if item["routing_cells"].get("V")
        and item["routing_cells"].get("W")
        and item["routing_cells"]["V"] != item["routing_cells"]["W"]
    )
    result = preview(_card(record["id"], offense=True, injured=True))
    police = _service(result, "Служба 102")
    assert police["mappings"] == [
        {"cell": f"V{record['source_row']}", "incident_type": record["routing_cells"]["V"]},
        {"cell": f"W{record['source_row']}", "incident_type": record["routing_cells"]["W"]},
    ]
    assert any("Служба 102" in warning for warning in result["warnings"])


def test_empty_selection_and_mismatched_classifier_are_safe() -> None:
    result = preview({})
    assert result["source_row"] is None
    assert result["suggestions"] == []
    assert "не выбрана" in result["warnings"][0]

    card = _card()
    card["classifier_features"] = ["wrong", "", ""]
    with pytest.raises(ValueError, match="features"):
        preview(card)


def test_ambulance_uses_injured_branch_unless_injured_is_offsite() -> None:
    onsite = preview(_card(injured=True))
    assert _service(onsite, "Служба 103")["mappings"] == [
        {"cell": "Y5", "incident_type": "пожар"}
    ]
    offsite = preview(_card(injured=True, injured_offsite=True))
    assert _service(offsite, "Служба 103") is None
    assert any(item["cell"] == "Z5" for item in offsite["excluded"])

    uppercase = preview(_card("14102200", injured=True, injured_offsite=True))
    assert _service(uppercase, "Служба 103") is None
    assert any(
        item == {
            "service": "Служба 103",
            "cell": "Z825",
            "reason": "Нет реагирования",
        }
        for item in uppercase["excluded"]
    )


def test_offsite_without_injured_is_ignored_with_warning() -> None:
    result = preview(_card(injured_offsite=True))
    assert _service(result, "Служба 103") is None
    assert any("игнорирован" in warning for warning in result["warnings"])


def test_service_catalog_exposes_all_services_and_boolean_flags() -> None:
    catalog = service_catalog()
    assert len(catalog["services"]) == len(set(catalog["services"]))
    assert {"ЦЭМП", "ФСБ", "ГУП МСР — КУБ", "ГУП МСР — пожары"} <= set(
        catalog["services"]
    )
    assert [item["id"] for item in catalog["flags"]] == list(FLAG_NAMES)
    assert all(set(item) == {"id", "label"} and item["label"] for item in catalog["flags"])


def test_rules_cover_every_source_column_from_n_through_cu() -> None:
    false_flags = {name: False for name in FLAG_NAMES}
    states = [false_flags, {name: True for name in FLAG_NAMES}]
    states.extend({name: name == selected for name in FLAG_NAMES} for selected in FLAG_NAMES)
    covered = {
        column
        for state in states
        for _service_name, selector in SERVICE_COLUMNS
        for column in selector(state)
    }

    def column_name(number: int) -> str:
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    assert covered == {column_name(number) for number in range(14, 100)}


def test_expanded_conditions_and_manual_territorial_routing() -> None:
    result = preview(
        _card(
            threat_to_people=True,
            medical_help=True,
            territorial_oiv=True,
            territorial_oiv_tinao=True,
        )
    )
    assert _service(result, "ЦЭМП")["mappings"] == [
        {"cell": "AD5", "incident_type": "карточка-112"},
        {"cell": "AF5", "incident_type": "карточка-112"},
    ]
    assert _service(result, "Территориальные ОИВ")["mappings"][0]["cell"] == "BW5"
    assert _service(result, "Территориальные ОИВ ТиНАО")["mappings"][0]["cell"] == "BX5"
    assert _service(result, "Автомобильные дороги АО г. Москвы") is None
    assert _service(result, "Департамент культуры") is None


def test_source_free_text_conditions_are_unresolved_not_suggestions() -> None:
    result = preview(_card("12990100", no_access=True, gasification=True))
    assert _service(result, "Служба 101") is None
    assert _service(result, "Служба 104") is None
    unresolved = {item["cell"]: item for item in result["unresolved"]}
    assert set(unresolved) >= {"O637", "AB637"}
    assert all("услов" in unresolved[cell]["reason"].casefold() for cell in ("O637", "AB637"))


def test_no_notification_and_agreement_markers_do_not_autosuggest() -> None:
    no_notification = preview(_card("23010000"))
    assert not no_notification["suggestions"]
    assert {item["reason"] for item in no_notification["excluded"]} >= {"Без оповещения"}

    agreement = preview(_card("23040000"))
    assert all(
        item["service"] not in {"ОДС ПСЦ", "Служба 102", "Служба 103", "Служба 104"}
        for item in agreement["suggestions"]
    )
    assert {item["cell"] for item in agreement["unresolved"]} == {
        "P1293",
        "U1293",
        "X1293",
        "AA1293",
    }
    assert _service(agreement, "Министерство обороны РХБЗ — События по полигонам") is None
    assert _service(agreement, "Министерство обороны РХБЗ — Москва") is None

    confirmed = preview(_card("23040000", rhbz_polygon=True, rhbz_moscow=True))
    assert _service(confirmed, "Министерство обороны РХБЗ — События по полигонам")
    assert _service(confirmed, "Министерство обороны РХБЗ — Москва")


def test_split_source_channels_remain_separate_service_entries() -> None:
    result = preview(_card())
    assert _service(result, "ГУП МСР — КУБ") is None
    assert _service(result, "ГУП МСР — пожары")["mappings"] == [
        {"cell": "CI5", "incident_type": "пожар: мусор"}
    ]
