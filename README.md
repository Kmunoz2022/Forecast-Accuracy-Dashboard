# Forecast-Accuracy-Dashboard

Problem Statement: Accessing specific 1-day-ahead historical forecasts (what
was predicted exactly 24 hours prior) is difficult because most services purge
short-term forecast data shortly after it expires. But this data is valuable
for the recent rise in weather betting in prediction markets. This project aims
to collect, store, and analyze 1-day-ahead historical forecasts from various
sources for various locations, making it easier to analyze and utilize this
data for research and betting assessment purposes.

This project collects next-day temperature forecasts for exact weather-station
locations, then compares them with NOAA daily high and low observations. The
initial pipeline covers eight U.S. locations commonly referenced by weather
markets and runs automatically with GitHub Actions.

## Current data sources

- **Open-Meteo** supplies the next-local-day high and low forecast. Its weather
  data is available under CC BY 4.0 and is stored with attribution.
- **NOAA/NCEI Daily Summaries** supplies observed station highs and lows. NOAA
  reports can arrive several days late, so the collector rechecks the preceding
  14 days and records corrections without creating duplicates.

The pipeline intentionally does not archive Google Weather, WeatherAPI,
AccuWeather, or free-tier Weatherbit forecasts. Their standard terms limit
forecast retention or archival use. Add a commercial provider only after its
license explicitly allows long-term storage and the intended redistribution.
Open-Meteo's public endpoint is intended for non-commercial use; obtain its
commercial API plan before using this collector in a commercial product.

## Forecast definition

The scheduled workflow runs at 12:17 UTC. For each station, it records the
forecast for the next calendar day in that station's IANA timezone. This is a
repeatable "next-day snapshot" definition; it is not a claim that each value was
issued exactly 24 hours before the eventual daily maximum or minimum.

Each normalized record includes the provider, exact coordinates, station ID,
retrieval timestamp, local target date, temperature values, source URL, and
attribution. Raw permitted responses are also retained for reproducibility.

## Locations

Station metadata lives in [`config/stations.csv`](config/stations.csv). It
contains verified NWS coordinates and the corresponding NOAA/NCEI identifiers
for:

- New York City — Central Park
- Los Angeles — Los Angeles International Airport
- Chicago — O'Hare International Airport
- Miami — Miami International Airport
- Dallas — Dallas Love Field
- Seattle — Seattle-Tacoma International Airport
- Boston — Logan International Airport
- San Francisco — San Francisco International Airport

The `settlement_source` values are deliberately set to `verify_market_rules`.
Market rules can differ or change, and neither an NWS station identifier nor an
NCEI daily summary should be assumed to be the official settlement source until
the exact contract rules are checked.

## Run locally

Python 3.11 or newer is recommended.

```shell
python -m pip install "tzdata>=2025.2"
python -m unittest discover -s tests -v
python src/collect_forecasts.py
python src/collect_observations.py
```

The collectors use only Python's standard library apart from `tzdata`, which is
needed on systems such as Windows that do not include the IANA timezone database.
No API keys are needed for the current providers.

Useful options:

```shell
# Re-run today's forecast snapshot and replace changed values.
python src/collect_forecasts.py --refresh

# Reproducible collection timestamp for testing.
python src/collect_forecasts.py --as-of 2026-10-25T12:17:00Z

# Extend the NOAA correction/backfill window.
python src/collect_observations.py --lookback-days 30
```

Generated file layout and licensing notes are documented in
[`data/README.md`](data/README.md).

## Enable GitHub Actions collection

The workflow at `.github/workflows/collect-weather.yml` runs daily and can also
be started manually from the repository's **Actions** tab. It tests the code,
collects both sources, commits changes under `data/`, and reports a failed run if
either collector had an error.

After merging the workflow into the default branch:

1. Open **Settings → Actions → General → Workflow permissions**.
2. Select **Read and write permissions** and save. The workflow needs this to
   commit collected data with `GITHUB_TOKEN`.
3. Open **Actions → Collect daily weather data → Run workflow** once.
4. Confirm that the run creates forecast and observation files under `data/`.
5. Enable GitHub Actions failure notifications for the repository/account.

If branch protection prevents the bot from pushing to the default branch, either
allow GitHub Actions to bypass that rule for this workflow or change the storage
step to write to an external object store/database.

## Data caveats

- NOAA Daily Summaries are suitable for research comparisons but must be checked
  against each market's named settlement report and rounding rules.
- Provider grid coordinates can differ slightly from the requested station point;
  both are retained in each forecast record.
- Scheduled GitHub workflows can be delayed. A manual trigger is included for
  recovery, and forecast writes are idempotent by provider, station, target date,
  and UTC snapshot date.
- This repository is for research and evaluation, not financial advice.

## Adding another provider

Create an adapter under `src/weather_pipeline/providers/` that returns the same
normalized forecast fields, add fixture-based tests, and call it from the
forecast collector. Before enabling archival collection, document the provider's
retention, attribution, redistribution, and commercial-use terms. Store any API
key as a GitHub Actions repository or environment secret—never in `.env`, source
code, workflow YAML, logs, or committed data.