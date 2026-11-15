# Security

## What is protected

1. **Uploaded data.** It may hold customer names, complaints, anything.
2. **The service itself.** One small free instance (512 MB, a fraction of a CPU).
3. **The people who open an export.** A CSV or report must not attack its reader.

There are no accounts, no passwords and no database, so there is nothing to steal
beyond what a user uploaded in the last 30 minutes.

## Threat model (STRIDE)

| Threat | How it could happen here | What stops it | Where |
|---|---|---|---|
| **Spoofing** | Reading someone else's upload by guessing its ID | IDs are random 128-bit UUIDs; an unknown ID is a 404 | `services/store.py`, `services/jobs.py` |
| | Forging `X-Forwarded-For` to dodge rate limits | The header is ignored unless `SL_TRUSTED_PROXY_HOPS` says a proxy is in front, and then only the proxy's own entry is used | `core/limits.py` |
| **Tampering** | A file that runs code when opened (pickle, joblib, macros) | Extension allow-list plus content check; pickle and joblib are never accepted; macro workbooks are refused; XLSX is read values-only | `services/ingest.py` |
| | A swapped model file | SHA-256 recorded at training, checked before loading | `ml/registry.py` |
| | A tampered dependency | Every package pinned with hashes; `pip install --require-hashes` | `requirements.txt` |
| **Repudiation** | An error nobody can trace | Every reply has an `X-Request-ID`; error logs carry the same ID | `main.py` |
| **Information disclosure** | Uploaded text leaking into logs | Logs record error types and code locations only, never messages or rows | `main.py`, `services/jobs.py` |
| | Data left behind | Memory only, never disk; removed after 30 minutes by a sweeper, or at once on "Start again"; a test checks the rows are really freed | `main.py` (`sweep`), `services/store.py` |
| | Uploaded text running as script in the page (XSS) | The page inserts text with `textContent` only; the report escapes everything; a content security policy blocks inline and third-party script | `static/app.js`, `services/export.py`, `main.py` |
| | Stack traces or internals in replies | Generic error messages; API docs endpoints are off | `main.py` |
| | A browser caching API replies | `Cache-Control: no-store` on every API reply | `main.py` |
| **Denial of service** | A huge upload | 20 MB cap counted while streaming; connection dropped at 40 MB | `api/routes.py` |
| | A small file that unpacks to gigabytes | Size and ratio limits for XLSX, text budgets for XLSX and Parquet, depth and container limits for JSON | `services/ingest.py` |
| | Flooding with requests | Per-address limits: 10 uploads and 20 analyses an hour, 120 status checks and 300 requests a minute | `core/limits.py` |
| | Many heavy jobs at once | 2 analyses run, 6 wait, the rest get 429; 3 uploads are read at once; a job is stopped after 5 minutes; jobs can be cancelled | `services/jobs.py`, `api/routes.py` |
| **Elevation of privilege** | Breaking out of the app | Container runs as a non-root user on a read-only file system; no shell tools added | `Dockerfile` |
| | A formula in an export running on the reader's machine | Cells starting with `= + - @` are prefixed with `'` in CSV and XLSX | `core/security.py` |

## HTTP headers

Sent on every reply: a content security policy (`default-src 'none'`, scripts,
styles and fonts from this site only, no framing), HSTS, `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a
`Permissions-Policy` that switches off camera, microphone and location, and
same-origin opener and resource policies. No CORS headers are sent, so other
sites cannot call the API from a browser.

## Known gaps

- **Parsing cannot be interrupted.** A file is bounded by size limits, not by a
  timer; the 5-minute limit applies to scoring only.
- **Limits are per process.** They reset on restart and would not be shared
  between replicas. Fine for one instance; needs Redis beyond that.
- **Addresses behind one NAT share a limit.** An office of ten people gets ten
  uploads an hour between them.
- **No authentication.** Anyone with a dataset ID can read that dataset for 30
  minutes. The ID is unguessable, but it is the only secret.
- **HTTPS is the host's job.** The app sends HSTS but does not terminate TLS.
- **Not yet done:** container image scan (Trivy) in CI, GitHub secret scanning
  (needs the repository), an outside penetration test.

## Reporting a problem

Open an issue on the repository, without including any real personal data.
