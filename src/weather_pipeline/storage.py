from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
    return rows


def _key(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    try:
        return tuple(row[field] for field in fields)
    except KeyError as exc:
        raise ValueError(f"Row is missing key field {exc.args[0]!r}") from exc


def upsert_jsonl(
    path: Path,
    incoming: Iterable[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
    replace: bool = False,
    ignore_compare_fields: frozenset[str] = frozenset(),
) -> int:
    existing = read_jsonl(path)
    rows_by_key = {_key(row, key_fields): row for row in existing}
    changes = 0

    for row in incoming:
        row_key = _key(row, key_fields)
        current = rows_by_key.get(row_key)
        if current is None:
            rows_by_key[row_key] = row
            changes += 1
            continue
        if not replace:
            continue

        comparable_current = {
            key: value for key, value in current.items() if key not in ignore_compare_fields
        }
        comparable_new = {
            key: value for key, value in row.items() if key not in ignore_compare_fields
        }
        if comparable_current != comparable_new:
            rows_by_key[row_key] = row
            changes += 1

    if changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        ordered_rows = [rows_by_key[key] for key in sorted(rows_by_key)]
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in ordered_rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
        temporary_path.replace(path)
    return changes


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(rendered, encoding="utf-8", newline="\n")
    temporary_path.replace(path)
