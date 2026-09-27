"""Source-provenanced routing preview for classifier columns N:CU."""

from __future__ import annotations

import copy
from typing import Any, Callable

from classifier import resolve
from territories import recipients as territorial_recipients, rules as territorial_rules


RULES_VERSION = "full-v2"
FLAGS = (
    {"id": "no_access", "label": "Нет доступа"},
    {"id": "injured", "label": "Пострадавшие / погибшие"},
    {"id": "threat_to_people", "label": "Угроза людям"},
    {"id": "offense", "label": "Правонарушение"},
    {"id": "injured_offsite", "label": "Пострадавшие не на месте"},
    {"id": "gasification", "label": "Газификация"},
    {"id": "medical_help", "label": "Требуется медицинская помощь"},
    {"id": "evacuation", "label": "Требуется эвакуация"},
    {"id": "fsb_special", "label": ">5 человек / ОД"},
    {"id": "traffic_blocked", "label": "Перекрытие движения"},
    {"id": "tunnel", "label": "Тоннель"},
    {"id": "pedestrian_structure", "label": "ГОРМОСТ: пеш (признак источника)"},
    {"id": "vehicle_structure", "label": "ГОРМОСТ: ав (признак источника)"},
    {"id": "telecom_object", "label": "Объект связи"},
    {"id": "construction_site", "label": "Стройка"},
    {"id": "culture_listed_object", "label": "Объект из перечня Департамента культуры"},
    {"id": "territorial_oiv", "label": "Территориальные ОИВ (выбор вручную)"},
    {"id": "territorial_oiv_tinao", "label": "Территориальные ОИВ ТиНАО (выбор вручную)"},
    {"id": "territorial_roads_moscow", "label": "Автомобильные дороги АО г. Москвы (выбор вручную)"},
    {"id": "rhbz_polygon", "label": "РХБЗ: События по полигонам (подтверждение вручную)"},
    {"id": "rhbz_moscow", "label": "РХБЗ: Москва (подтверждение вручную)"},
)
FLAG_NAMES = tuple(item["id"] for item in FLAGS)

# Колонка «Главная служба» классификатора хранит коды, а не названия служб.
# Инструкция оператора требует отличать основную службу для типа происшествия
# от остальных назначенных («подчёркиваются двойной линией»), поэтому коды
# переводятся в те же названия, которыми оперирует полоса служб карточки.
# Коды вне списка основной службой не считаются: лучше не выделить, чем выделить
# не ту.
MAIN_SERVICE_NAMES: dict[str, str] = {
    "MCHS": "Служба 101",
    "POLICE": "Служба 102",
    "AMBULANCE": "Служба 103",
    "MOSGAZ": "Служба 104",
    "ZEMP": "ЦЭМП",
    "MOSLIFT": "Мослифт",
    "AUTOROADS": "Автомобильные дороги",
    "MOSVODOCANAL": "Мосводоканал",
    "MOSVODOSTOK": "Мосводосток",
    "MOSCOLLECTOR": "Москоллектор",
    "METRO": "Метро",
    "OEK": "ОЭК",
    "MOEK": "МОЭК",
    "MOESK": "МОЭСК (ПАО «Россети Московский регион»)",
    "MOSGORTRANS": "Мосгортранс",
    "MZD": "РЖД (МосковскаяЖД)",
    "MGTS": "МГТС",
    "GORMOST": "ГОРМОСТ",
    "GKH": "Деп. ЖКХ",
    "ZODD": "ЦОДД",
    "MSPPN": "МСППН",
    "DEPECO": "Департамент ППиООС",
    "DEP.TSZN": "ОД Департамент ТСЗН",
}


def main_services(raw: str) -> list[str]:
    """Названия основных служб записи классификатора.

    В источнике встречается перечисление через запятую («METRO, MZD»).
    """
    if not isinstance(raw, str):
        return []
    names = []
    for code in raw.split(","):
        name = MAIN_SERVICE_NAMES.get(code.strip().upper())
        if name and name not in names:
            names.append(name)
    return names

ColumnSelector = Callable[[dict[str, bool]], list[str]]


def _always(column: str) -> ColumnSelector:
    return lambda _flags: [column]


def _when(flag: str, column: str) -> ColumnSelector:
    return lambda flags: [column] if flags[flag] else []


def _default_or_conditions(default: str, *conditions: tuple[str, str]) -> ColumnSelector:
    def select(flags: dict[str, bool]) -> list[str]:
        selected = [column for flag, column in conditions if flags[flag]]
        return selected or [default]

    return select


