"""Turn an uploaded file into a pandas DataFrame, refusing anything unsafe.

Nothing here executes file content. Pickle, joblib, archives and executables are
never opened: a file must have an allowed extension *and* content that matches it.
Row content is never logged.
"""

import io
import json
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from charset_normalizer import from_bytes
from openpyxl import load_workbook
from pandas.api import types as pdt

from app.core.config import Settings, settings
from app.core.errors import UserError

logger = logging.getLogger(__name__)

FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".txt": "txt",
    ".xlsx": "xlsx",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".parquet": "parquet",
}

_LABELS = {
    "csv": "a CSV file",
    "tsv": "a TSV file",
    "txt": "a text file",
    "xlsx": "an Excel workbook",
    "json": "a JSON file",
    "jsonl": "a JSON Lines file",
    "parquet": "a Parquet file",
}

# First bytes of common non-text files. A "CSV" that starts like this is not a CSV.
_BINARY_SIGNATURES = (
    b"PK\x03\x04",  # zip, docx, xlsx, jar
    b"PK\x05\x06",
    b"MZ",  # Windows executable
    b"\x7fELF",  # Linux executable
    b"\xca\xfe\xba\xbe",  # Mach-O / Java class
    b"%PDF",
    b"\x1f\x8b",  # gzip
    b"BZh",
    b"\xfd7zXZ",
    b"7z\xbc\xaf",
    b"Rar!",
    b"PAR1",
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"\xd0\xcf\x11\xe0",  # legacy Office (.xls, .doc)
)
_WIDE_BOMS = (b"\xff\xfe", b"\xfe\xff")  # UTF-16 / UTF-32, which legitimately contain NUL bytes

_UNSUPPORTED = (
    "This file type isn't supported. Upload a CSV, TSV, TXT, XLSX, JSON, JSONL or Parquet file."
)
_EMPTY = "This file is empty. Upload a file with at least one row of data."
_MISMATCH = (
    "This file's content doesn't match its extension, so it wasn't opened. "
    "Check that it really is {label}."
)
_TOO_MUCH = (
    "This file unpacks to far more data than its size suggests, so it wasn't opened. "
    "Upload a smaller or simpler file."
)
_TOO_MANY_COLUMNS = "This file has more than {limit:,} columns. Remove the ones you don't need."
_MAX_JSON_CONTAINERS = 2_000_000


@dataclass
class Parsed:
    frame: pd.DataFrame
    format: str
    warnings: list[str]


