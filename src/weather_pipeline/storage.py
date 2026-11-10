from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _read_text(path: Path, encryption_key: str | None) -> str:
    if path.suffix == ".enc":
        from weather_pipeline.encryption import decrypt

        if not encryption_key:
            raise ValueError("An encryption key is required to read this archive")
        return decrypt(path.read_bytes(), encryption_key)
    if encryption_key is not None:
        raise ValueError("Encrypted archives must use the .enc extension")
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, rendered: str, encryption_key: str | None) -> None:
    if path.suffix == ".enc":
        from weather_pipeline.encryption import encrypt

        if not encryption_key:
            raise ValueError("An encryption key is required to write this archive")
        content = encrypt(rendered, encryption_key)
    else:
        if encryption_key is not None:
            raise ValueError("Encrypted archives must use the .enc extension")
        content = rendered.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    # The temporary file also contains only ciphertext for encrypted archives.
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def read_jsonl(path: Path, *, encryption_key: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(_read_text(path, encryption_key).splitlines(), start=1):
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
    encryption_key: str | None = None,
) -> int:
    existing = read_jsonl(path, encryption_key=encryption_key)
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
        ordered_rows = [rows_by_key[key] for key in sorted(rows_by_key)]
        rendered = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                           for row in ordered_rows)
        _write_text(path, rendered, encryption_key)
    return changes


def write_json(path: Path, value: Any, *, encryption_key: str | None = None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists() and _read_text(path, encryption_key) == rendered:
        return
    _write_text(path, rendered, encryption_key)
