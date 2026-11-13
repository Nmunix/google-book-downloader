# Google Books Downloader

An open-source utility that saves the pages of a Google Books preview as
images or as a single PDF.

This is version **2.0**, a full rewrite of the 2020 script. Same job, modern
stack: no bundled driver binary, no proxy-based sniffing, typed code, tests and
a real command-line interface.

---

## What changed in 2.0

| | 2020 (`gbd.py`) | 2026 (`gbd`) |
|---|---|---|
| Browser driver | 24 MB `chromedriver.exe` committed to the repo, pinned to one Chrome version | **Selenium Manager** downloads the matching driver (and browser, if needed) on first run |
| Network capture | `selenium-wire` (a mitmproxy proxy wrapper, last released 2022, incompatible with modern Selenium) | **WebDriver BiDi** — the W3C standard. Selenium subscribes to `network.responseStarted` events directly |
| Selenium pin | `selenium==4.0.0` (2021) | `selenium>=4.45,<5`, current release line |
| HTTP | `requests` | `httpx` with a shared connection pool, explicit timeouts |
| Parsing / regex | `regex` + `eval()` of a Python dict for backups | stdlib `re`, JSON backups with a schema version |
| CLI | interactive `input()` chain, recursion on bad input | `typer` commands + optional wizard, validated arguments |
| Output UI | `progressbar2` | `rich` progress, tables and spinners |
| PDF | "use an online converter" | built in, via Pillow (per-page, memory-safe) |
| Errors | bare `except:` everywhere, crash log file | typed exception hierarchy with actionable messages |
| Code | one 300-line untested script | `src/` package, 143 tests, `ruff` + `mypy --strict` clean, CI on 3 OS × 4 Python versions |

The whole dependency list is now five packages, all current:

```
selenium>=4.45   httpx>=0.28   rich>=13.8   typer>=0.19   pillow>=11
```

---

## Requirements

- **Python 3.10+**
- **Chrome, Edge or Firefox** installed. That is all — you do **not** need to
  download a driver. Selenium Manager fetches the matching one automatically.

Check your setup at any time:

```bash
gbd doctor
```

---

## Install

```bash
git clone https://github.com/Nmunix/google-book-downloader
cd google-book-downloader
python -m pip install .
```

For development (adds pytest and ruff):

```bash
python -m pip install -e ".[dev]"
```

Prefer a virtual environment: `python -m venv .venv && source .venv/bin/activate`
(on Windows: `.venv\Scripts\activate`).

---

## Usage

### Guided wizard (the classic experience)

```bash
gbd
```

You are asked, in order: where to start (URL or an existing backup), the book
URL, which pages, where to save, and the format. Every prompt validates its
input instead of crashing and restarting.

### Non-interactive

```bash
# Everything, as a PDF plus the page images
gbd download "https://books.google.com/books?id=buc0AAAAMAAJ"

# Only pages 1–30 as a PDF, inside ./books
gbd download buc0AAAAMAAJ --pages 1-30 --format pdf --output ./books

# Odd pages only, images only
gbd download buc0AAAAMAAJ --pages odd --format images

# A bare volume id works too
gbd download buc0AAAAMAAJ --pages first-10
```

### Capture once, download many times

Walking a long preview is the slow part. Save the page URLs and reuse them:

```bash
gbd capture buc0AAAAMAAJ --backup my-book.gbd.json
gbd download whatever --reuse-backup my-book.gbd.json --pages 1-100 --output ./books
```

The backup is plain, versioned JSON (unlike 1.x, which wrote a Python `dict`
literal and read it back with `eval()`).

### Page selection syntax

| Token | Meaning |
|---|---|
| `all` | every page in the preview (default) |
| `5, 12, 603` | individual pages |
| `10-50` | inclusive range |
| `odd` / `even` | parity filter |
| `first`, `last` | the first/last captured page |
| `first-20`, `90-last` | ranges anchored on an edge |
| `-20`, `30-` | open-ended ranges |

Combine with commas: `1, 10-50, odd, 603`. Pages that the preview does not
expose are skipped with a warning, never silently ignored.

---

## Commands

