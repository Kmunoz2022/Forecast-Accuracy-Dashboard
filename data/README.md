# Collected data

The collection workflow writes deterministic, date-partitioned files here:

- `forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains normalized next-day forecasts.
- `observations/YYYY/MM/YYYY-MM-DD.jsonl` contains NOAA/NCEI daily outcomes.
- `markets/YYYY/MM/YYYY-MM-DD.jsonl` contains contract rules and source metadata,
  partitioned by the market's temperature date and keyed by ticker/rules version.
- `market_forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains immutable first forecasts
  after a market opens, keyed by provider/event/opening timestamp. These use the
  separate market stations and a 24-hour local-standard-time reporting window.
- `market_collection/YYYY/MM/YYYY-MM-DD.jsonl` records capture status, including
  missed openings and provider failures that must not be mistaken for data.
- `raw/<provider>/...` preserves source responses only when the collector runs
  with `--save-raw` and the source permits archival storage. Scheduled runs store
  only the normalized records. Existing raw archives are left in place.

JSONL files contain one JSON object per line. The `schema_version` field allows
future migrations without silently changing existing records.

Market forecasts use schema version 2 and link to the exact contract rule versions.
They preserve the actual fetch time, reporting-window start/end, and opening delay.
Only `eligible_for_opening_comparison=true` rows meet the configured timing tolerance;
other retained forecasts are marked `late`. The original daily research snapshots
are not replaced by market snapshots. Do not join O'Hare/Love Field observations to
Midway/DFW markets or treat NOAA/NCEI as The Weather Company's settlement report.

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
