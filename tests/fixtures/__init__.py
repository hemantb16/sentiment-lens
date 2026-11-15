"""Test data, built in memory.

Everything here is synthetic and written for this project, so there is no licence
to track. The hostile files (zip bomb, fake extensions, deep JSON) are generated
on demand rather than committed, so no dangerous file ever sits in the repository.
"""

import io
import json
import zipfile
from typing import Literal

import pandas as pd
from openpyxl import Workbook

_OPENERS = [
    "The delivery was",
    "Customer support was",
    "The battery life is",
    "Setting it up was",
    "The screen is",
    "The packaging was",
    "The mobile app is",
    "Paying at checkout was",
]
_VERDICTS = [
    "excellent and I would buy again",
    "terrible and I want a refund",
    "fine but nothing special",
    "slower than I expected",
    "much better than the old model",
    "confusing for a first-time user",
]


def sentence(i: int) -> str:
    return f"{_OPENERS[i % 8]} {_VERDICTS[i % 6]}, visit {i}."


# --- five sample datasets for column detection -------------------------------


def reviews(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "review_text": [sentence(i) for i in range(n)],
            "rating": [1 + i % 5 for i in range(n)],
            "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
            "product": [["Kettle", "Toaster", "Blender"][i % 3] for i in range(n)],
        }
    )


def tweets(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "created_at": pd.date_range("2026-03-01 08:00", periods=n, freq="h").strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "text": [sentence(i) for i in range(n)],
            "lang": ["en" if i % 7 else "de" for i in range(n)],
            "handle": [f"@user{i}" for i in range(n)],
        }
    )


def tickets(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticket_id": [10_000 + i for i in range(n)],
            "opened": pd.date_range("2026-01-10", periods=n, freq="D").strftime("%d/%m/%Y"),
            "channel": [["email", "phone", "chat", "web"][i % 4] for i in range(n)],
            "subject": [
                ["Refund", "Login", "Delivery", "Invoice", "Other"][i % 5] for i in range(n)
            ],
            "body": [sentence(i) + " " + sentence(i + 3) for i in range(n)],
        }
    )


def survey(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "respondent": [f"R-{i:04d}" for i in range(n)],
            "department": [["Sales", "Finance", "Engineering"][i % 3] for i in range(n)],
            "submitted_on": pd.date_range("2026-02-01", periods=n, freq="D").strftime("%b %d, %Y"),
            "answer": [sentence(i) for i in range(n)],
        }
    )


def comments(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame({"id": range(1, n + 1), "comment": [sentence(i) for i in range(n)]})


# --- encoders ----------------------------------------------------------------


def csv_bytes(frame: pd.DataFrame, sep: str = ",", encoding: str = "utf-8") -> bytes:
    return frame.to_csv(index=False, sep=sep).encode(encoding)


def json_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_json(orient="records").encode()


def jsonl_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_json(orient="records", lines=True).encode()


def parquet_bytes(frame: pd.DataFrame, compression: Literal["snappy", "gzip"] = "snappy") -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False, compression=compression)
    return buffer.getvalue()


def xlsx_bytes(frame: pd.DataFrame, extra_sheets: int = 0) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False):
        sheet.append(list(row))
    for _ in range(extra_sheets):
        workbook.create_sheet()
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def txt_bytes(n: int = 20) -> bytes:
    return "\n".join(sentence(i) for i in range(n)).encode()


# --- hostile files -----------------------------------------------------------


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def zip_bomb_xlsx() -> bytes:
    """Looks like a workbook; one sheet inflates from a few KB to 30 MB."""
    return _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"0" * 30_000_000,
        }
    )


def plain_zip() -> bytes:
    return _zip({"notes.txt": b"just a zip archive"})


def macro_xlsx() -> bytes:
    return _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/vbaProject.bin": b"macro",
        }
    )


EXECUTABLE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PICKLE = b"\x80\x04\x95\x10\x00\x00\x00\x00\x00\x00\x00\x8c\x02os\x94\x8c\x06system\x94\x93\x94."

FORMULA_CELLS = ['=HYPERLINK("http://example.test","click")', "+1+1", "-2+3", "@SUM(A1:A2)"]


def formula_csv() -> bytes:
    frame = pd.DataFrame({"comment": FORMULA_CELLS + ["An ordinary comment about the service."]})
    return csv_bytes(frame)


def deep_json(depth: int) -> bytes:
    return ("[" * depth + "]" * depth).encode()


def nested_record_json(depth: int) -> bytes:
    value: object = "leaf"
    for _ in range(depth):
        value = {"child": value}
    return json.dumps([{"text": "A normal looking record.", "extra": value}]).encode()
