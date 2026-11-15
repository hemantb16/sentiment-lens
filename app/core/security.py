"""Helpers that keep user-supplied text inert."""

from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def formula_safe(value: Any) -> Any:
    """Stop a spreadsheet from running a cell as a formula when a file is exported.

    Text beginning with = + - @ (or a tab / carriage return, which Excel strips
    before looking) gets a leading apostrophe. Non-text values pass through.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
