# Architecture

State after Sprint 3. One FastAPI process; no database, no disk writes.

```
GET  /                      the page (static HTML, CSS, JS, fonts under /static)
POST /api/analyse           -> jobs.submit(dashboard.analyse)      returns job_id
GET  /api/jobs/{id}         status, rows done / total, then the dashboard data
GET  /api/report/{id}       export.to_html / to_csv / to_xlsx
```

`services/jobs.py` runs at most two analyses at a time on threads and keeps
results for 30 minutes. `services/dashboard.py` turns the scores into the data
and the one-sentence takeaways for each section; the page and the HTML report
both render that same data, so they cannot disagree. The page inserts all user
text with `textContent`, and the report escapes it, so uploaded text never
becomes markup. There are no inline scripts or styles, ready for the strict
content security policy in Sprint 4.

The upload path, unchanged since Sprint 1:

```
POST /api/upload            raw file body + X-Filename header
  -> routes.upload          size cap while streaming
  -> ingest.parse_upload    extension + content check, parse, clean up   -> DataFrame
  -> columns.describe/detect  kind of each column; text / date / category guess
  -> store.put              in memory, random UUID, 30 min TTL
  -> profile.build_profile  shape, missing, duplicates, text lengths, time, category
GET    /api/datasets/{id}           same description again
PUT    /api/datasets/{id}/mapping   user overrides the column guess
DELETE /api/datasets/{id}           forget the data now
GET    /healthz
```

## Decisions

**The file is the request body, not a multipart form.** Multipart parsing in
Starlette spools the whole upload to a temp file before our code runs, which would
break two rules at once: enforce the size cap before reading fully, and never put
user data on disk. Reading the raw stream lets the handler count bytes as they
arrive and keep everything in memory. In the browser this is
`fetch("/api/upload", {method: "POST", body: file, headers: {"X-Filename": ...}})`.

**A refused upload is drained up to twice the cap.** Browsers show a server's
error only after they finish sending. Cutting the connection at 20 MB turned the
friendly message into a network error in testing. So the server keeps reading
(holding nothing) up to 40 MB, then answers 413. Beyond 40 MB it drops the
connection. The UI will also check the size before sending.

**Two checks per file: extension allow-list, then content.** Text formats must
not start with a known binary signature or contain NUL bytes. XLSX must be a zip
holding a workbook, with no macros. Parquet must start and end with `PAR1`.
Pickle, joblib, archives and executables are never opened.

**Decompression bombs are bounded three ways.**
- XLSX: declared unpacked size at most 200 MB and at most 100x the packed size.
  Python's `zipfile` never inflates past the declared size, so the sum is a true bound.
- XLSX shared strings and Parquet dictionaries store a value once and reference it
  from every row. Both parsers count the text each row would expand to and stop at
  200 MB. Parquet text is read dictionary-encoded so it is measured before expanding.
- JSON: at most 2,000,000 containers (checked on the raw text) and 20 levels deep.

**"Formula cells" are kept as inert text, not rejected.** A review that reads
`-5 stars` or `=great` is legitimate data. Nothing in the app evaluates cell
content: XLSX is opened with `data_only=True` (stored values only) and CSV cells
are plain strings. The risk is on the way *out*, when a user opens an export in
Excel, so `core/security.formula_safe` prefixes such cells with `'`. Exports in
Sprint 3 must pass every text cell through it.

**Row and cell caps trim instead of failing.** More than 100,000 rows: the first
100,000 are used. Cells over 10,000 characters are shortened. Both are reported in
`warnings` so the UI can say so. Column count over 200 is refused.

**Encoding.** UTF-8 (with or without BOM) first. If that fails, Windows-1252 when
the bytes read like Western European text, otherwise `charset-normalizer`. The
user is always told which encoding was assumed. Known limit: East Asian legacy
encodings can be misidentified; v1 targets English text.

**Dates.** A `31/01/2026`-style column is read day-first if any value has a first
number above 12; the choice is made once per column. A column where every value is
ambiguous (`05/01/2026`) is read month-first.

**Errors.** `UserError` carries a message written for the user and is returned as
`{"error": "..."}`. Anything else becomes a generic 500. Logs record the error type
and code locations only, never the message, because library messages can quote
cell values.

**One worker.** The dataset store is a dict in one process. Running several
workers would send a follow-up request to a process that has never seen the
dataset. Sprint 5 documents the Redis swap.
