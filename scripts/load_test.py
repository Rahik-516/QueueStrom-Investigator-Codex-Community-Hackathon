"""
scripts/load_test.py
--------------------

A dependency-light, single-file load harness.  We deliberately avoid
`locust`/`k6`/`wrk` so the test runs anywhere Python runs.

Usage::

    python scripts/load_test.py --base http://localhost:8000 --n 200 --concurrency 20

It measures:
    * p50 / p95 / p99 latency
    * error rate
    * min / max latency
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass

import httpx


@dataclass
class Sample:
    status: int
    elapsed_ms: float


SAMPLE_BODY = {
    "ticket_id": "TCK-LOAD",
    "customer_id": "CUST-LOAD",
    "complaint": "Load test: I sent 5000 BDT to merchant QuickMart yesterday.",
    "transaction_history": [
        {
            "transaction_id": "TX-LT-1",
            "timestamp": "2026-06-25T10:00:00Z",
            "amount": -5000.0,
            "currency": "BDT",
            "counterparty": "QuickMart",
            "direction": "debit",
            "status": "completed",
        }
    ],
}


async def _one_call(client: httpx.AsyncClient, url: str) -> Sample:
    t0 = time.perf_counter()
    try:
        r = await client.post(url, json=SAMPLE_BODY, timeout=30.0)
        elapsed = (time.perf_counter() - t0) * 1000
        return Sample(r.status_code, elapsed)
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - t0) * 1000
        return Sample(status=0, elapsed_ms=elapsed)


async def _run(base: str, n: int, concurrency: int) -> None:
    url = f"{base.rstrip('/')}/analyze-ticket"
    sem = asyncio.Semaphore(concurrency)
    samples: list[Sample] = []

    async with httpx.AsyncClient() as client:
        async def _wrapped():
            async with sem:
                samples.append(await _one_call(client, url))

        t0 = time.perf_counter()
        await asyncio.gather(*[_wrapped() for _ in range(n)])
        wall = time.perf_counter() - t0

    latencies = sorted(s.elapsed_ms for s in samples)
    statuses = [s.status for s in samples]

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = max(0, min(len(latencies) - 1, int(round(p * (len(latencies) - 1)))))
        return latencies[k]

    print(f"requests:        {n}")
    print(f"concurrency:     {concurrency}")
    print(f"wall time (s):   {wall:.2f}")
    print(f"throughput rps:  {n / wall:.1f}")
    print(f"statuses:        {dict((s, statuses.count(s)) for s in sorted(set(statuses)))}")
    print(f"latency p50 ms:  {pct(0.50):.1f}")
    print(f"latency p95 ms:  {pct(0.95):.1f}")
    print(f"latency p99 ms:  {pct(0.99):.1f}")
    print(f"latency max ms:  {max(latencies):.1f}" if latencies else "n/a")
    print(f"latency mean ms: {statistics.fmean(latencies):.1f}" if latencies else "n/a")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=20)
    args = p.parse_args()
    asyncio.run(_run(args.base, args.n, args.concurrency))


if __name__ == "__main__":
    main()
