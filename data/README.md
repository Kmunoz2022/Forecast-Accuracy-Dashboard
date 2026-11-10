# Collected data

The collection workflow writes deterministic, date-partitioned files here:

- `forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains normalized next-day forecasts.
- `forecasts/YYYY/MM/YYYY-MM-DD.jsonl.visual_crossing.enc` contains encrypted
  Visual Crossing next-day forecasts with the same normalized schema.
- `observations/YYYY/MM/YYYY-MM-DD.jsonl` contains NOAA/NCEI daily outcomes.
- `markets/YYYY/MM/YYYY-MM-DD.jsonl` contains contract rules and source metadata,
  partitioned by the market's temperature date and keyed by ticker/rules version.
- `market_forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains immutable first forecasts
  after a market opens, keyed by provider/event/opening timestamp. These use the
  shared `config/stations.csv` registry and a 24-hour local-standard-time window.
- `market_forecasts/YYYY/MM/YYYY-MM-DD.jsonl.visual_crossing.enc` contains
  encrypted Visual Crossing market snapshots with the same immutability rules.
- `market_collection/YYYY/MM/YYYY-MM-DD.jsonl` records capture status, including
  missed openings and provider failures that must not be mistaken for data.
- `raw/<provider>/...` preserves source responses only when the collector runs
  with `--save-raw` and the source permits archival storage. Scheduled runs store
  only the normalized records. Existing raw archives are left in place.
- `private/visual_crossing/` holds local decrypted exports and is ignored by Git.

Unencrypted JSONL files contain one JSON object per line. The `schema_version` field allows
future migrations without silently changing existing records.

Market forecasts use schema version 2 and link to the exact contract rule versions.
They preserve the actual fetch time, reporting-window start/end, and opening delay.
Only `eligible_for_opening_comparison=true` rows meet the configured timing tolerance;
other retained forecasts are marked `late`. The original daily research snapshots
are not replaced by market snapshots. Do not join O'Hare/Love Field observations to
Midway/DFW markets or treat NOAA/NCEI as The Weather Company's settlement report.
All future collection uses the Kalshi stations in the shared registry, including
Midway and DFW. Legacy O'Hare/Love Field rows remain unchanged for provenance and
must be excluded from Kalshi station comparisons. The NOAA lookback automatically
adds available Midway/DFW observations; historical forecasts cannot be recreated.

Open-Meteo data requires attribution under CC BY 4.0. NOAA/NCEI data is U.S.
government open data. Do not add another provider's raw or normalized data until
its terms explicitly permit long-term storage and the intended redistribution.
Use Open-Meteo's commercial endpoint and plan for commercial workloads.

OpenWeather records generated from the free five-day endpoint contain extrema
derived from three-hour forecast points; they are not native daily extrema.
New rows aggregate `main.temp` and use the method
`max_min_of_3_hour_temperature`. Older `max_min_of_3_hour_points` records used
spatial min/max values and must be distinguished in analysis.
NWS and Tomorrow.io extrema are derived from hourly temperatures. All sampled
providers now reject incomplete local-day coverage and store sample counts;
daylight saving transitions are handled as 23-hour or 25-hour days.
Weatherbit and Tomorrow.io output is written only when the corresponding
archival-authorization repository variable is enabled.

## Pirate Weather and Visual Crossing

Both providers use hourly temperature samples, with full 23/24/25-hour local-day
coverage for morning snapshots and full 24-hour standard-time coverage for market
snapshots. Pirate Weather's daytime `temperatureHigh` and overnight
`temperatureLow` are not used as full-day extrema. Visual Crossing requests only
the civil dates needed by the window and `include=hours,fcst`; every included
hour must have `source=fcst`. Neither adapter requests historical data. Retrieved
times are collection times, not claims about model initialization times.

Attribution is included in every normalized record:
- [Pirate Weather](https://pirateweather.net/) ([API documentation](https://docs.pirateweather.net/en/latest/API/))
- [Visual Crossing](https://www.visualcrossing.com/) ([Timeline documentation](https://www.visualcrossing.com/resources/documentation/weather-api/timeline-weather-api/))

[Visual Crossing's storage guidance](https://www.visualcrossing.com/resources/documentation/can-i-cache-and-store-visual-crossing-weather-data/)
permits application archives but restricts public data downloads. All its forecast
records and optional raw payloads are therefore encrypted before writing, including
temporary files. Public collection audits contain status/timing metadata, not
temperatures. The format is versioned (`FAD1`), with a random salt, scrypt-derived
key, and authenticated Fernet encryption. The only key source is the existing
`VISUALCROSSING_API_KEY` secret. Preserve old keys securely if credentials rotate;
old files still need the key originally used. Wrong keys or damaged ciphertext
fail without replacing existing data.

`python src/export_private_forecasts.py` decrypts normalized snapshots under
`data/private/visual_crossing/` using the locally exported key and makes no network
requests. Keep those files private. The scheduled workflows never decrypt to disk
or upload plaintext artifacts. Pirate Weather is served from public weather models;
its code license is not a statement about every upstream dataset's license. Preserve
provider attribution and review applicable data terms before redistributing raw feeds.
