# Collected data

The collection workflow writes deterministic, date-partitioned files here:

- `forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains normalized next-day forecasts.
- `observations/YYYY/MM/YYYY-MM-DD.jsonl` contains NOAA/NCEI daily outcomes.
- `raw/open_meteo/...` and `raw/ncei/...` preserve source responses where the
  source license permits archival storage.

JSONL files contain one JSON object per line. The `schema_version` field allows
future migrations without silently changing existing records.

Open-Meteo data requires attribution under CC BY 4.0. NOAA/NCEI data is U.S.
government open data. Do not add another provider's raw or normalized data until
its terms explicitly permit long-term storage and the intended redistribution.
Use Open-Meteo's commercial endpoint and plan for commercial workloads.

OpenWeather records generated from the free five-day endpoint contain extrema
derived from three-hour forecast points; they are not native daily extrema.
Weatherbit and Tomorrow.io output is written only when the corresponding
archival-authorization repository variable is enabled.
