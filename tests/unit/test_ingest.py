import io
from collections.abc import Callable

import pandas as pd
import pytest
from openpyxl import Workbook

from app.core.config import Settings
from app.core.errors import UserError
from app.core.security import formula_safe
from app.services.ingest import parse_upload
from tests import fixtures as fx

ENCODERS: dict[str, Callable[[pd.DataFrame], bytes]] = {
    "data.csv": fx.csv_bytes,
    "data.tsv": lambda frame: fx.csv_bytes(frame, sep="\t"),
    "data.xlsx": fx.xlsx_bytes,
    "data.json": fx.json_bytes,
    "data.jsonl": fx.jsonl_bytes,
    "data.ndjson": fx.jsonl_bytes,
    "data.parquet": fx.parquet_bytes,
}


def rejected(filename: str, data: bytes, limits: Settings | None = None) -> UserError:
    with pytest.raises(UserError) as caught:
        parse_upload(filename, data, limits or Settings())
    return caught.value


# --- every supported format parses --------------------------------------------


@pytest.mark.parametrize("filename", ENCODERS)
def test_table_formats_round_trip(filename: str) -> None:
    source = fx.reviews()
    parsed = parse_upload(filename, ENCODERS[filename](source))
    assert list(parsed.frame.columns) == list(source.columns)
    assert len(parsed.frame) == len(source)
    assert parsed.frame["review_text"].tolist() == source["review_text"].tolist()
    assert parsed.frame["rating"].tolist() == source["rating"].tolist()
    assert parsed.warnings == []


def test_txt_is_one_record_per_line() -> None:
    parsed = parse_upload("notes.TXT", fx.txt_bytes(20) + b"\n\n  \n")
    assert list(parsed.frame.columns) == ["text"]
    assert len(parsed.frame) == 20
    assert parsed.format == "txt"


def test_semicolon_delimiter_is_detected() -> None:
    parsed = parse_upload("data.csv", fx.csv_bytes(fx.reviews(), sep=";"))
    assert parsed.frame.shape == (60, 4)


def test_pipe_delimiter_is_detected() -> None:
    parsed = parse_upload("data.csv", fx.csv_bytes(fx.reviews(), sep="|"))
    assert parsed.frame.shape == (60, 4)


def test_single_column_csv() -> None:
    parsed = parse_upload("data.csv", fx.csv_bytes(fx.comments()[["comment"]]))
    assert list(parsed.frame.columns) == ["comment"]


def test_utf8_bom_is_removed() -> None:
    parsed = parse_upload("data.csv", b"\xef\xbb\xbf" + fx.csv_bytes(fx.reviews()))
    assert parsed.frame.columns[0] == "review_text"
    assert parsed.warnings == []


def test_utf16_is_read_with_a_warning() -> None:
    parsed = parse_upload("data.csv", fx.csv_bytes(fx.reviews(), encoding="utf-16"))
    assert parsed.frame.shape == (60, 4)
    assert "not UTF-8" in parsed.warnings[0]


@pytest.mark.parametrize(
    "sentence",
    [
        "Le café était très bon, déjà la 2e visite à l'hôtel.",
        "Die Qualität ist schön, aber die Lieferung kam zu spät.",
        "El envío llegó rápido, ¡qué buena atención!",
        "Great product – really “worth” the £20 price…",
    ],
)
def test_windows_1252_is_read_correctly(sentence: str) -> None:
    for rows in (1, 80):  # short files are where statistical detectors go wrong
        frame = pd.DataFrame({"comment": [sentence] * rows})
        parsed = parse_upload("data.csv", fx.csv_bytes(frame, encoding="cp1252"))
        assert parsed.frame["comment"][0] == sentence
        assert "read as Windows-1252" in parsed.warnings[0]


def test_cyrillic_is_not_forced_into_windows_1252() -> None:
    sentence = "Доставка была быстрой, но упаковка оказалась повреждённой."
    frame = pd.DataFrame({"comment": [f"{sentence} {i}" for i in range(80)]})
    parsed = parse_upload("data.csv", fx.csv_bytes(frame, encoding="cp1251"))
    assert parsed.frame["comment"][0] == f"{sentence} 0"
    assert "Windows-1252" not in parsed.warnings[0]


def test_east_asian_legacy_encoding_is_never_read_as_windows_1252() -> None:
    # Known limit: the detector may pick the wrong East Asian encoding (v1 is
    # English-only). What matters here is that the user is told which one was assumed.
    frame = pd.DataFrame(
        {"comment": [f"配送は早かったですが、箱が壊れていました。 {i}" for i in range(80)]}
    )
    parsed = parse_upload("data.csv", fx.csv_bytes(frame, encoding="shift_jis"))
    assert "not UTF-8" in parsed.warnings[0]
    assert "Windows-1252" not in parsed.warnings[0]


