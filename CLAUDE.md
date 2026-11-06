# quizjet.app — Claude Code Project Guide

Static website of the QuizJet Telegram bot, published by GitHub Pages. Python 3.13 build scripts, no
framework, no npm. The bot itself lives in `CambelFatih/quizjet-bot`; this repo only reads its public
Discover export.

## Build and checks

```
python scripts/build_locales.py            # regenerates the 18 landing pages, legal pages, 404.html, sitemap.xml — commit the output
python scripts/build_locales.py --check    # ...and these four are the gate: run all before a PR
python scripts/check_seo.py
python scripts/test_build_discover.py
python scripts/test_build_news.py
python scripts/build_discover.py --out /tmp/site [--export export.json]   # the deployable artifact, outside the repo
```

CI runs the four checks on push to `main` only (owner decision 2026-11-04: a PR triggers nothing), then
`deploy.yml` publishes. **Merged = deployed** here, unlike the bot. The daily run (02:41 UTC) also pulls
the Discover catalog and writes the day's news lesson.

## URL rule — path segments are English, never localized

- Every section has one English name: `/discover/`, `/books/<slug>/`, `/news/<date>/`, `/q/<key>/`,
  `privacy.html`, `terms.html`, `support.html`. The language is only a prefix (`/tr/`, `/pt-br/`); the
  rest of the address is identical in all 18 languages. **UI text is localized through
  `_data/locales.json`; paths are not.**
- The allowed names live in `build_discover.ROUTES` and nowhere else; `test_build_discover.py` pins the
  built tree against that set and `check_seo.py` rejects a link to any other generated route. A new
  section = one English name added to `ROUTES`.
- The site launched with Turkish names (`kesfet`, `kitaplar`, `haberler`, fixed 2026-11-06).
  `build_discover.LEGACY_ROUTES` keeps a redirect page at every old address (search engines, shared
  links). Never reuse those names, never drop the redirects.
- The reader loads chapters from `D.base` (`/books/<slug>/<n>.json`, `/news/<date>/<n>.json`); a path
  typed anywhere else is a bug.

## Where things live

| What | Source | Output |
|---|---|---|
| Landing page | `_includes/index.template.html` (Turkish placeholder text, `data-copy` keys) + `_data/locales.json` | `<route>/index.html`, committed |
| Legal pages | `_legal/<en\|tr>/*.html` + `_data/legal.json` (bump `updated`) | `privacy.html` etc., committed; other languages link to English |
| Discover | the bot's `GET /public/discover/export` (secret `QUIZJET_EXPORT_TOKEN`) | built at deploy time, never committed (a quiz taken out of Discover must vanish) |
| Books | `_data/books.json` + `_data/books/<slug>.json` (`scripts/import_gutenberg.py`, Project Gutenberg) | `/books/`, chapters as `/books/<slug>/<n>.json` |
| News | `_data/news/<date>.json`, written and **committed by the workflow** (`scripts/build_news.py`, secret `OPENAI_API_KEY`) | `/news/`, `/news/<date>/` |
| Reader | `assets/reader.js`, `assets/reader.css` (books and news share it; copy keys `bk*`, `nw*`) | |

## Rules that the checks enforce

- Every copy key exists in all 18 locales (`build_locales --check`); English owns `/`, Turkish `/tr/`.
- Nothing under `scripts/`, nothing starting with `_` or `.` is published (`build_discover.publishable`).
- Generated pages carry no `data-copy`, exactly one `h1`, a versioned stylesheet URL, hreflang for all
  18 languages; the committed `sitemap.xml` lists the landing pages only (generated routes are added at build).
- News texts are written in a clean room: a research call (hosted web search) returns facts of at most 12
  words plus report links, dated from the runner's clock; the writer call sees those facts only. The
  copyright posture is the docstring of `scripts/build_news.py`; keep code and docstring in step.
- User-facing text is never hardcoded in a script: add a key to all 18 locales.

## Working loop

Branch → change → run the four checks → PR to `main` → merge → it is live within minutes. The owner
reads Turkish; code, commits and comments are English.