SERVICE_COLUMNS: tuple[tuple[str, ColumnSelector], ...] = (
    ("Служба 101", lambda flags: ["O" if flags["no_access"] else "N"]),
    ("ОДС ПСЦ", _default_or_conditions("P", ("threat_to_people", "Q"), ("injured", "R"), ("no_access", "S"))),
    ("МГПСС", _always("T")),
    ("Служба 102", _default_or_conditions("U", ("offense", "V"), ("injured", "W"))),
    ("Служба 103", lambda flags: ["Z" if flags["injured"] and flags["injured_offsite"] else "Y" if flags["injured"] else "X"]),
    ("Служба 104", lambda flags: ["AB" if flags["gasification"] else "AA"]),
    ("ЦЭМП", _default_or_conditions("AC", ("threat_to_people", "AD"), ("injured", "AE"), ("medical_help", "AF"), ("evacuation", "AG"))),
    ("ФСБ", lambda flags: ["AI" if flags["fsb_special"] else "AH"]),
    ("Мособлгаз", _always("AJ")),
    ("Автомобильные дороги", _always("AK")),
    ("Мосгортранс", _default_or_conditions("AL", ("injured", "AM"), ("traffic_blocked", "AN"))),
    ("Гор. Хозяйство", _always("AO")),
    ("ГОРМОСТ", _default_or_conditions("AP", ("tunnel", "AQ"), ("pedestrian_structure", "AR"), ("vehicle_structure", "AS"))),
    ("Канал имени Москвы", _always("AT")),
    ("МГТС", lambda flags: ["AU"] + (["AV"] if flags["telecom_object"] else [])),
    ("Метро", _always("AW")),
    ("Мосводоканал", _always("AX")),
    ("МОЭК", _always("AY")),
    ('МОЭСК (ПАО "Россети Московский регион")', _always("AZ")),
    ("ОЭК", _always("BA")),
    ("Мослифт", _always("BB")),
    ("ЦОДД", _always("BC")),
    ("Деп. ЖКХ", _always("BD")),
    ("Департамент РБиПК (ГКУ МОСБЕЗ) — Дежурная служба АРМ-112", _always("BE")),
    ("Департамент РБиПК (ГКУ МОСБЕЗ) — МКП, Аналитика (Старый КРИМ)", _always("BF")),
    ("Аппарат МЭРА", _always("BG")),
    ("Москоллектор", _always("BH")),
    ("РЖД (МосковскаяЖД)", _always("BI")),
    ("Департамент образования", _always("BJ")),
    ("Центррегионводхоз (Московско-Окское БВУ)", _always("BK")),
    ("Военная комендатура", _always("BL")),
    ("ОАТИ", _always("BM")),
    ("Мосводосток", _always("BN")),
    ("Департамент ППиООС", _always("BO")),
    ("ОД Департамент ТСЗН", _always("BP")),
    ("РСВО", _always("BQ")),
    ("ЭВАЖД", _always("BR")),
    ("МСППН", _always("BS")),
    ("ДТУ_Р (Ритуал)", _always("BT")),
    ("ДТУ", _always("BU")),
    ("Росгвардия", _always("BV")),
    ("Территориальные ОИВ", _when("territorial_oiv", "BW")),
    ("Территориальные ОИВ ТиНАО", _when("territorial_oiv_tinao", "BX")),
    ("Автомобильные дороги АО г. Москвы", _when("territorial_roads_moscow", "BY")),
    ("Департамент строительства города Москвы", lambda flags: ["CA" if flags["construction_site"] else "BZ"]),
    ("Комитет ветеринарии", _always("CB")),
    ("Мосжилинспекция", _always("CC")),
    ("Департамент культуры", _when("culture_listed_object", "CD")),
    ("ГКУ ЦСА имени Е.П.Глинки", _always("CE")),
    ("ГКУ НТУ", _always("CF")),
    ("ФСО", _always("CG")),
    ("ГУП МСР — КУБ", _always("CH")),
    ("ГУП МСР — пожары", _always("CI")),
    ("Комитет по туризму г. Москвы", _always("CJ")),
    ("ДГП — интеграция", _always("CK")),
    ("ДГП — АРМ-112", _always("CL")),
    ("ЦУКБ Министерство обороны", _always("CM")),
    ("ЦУКБ.БПЛА Министерство обороны", _always("CN")),
    ("ГКУ Организатор перевозок", lambda flags: ["CP" if flags["traffic_blocked"] else "CO"]),
    ("ГПБУ Мосэкомониторинг", _always("CQ")),
    ("Министерство обороны РХБЗ — События по полигонам", _when("rhbz_polygon", "CR")),
    ("Министерство обороны РХБЗ — Москва", _when("rhbz_moscow", "CS")),
    ("ООО Ситиэнерго", _always("CT")),
    ("Депортамент гражданского строительства", _always("CU")),
)