def test_json_wrapper_object() -> None:
    data = b'{"data": [{"text": "Good"}, {"text": "Bad"}], "total": 2}'
    assert parse_upload("d.json", data).frame["text"].tolist() == ["Good", "Bad"]


def test_json_column_oriented() -> None:
    data = b'{"text": ["Good", "Bad"], "stars": [5, 1]}'
    parsed = parse_upload("d.json", data)
    assert parsed.frame.shape == (2, 2)
    assert parsed.frame["stars"].tolist() == [5, 1]


def test_json_list_of_strings_becomes_text_column() -> None:
    parsed = parse_upload("d.json", b'["Good", "Bad", "Fine"]')
    assert parsed.frame["text"].tolist() == ["Good", "Bad", "Fine"]


def test_json_single_object_is_one_row() -> None:
    assert len(parse_upload("d.json", b'{"text": "Good", "stars": 5}').frame) == 1


def test_json_nested_values_become_text() -> None:
    data = b'[{"text": "Good", "tags": ["a", "b"]}, {"text": "Bad", "tags": {"k": 1}}]'
    assert parse_upload("d.json", data).frame["tags"].tolist() == ['["a", "b"]', '{"k": 1}']


def test_xlsx_reads_first_sheet_only_and_says_so() -> None:
    parsed = parse_upload("d.xlsx", fx.xlsx_bytes(fx.reviews(), extra_sheets=2))
    assert len(parsed.frame) == 60
    assert "3 sheets" in parsed.warnings[0]


def test_xlsx_dates_and_blank_header_cells() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["when", None, "note", "note"])
    sheet.append([pd.Timestamp("2026-01-05").to_pydatetime(), 1, "Good", "x"])
    sheet.append([None, None, None, None])
    sheet.append([pd.Timestamp("2026-01-06").to_pydatetime(), 2, "Bad", "y"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    frame = parse_upload("d.xlsx", buffer.getvalue()).frame
    assert list(frame.columns) == ["when", "column_2", "note", "note_2"]
    assert len(frame) == 2
    assert str(frame["when"].dtype).startswith("datetime64")


def test_parquet_skips_nested_columns() -> None:
    frame = pd.DataFrame({"text": ["Good", "Bad"], "tags": [["a"], ["b", "c"]]})
    parsed = parse_upload("d.parquet", fx.parquet_bytes(frame))
    assert list(parsed.frame.columns) == ["text"]
    assert "skipped" in parsed.warnings[0]


def test_mixed_type_column_becomes_text() -> None:
    data = b'[{"n": 1, "v": 1}, {"n": 2, "v": "two"}, {"n": 3, "v": null}, {"n": 4, "v": true}]'
    parsed = parse_upload("d.json", data)
    assert parsed.frame["v"].tolist()[:2] == ["1", "two"]
    assert parsed.frame["v"].isna().tolist() == [False, False, True, False]


# --- limits ---------------------------------------------------------------------


def test_row_cap_keeps_first_rows_and_warns() -> None:
    limits = Settings(max_rows=10)
    for filename, encode in ENCODERS.items():
        parsed = parse_upload(filename, encode(fx.reviews(25)), limits)
        assert len(parsed.frame) == 10, filename
        assert "first 10" in parsed.warnings[-1], filename
    assert len(parse_upload("d.txt", fx.txt_bytes(25), limits).frame) == 10


def test_long_cells_are_shortened_and_counted() -> None:
    frame = pd.DataFrame({"text": ["x" * 50, "short", None]})
    parsed = parse_upload("d.csv", fx.csv_bytes(frame), Settings(max_cell_chars=20))
    assert parsed.frame["text"].str.len().max() == 20
    assert parsed.warnings == ["1 cell(s) were longer than 20 characters and were shortened."]


@pytest.mark.parametrize("filename", ["d.csv", "d.xlsx", "d.json", "d.parquet"])
def test_too_many_columns(filename: str) -> None:
    frame = pd.DataFrame({f"c{i}": [1, 2] for i in range(12)})
    error = rejected(
        filename, ENCODERS[filename.replace("d.", "data.")](frame), Settings(max_columns=5)
    )
    assert "more than 5 columns" in error.message


def test_ragged_csv_gets_a_plain_explanation() -> None:
    error = rejected("d.csv", b"a,b\n1,2\n3,4,5,6\n")
    assert "more columns than the header" in error.message


def test_jsonl_bad_line_is_named() -> None:
    error = rejected("d.jsonl", b'{"text": "ok"}\n{"text": broken}\n')
    assert error.message.startswith("Line 2 isn't valid JSON")


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("d.csv", b""),
        ("d.csv", b"   \n\n"),
        ("d.csv", b"only,a,header\n"),
        ("d.txt", b"\n\n"),
        ("d.json", b"[]"),
        ("d.jsonl", b"\n"),
    ],
)
def test_empty_files(filename: str, data: bytes) -> None:
    message = rejected(filename, data).message
    assert "empty" in message or "no rows" in message


