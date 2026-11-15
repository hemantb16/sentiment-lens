"""Runtime limits. Every value can be overridden with an environment variable."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int = field(default_factory=lambda: _int("SL_MAX_UPLOAD_BYTES", 20_000_000))
    max_rows: int = field(default_factory=lambda: _int("SL_MAX_ROWS", 100_000))
    max_columns: int = field(default_factory=lambda: _int("SL_MAX_COLUMNS", 200))
    max_cell_chars: int = field(default_factory=lambda: _int("SL_MAX_CELL_CHARS", 10_000))
    # Guards against decompression bombs in XLSX (a zip) and Parquet.
    max_unpacked_bytes: int = field(
        default_factory=lambda: _int("SL_MAX_UNPACKED_BYTES", 200_000_000)
    )
    max_compression_ratio: int = field(
        default_factory=lambda: _int("SL_MAX_COMPRESSION_RATIO", 100)
    )
    max_json_depth: int = field(default_factory=lambda: _int("SL_MAX_JSON_DEPTH", 20))
    dataset_ttl_seconds: int = field(default_factory=lambda: _int("SL_DATASET_TTL_SECONDS", 1800))
    max_datasets: int = field(default_factory=lambda: _int("SL_MAX_DATASETS", 8))
    preview_rows: int = field(default_factory=lambda: _int("SL_PREVIEW_ROWS", 10))
    max_concurrent_jobs: int = field(default_factory=lambda: _int("SL_MAX_CONCURRENT_JOBS", 2))
    max_queued_jobs: int = field(default_factory=lambda: _int("SL_MAX_QUEUED_JOBS", 6))
    max_concurrent_uploads: int = field(
        default_factory=lambda: _int("SL_MAX_CONCURRENT_UPLOADS", 3)
    )
    job_timeout_seconds: int = field(default_factory=lambda: _int("SL_JOB_TIMEOUT_SECONDS", 300))
    sweep_seconds: int = field(default_factory=lambda: _int("SL_SWEEP_SECONDS", 60))
    # Per-address limits.
    rate_uploads_per_hour: int = field(default_factory=lambda: _int("SL_RATE_UPLOADS_PER_HOUR", 10))
    rate_analyses_per_hour: int = field(
        default_factory=lambda: _int("SL_RATE_ANALYSES_PER_HOUR", 20)
    )
    rate_polls_per_minute: int = field(
        default_factory=lambda: _int("SL_RATE_POLLS_PER_MINUTE", 120)
    )
    rate_requests_per_minute: int = field(
        default_factory=lambda: _int("SL_RATE_REQUESTS_PER_MINUTE", 300)
    )
    # How many reverse proxies sit in front of the app. 0 = none: use the socket
    # address and ignore X-Forwarded-For, which any client can forge. 1 on Render.
    trusted_proxy_hops: int = field(default_factory=lambda: _int("SL_TRUSTED_PROXY_HOPS", 0))
    model_dir: str = field(
        default_factory=lambda: os.environ.get(
            "SL_MODEL_DIR", str(Path(__file__).resolve().parents[2] / "models")
        )
    )


settings = Settings()
