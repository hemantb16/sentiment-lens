# Design

The page should read like an editorial data page, not a product demo. The tokens
live at the top of `app/static/app.css`.

## Rules

- Off-white paper, near-black ink, **one** accent (ink blue) for links, buttons,
  focus rings and progress.
- A fixed three-colour sentiment scale, always in the order positive, neutral,
  negative, always with a text label and a number beside it.
- 1px rules instead of shadows. Corners are square (2px on controls).
- IBM Plex Sans for text, IBM Plex Mono for numbers. Both self-hosted.
- A 12-column grid: a narrow rail with the section number and title, content
  beside it. One column below 860px.
- Every section opens with one plain sentence that says what the chart shows.
- Buttons say what they do: "Choose a file", "Analyse", "Download report".

Not used anywhere: gradients, glows, glass effects, emoji, sparkle icons, chat
bubbles, robot imagery, the words "AI" or "magic", card grids, pie charts, word
clouds, spinners.

## Charts

Drawn by hand in `app.js` (HTML bars and one SVG), with no chart library. A
library would add 300 KB to 1 MB and its own look; these five charts are simple
enough not to need one. Each chart has a text alternative (`role="img"` with a
label) and a "Show the numbers as a table" control beneath it.

## Contrast (WCAG 2.1 AA), measured 2026-11-16

| Pair | Ratio | Needs |
|---|---|---|
| Body text `#1A1A18` on paper `#FAFAF7` | 16.7:1 | 4.5:1 |
| Secondary text `#5C5B55` on paper | 6.5:1 | 4.5:1 |
| Secondary text on wash `#F0EFE9` | 5.9:1 | 4.5:1 |
| Accent / links `#1D3557` on paper | 11.8:1 | 4.5:1 |
| Button text on accent | 11.8:1 | 4.5:1 |
| Error text `#9E2B1F` on paper | 7.1:1 | 4.5:1 |
| Chart positive `#14614F` on paper | 7.1:1 | 3:1 |
| Chart neutral `#8E897D` on paper | 3.3:1 | 3:1 |
| Chart negative `#C4553B` on paper | 4.3:1 | 3:1 |
| Input borders, histogram bars `#5C5B55` | 6.5:1 | 3:1 |

The three sentiment colours differ in hue (bluish green, grey, vermilion, a pair
chosen to survive red-green colour blindness) and in brightness, but neutral and
negative are close in brightness. So colour is never the only signal: segments
are separated by a gap, the order is fixed, and labels and numbers are always
shown.

## Other accessibility points

- One `<h1>` per view, moved to on each step so screen readers announce the change.
- Status changes ("Reading file", "Analysis finished") go to a live region.
- The file picker is a real `<input type="file">` with a visible label; drag and
  drop is an extra.
- Progress is a `<progress>` element with a text count beside it. No spinners.
- No horizontal scrolling at 375px; wide tables scroll inside their own box.
- Animation (a slow fade on the loading placeholder) is off when the system asks
  for reduced motion.

Not yet checked: a full screen-reader pass and Lighthouse (Sprint 6).
