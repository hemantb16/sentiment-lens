# Model card: sentiment-v1

Every number here is in `models/sentiment-v1.json`, written by the training run.

## What it is

A logistic regression over TF-IDF features of words and two-word phrases
(200,000 features, 3.2 MB). Text is lower-cased, URLs, HTML and @mentions are
replaced, common emoji and emoticons become "good" / "bad", and the three words
after a negation are marked ("not good" becomes its own feature).

It outputs the probability that a text is positive. Labels come from that:
0.65 or more is positive, 0.35 or less is negative, in between is neutral
(see [ADR-002](ADRs/002-neutral-class.md)).

## Intended use

Getting an overview of opinion in a few hundred to 100,000 English texts such as
reviews, survey answers and support tickets. It is for reading a dataset as a
whole. It is not for decisions about individual people, and one row's label
should not be trusted on its own.

## Training data

150,000 Amazon product reviews (title and body), a seeded random sample of the
first training shard of **Amazon Polarity** (Zhang, Zhao and LeCun, 2015;
Apache-2.0 as published on the Hugging Face Hub). 50.4% positive. In that dataset
1-2 stars are negative, 4-5 stars positive, and 3-star reviews are left out.

## Results

| Test | Rows | Accuracy | Macro-F1 | VADER baseline accuracy |
|---|---|---|---|---|
| In-domain: Amazon reviews (official test split) | 50,000 | 92.9% | 0.929 | 71.7% |
| Cross-domain: all UCI sentences | 3,000 | 89.2% | 0.892 | 75.5% |
| Restaurants (Yelp sentences) | 1,000 | 89.3% | 0.893 | 72.7% |
| Films (IMDb sentences) | 1,000 | 88.4% | 0.884 | 77.1% |
| Phones (Amazon sentences) | 1,000 | 90.0% | 0.900 | 76.8% |

Cross-domain data: Sentiment Labelled Sentences (Kotzias et al., 2015; CC BY 4.0).
These are single sentences, much shorter than the training reviews.

Confusion matrix, in-domain (rows = actual, columns = predicted):

| | Predicted negative | Predicted positive |
|---|---|---|
| Actually negative | 23,134 | 1,731 |
| Actually positive | 1,818 | 23,317 |

Calibration, in-domain. If the model were perfectly calibrated the last two
columns would match. Expected calibration error: 0.026 (0.038 cross-domain).

| Predicted probability | Rows | Mean predicted | Actually positive |
|---|---|---|---|
| 0.0-0.1 | 17,170 | 0.025 | 0.015 |
| 0.1-0.2 | 3,187 | 0.144 | 0.090 |
| 0.2-0.3 | 1,885 | 0.247 | 0.180 |
| 0.3-0.4 | 1,373 | 0.349 | 0.310 |
| 0.4-0.5 | 1,337 | 0.448 | 0.386 |
| 0.5-0.6 | 1,272 | 0.551 | 0.578 |
| 0.6-0.7 | 1,358 | 0.652 | 0.707 |
| 0.7-0.8 | 1,873 | 0.754 | 0.818 |
| 0.8-0.9 | 3,171 | 0.856 | 0.922 |
| 0.9-1.0 | 17,374 | 0.974 | 0.988 |

Speed: 50,000 reviews (430 characters on average) scored with explanations in
22 seconds on one laptop core. The free host has about a tenth of a core, so
expect it to be several times slower there.

Strongest terms: *great, excellent, good, best, awesome, amazing, perfect* against
*disappointing, worst, boring, poor, awful, horrible, terrible*.

## Known limits

- **Domain shift.** Accuracy fell from 92.9% to 89.2% on short sentences about
  restaurants, films and phones. Expect a larger drop on text further from
  product reviews: tweets, chat, clinical or legal notes, news.
- **Neutral is not measured.** It means "no clear lean", which mixes truly neutral
  text, mixed opinions and text the model reads poorly.
- **Sarcasm and irony** are read literally ("great, another broken charger").
- **Mixed opinions** get one overall score.
- **English only.** A light check skips text in non-Latin scripts and in German,
  French, Spanish, Italian, Portuguese and Dutch. Other languages written in
  Latin letters are not detected and will be scored badly.
- **Very short texts** ("ok", "fine") carry little signal and often land in neutral.
- **Emoji** are handled by a fixed list of about 35; others are ignored.
- **Negation** is handled for three words after the cue. Longer constructions
  ("I would not say that I was happy") can be missed.

## Bias notes

The training reviews are consumer writing about products, mostly from one
marketplace and in standard written English. Dialects, non-native phrasing and
community-specific vocabulary are under-represented, so such text is more likely
to be mis-scored or pushed into neutral. Words that describe a product category
can carry sentiment weight learned from that category's reviews rather than from
the word's meaning. The explanation terms shown per row make such cases visible.

## Reproduce

```powershell
python -m app.ml.train
```

Seed 42 for sampling and fitting; scikit-learn 1.9.1. The run downloads the data
from the official sources into `data/` (about 380 MB), trains in about half a
minute, and rewrites `models/sentiment-v1.joblib` and `.json`. The app checks the
model file's SHA-256 against the `.json` before loading it.
