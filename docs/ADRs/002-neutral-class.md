# ADR-002: Where "neutral" comes from, and which features the model uses

Status: accepted · Date: 2026-11-15

## Context

The dashboard shows positive / neutral / negative. The training data must have a
clear licence. Checked at the source on 2026-11-15:

| Dataset | Licence stated at source | Used? |
|---|---|---|
| Amazon Polarity (`fancyzhx/amazon_polarity`, Hugging Face Hub) | Apache-2.0 | Yes, training and in-domain test |
| Sentiment Labelled Sentences (UCI ML Repository) | CC BY 4.0 | Yes, cross-domain test |
| IMDB Large Movie Review (`stanfordnlp/imdb`) | "other" | No, licence unclear |
| SST-2 (`stanfordnlp/sst2`) | "unknown" | No, licence unclear |

Both usable datasets have two classes only. No three-class dataset with a clear
licence and a suitable domain was found.

## Decision 1: neutral is a confidence band, not a trained class

The model outputs the probability that a text is positive.

- 0.65 or more: **positive**
- 0.35 or less: **negative**
- in between: **neutral**

So "neutral" means *the model sees no clear lean*. That covers genuinely neutral
text, mixed opinions, and text the model simply cannot read well. The UI must say
this in plain words and must not present neutral as a measured third sentiment.

Evidence that the band marks real uncertainty (accuracy if forced to pick a side):

| Test set | Rows in band | Accuracy inside band | Accuracy outside band |
|---|---|---|---|
| Amazon reviews (50,000) | 7.9% | 61.7% | 95.6% |
| UCI sentences (3,000) | 13.2% | 59.2% | 93.8% |

Inside the band the model is barely better than a coin toss, so withholding a
verdict there is the honest call.

Alternatives rejected: deriving neutral from 3-star reviews (needs a dataset with
ratings and a clear licence; none found), and mapping an emotion dataset
(GoEmotions, Apache-2.0) to three classes (Reddit comments, a poor match for
reviews, and the mapping itself would be our invention).

## Decision 2: word features only, no character n-grams

The plan called for word 1-2 grams plus character n-grams. Both were trained on
the same 150,000 reviews:

| | Words only (shipped) | Words + characters |
|---|---|---|
| In-domain accuracy | 92.9% | 92.7% |
| Cross-domain accuracy | 89.2% | 90.0% |
| Raw scoring speed | 6,828 rows/s | 1,436 rows/s |
| Training time | 32 s | 120 s |

Character features buy 0.8 points across domains and cost 4.8x in speed. With
them, 50,000 rows would take well over the 30-second target, and the live host
has a fraction of one CPU. Words only it is. Rerun the comparison with
`python -m app.ml.train --features word+char --no-save`.

## Decision 3: plain logistic regression, no separate calibration step

Logistic regression already outputs probabilities. Measured calibration error is
0.026 in-domain and 0.038 cross-domain. The model is slightly *under*-confident in
the middle (texts scored 0.75 are positive 82% of the time), which makes the
neutral band a little wider than strictly needed. That errs on the cautious side,
so no extra calibration layer was added. The full table is in the model card.

## Consequences

- The neutral share on a user's data partly reflects how unlike the training data
  it is. The model note in the dashboard must say so.
- Changing the band needs no retraining: it is read from `models/sentiment-v1.json`.
