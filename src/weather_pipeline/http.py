from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = (
    "ForecastAccuracyDashboard/0.1 "
    "(https://github.com/ZerroCode/Forecast-Accuracy-Dashboard)"
)


class RequestError(RuntimeError):
    pass


def get_json(
    url: str,
    *,
    attempts: int = 3,
    timeout_seconds: int = 30,
) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": os.getenv("WEATHER_USER_AGENT", DEFAULT_USER_AGENT),
    }

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                break
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))

    raise RequestError(f"Request failed after {attempts} attempts: {url}") from last_error