def test_json_that_is_not_a_table() -> None:
    assert "isn't a table" in rejected("d.json", b'[{"a": 1}, [1, 2]]').message


def test_damaged_file_message_does_not_leak_details() -> None:
    error = rejected("d.json", b'{"text": "secret customer words"')
    assert error.message == (
        "We couldn't read this as a JSON file. It may be damaged or saved in a different format."
    )


# --- hostile files are refused ----------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["model.pkl", "model.joblib", "run.exe", "a.zip", "old.xls", "data.csv.exe", "noext"],
)
def test_unsupported_extensions(filename: str) -> None:
    error = rejected(filename, fx.csv_bytes(fx.reviews()))
    assert error.status == 415
    assert "isn't supported" in error.message


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("report.csv", fx.EXECUTABLE),
        ("report.txt", fx.PNG),
        ("report.json", fx.plain_zip()),
        ("report.csv", fx.PICKLE + b"\x00" * 8),
        ("report.xlsx", fx.PNG),
        ("report.xlsx", fx.plain_zip()),
        ("report.xlsx", fx.csv_bytes(fx.reviews())),
        ("report.parquet", fx.csv_bytes(fx.reviews())),
        ("report.parquet", fx.EXECUTABLE),
    ],
)
def test_fake_extensions(filename: str, data: bytes) -> None:
    error = rejected(filename, data)
    assert error.status == 415
    assert "doesn't match its extension" in error.message


def test_zip_bomb_xlsx_is_not_inflated() -> None:
    bomb = fx.zip_bomb_xlsx()
    assert len(bomb) < 100_000  # tiny on disk, 30 MB unpacked
    error = rejected("big.xlsx", bomb)
    assert error.status == 413
    assert "unpacks to far more data" in error.message


def test_xlsx_with_macros() -> None:
    assert "macros" in rejected("m.xlsx", fx.macro_xlsx()).message


def test_xlsx_repeated_text_over_budget() -> None:
    frame = pd.DataFrame({"text": ["the same long sentence again and again " * 5] * 200})
    error = rejected("d.xlsx", fx.xlsx_bytes(frame), Settings(max_unpacked_bytes=10_000))
    assert error.status == 413


def test_parquet_repeated_text_over_budget() -> None:
    # One long value repeated: a few KB on disk, 5 MB once every row is expanded.
    frame = pd.DataFrame({"text": ["x" * 1000] * 5000})
    data = fx.parquet_bytes(frame, compression="gzip")
    assert len(data) < 20_000
    error = rejected("d.parquet", data, Settings(max_unpacked_bytes=1_000_000))
    assert error.status == 413


def test_parquet_declared_size_over_budget() -> None:
    frame = pd.DataFrame({"text": [fx.sentence(i) for i in range(2000)]})
    error = rejected("d.parquet", fx.parquet_bytes(frame), Settings(max_unpacked_bytes=1000))
    assert error.status == 413


@pytest.mark.parametrize("depth", [100_000, 30])
def test_deeply_nested_json(depth: int) -> None:
    assert "nested more than 20 levels" in rejected("d.json", fx.deep_json(depth)).message


def test_deeply_nested_json_inside_a_record() -> None:
    assert "nested more than" in rejected("d.json", fx.nested_record_json(40)).message
    assert "nested more than" in rejected("d.jsonl", fx.nested_record_json(40)).message


def test_json_container_flood() -> None:
    error = rejected("d.json", b"[" + b"[]," * 2_000_001 + b"[]]")
    assert error.status == 413


# --- formulas stay inert ------------------------------------------------------------


def test_formula_cells_are_kept_as_plain_text() -> None:
    frame = parse_upload("f.csv", fx.formula_csv()).frame
    assert frame["comment"].tolist()[:4] == fx.FORMULA_CELLS


def test_xlsx_formulas_are_never_evaluated() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["text", "total"])
    sheet.append(["Good", "=1+1"])
    sheet.append(["Bad", 7])
    buffer = io.BytesIO()
    workbook.save(buffer)
    frame = parse_upload("f.xlsx", buffer.getvalue()).frame
    # No stored result exists for the formula, and we do not compute one.
    assert pd.isna(frame["total"][0])
    assert frame["total"][1] == 7


@pytest.mark.parametrize("cell", fx.FORMULA_CELLS + ["\t=1+1", "\r=1+1"])
def test_formula_safe_neutralises_exported_cells(cell: str) -> None:
    assert formula_safe(cell) == "'" + cell


def test_formula_safe_leaves_ordinary_values_alone() -> None:
    assert formula_safe("An ordinary comment") == "An ordinary comment"
    assert formula_safe(-5) == -5
    assert formula_safe(None) is None
