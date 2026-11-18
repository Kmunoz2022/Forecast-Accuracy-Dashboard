"""Encrypt permitted live archives in place; make no API requests or Git history changes."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from weather_pipeline.archive import MIGRATION_PROVIDERS, archive_provider
from weather_pipeline.encryption import MAGIC, MAGIC_V2, configured_key, decrypt, encrypt, key_id, validate_key

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"forecasts": ("provider", "station_id", "target_date_local", "snapshot_date_utc"),
        "market_forecasts": ("provider", "event_ticker", "market_open_time_utc")}


def read_key_file(path: Path) -> str:
    """Accept a private raw key file or a DATA_ENCRYPTION_KEY line in an env file."""
    content = path.read_text(encoding="utf-8").strip()
    lines = [line.split("=", 1)[1].strip().strip("\"'") for line in content.splitlines()
             if line.startswith("DATA_ENCRYPTION_KEY=")]
    if len(lines) > 1:
        raise ValueError("Key file has multiple DATA_ENCRYPTION_KEY entries")
    return validate_key(lines[0] if lines else content)


@dataclass
class Migration:
    inputs: dict[Path, bytes | None] = field(default_factory=dict, repr=False)
    writes: dict[Path, bytes | None] = field(default_factory=dict, repr=False)
    forecast_rows: int = 0
    raw_files: int = 0

    def read(self, path):
        if path not in self.inputs:
            self.inputs[path] = path.read_bytes() if path.exists() else None
        return self.inputs[path]

    def unchanged(self):
        for path, original in self.inputs.items():
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise ValueError(f"Source changed during migration: {path.name}; rerun before collecting again")

    def apply(self):
        """Verify all inputs, stage every output, then replace encrypted files first.

        No original plaintext is removed until its verified encrypted destination
        exists. If interrupted, rerunning merges identical duplicates and resumes.
        """
        self.unchanged()
        staged = {}
        try:
            for path, content in self.writes.items():
                if content is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + ".migration.tmp")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                staged[path] = temporary
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if temporary.read_bytes() != content:
                    raise OSError("Migration staging verification failed")
            self.unchanged()
            for path in sorted(self.writes, key=lambda p: (p.suffix != ".enc", str(p))):
                original = self.inputs[path]
                if (path.read_bytes() if path.exists() else None) != original:
                    raise ValueError(f"Source changed during migration: {path.name}; no source data was discarded")
                if self.writes[path] is None:
                    path.unlink()
                else:
                    staged[path].replace(path)
        finally:
            for path in staged.values():
                path.unlink(missing_ok=True)


def plan_migration(root: Path, key: str, *, providers=MIGRATION_PROVIDERS, legacy_key=None, previous_key=None):
    validate_key(key)
    if previous_key:
        validate_key(previous_key)
    providers = frozenset(providers)
    if not providers <= MIGRATION_PROVIDERS:
        raise ValueError("Migration is limited to providers approved for this archive; Weatherbit and Tomorrow.io are excluded")
    plan = Migration()

    def decode(path, content):
        if content.startswith(MAGIC):
            if archive_provider(path) != "visual_crossing" or not legacy_key:
                raise ValueError(f"Original VISUALCROSSING_API_KEY is required for {path.name}; no files changed")
            return decrypt(content, legacy_key)
        if content.startswith(MAGIC_V2):
            identifier = content[len(MAGIC_V2):].split(b"\n", 1)[0]
            chosen = previous_key if previous_key and identifier == key_id(previous_key).encode() else key
            return decrypt(content, chosen)
        raise ValueError(f"Unsupported archive format: {path.name}; no files changed")

    def seal(path, text):
        content = encrypt(text, key)
        if decrypt(content, key) != text:
            raise ValueError("Encrypted archive verification failed")
        plan.writes[path] = content

    def merge_rows(existing, incoming, provider, fields):
        indexed = {}
        for row in [*existing, *incoming]:
            if not isinstance(row, dict) or row.get("provider") != provider:
                raise ValueError("Unexpected provider in archive; no files changed")
            try:
                identity = tuple(row[field] for field in fields)
            except (KeyError, TypeError):
                raise ValueError("Snapshot identity is incomplete; no files changed") from None
            if identity in indexed and indexed[identity] != row:
                raise ValueError("Conflicting original snapshots; no files changed")
            indexed[identity] = row
        return [indexed[k] for k in sorted(indexed)]

    for category, fields in KEYS.items():
        incoming = {}
        for source in sorted((root / category).rglob("*.jsonl")):
            content = plan.read(source)
            remaining, changed = [], False
            for line in content.decode("utf-8").splitlines(keepends=True):
                if not line.strip():
                    remaining.append(line)
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Forecast rows must be objects; no files changed")
                provider = row.get("provider")
                if provider in providers:
                    destination = source.with_name(f"{source.name}.{provider}.enc")
                    incoming.setdefault(destination, []).append(row)
                    plan.forecast_rows += 1
                    changed = True
                else:
                    remaining.append(line)
            if changed:
                text = "".join(remaining)
                plan.writes[source] = text.encode("utf-8") if text.strip() else None
        destinations = set(incoming) | {p for p in (root / category).rglob("*.enc") if archive_provider(p) in providers}
        for path in sorted(destinations):
            original = plan.read(path)
            text = decode(path, original) if original is not None else ""
            existing = [json.loads(line) for line in text.splitlines() if line.strip()]
            merged = merge_rows(existing, incoming.get(path, []), archive_provider(path), fields)
            current_key = MAGIC_V2 + key_id(key).encode() + b"\n"
            if original is not None and original.startswith(current_key) and merged == existing:
                continue
            rendered = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in merged)
            seal(path, rendered)

    for provider in sorted(providers):
        raw_root = root / "raw" / provider
        incoming = {}
        for source in sorted(raw_root.rglob("*.json")):
            content = plan.read(source)
            value = json.loads(content)
            destination = source.with_name(f"{source.name}.{provider}.enc")
            incoming[destination] = value
            plan.writes[source] = None
            plan.raw_files += 1
        destinations = set(incoming) | {p for p in raw_root.rglob("*.enc") if archive_provider(p) == provider}
        for path in sorted(destinations):
            original = plan.read(path)
            existing = json.loads(decode(path, original)) if original is not None else None
            if path in incoming and original is not None and existing != incoming[path]:
                raise ValueError("Conflicting original raw responses; no files changed")
            value = incoming[path] if path in incoming else existing
            if original is not None and original.startswith(MAGIC_V2 + key_id(key).encode() + b"\n"):
                continue
            seal(path, json.dumps(value, indent=2, sort_keys=True) + "\n")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--providers", nargs="+", choices=sorted(MIGRATION_PROVIDERS), default=sorted(MIGRATION_PROVIDERS))
    parser.add_argument("--key-file", type=Path, help="Private DATA_ENCRYPTION_KEY file; otherwise use the environment")
    parser.add_argument("--previous-key-file", type=Path, help="Previous dedicated key, only when rotating archives")
    parser.add_argument("--apply", action="store_true", help="Apply the verified migration; default is a dry run")
    args = parser.parse_args()
    try:
        key = read_key_file(args.key_file) if args.key_file else configured_key()
        previous = read_key_file(args.previous_key_file) if args.previous_key_file else None
        plan = plan_migration(args.data_dir, key, providers=args.providers,
                              legacy_key=os.getenv("VISUALCROSSING_API_KEY"), previous_key=previous)
        if args.apply:
            plan.apply()
    except (ValueError, OSError, ImportError) as exc:
        # JSON decoder messages can contain data; keep errors independent of payloads.
        message = "Invalid JSON in an archive; no migration applied" if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(f"Migration stopped: {message}")
        return 1
    print(f"{'Applied' if args.apply else 'Dry run'}: {len(plan.writes)} file changes; "
          f"{plan.forecast_rows} plaintext forecast rows and {plan.raw_files} raw files selected. "
          f"Archive key ID: {key_id(key)}. Weatherbit and Tomorrow.io are not migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
