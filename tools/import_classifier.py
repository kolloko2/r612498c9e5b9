"""Import the supplied incident classifier workbook into a bounded JSON catalog.

Only columns A-D, F-K and M-CU are part of the runtime catalog.  In particular,
the service in column M and the bounded routing cells in N:CU are copied from the
source; this importer does not derive routing.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile

import openpyxl


MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_WORKBOOK_BYTES = 16 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 256
SCHEMA_VERSION = 3
ROUTING_FIRST_COLUMN = 14  # N
ROUTING_LAST_COLUMN = 99  # CU
DIRECT_REFERENCE = re.compile(r"=\s*\$?([A-Z]{1,3})\$?(\d+)\s*", re.IGNORECASE)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _text(value: Any) -> str:
    """Preserve spreadsheet text, representing a genuinely blank cell as ''."""
    return "" if value is None else str(value)


def _read_source(source: Path, member_name: str | None = None) -> tuple[bytes, str | None]:
    size = source.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise ValueError(f"source exceeds {MAX_SOURCE_BYTES} bytes")
    if source.suffix.lower() == ".xlsx":
        if size > MAX_WORKBOOK_BYTES:
            raise ValueError(f"workbook exceeds {MAX_WORKBOOK_BYTES} bytes")
        return source.read_bytes(), None
    if source.suffix.lower() != ".zip":
        raise ValueError("source must be an .xlsx workbook or a .zip containing one")

    try:
        with ZipFile(source) as archive:
            candidates = [item for item in archive.infolist() if item.filename.lower().endswith(".xlsx")]
            if member_name is not None:
                candidates = [item for item in candidates if item.filename == member_name]
            if len(candidates) != 1:
                raise ValueError(
                    f"expected exactly one XLSX member, found {len(candidates)}; "
                    "use --member when the archive contains more than one"
                )
            member = candidates[0]
            if member.file_size > MAX_WORKBOOK_BYTES:
                raise ValueError(f"workbook exceeds {MAX_WORKBOOK_BYTES} bytes")
            return archive.read(member), member.filename
    except BadZipFile as exc:
        raise ValueError("source is not a valid ZIP archive") from exc


def _rows(workbook_bytes: bytes, sheet_name: str | None) -> tuple[str, int, list[tuple[Any, ...]]]:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=False)
    except Exception as exc:
        raise ValueError("unable to read XLSX workbook") from exc
    try:
        if sheet_name is None:
            if len(workbook.sheetnames) != 1:
                raise ValueError("workbook has multiple sheets; use --sheet")
            worksheet = workbook[workbook.sheetnames[0]]
        elif sheet_name not in workbook.sheetnames:
            raise ValueError(f"sheet not found: {sheet_name}")
        else:
            worksheet = workbook[sheet_name]
        if worksheet.max_row > MAX_ROWS or worksheet.max_column > MAX_COLUMNS:
            raise ValueError(
                f"worksheet dimensions {worksheet.max_row}x{worksheet.max_column} exceed "
                f"the {MAX_ROWS}x{MAX_COLUMNS} import limit"
            )
        # Ninety-nine columns are sufficient through column CU. Keeping rows in memory
        # makes the second pass deterministic while remaining within the hard bounds.
        values = list(worksheet.iter_rows(min_col=1, max_col=ROUTING_LAST_COLUMN, values_only=True))
        return worksheet.title, worksheet.max_column, values
    finally:
        workbook.close()


def _routing_value(values: tuple[Any, ...], source_row: int, column: int) -> Any:
    """Resolve only a direct same-row reference in an executable routing cell."""
    value = values[column - 1]
    seen: set[int] = set()
    while isinstance(value, str) and value.startswith("="):
        match = DIRECT_REFERENCE.fullmatch(value)
        if match is None or int(match.group(2)) != source_row:
            coordinate = f"{openpyxl.utils.get_column_letter(column)}{source_row}"
            raise ValueError(f"unsupported routing formula in {coordinate}: {value}")
        target = openpyxl.utils.column_index_from_string(match.group(1))
        if target < 1 or target > len(values) or target in seen:
            coordinate = f"{openpyxl.utils.get_column_letter(column)}{source_row}"
            raise ValueError(f"unsupported routing formula in {coordinate}: {value}")
        seen.add(target)
        value = values[target - 1]
    return value


def build_catalog(
    source: str | Path,
    *,
    member_name: str | None = None,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    """Build a normalized catalog without writing it to disk."""
    source_path = Path(source)
    workbook_bytes, resolved_member = _read_source(source_path, member_name)
    selected_sheet, source_columns, rows = _rows(workbook_bytes, sheet_name)
    digest = hashlib.sha256(workbook_bytes).hexdigest()

    explicit_titles: dict[str, str] = {}
    candidates: dict[str, set[str]] = defaultdict(set)
    raw_records: list[dict[str, Any]] = []
    ambiguous_rows: list[int] = []

    for source_row, values in enumerate(rows, start=1):
        codes = tuple(_integer(value) for value in values[:4])
        if all(code is not None for code in codes):
            group, p1, p2, p3 = (int(code) for code in codes)
            if group < 0 or p1 < 0 or p2 < 0 or p3 < 0:
                ambiguous_rows.append(source_row)
                continue
            incident_type = _text(values[10])
            if not incident_type:
                ambiguous_rows.append(source_row)
                continue
            group_id = str(group)
            row_group_title = _text(values[5])
            if row_group_title:
                candidates[group_id].add(row_group_title)
            code = group * 1_000_000 + p1 * 10_000 + p2 * 100 + p3
            raw_records.append(
                {
                    "code": str(code),
                    "group_id": group_id,
                    "features": [_text(values[6]), _text(values[7]), _text(values[8])],
                    "incident_type": incident_type,
                    "main_service": _text(values[12]),
                    "additional_details": _text(values[9]),
                    "routing_cells": {},
                    "source_row": source_row,
                }
            )
            for column in range(ROUTING_FIRST_COLUMN, ROUTING_LAST_COLUMN + 1):
                resolved = _text(_routing_value(values, source_row, column))
                if resolved != "":
                    raw_records[-1]["routing_cells"][
                        openpyxl.utils.get_column_letter(column)
                    ] = resolved
            continue

        # Group heading rows have blank A-D, an integral group number in E, and
        # a title in F.  They are metadata, never templates for filling data rows.
        heading_id = _integer(values[4])
        heading_title = _text(values[5])
        if all(value is None for value in values[:4]) and heading_id is not None and heading_title:
            key = str(heading_id)
            if key in explicit_titles and explicit_titles[key] != heading_title:
                ambiguous_rows.append(source_row)
            else:
                explicit_titles[key] = heading_title

    duplicate_codes = sorted(
        code for code, count in Counter(record["code"] for record in raw_records).items() if count > 1
    )
    duplicate_set = set(duplicate_codes)
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        code = raw.pop("code")
        # Domain codes are the stable identifier.  A source-row suffix keeps IDs
        # deterministic and unique if a future source introduces a duplicate.
        raw["id"] = f"{code}@row-{raw['source_row']}" if code in duplicate_set else code
        records.append(
            {
                "id": raw["id"],
                "group_id": raw["group_id"],
                "features": raw["features"],
                "incident_type": raw["incident_type"],
                "main_service": raw["main_service"],
                "additional_details": raw["additional_details"],
                "routing_cells": raw["routing_cells"],
                "source_row": raw["source_row"],
            }
        )

    groups: list[dict[str, str]] = []
    missing_group_titles: list[str] = []
    group_ids = sorted({record["group_id"] for record in records}, key=int)
    for group_id in group_ids:
        title = explicit_titles.get(group_id)
        if title is None and len(candidates[group_id]) == 1:
            # This covers a group whose sheet omits the numbered heading while its
            # own data rows consistently name it (currently the final BPLA group).
            title = next(iter(candidates[group_id]))
        if title is None:
            missing_group_titles.append(group_id)
            title = ""
        groups.append({"id": group_id, "title": title})

    if missing_group_titles:
        ambiguous_rows.extend(
            record["source_row"] for record in records if record["group_id"] in missing_group_titles
        )

    source_info: dict[str, Any] = {
        "file": source_path.name,
        "workbook": resolved_member or source_path.name,
        "sheet": selected_sheet,
        "sha256": digest,
    }
    skipped_count = len(rows) - len(raw_records)
    return {
        "version": f"sha256:{digest}",
        "source": source_info,
        "groups": groups,
        "records": records,
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "source_rows": len(rows),
            "source_columns": source_columns,
            "imported_records": len(records),
            "skipped_rows": skipped_count,
            "ambiguous_count": len(set(ambiguous_rows)),
            "ambiguous_rows": sorted(set(ambiguous_rows)),
            "duplicate_code_count": len(duplicate_codes),
            "duplicate_codes": duplicate_codes,
        },
    }


def write_catalog(catalog: dict[str, Any], output: str | Path) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source XLSX or ZIP archive")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "backend" / "data" / "classifier.json",
    )
    parser.add_argument("--member", help="exact XLSX member name for a multi-workbook ZIP")
    parser.add_argument("--sheet", help="worksheet name when the workbook has multiple sheets")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    catalog = build_catalog(args.source, member_name=args.member, sheet_name=args.sheet)
    write_catalog(catalog, args.output)
    metadata = catalog["metadata"]
    print(
        f"Imported {metadata['imported_records']} records, "
        f"skipped {metadata['skipped_rows']}, ambiguous {metadata['ambiguous_count']} "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
