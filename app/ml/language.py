"""A light check for text the English-only model should not score.

This is a heuristic, not a language identifier. It flags a text when
- most of its letters are outside the Latin alphabet (Hindi, Arabic, Chinese, ...), or
- it contains common words of German, French, Spanish, Italian, Portuguese or
  Dutch, and more of those than common English words.
It needs positive evidence before flagging, so short English texts are never
skipped by mistake. Other Latin-script languages are not detected.
"""

import re

import pandas as pd

_ENGLISH_WORDS = (
    "the and is it to of this that was for not with you but have my on are be so at very "
    "they we he she had has an were would if or from all there one just what when out "
    "about more can do did will your their been which its i"
)
# Function words that are not also English words.
_OTHER_WORDS = (
    "der das und ist nicht ich sehr mit für auch aber wir ein eine gut "  # German
    "le les et est pas je très avec mais nous une était cette "  # French
    "el los las es muy para pero que una está del este "  # Spanish
    "il gli è molto che sono della questo "  # Italian
    "não muito com uma os isso "  # Portuguese
    "het een niet ik zeer voor maar wij goed"  # Dutch
)


_ENGLISH = frozenset(_ENGLISH_WORDS.split())
_OTHER = frozenset(_OTHER_WORDS.split())
_WORD = re.compile(r"[^\W\d_]+")
_SAMPLE = 400  # characters; enough to judge, and keeps long texts cheap


def _latin(char: str) -> bool:
    return "a" <= char <= "z" or "À" <= char <= "ɏ"


def is_not_english(text: str) -> bool:
    sample = text[:_SAMPLE].lower()
    words = _WORD.findall(sample)
    if not sample.isascii():
        letters = "".join(words)
        if len(letters) >= 3 and sum(not _latin(c) for c in letters) > len(letters) / 2:
            return True
    other = sum(word in _OTHER for word in words)
    return other >= 2 and other > sum(word in _ENGLISH for word in words)


def not_english(texts: pd.Series) -> pd.Series:
    """True for each text that looks like it is not English. Missing values are False."""
    flags = [isinstance(text, str) and is_not_english(text) for text in texts]
    return pd.Series(flags, index=texts.index, dtype=bool)