def parse_upload(filename: str, data: bytes, limits: Settings = settings) -> Parsed:
    """Validate and parse one uploaded file. Raises UserError with a friendly message."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    fmt = FORMATS.get(suffix)
    if fmt is None:
        raise UserError(_UNSUPPORTED, 415)
    if not data:
        raise UserError(_EMPTY)

    warnings: list[str] = []
    try:
        frame = _PARSERS[fmt](data, limits, warnings)
    except UserError:
        raise
    except RecursionError:
        raise UserError(_json_too_deep(limits)) from None
    except MemoryError:
        raise UserError(_TOO_MUCH, 413) from None
    except Exception as exc:
        # Library error messages can quote cell values, so only the type is logged.
        logger.info("could not parse %s upload: %s", fmt, type(exc).__name__)
        raise UserError(
            f"We couldn't read this as {_LABELS[fmt]}. "
            "It may be damaged or saved in a different format."
        ) from None
    return Parsed(_normalise(frame, limits, warnings), fmt, warnings)


# --- text formats -----------------------------------------------------------


def _decode(data: bytes, label: str, warnings: list[str]) -> str:
    if data.startswith(_BINARY_SIGNATURES):
        raise UserError(_MISMATCH.format(label=label), 415)
    wide = data.startswith(_WIDE_BOMS)
    if not wide:
        if b"\x00" in data[:65536]:
            raise UserError(_MISMATCH.format(label=label), 415)
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        # Windows-1252 is what Excel writes for "CSV" on most Western systems, and
        # statistical detectors often miss it on short or repetitive text. Try it first.
        if _looks_western(data):
            warnings.append(_encoding_warning("Windows-1252"))
            return data.decode("cp1252")
    match = from_bytes(data).best()
    if match is None:
        raise UserError(
            "We couldn't work out this file's text encoding. Save it as UTF-8 and try again."
        )
    warnings.append(_encoding_warning(match.encoding))
    return str(match)


def _encoding_warning(encoding: str) -> str:
    return (
        f"The file is not UTF-8, so it was read as {encoding}. "
        "If letters look wrong, save it as UTF-8 and upload it again."
    )


def _looks_western(data: bytes) -> bool:
    """True if the bytes read sensibly as Windows-1252.

    In Western European text an accented letter almost always stands alone between
    plain letters. Cyrillic, Greek or East Asian text read as Windows-1252 instead
    shows long runs of accented characters, which is what this looks for.
    """
    try:
        sample = data[:200_000].decode("cp1252")
    except UnicodeDecodeError:
        return False
    high = [position for position, char in enumerate(sample) if ord(char) > 127]
    if not high:
        return True
    adjacent = sum(1 for a, b in zip(high, high[1:], strict=False) if b - a == 1)
    return adjacent / len(high) < 0.2


def _sniff_delimiter(text: str) -> str:
    header = text.lstrip("\r\n").split("\n", 1)[0]
    counts = {sep: header.count(sep) for sep in (",", ";", "\t", "|")}
    best = max(counts, key=lambda sep: counts[sep])  # ties go to the earlier candidate
    return best if counts[best] else ","


def _parse_delimited(sep: str | None, label: str) -> "_Parser":
    def parse(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
        text = _decode(data, label, warnings)
        if not text.strip():
            raise UserError(_EMPTY)
        delimiter = sep or _sniff_delimiter(text)
        header = text.lstrip("\r\n").split("\n", 1)[0]
        if header.count(delimiter) >= limits.max_columns:
            raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
        try:
            return pd.read_csv(
                io.StringIO(text),
                sep=delimiter,
                nrows=limits.max_rows + 1,
                skip_blank_lines=True,
                on_bad_lines="error",
            )
        except pd.errors.EmptyDataError:
            raise UserError(_EMPTY) from None
        except pd.errors.ParserError:
            raise UserError(
                "Some rows have more columns than the header row. "
                "Check for stray delimiters or quotes that are never closed."
            ) from None

    return parse


def _parse_txt(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    text = _decode(data, _LABELS["txt"], warnings)
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
            if len(lines) > limits.max_rows:
                break
    if not lines:
        raise UserError(_EMPTY)
    return pd.DataFrame({"text": lines})


# --- JSON -------------------------------------------------------------------


def _json_too_deep(limits: Settings) -> str:
    return (
        f"This JSON is nested more than {limits.max_json_depth} levels deep. "
        "Upload a flat list of records instead."
    )


def _check_depth(value: Any, limits: Settings) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limits.max_json_depth:
            raise UserError(_json_too_deep(limits))
        stack.extend((child, depth + 1) for child in children)


def _records_frame(records: list[Any], limits: Settings) -> pd.DataFrame:
    records = records[: limits.max_rows + 1]
    if not records:
        raise UserError(_EMPTY)
    if all(isinstance(r, dict) for r in records):
        keys: set[str] = set()
        for record in records:
            keys.update(map(str, record))
            if len(keys) > limits.max_columns:
                raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
        return pd.DataFrame(records)
    if not any(isinstance(r, dict | list) for r in records):
        return pd.DataFrame({"text": records})
    raise UserError(
        "This JSON isn't a table. Upload a list of records, "
        'for example [{"text": "Great service", "date": "2026-01-31"}].'
    )


def _parse_json(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    text = _decode(data, _LABELS["json"], warnings)
    # An empty list costs 2 bytes on disk and 56 in memory; cap them before loading.
    if text.count("[") + text.count("{") > _MAX_JSON_CONTAINERS:
        raise UserError(_TOO_MUCH, 413)
    value = json.loads(text)
    _check_depth(value, limits)
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        if len(lists) == 1:  # {"data": [...]} wrapper
            value = lists[0]
        elif lists and len(lists) == len(value) and len({len(v) for v in lists}) == 1:
            if len(value) > limits.max_columns:  # {"column": [values], ...}
                raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
            return pd.DataFrame({k: v[: limits.max_rows + 1] for k, v in value.items()})
        else:
            value = [value]
    if not isinstance(value, list):
        raise UserError(_EMPTY)
    return _records_frame(value, limits)


def _parse_jsonl(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    text = _decode(data, _LABELS["jsonl"], warnings)
    records: list[Any] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise UserError(
                f"Line {number} isn't valid JSON. A JSON Lines file needs one complete record "
                "on each line."
            ) from None
        _check_depth(record, limits)
        records.append(record)
        if len(records) > limits.max_rows:
            break
    return _records_frame(records, limits)


# --- XLSX -------------------------------------------------------------------


def _check_xlsx_container(data: bytes, limits: Settings) -> None:
    label = _LABELS["xlsx"]
    if not data.startswith(b"PK\x03\x04"):
        raise UserError(_MISMATCH.format(label=label), 415)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile:
        raise UserError(_MISMATCH.format(label=label), 415) from None
    names = {info.filename for info in infos}
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise UserError(_MISMATCH.format(label=label), 415)
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise UserError(
            "This workbook contains macros, so it wasn't opened. Save it as a plain .xlsx file.",
            415,
        )
    # zipfile never inflates an entry past its declared size, so these sums are a true bound.
    unpacked = sum(info.file_size for info in infos)
    packed = max(sum(info.compress_size for info in infos), 1)
    if (
        len(infos) > 2000
        or unpacked > limits.max_unpacked_bytes
        or unpacked / packed > limits.max_compression_ratio
    ):
        raise UserError(_TOO_MUCH, 413)


def _parse_xlsx(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    _check_xlsx_container(data, limits)
    # read_only streams the sheet; data_only returns stored values, never formulas.
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        if not workbook.worksheets:
            raise UserError(_EMPTY)
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()  # ignore the declared size; it can claim millions of rows
        header: list[Any] | None = None
        rows: list[list[Any]] = []
        budget = limits.max_unpacked_bytes
        for raw in sheet.iter_rows(values_only=True):
            if all(cell is None for cell in raw):
                continue
            if header is None:
                header = list(raw)
                while header and header[-1] is None:
                    header.pop()
                if len(header) > limits.max_columns:
                    raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
                continue
            row = list(raw[: len(header)])
            row.extend([None] * (len(header) - len(row)))
            for cell in row:
                # A shared string is stored once but can be referenced by every cell.
                if isinstance(cell, str):
                    budget -= len(cell)
            if budget < 0:
                raise UserError(_TOO_MUCH, 413)
            rows.append(row)
            if len(rows) > limits.max_rows:
                break
        sheet_count = len(workbook.sheetnames)
    finally:
        workbook.close()
    if header is None:
        raise UserError(_EMPTY)
    if sheet_count > 1:
        warnings.append(f"Only the first sheet was read. The workbook has {sheet_count} sheets.")
    return pd.DataFrame(rows, columns=pd.Index(header, dtype=object))


# --- Parquet ----------------------------------------------------------------


def _parse_parquet(data: bytes, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    if not (data.startswith(b"PAR1") and data.endswith(b"PAR1")):
        raise UserError(_MISMATCH.format(label=_LABELS["parquet"]), 415)
    schema = pq.read_schema(io.BytesIO(data))
    flat: list[str] = []
    text_columns: list[str] = []
    skipped = 0
    for column in schema:
        kind = column.type
        if pa.types.is_nested(kind) or pa.types.is_binary(kind) or pa.types.is_large_binary(kind):
            skipped += 1
            continue
        flat.append(column.name)
        if pa.types.is_string(kind) or pa.types.is_large_string(kind):
            text_columns.append(column.name)
    if len(flat) > limits.max_columns:
        raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
    if not flat:
        raise UserError("This Parquet file has no plain columns to read.")
    if skipped:
        warnings.append(f"{skipped} nested or binary column(s) were skipped.")

    # Text is read dictionary-encoded so a repeated value is held once until it is measured.
    file = pq.ParquetFile(io.BytesIO(data), read_dictionary=text_columns)
    meta = file.metadata
    unpacked = sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
    if unpacked > limits.max_unpacked_bytes:
        raise UserError(_TOO_MUCH, 413)

    budget = limits.max_unpacked_bytes
    batches: list[pa.RecordBatch] = []
    rows = 0
    for batch in file.iter_batches(batch_size=10_000, columns=flat):
        for column in batch.columns:
            if pa.types.is_dictionary(column.type):
                lengths = pc.binary_length(column.dictionary)
                budget -= pc.sum(pc.take(lengths, column.indices)).as_py() or 0
        if budget < 0:
            raise UserError(_TOO_MUCH, 413)
        batches.append(batch)
        rows += batch.num_rows
        if rows > limits.max_rows:
            break
    if not batches:
        raise UserError(_EMPTY)
    table = pa.Table.from_batches(batches).slice(0, limits.max_rows + 1)
    for index, column in enumerate(table.columns):
        if pa.types.is_dictionary(column.type):
            table = table.set_column(index, table.schema.names[index], column.cast(pa.string()))
    frame: pd.DataFrame = table.to_pandas()
    return frame


_Parser = Callable[[bytes, Settings, list[str]], pd.DataFrame]

_PARSERS: dict[str, _Parser] = {
    "csv": _parse_delimited(None, _LABELS["csv"]),
    "tsv": _parse_delimited("\t", _LABELS["tsv"]),
    "txt": _parse_txt,
    "xlsx": _parse_xlsx,
    "json": _parse_json,
    "jsonl": _parse_jsonl,
    "parquet": _parse_parquet,
}


# --- common clean-up --------------------------------------------------------


def _unique_names(raw: list[Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for position, value in enumerate(raw, start=1):
        base = "" if value is None else str(value).strip()
        if not base or base.lower() == "nan":
            base = f"column_{position}"
        name, suffix = base, 2
        while name in seen:
            name, suffix = f"{base}_{suffix}", suffix + 1
        seen.add(name)
        names.append(name)
    return names


def _as_text(value: Any) -> Any:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _normalise(frame: pd.DataFrame, limits: Settings, warnings: list[str]) -> pd.DataFrame:
    if frame.shape[1] > limits.max_columns:
        raise UserError(_TOO_MANY_COLUMNS.format(limit=limits.max_columns))
    frame = frame.copy()
    frame.columns = pd.Index(_unique_names(list(frame.columns)))
    frame = frame.dropna(how="all").reset_index(drop=True)
    if frame.empty:
        raise UserError("This file has column names but no rows of data.")
    if len(frame) > limits.max_rows:
        frame = frame.iloc[: limits.max_rows]
        warnings.append(
            f"The file has more than {limits.max_rows:,} rows. "
            f"Only the first {limits.max_rows:,} are used."
        )

    shortened = 0
    for name in frame.columns:
        column = frame[name]
        if pdt.is_object_dtype(column):
            kind = pd.api.types.infer_dtype(column, skipna=True)
            if kind == "boolean":
                frame[name] = column.astype("boolean")
                continue
            if kind in ("datetime", "date"):
                frame[name] = pd.to_datetime(column, errors="coerce")
                continue
            column = column.map(_as_text).astype("str")
        elif not pdt.is_string_dtype(column):
            continue
        too_long = (column.str.len() > limits.max_cell_chars).fillna(False).astype(bool)
        count = int(too_long.sum())
        if count:
            shortened += count
            column = column.where(~too_long, column.str.slice(0, limits.max_cell_chars))
        frame[name] = column
    if shortened:
        warnings.append(
            f"{shortened:,} cell(s) were longer than {limits.max_cell_chars:,} characters "
            "and were shortened."
        )
    return frame
