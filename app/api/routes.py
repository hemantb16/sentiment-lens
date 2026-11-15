import json
import threading
from pathlib import Path as FilePath
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Path, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import UserError, busy
from app.services import columns, dashboard, export
from app.services.ingest import FORMATS, parse_upload
from app.services.jobs import jobs
from app.services.profile import build_profile
from app.services.store import Dataset, store

router = APIRouter(prefix="/api")
_upload_slots = threading.BoundedSemaphore(settings.max_concurrent_uploads)

DatasetId = Annotated[str, Path(pattern="^[0-9a-f]{32}$")]


class MappingChoice(BaseModel):
    text: str = Field(max_length=500)
    date: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=500)


def _too_large() -> UserError:
    megabytes = settings.max_upload_bytes // 1_000_000
    return UserError(f"This file is larger than {megabytes} MB. Upload a smaller file.", 413)


def _describe(dataset_id: str, dataset: Dataset) -> dict[str, Any]:
    preview = dataset.frame.head(settings.preview_rows)
    return {
        "dataset_id": dataset_id,
        "filename": dataset.filename,
        "format": dataset.format,
        "warnings": dataset.warnings,
        "mapping": dataset.mapping,
        "profile": build_profile(dataset.frame, dataset.infos, dataset.mapping),
        "preview": json.loads(preview.to_json(orient="records", date_format="iso")),
        "expires_in_seconds": store.seconds_left(dataset),
    }


def _ingest(filename: str, data: bytes) -> dict[str, Any]:
    parsed = parse_upload(filename, data)
    infos = columns.describe_columns(parsed.frame)
    mapping = columns.detect(infos, len(parsed.frame))
    dataset = Dataset(parsed.frame, filename, parsed.format, parsed.warnings, infos, mapping)
    return _describe(store.put(dataset), dataset)


@router.post("/upload")
async def upload(request: Request) -> dict[str, Any]:
    """Receive one file as the raw request body, with its name in the X-Filename header.

    The body is read in chunks and nothing is kept once it passes the size cap or the
    file type is known to be unsupported. Nothing is written to disk.

    A refused upload is still read (and thrown away) up to twice the cap. Browsers
    only show a server's error once they have finished sending, so cutting the
    connection at the cap would turn the friendly message into a bare network error.
    Past twice the cap the connection is simply dropped.
    """
    limit = settings.max_upload_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > 2 * limit:
        raise _too_large()
    raw_name = unquote(request.headers.get("x-filename", "")).replace("\\", "/")
    filename = PurePosixPath(raw_name).name[:255]
    supported = PurePosixPath(filename).suffix.lower() in FORMATS

    # Each upload in flight can hold 20 MB plus its parsed table, so only a few are
    # let in at once. A turned-away upload is still read out, holding nothing.
    admitted = _upload_slots.acquire(blocking=False)
    try:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2 * limit:
                raise _too_large()
            if admitted and supported and size <= limit:
                chunks.append(chunk)
            else:
                chunks.clear()

        if not admitted:
            raise busy("Too many files are being read right now. Try again in a few seconds.", 5)
        if not filename:
            raise UserError("The upload is missing a file name.")
        if not supported:
            parse_upload(filename, b"")  # raises the standard "not supported" error
        if size > limit:
            raise _too_large()
        return await run_in_threadpool(_ingest, filename, b"".join(chunks))
    finally:
        if admitted:
            _upload_slots.release()


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: DatasetId) -> dict[str, Any]:
    return await run_in_threadpool(_describe, dataset_id, store.get(dataset_id))


@router.put("/datasets/{dataset_id}/mapping")
async def set_mapping(dataset_id: DatasetId, choice: MappingChoice) -> dict[str, Any]:
    dataset = store.get(dataset_id)

    def update() -> dict[str, Any]:
        dataset.mapping = columns.apply_override(
            dataset.frame,
            dataset.infos,
            dataset.mapping,
            choice.text,
            choice.date,
            choice.category,
        )
        return _describe(dataset_id, dataset)

    return await run_in_threadpool(update)


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: DatasetId) -> Response:
    store.delete(dataset_id)
    jobs.discard_for(dataset_id)  # results hold a copy of the rows; they go too
    return Response(status_code=204)


# --- sample data ---------------------------------------------------------------

SAMPLES = {
    "reviews": {
        "file": "reviews.csv",
        "title": "3,000 real review sentences",
        "note": "Restaurants, films and phones. UCI Sentiment Labelled Sentences, CC BY 4.0.",
    },
    "shop-feedback": {
        "file": "shop_feedback.csv",
        "title": "600 made-up shop comments",
        "note": "With dates and categories. Written for this project, not real customers.",
    },
}
_SAMPLE_DIR = FilePath(__file__).resolve().parents[1] / "static" / "samples"


@router.get("/samples")
async def list_samples() -> list[dict[str, str]]:
    return [{"id": key, "title": s["title"], "note": s["note"]} for key, s in SAMPLES.items()]


@router.post("/samples/{sample_id}")
async def use_sample(sample_id: str) -> dict[str, Any]:
    sample = SAMPLES.get(sample_id)
    if sample is None:
        raise UserError("That sample doesn't exist.", 404)
    data = (_SAMPLE_DIR / sample["file"]).read_bytes()
    return await run_in_threadpool(_ingest, sample["file"], data)


# --- analysis ------------------------------------------------------------------


class AnalyseRequest(BaseModel):
    dataset_id: str = Field(pattern="^[0-9a-f]{32}$")


@router.post("/analyse", status_code=202)
async def analyse(request: AnalyseRequest) -> dict[str, str]:
    dataset = store.get(request.dataset_id)
    job_id = jobs.submit(
        lambda progress: dashboard.analyse(dataset, progress), owner=request.dataset_id
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: DatasetId) -> dict[str, Any]:
    job = jobs.get(job_id)
    return {
        "status": job.status,
        "done": job.done,
        "total": job.total,
        "position": jobs.position(job_id) if job.status == "queued" else 0,
        "error": job.error,
        "result": job.result.payload if job.status == "done" else None,
    }


@router.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: DatasetId) -> Response:
    jobs.cancel(job_id)
    return Response(status_code=204)


_DOWNLOADS: dict[str, tuple[str, str]] = {
    "csv": ("sentiment-scored.csv", "text/csv; charset=utf-8"),
    "xlsx": (
        "sentiment-scored.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "html": ("sentiment-report.html", "text/html; charset=utf-8"),
}


@router.get("/report/{job_id}")
async def report(job_id: DatasetId, format: Literal["csv", "xlsx", "html"] = "html") -> Response:
    job = jobs.get(job_id)
    if job.status != "done":
        raise UserError("The analysis hasn't finished yet.", 409)
    analysis: dashboard.Analysis = job.result

    def build() -> bytes:
        if format == "csv":
            return export.to_csv(analysis.table)
        if format == "xlsx":
            return export.to_xlsx(analysis.table)
        return export.to_html(analysis.payload).encode("utf-8")

    filename, media_type = _DOWNLOADS[format]
    return Response(
        await run_in_threadpool(build),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # The report carries its own <style>. If a browser ever shows it instead
            # of saving it, it runs sandboxed: no scripts, no access to this site.
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        },
    )
