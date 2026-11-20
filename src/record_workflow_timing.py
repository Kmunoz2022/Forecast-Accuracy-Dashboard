"""Attach GitHub trigger/job timing after collection, outside the capture path."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from weather_pipeline.http import get_json
from weather_pipeline.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def annotate(root: Path, *, environ=os.environ, fetch=get_json) -> int:
    repo, run_id = environ.get("GITHUB_REPOSITORY"), environ.get("GITHUB_RUN_ID")
    if not repo or not run_id:
        return 0
    attempt = environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    headers = {"Accept": "application/vnd.github+json"}
    if environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + environ["GITHUB_TOKEN"]
    try:
        run = fetch(base, headers=headers, attempts=1, timeout_seconds=10)
        jobs = fetch(f"{base}/attempts/{attempt}/jobs?per_page=100", headers=headers,
                     attempts=1, timeout_seconds=10)
        job = next((j for j in jobs["jobs"] if j["name"] == environ.get("GITHUB_JOB")), {})
        timing = {"timing_status": "available" if job else "job_not_found",
                  "workflow_created_at_utc": run.get("created_at"),
                  "workflow_started_at_utc": run.get("run_started_at"),
                  "job_started_at_utc": job.get("started_at"),
                  "workflow_url": run.get("html_url")}
    except Exception:
        # Diagnostic failure must not erase a successful forecast capture.
        timing = {"timing_status": "unavailable"}
    for path in (root / "collection_runs").rglob(f"*-{run_id}-{attempt}.json"):
        data = json.loads(path.read_text())
        data.update(timing)
        write_json(path, data)
    print(f"Workflow timing: {timing['timing_status']}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    raise SystemExit(annotate(parser.parse_args().data_dir))
