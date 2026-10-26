# Collected data

The collection workflow writes deterministic, date-partitioned files here:

- `forecasts/YYYY/MM/YYYY-MM-DD.jsonl` contains normalized next-day forecasts.
- `observations/YYYY/MM/YYYY-MM-DD.jsonl` contains NOAA/NCEI daily outcomes.
- `raw/<provider>/...` preserves source responses only when the collector runs
  with `--save-raw` and the source permits archival storage. Scheduled runs store
  only the normalized records. Existing raw archives are left in place.

JSONL files contain one JSON object per line. The `schema_version` field allows
future migrations without silently changing existing records.

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
