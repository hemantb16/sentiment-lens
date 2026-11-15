import pandas as pd
import pytest

from app.ml.language import not_english
from app.ml.preprocess import clean, clean_many


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("GREAT Product!!", "great product!!"),
        ("see https://example.test/a?b=1 now", "see url now"),
        ("visit www.example.test today", "visit url today"),
        ("<p>Nice &amp; <b>solid</b></p>", "nice & solid"),
        ("thanks @someone for this", "thanks user for this"),
        ("soooooo goooood", "soo good"),
        ("love it 😍", "love it good"),
        ("broken 👎", "broken bad"),
        ("works :)", "works good"),
        ("late again :(", "late again bad"),
        ("  lots   of\n\tspace ", "lots of space"),
    ],
)
def test_clean(raw: str, cleaned: str) -> None:
    assert clean(raw) == cleaned


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("this is not good at all, sadly", "this is not not_good not_at not_all, sadly"),
        ("I don’t like it", "i don't not_like not_it"),
        ("never again. fine otherwise", "never not_again. fine otherwise"),
        ("no problems so far", "no not_problems not_so not_far"),
    ],
)
def test_negation_marks_the_following_words(raw: str, cleaned: str) -> None:
    assert clean(raw) == cleaned


def test_clean_many_keeps_order() -> None:
    assert clean_many(["A", "B :)"]) == ["a", "b good"]


@pytest.mark.parametrize(
    "text",
    [
        "The delivery was fast and the product works well.",
        "Great!",
        "Excellent quality, sturdy build, fast shipping, highly recommend seller",
        "ok",
        "Loved the café, très chic decor and the staff were lovely",
        "5/5 ⭐⭐⭐⭐⭐",
        "",
    ],
)
def test_english_and_short_texts_are_not_flagged(text: str) -> None:
    assert not not_english(pd.Series([text], dtype="str"))[0]


@pytest.mark.parametrize(
    "text",
    [
        "Die Lieferung war schnell und das Produkt ist sehr gut.",
        "Le produit est arrivé cassé et je ne suis pas content.",
        "El producto es muy bueno pero la entrega fue lenta.",
        "Il prodotto è molto buono, sono contento.",
        "Het product is niet goed, ik ben zeer teleurgesteld.",
        "डिलीवरी बहुत तेज थी और उत्पाद अच्छा है",
        "Доставка была быстрой",
        "配送は早かったです",
        "المنتج جيد جدا",
    ],
)
def test_other_languages_are_flagged(text: str) -> None:
    assert not_english(pd.Series([text], dtype="str"))[0]
