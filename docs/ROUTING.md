# Source-column routing

Source: supplied `Датасет.zip`, classifier workbook v_046_24, header rows 1–3.
The importer preserves J (`additional_details`) and all routing cells N:CU. This
is a bounded translation of a supplied training dataset, not an independent
statement of official 112 regulations.

## Rules

`full-v2` evaluates every source routing column through CU. Blank selected cells
mean no source match. `нет реагирования` and values beginning `Без оповещения`
are explicit exclusions. `По согласованию` and values that contain only a
free-text `условие оповещения` are returned as unresolved and are never added to
automatic suggestions. The latter occur in O/AB/Y in the current workbook and
do not expose a machine-readable condition.

| Service | Default/direct column | Conditional columns |
| --- | --- | --- |
| Служба 101 | N | O when `no_access` |
| ОДС ПСЦ | P | Q `threat_to_people`, R `injured`, S `no_access`; default only when none apply |
| МГПСС | T | — |
| Служба 102 | U | V `offense`, W `injured`; default only when neither applies |
| Служба 103 | X | Y `injured`, Z `injured` and `injured_offsite` |
| Служба 104 | AA | AB `gasification` |
| ЦЭМП | AC | AD `threat_to_people`, AE `injured`, AF `medical_help`, AG `evacuation`; default only when none apply |
| ФСБ | AH | AI `fsb_special` (`>5 чел / ОД` is retained as the source label, not interpreted) |
| Мосгортранс | AL | AM `injured`, AN `traffic_blocked`; default only when neither applies |
| ГОРМОСТ | AP | AQ `tunnel`, AR `pedestrian_structure`, AS `vehicle_structure`; default only when none apply |
| МГТС | AU, always evaluated | AV is additive when `telecom_object` |
| Департамент строительства города Москвы | BZ | CA `construction_site` |
| Департамент культуры | no default | CD only when `culture_listed_object` |
| ГКУ Организатор перевозок | CO | CP `traffic_blocked` |

These direct columns are evaluated when their source cell is nonblank: AJ
Мособлгаз, AK Автомобильные дороги, AO Гор. Хозяйство, AT Канал имени Москвы,
AW Метро, AX Мосводоканал, AY МОЭК, AZ МОЭСК, BA ОЭК, BB Мослифт, BC ЦОДД,
BD Деп. ЖКХ, BG Аппарат МЭРА, BH Москоллектор, BI РЖД, BJ Департамент
образования, BK Центррегионводхоз, BL Военная комендатура, BM ОАТИ, BN
Мосводосток, BO Департамент ППиООС, BP ОД Департамент ТСЗН, BQ РСВО, BR
ЭВАЖД, BS МСППН, BT ДТУ_Р, BU ДТУ, BV Росгвардия, CB Комитет ветеринарии,
CC Мосжилинспекция, CE ГКУ ЦСА имени Е.П.Глинки, CF ГКУ НТУ, CG ФСО, CJ
Комитет по туризму, CM ЦУКБ Министерство обороны, CN ЦУКБ.БПЛА Министерство
обороны, CQ ГПБУ Мосэкомониторинг, CT ООО Ситиэнерго and CU Депортамент
гражданского строительства. Source spelling is retained where it identifies a
column.

Columns sharing an organization but naming distinct source channels are separate
service entries. This prevents a channel-specific cell from silently becoming a
generic organizational notification:

| Column | Service entry |
| --- | --- |
| BE | Департамент РБиПК (ГКУ МОСБЕЗ) — Дежурная служба АРМ-112 |
| BF | Департамент РБиПК (ГКУ МОСБЕЗ) — МКП, Аналитика (Старый КРИМ) |
| CH | ГУП МСР — КУБ |
| CI | ГУП МСР — пожары |
| CK | ДГП — интеграция |
| CL | ДГП — АРМ-112 |
| CR | Министерство обороны РХБЗ — События по полигонам; only with `rhbz_polygon` |
| CS | Министерство обороны РХБЗ — Москва; only with `rhbz_moscow` |

CI currently contains 126 formulas. The importer accepts only direct same-row
references, such as `=K5`, resolves them during import, and rejects every other
formula. It never executes spreadsheet formulas or rewrites the workbook.

## Manual territory and destination flags

The classifier does not contain enough address-to-jurisdiction information to
choose BW, BX or BY. They are disabled unless the operator explicitly confirms
the corresponding boolean. CR and CS also name scope-specific destinations rather
than a rule that can be derived from incident classification. They require manual
confirmation too:

| Flag | Column |
| --- | --- |
| `territorial_oiv` | BW Territorial OIV |
| `territorial_oiv_tinao` | BX Territorial OIV TiNAO |
| `territorial_roads_moscow` | BY Automobile roads of Moscow administrative districts |
| `rhbz_polygon` | CR RKhBZ: events at polygons |
| `rhbz_moscow` | CS RKhBZ: Moscow |

These are source-column destination confirmations, not geocoded real-service
routing or geographic inference. A checked flag still produces no suggestion
when that row's source cell is blank.

## Snapshot and API behavior

`POST /api/v1/student/routing/preview` is side-effect free and contacts no service.
Only entries in `suggestions` are fully resolved. `unresolved` entries require a
human decision and must not be auto-added. Saving records the selected services
and a routing snapshot with classifier hash, source row, flags and rules version.
Existing `core-v1` snapshots remain historical evidence and are not recalculated;
new previews use `full-v2`.

`service_catalog()` returns the ordered service names plus all flag IDs and UI
labels. The original six flags remain unchanged. Added Card booleans are:
`medical_help`, `evacuation`, `fsb_special`, `traffic_blocked`, `tunnel`,
`pedestrian_structure`, `vehicle_structure`, `telecom_object`,
`construction_site`, `culture_listed_object`, `territorial_oiv`,
`territorial_oiv_tinao`, `territorial_roads_moscow`, `rhbz_polygon`, and
`rhbz_moscow`.

The abbreviated source labels `пеш`, `ав`, and `>5 чел / ОД` are not expanded
into factual incident attributes. They are explicit operator choices. The source
also provides no priority between multiple matching conditional cells; all
resolved matches are retained and differing incident types produce a warning.
