# Sentiment Lens

Upload a table with a text column (reviews, survey answers, tickets) and get a
plain dashboard: what the data looks like, and how positive or negative it is.

Status: Sprint 4 done. The whole flow works in the browser: upload a file (or
pick a sample), check the columns, watch the progress bar, read the dashboard,
download the results. It is hardened (security headers, rate limits, a bounded
job queue, timed deletion) and load-tested.

It is not hosted anywhere: this repository is the project. Run it on your own
machine with the steps below, or from the Docker image.

Security design and known gaps: [docs/SECURITY.md](docs/SECURITY.md). Load-test
results: [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

Start the app (see "Run locally" below) and open http://127.0.0.1:8000.

## The dashboard

Nine sections, each opening with one plain sentence: summary, data overview,
sentiment split, sentiment over time, sentiment by category, what drives it,
examples, about the model, downloads. Downloads are the scored rows as CSV or
XLSX and a one-page HTML report (print it to get a PDF). Design rules and
measured colour contrast: [docs/DESIGN.md](docs/DESIGN.md).

## The model

TF-IDF + logistic regression, trained on 150,000 Amazon reviews. 92.9% accurate
on held-out reviews and 89.2% on sentences about restaurants, films and phones
(a rule-based baseline, VADER, gets 71.7% and 75.5%). It is English-only and
loses accuracy on text unlike product reviews. Details, calibration and limits:
[docs/MODEL_CARD.md](docs/MODEL_CARD.md). Retrain with `python -m app.ml.train`.

## What works now

Upload CSV, TSV, TXT, XLSX, JSON, JSONL or Parquet, up to 20 MB:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/upload -H "X-Filename: reviews.csv" --data-binary "@reviews.csv"
```

The reply holds a `dataset_id`, the guessed text, date and category columns, a
profile (column kinds, missing values, duplicates, text lengths, rows over time,
category counts), the first ten rows, and any warnings.

| Call | Does |
|---|---|
| `POST /api/upload` | Upload a file (raw body, name in `X-Filename`) |
| `GET /api/datasets/{id}` | Get the description again |
| `PUT /api/datasets/{id}/mapping` | Choose the columns yourself: `{"text": "...", "date": null, "category": "..."}` |
| `DELETE /api/datasets/{id}` | Delete the data now |
| `GET /api/samples`, `POST /api/samples/{id}` | List the sample datasets, load one |
| `POST /api/analyse` | Start scoring: `{"dataset_id": "..."}` returns a `job_id` |
| `GET /api/jobs/{id}` | Progress (or place in the queue), then the dashboard data |
| `DELETE /api/jobs/{id}` | Cancel an analysis |
| `GET /api/report/{id}?format=html\|csv\|xlsx` | Download the report or the scored rows |

Limits (each can be changed with an `SL_...` environment variable, see
`app/core/config.py`): 20 MB per file, first 100,000 rows, 200 columns, cells cut
at 10,000 characters, data forgotten after 30 minutes.

How the upload checks work, and why: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run locally (Windows, PowerShell)

Everything stays inside this folder, including the pip cache.

```powershell
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"; $env:TEMP = "$PWD\.cache\tmp"; $env:TMP = $env:TEMP
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install --require-hashes -r requirements.txt
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/healthz.

## Checks

```powershell
pre-commit install          # once
pre-commit run --all-files  # ruff (lint + format) and mypy
pytest                      # tests, fails under 80% coverage
bandit -r app -ll
pip-audit --skip-editable --cache-dir .cache\pip-audit
```

After changing dependencies in `pyproject.toml`, rebuild the hash-locked list:

```powershell
pip-compile --generate-hashes --strip-extras --cache-dir .cache\pip-tools --output-file requirements.txt pyproject.toml
```

## Docker

```powershell
docker build -t sentiment-lens .
docker run --rm -p 8000:8000 --read-only --tmpfs /tmp sentiment-lens
```

CI builds the image on every push and fails unless `/healthz` answers within
five seconds as a non-root user.

## Tools, data and licences

Only free, open-source tools and openly licensed data are used. Each one is
listed with its licence and source in [docs/LICENSES.md](docs/LICENSES.md).
Hosting choice and the free-tier limits behind it: [ADR-001](docs/ADRs/001-hosting.md).

## Privacy

Uploaded data is held in memory only, never written to disk and never logged. It
is forgotten after 30 minutes, or at once when you call the delete endpoint.