TERRITORIAL_WARNING = (
    "Территориальные маршруты BW:BY и направления CR:CS нельзя определить из "
    "классификации: оператор выбирает соответствующие признаки вручную."
)


def service_catalog() -> dict[str, Any]:
    """Return UI metadata without exposing mutable module-level structures."""
    services = [service for service, _ in SERVICE_COLUMNS]
    services.extend(s for rule in territorial_rules() for s in rule['services'])
    return {"services": list(dict.fromkeys(services)), "flags": copy.deepcopy(list(FLAGS))}


def _flags(card: dict[str, Any]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for name in FLAG_NAMES:
        value = card.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"routing flag must be boolean: {name}")
        flags[name] = value
    return flags


def _cell_disposition(raw: str) -> tuple[str, str]:
    normalized = " ".join(raw.strip().casefold().split())
    if normalized == "":
        return "excluded", "Нет соответствия в источнике"
    if normalized == "нет реагирования":
        return "excluded", "Нет реагирования"
    if normalized.startswith("без оповещения"):
        return "excluded", "Без оповещения"
    if normalized == "по согласованию":
        return "unresolved", "Требуется согласование"
    if "условие оповещения" in normalized:
        return "unresolved", "Условие оповещения задано только свободным текстом"
    return "suggestion", ""


def preview(card: dict[str, Any]) -> dict[str, Any]:
    """Return routing suggestions without mutating the incident card."""
    if not isinstance(card, dict):
        raise ValueError("card must be an object")

    classifier_id = card.get("classifier_id", "")
    classifier_version = card.get("classifier_version", "")
    flags = _flags(card)
    result: dict[str, Any] = {
        "rules_version": RULES_VERSION,
        "classifier_id": classifier_id if isinstance(classifier_id, str) else "",
        "classifier_version": classifier_version if isinstance(classifier_version, str) else "",
        "source_row": None,
        "flags": flags,
        "primary_services": [],
        "suggestions": territorial_recipients(card),
        "excluded": [],
        "unresolved": [],
        "warnings": [TERRITORIAL_WARNING],
    }
    if classifier_id == "":
        result["warnings"].insert(0, "Классификация не выбрана; сформированы только совпавшие территориальные правила.")
        return result
    if not isinstance(classifier_id, str) or not isinstance(classifier_version, str):
        raise ValueError("classifier id and version must be strings")

    record = resolve(classifier_id, classifier_version)
    if card.get("classifier_group") != record["group_id"]:
        raise ValueError("classifier group mismatch")
    if card.get("classifier_features") != record["features"]:
        raise ValueError("classifier features mismatch")

    result["source_row"] = record["source_row"]
    result["primary_services"] = main_services(record["main_service"])
    cells = record["routing_cells"]
    if flags["injured_offsite"] and not flags["injured"]:
        result["warnings"].append(
            "Признак отсутствия пострадавшего на месте проигнорирован без признака пострадавших."
        )
    for service, choose_columns in SERVICE_COLUMNS:
        mappings: list[dict[str, str]] = []
        for column in choose_columns(flags):
            raw = cells.get(column, "")
            coordinate = f"{column}{record['source_row']}"
            disposition, reason = _cell_disposition(raw)
            if disposition == "excluded":
                result["excluded"].append({"service": service, "cell": coordinate, "reason": reason})
            elif disposition == "unresolved":
                result["unresolved"].append(
                    {"service": service, "cell": coordinate, "reason": reason, "source_value": raw}
                )
            else:
                mappings.append({"cell": coordinate, "incident_type": raw})
        if mappings:
            result["suggestions"].append({
                "service": service, "mappings": mappings,
                "primary": service in result["primary_services"],
            })
            if len({mapping["incident_type"] for mapping in mappings}) > 1:
                result["warnings"].append(
                    f"Для {service} источник содержит несколько разных типов происшествия."
                )
    if result["unresolved"]:
        result["warnings"].append(
            "Часть исходных ячеек требует ручного решения и не включена в рекомендации."
        )
    return result
