"""Downloads: the scored rows as CSV or XLSX, and a self-contained HTML report.

Two rules hold for every export:
- text cells pass through `formula_safe`, so a spreadsheet never runs a cell as a formula;
- everything written into HTML is escaped, so uploaded text can never become markup.
"""

import io
from html import escape
from typing import Any

import pandas as pd
from openpyxl import Workbook

from app.core.security import formula_safe

_COLOURS = {"positive": "#14614F", "neutral": "#8E897D", "negative": "#C4553B"}


def _safe_cells(table: pd.DataFrame) -> pd.DataFrame:
    safe = table.copy()
    safe.columns = pd.Index([formula_safe(str(name)) for name in table.columns])
    for name in safe.columns:
        if not pd.api.types.is_numeric_dtype(
            safe[name]
        ) and not pd.api.types.is_datetime64_any_dtype(safe[name]):
            safe[name] = safe[name].map(formula_safe)
    return safe


def to_csv(table: pd.DataFrame) -> bytes:
    # utf-8-sig: Excel only reads accented letters correctly when the file starts with a BOM.
    return _safe_cells(table).to_csv(index=False).encode("utf-8-sig")


def to_xlsx(table: pd.DataFrame) -> bytes:
    safe = _safe_cells(table)
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Scored rows")
    sheet.append(list(safe.columns))
    for row in safe.itertuples(index=False):
        sheet.append([None if pd.isna(value) else _plain(value) for value in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _plain(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value.item() if hasattr(value, "item") else value


# --- HTML report ------------------------------------------------------------

_STYLE = """
body{font:16px/1.5 "IBM Plex Sans",system-ui,sans-serif;color:#1A1A18;background:#FAFAF7;
max-width:860px;margin:48px auto;padding:0 20px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:14px;letter-spacing:.06em;text-transform:uppercase;
color:#5C5B55;border-top:1px solid #DDDBD2;padding-top:20px;margin:40px 0 8px}
.take{font-size:20px;line-height:1.35;margin:0 0 16px}.meta{color:#5C5B55;margin:0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:6px 10px 6px 0;
border-bottom:1px solid #DDDBD2;vertical-align:top}td.n,th.n{text-align:right;
font-family:"IBM Plex Mono",ui-monospace,monospace}
.bar{display:flex;height:22px;margin:8px 0}.bar span{display:block}
.key span{display:inline-block;width:10px;height:10px;margin:0 6px 0 14px}
@media print{body{margin:0 auto}}
"""


def _table(headers: list[str], rows: list[list[Any]], numeric: set[int]) -> str:
    def cell(tag: str, index: int, value: Any) -> str:
        css = ' class="n"' if index in numeric else ""
        shown = f"{value:,}" if isinstance(value, int) else str(value)
        return f"<{tag}{css}>{escape(shown)}</{tag}>"

    head = "".join(cell("th", i, h) for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(cell("td", i, v) for i, v in enumerate(row)) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _section(title: str, takeaway: str | None, body: str) -> str:
    lead = f'<p class="take">{escape(takeaway)}</p>' if takeaway else ""
    return f"<h2>{escape(title)}</h2>{lead}{body}"


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "0%"


def to_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    counts, scored = summary["counts"], summary["scored"]
    bar = "".join(
        f'<span style="width:{100 * counts[name] / scored if scored else 0:.2f}%;'
        f'background:{colour}"></span>'
        for name, colour in _COLOURS.items()
    )
    key = "".join(
        f'<span style="background:{colour}"></span>{name.capitalize()} '
        f"{_share(counts[name], scored)}"
        for name, colour in _COLOURS.items()
    )
    parts = [
        "<h1>Sentiment report</h1>",
        f'<p class="meta">{escape(payload["filename"])} · text column '
        f'"{escape(str(payload["mapping"]["text"]))}"</p>',
        _section(
            "Summary",
            summary["takeaway"],
            f'<div class="bar">{bar}</div><p class="key">{key}</p>'
            + _table(
                ["Rows", "Scored", "Skipped", "Average confidence"],
                [
                    [
                        summary["rows"],
                        scored,
                        summary["rows"] - scored,
                        f"{summary['mean_confidence']:.0%}" if scored else "n/a",
                    ]
                ],
                {0, 1, 2, 3},
            ),
        ),
    ]

    time = payload["time"]
    if time:
        points = time["series"][time["default"]]
        parts.append(
            _section(
                f"Sentiment over time (by {time['default']})",
                time["takeaway"],
                _table(
                    ["Starting", "Positive", "Neutral", "Negative", "Rows"],
                    [
                        [p["period"], p["positive"], p["neutral"], p["negative"], p["total"]]
                        for p in points
                    ],
                    {1, 2, 3, 4},
                ),
            )
        )
    category = payload["category"]
    if category:
        parts.append(
            _section(
                f"Sentiment by {payload['mapping']['category']}",
                category["takeaway"],
                _table(
                    ["Category", "Positive", "Neutral", "Negative", "Rows"],
                    [
                        [
                            g["value"],
                            _share(g["positive"], g["total"]),
                            _share(g["neutral"], g["total"]),
                            _share(g["negative"], g["total"]),
                            g["total"],
                        ]
                        for g in category["groups"]
                    ],
                    {1, 2, 3, 4},
                ),
            )
        )

    drivers = payload["drivers"]
    longest = max(len(drivers["terms"]["positive"]), len(drivers["terms"]["negative"]))

    def term(side: str, i: int) -> str:
        terms = drivers["terms"][side]
        return f"{terms[i]['term']} ({terms[i]['rows']:,} rows)" if i < len(terms) else ""

    parts.append(
        _section(
            "What drives it",
            drivers["takeaway"],
            _table(
                ["Pushes positive", "Pushes negative"],
                [[term("positive", i), term("negative", i)] for i in range(min(longest, 10))],
                set(),
            ),
        )
    )

    for group in ("Most positive", "Most negative", "Least confident"):
        rows = [e for e in payload["examples"] if e["group"] == group][:10]
        if rows:
            parts.append(
                _section(
                    f"Examples: {group.lower()}",
                    None,
                    _table(
                        ["Row", "Text", "Label", "P(positive)"],
                        [
                            [e["row"], e["text"], e["label"], f"{e['probability']:.2f}"]
                            for e in rows
                        ],
                        {0, 3},
                    ),
                )
            )

    profile = payload["profile"]
    parts.append(
        _section(
            "Data overview",
            f"{profile['rows']:,} rows, {len(profile['columns'])} columns, "
            f"{profile['duplicate_rows']:,} duplicate rows.",
            _table(
                ["Column", "Kind", "Missing", "Distinct values"],
                [
                    [c["name"], c["kind"], f"{c['missing_pct']}%", c["unique"]]
                    for c in profile["columns"]
                ],
                {2, 3},
            ),
        )
    )
    parts.append(_section("About the model", payload["model"]["takeaway"], ""))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Sentiment report</title><style>{_STYLE}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )
