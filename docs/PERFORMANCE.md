# Performance

Measured on 2026-11-15 on a developer laptop (12 logical cores, 16 GB RAM,
Windows 11, Python 3.13), one Uvicorn worker. **The free host has about a tenth of
one core and 512 MB**, so treat these as a best case; Sprint 5 repeats the key
numbers on the host.

## Load test (Locust, 20 concurrent users)

Each simulated user waits 1 to 3 seconds between actions and either opens the
page, lists the samples, or runs a whole analysis: upload a 2,000-row CSV, start
the job, poll once a second, download the CSV, delete the data. A 429 counts as a
correct answer; any 5xx counts as a failure. Rerun with `./scripts/load_test.ps1`.

### A. Per-address limits raised (all 20 users share one address)

60 seconds, 1,482 requests, **0 failures, 0 server errors**, peak memory 329 MB.

| Endpoint | Requests | Median | p95 | Max |
|---|---|---|---|---|
| `GET /` | 227 | 6 ms | 87 ms | 247 ms |
| `GET /static/app.css`, `app.js` | 454 | 5 ms | 65 ms | 181 ms |
| `GET /api/samples` | 145 | 3 ms | 43 ms | 241 ms |
| `GET /healthz` | 42 | 5 ms | 54 ms | 244 ms |
| `POST /api/upload` (300 KB CSV) | 105 | 49 ms | 240 ms | 443 ms |
| `POST /api/analyse` | 104 | 2 ms | 10 ms | 41 ms |
| `GET /api/jobs/{id}` | 205 | 6 ms | 68 ms | 140 ms |
| `GET /api/report/{id}` (CSV) | 100 | 20 ms | 95 ms | 179 ms |
| `DELETE /api/datasets/{id}` | 100 | 3 ms | 9 ms | 58 ms |
| **All** | **1,482** | **5 ms** | **83 ms** | **443 ms** |

Target: p95 under 500 ms for everything except analysis. Met, with the upload
itself the slowest at 240 ms.

### B. Real limits (10 uploads an hour per address)

30 seconds, 540 requests, **0 failures, 0 server errors**, peak memory 327 MB.
Of 62 upload attempts, 10 were accepted and 52 were turned away with a 429 and a
`Retry-After` header, in 2 ms at the median. Page and static requests were
unaffected (p95 31 ms).

### What this did not exercise

- The job queue never filled: a 2,000-row job finishes in about a second, so two
  workers kept up with 20 users. The queue cap, queue position, cancellation and
  the time limit are covered by tests in `tests/integration/test_security.py`.
- Large files. Memory was measured with 300 KB uploads. One 50,000-row dataset
  held 183 MB in Sprint 1; several at once would not fit in 512 MB. The host
  setting for how many datasets are kept (`SL_MAX_DATASETS`, default 8) must be
  chosen from a measurement on the host.

## Single-request timings

| What | Time |
|---|---|
| Profile 50,000 rows | 0.32 s |
| Upload and profile 50,000 rows: CSV / Parquet / JSONL / XLSX | 0.42 / 0.32 / 0.56 / 3.04 s |
| Score 50,000 reviews (430 characters on average) with explanations | 22 s |
| Model load at first use | 1.2 s |
| Server start to first `/healthz` answer | 1.5 s |

## Size

Page (HTML + CSS + JS) 42 KB uncompressed, fonts 100 KB, model 3.2 MB.
