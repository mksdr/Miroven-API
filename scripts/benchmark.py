"""Bounded read-only HTTP load probe; credentials are read from the environment only."""

import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def measure(base_url, requests, concurrency, cookie=None):
    cookies = (
        {os.environ.get("MIROVEN_BENCH_COOKIE_NAME", "miroven_session"): cookie} if cookie else {}
    )
    path = "/api/v1/account/usage" if cookie else "/api/v1/auth/session"
    expected = 200 if cookie else 401
    with httpx.Client(
        base_url=base_url, cookies=cookies, timeout=10, follow_redirects=False
    ) as client:
        # Warm up the connection before timing the bounded run.
        if client.get(path).status_code != expected:
            raise RuntimeError("Probe returned an unexpected status; check deployment privately")

        def request(_):
            started = time.perf_counter()
            try:
                status = client.get(path).status_code
            except httpx.HTTPError:
                status = 0
            return (time.perf_counter() - started) * 1000, status

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(request, range(requests)))
        elapsed = time.perf_counter() - started
    latencies = sorted(latency for latency, _ in results)
    statuses = {}
    for _, status in results:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "path": path,
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(requests / elapsed, 2),
        "latency_ms": {
            "p50": round(latencies[math.ceil(requests * 0.50) - 1], 2),
            "p95": round(latencies[math.ceil(requests * 0.95) - 1], 2),
            "max": round(latencies[-1], 2),
        },
        "statuses": statuses,
        "failed_requests": sum(status != expected for _, status in results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("base-url must be an HTTP(S) origin without credentials or a path")
    if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 32:
        parser.error("requests must be 1..10000 and concurrency 1..32")
    try:
        report = measure(
            args.base_url, args.requests, args.concurrency, os.environ.get("MIROVEN_BENCH_SESSION")
        )
    except (httpx.HTTPError, RuntimeError):
        parser.exit(1, "Probe failed; inspect connectivity and session privately.\n")
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        # Do not overwrite an earlier measurement.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(rendered)
    print(rendered, end="")
    return int(report["failed_requests"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