| Command | Purpose |
|---|---|
| `gbd` / `gbd wizard` | guided, step-by-step flow |
| `gbd download URL` | scriptable download |
| `gbd capture URL -B file` | only walk the preview and save the URLs |
| `gbd doctor` | report Python, Selenium, Pillow and browser versions |

Useful options on `download`: `--pages/-p`, `--output/-o`, `--format/-f`
(`pdf`, `images`, `both`), `--browser/-b` (`chrome`, `edge`, `firefox`),
`--headless/--headed`, `--concurrency`, `--retries`, `--timeout`,
`--max-pages`, `--backup`, `--reuse-backup`, `--no-keep-images`, `--overwrite`,
and the global `--verbose/-v` and `--quiet/-q` (which must come before the
subcommand: `gbd -v download ...`).

### `--headed` when something breaks

Google occasionally changes the viewer markup. Running with `--headed`
(or `--headless` off) lets you watch what the browser actually does, which is
the fastest way to tell whether the book has a preview at all.

---

## How it works

1. **Open the viewer.** The embedded preview
   (`...&output=embed&source=gbs_embed`) is loaded in a headless browser.
2. **Subscribe to the network.** With `options.enable_bidi = True`, Selenium
   opens a WebDriver BiDi session. Every `network.responseStarted` event whose
   URL is a `books/content` page image is recorded — no proxy, no certificate,
   no `selenium-wire`.
3. **Turn pages.** The viewer's own state is read from the page, the next page
   is requested through the viewer API, and a click/arrow-key fallback is kept
   for revisions that ignore it. Capture stops at the last page, when page
   turning stops advancing, or at `--max-pages`.
4. **Upgrade quality.** Observed URLs are rewritten to `zoom=3` with a large
   `w`, so the server returns the native scan resolution rather than the
   screen-sized thumbnail the viewer asked for.
5. **Download.** `httpx` fetches the images with bounded concurrency, retries
   on 5xx/timeouts, stops early on 4xx, and writes each file atomically.
6. **Export.** Pillow assembles the images into a PDF, feeding extra pages
   lazily so a 900-page book does not all sit in RAM.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not start chrome` | Install Chrome/Edge/Firefox, or point at one with `--binary /path/to/browser` |
| Driver download blocked | Pre-install a driver and pass `--driver-path`, or set `HTTPS_PROXY` for the download |
| `No page images were found` | The book has no preview (only "About this book"). Open the URL in a browser to confirm. If it *does* preview, retry with `--headed` and report the markup |
| `did not enable WebDriver BiDi` | Update Selenium (`pip install -U selenium`) or switch browser with `--browser` |
| Some pages fail | Google throttled the burst: lower `--concurrency` and raise `--retries` |
| Old backup rejected | 1.x wrote Python literals; re-run `gbd capture` for that book |

---

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q                       # 143 tests, no network or browser needed
ruff check src tests            # lint
ruff format --check src tests   # formatting
pre-commit install              # optional: run the hooks on every commit
```

The browser layer is the only part that needs a real browser; everything else
(URL parsing, page selection, backups, downloading, PDF export, CLI) is tested
with the network and the driver stubbed out.

### Layout

```
src/google_book_downloader/
├── cli.py          # typer app: download, capture, wizard, doctor
├── ui.py           # rich output helpers
├── browser.py      # Selenium + WebDriver BiDi capture
├── bookurl.py      # URL parsing, page-image URL rewriting
├── pages.py        # page-selection mini-language
├── downloader.py   # httpx concurrent downloader
├── exporters.py    # Pillow PDF assembly
├── backup.py       # versioned JSON backups
├── models.py       # Book / BookPage dataclasses
├── paths.py        # slugify, unique dirs, atomic writes
└── errors.py       # exception hierarchy
tests/              # pytest suite mirroring the modules above
```

---

## Legal

Books are scraped **only** as far as Google itself serves them in a preview.
Respect copyright: this tool is meant for public-domain works, previews you
are entitled to view, and personal research. The authors are not responsible
for any misuse. The software is provided as-is, without warranty, under the
MIT licence — see [LICENSE](LICENSE).
