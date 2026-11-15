"""Text cleaning shared by training and scoring. Changing it means retraining."""

import html
import re
from collections.abc import Iterable

_URL = re.compile(r"https?://\S+|www\.\S+")
_TAG = re.compile(r"<[^>]{1,200}>")
_MENTION = re.compile(r"@\w+")
_REPEAT = re.compile(r"(.)\1{2,}")
_SPACE = re.compile(r"\s+")

# The training reviews contain almost no emoji, so a dedicated emoji token would
# never get a weight. Mapping them to a plain word the model does know works instead.
_POSITIVE = "😀😃😄😁😊🙂😍🥰😘👍👏🎉❤💯😻🤩😎🙌💖💕"
_NEGATIVE = "😡😠🤬👎😞😢😭💔😤😩😫🤮😒🙁😖😣"
_EMOJI = str.maketrans(
    {
        **dict.fromkeys(_POSITIVE, " good "),
        **dict.fromkeys(_NEGATIVE, " bad "),
        "’": "'",
        "‘": "'",
        "️": "",
    }
)
_HAPPY_FACE = re.compile(r"(?<![\w:])[:;=]-?[)\]dp](?!\w)|<3")
_SAD_FACE = re.compile(r"(?<![\w:])[:;=]-?[(\[/](?!\w)")

# "not good at all" -> "not not_good not_at not_all": the words after a negation
# become different features, so "good" and "not good" stop looking alike.
_NEGATION = re.compile(
    r"\b(not|no|never|cannot|without|hardly|barely|[a-z]+n't)((?: [a-z']+){1,3})"
)


def _negate(match: re.Match[str]) -> str:
    return match.group(1) + match.group(2).replace(" ", " not_")


def clean(text: str) -> str:
    # Each `in` test skips a regex pass that could not match; the output is unchanged.
    if "&" in text:
        text = html.unescape(text)
    text = text.lower().translate(_EMOJI)
    if "http" in text or "www." in text:
        text = _URL.sub(" url ", text)
    if "<" in text:
        text = _TAG.sub(" ", text)
    if "@" in text:
        text = _MENTION.sub(" user ", text)
    if ":" in text or ";" in text or "=" in text or "<3" in text:
        text = _HAPPY_FACE.sub(" good ", text)
        text = _SAD_FACE.sub(" bad ", text)
    text = _REPEAT.sub(r"\1\1", text)  # "sooooo" -> "soo"
    text = _SPACE.sub(" ", text).strip()
    return _NEGATION.sub(_negate, text)


def clean_many(texts: Iterable[str]) -> list[str]:
    return [clean(text) for text in texts]
