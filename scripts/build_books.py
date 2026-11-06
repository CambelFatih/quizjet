"""Books pages: /books/ (the shelf) and /books/<slug>/ (the reader), in all 18 languages.

build_discover.build() calls this and wraps each page in the site frame. Book metadata is
_data/books.json; the text is _data/books/<slug>.json (made by scripts/import_gutenberg.py). Each
chapter is published once, language-neutral, as /books/<slug>/<n>.json and loaded on demand by
assets/reader.js; the reader page itself only carries the interface text.

Quiz links ("ekiştirmeli öğrenme"): put the bot quiz key (the code /start takes, the same one the
Discover pages link with) in "quiz" for the whole book, or in "chapterQuizzes": {"3": "key"} for one
chapter (1-based). The chapter key wins; with neither, the button just opens the bot.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKS_PATH = ROOT / "_data" / "books.json"
TEXT_DIR = ROOT / "_data" / "books"
BASE_URL = "https://quizjet.app"
BOT = "QuizJetBot"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # same alphabet as build_discover.KEY_RE (Telegram deep links)
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def asset_version(name: str) -> str:
    return hashlib.sha256((ROOT / "assets" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


def script_json(value: object) -> str:
    """JSON that is safe inside <script>: no "</" can close the tag early."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def load_books(books_path: Path | None = None, text_dir: Path | None = None) -> list[dict[str, object]]:
    books = []
    for meta in json.loads((books_path or BOOKS_PATH).read_text(encoding="utf-8")):
        slug = str(meta["slug"])
        if not SLUG_RE.match(slug):
            raise ValueError(f"book slug {slug!r} must be lowercase letters, digits and dashes")
        chapters = json.loads(((text_dir or TEXT_DIR) / f"{slug}.json").read_text(encoding="utf-8"))["chapters"]
        if not chapters or any(not c["p"] for c in chapters):
            raise ValueError(f"book {slug!r} has an empty chapter")
        keys = {"book": meta.get("quiz") or "", **{str(k): v for k, v in (meta.get("chapterQuizzes") or {}).items()}}
        for where, key in keys.items():
            if key and not KEY_RE.match(str(key)):
                raise ValueError(f"book {slug!r}: quiz key {key!r} ({where}) is not a Telegram start code")
            if where != "book" and not (where.isdigit() and 1 <= int(where) <= len(chapters)):
                raise ValueError(f"book {slug!r}: chapterQuizzes has no chapter {where!r}")
        books.append({**meta, "chapters": chapters})
    return books


def chapter_files(book: dict[str, object]) -> dict[str, str]:
    """Published path (under the site root) → JSON for every chapter of ``book``."""
    return {
        f"books/{book['slug']}/{n}.json": json.dumps(chapter, ensure_ascii=False, separators=(",", ":")) + "\n"
        for n, chapter in enumerate(book["chapters"], start=1)
    }


def cover(book: dict[str, object]) -> str:
    c1, c2 = (book.get("cover") or ["#1d3b5c", "#c8a45a"])[:2]
    return (f'<span class="book-cover" style="--c1:{e(c1)};--c2:{e(c2)}" aria-hidden="true" lang="en" dir="ltr">'
            f'<b>{e(book["title"])}</b><i>{e(book["author"])}</i></span>')


def reader_css() -> str:
    return f'    <link rel="stylesheet" href="/assets/reader.css?v={asset_version("reader.css")}">\n'


def list_page(locale: dict[str, object], pre: str, books: list[dict[str, object]]) -> tuple[str, str, str, str]:
    """(title, description, body, extra head) of /books/."""
    c = locale["copy"]
    rows = "".join(
        f'        <li><a href="{pre}/books/{b["slug"]}/">{cover(b)}<span class="book-info">'
        f'<strong lang="en" dir="ltr">{e(b["title"])}</strong><span lang="en" dir="ltr">{e(b["author"])}</span>'
        f'<small>{e(c["bkLevel"])}: {e(b["level"])} · {e(c["bkChapters"])}: {len(b["chapters"])}</small>'
        f'<span class="book-read">{e(c["bkRead"])} <span aria-hidden="true">→</span></span></span></a></li>\n'
        for b in books
    )
    body = (
        f'      <h1>{e(c["bkTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["bkLead"])}</p>\n'
        f'      <ul class="book-list">\n{rows}      </ul>\n'
        f'      <p class="quiz-note">{e(c["bkSource"])}</p>\n'
    )
    return f'{c["bkTitle"]} — QuizJet', c["bkMeta"], body, reader_css()


def radios(name: str, options: list[tuple[str, str]]) -> str:
    return "".join(f'<label><input type="radio" name="{name}" value="{e(v)}"> {e(label)}</label>' for v, label in options)


def reader_page(locale: dict[str, object], pre: str, book: dict[str, object]) -> tuple[str, str, str, str, str]:
    """(title, description, body, jsonld, extra head) of /books/<slug>/."""
    c, slug = locale["copy"], str(book["slug"])
    back = "→" if locale["dir"] == "rtl" else "←"
    # Optional keys a news edition sets (scripts/build_news.py); a Gutenberg book has none of them.
    back_href, back_label = book.get("back") or (f"{pre}/books/", c["bkBack"])
    quiz_copy = book.get("quizCopy") or {"title": c["bkQuizTitle"], "body": c["bkQuizBody"], "cta": c["bkQuizCta"], "next": c["bkNextChapter"]}
    title_lang = str(book.get("titleLang") or "en")
    title_dir = "ltr" if title_lang == "en" else str(locale["dir"])
    source_html = book.get("sourceHtml") or (
        f'{e(c["bkSource"])} <a href="{e(book["source"])}" target="_blank" rel="noopener noreferrer">gutenberg.org</a>')
    chapters = book["chapters"]
    options = "".join(f'<option value="{n}">{n}. {e(ch["title"])}</option>' for n, ch in enumerate(chapters, start=1))
    targets = [("", c["bkNone"])] + [(str(l["code"]), str(l["name"])) for l in LOCALES if l["code"] != "en"]
    target_options = "".join(f'<option value="{e(code)}">{e(name)}</option>' for code, name in targets)
    data = {
        "slug": slug,
        "base": book.get("base") or f"/books/{slug}/",
        "lang": locale["code"],
        "bot": BOT,
        "quiz": book.get("quiz") or "",
        "chapterQuizzes": book.get("chapterQuizzes") or {},
        "chapters": [ch["title"] for ch in chapters],
        "copy": {k: v for k, v in c.items() if k.startswith(("bk", "nw"))},
    }
    body = f"""      <div class="reader" id="reader">
        <p class="quiz-back"><a href="{e(back_href)}">{back} {e(back_label)}</a></p>
        <h1 lang="{e(title_lang)}" dir="{title_dir}">{e(book["title"])}</h1>
        <p class="rd-meta"><span lang="en" dir="ltr">{e(book["author"])}</span> · {e(c["bkLevel"])}: {e(book["level"])}</p>
        <p class="quiz-note">{source_html}</p>
        <div class="rd-tools">
          <label class="sr-only" for="rd-ch">{e(c["bkChapter"])}</label>
          <select id="rd-ch" lang="en" dir="ltr">{options}</select>
          <button type="button" class="rd-icon" id="rd-words-btn" aria-label="{e(c["bkWords"])}"><span aria-hidden="true">★</span> <span id="rd-words-n">0</span></button>
          <button type="button" class="rd-icon" id="rd-set-btn" aria-label="{e(c["bkSettings"])}" title="{e(c["bkSettings"])}"><span aria-hidden="true">⚙︎</span></button>
        </div>
        <div class="rd-pager" dir="ltr">
          <button type="button" class="rd-icon" id="rd-pg-prev" aria-label="{e(c["bkPrevPage"])}">‹</button>
          <p class="rd-progress" id="rd-progress" dir="{locale["dir"]}"></p>
          <button type="button" class="rd-icon" id="rd-pg-next" aria-label="{e(c["bkNextPage"])}">›</button>
        </div>
        <p class="rd-tip" id="rd-tip">{e(c["bkTip"])}</p>
        <article class="rd-text" id="rd-text" lang="en" dir="ltr"><p>{e(c["bkLoading"])}</p></article>
        <aside class="rd-quiz" id="rd-quiz" lang="{e(locale["code"])}" dir="{locale["dir"]}" hidden>
          <h2>{e(quiz_copy["title"])}</h2>
          <p>{e(quiz_copy["body"])}</p>
          <div class="quiz-actions">
            <a class="button button-primary" id="rd-quiz-link" href="https://t.me/{BOT}" target="_blank" rel="noopener noreferrer">{e(quiz_copy["cta"])} <span aria-hidden="true">↗</span></a>
            <a class="button button-light" id="rd-next-ch" href="#">{e(quiz_copy["next"])} <span aria-hidden="true">→</span></a>
          </div>
        </aside>
        <div class="rd-bar" id="rd-bar" dir="ltr">
          <div class="rd-row">
            <button type="button" id="rd-prev" aria-label="{e(c["bkPrev"])}">‹</button>
            <button type="button" id="rd-this"><span aria-hidden="true">▶</span> {e(c["bkThis"])}</button>
            <button type="button" id="rd-tr" aria-pressed="false"><span aria-hidden="true">文A</span> {e(c["bkTranslate"])}</button>
            <button type="button" id="rd-next" aria-label="{e(c["bkNext"])}">›</button>
          </div>
          <div class="rd-row rd-player" id="rd-player">
            <button type="button" id="rd-play" aria-label="{e(c["bkPlay"])}">▶</button>
            <input type="range" id="rd-seek" min="0" max="0" value="0" aria-label="{e(c["bkSentence"])}">
            <span class="rd-pos" id="rd-pos">0 / 0</span>
            <button type="button" id="rd-speed" aria-label="{e(c["bkSpeed"])}">1×</button>
          </div>
          <p class="rd-novoice" id="rd-novoice" hidden>{e(c["bkNoVoice"])}</p>
        </div>
        <dialog class="rd-sheet" id="rd-panel"><div class="rd-sheet-in">
          <button type="button" class="rd-x" data-close aria-label="{e(c["bkClose"])}">×</button>
          <div id="rd-panel-body"></div>
        </div></dialog>
        <dialog class="rd-sheet" id="rd-set"><form class="rd-sheet-in" method="dialog" id="rd-set-form">
          <button class="rd-x" aria-label="{e(c["bkClose"])}">×</button>
          <h2>{e(c["bkSettings"])}</h2>
          <div id="rd-voice"><label class="rd-field">{e(c["bkVoice"])} <select name="voice" dir="ltr"><option value="">{e(c["bkVoiceAuto"])}</option></select></label>
          <p class="rd-hint">{e(c["bkVoiceHint"])}</p></div>
          <div class="rd-field"><span>{e(c["bkFont"])}</span><button type="button" data-font="-1">A−</button><button type="button" data-font="1">A+</button></div>
          <fieldset><legend>{e(c["bkSpacing"])}</legend>{radios("spacing", [("1.5", "1.5"), ("1.8", "1.8"), ("2.1", "2.1")])}</fieldset>
          <fieldset><legend>{e(c["bkTheme"])}</legend>{radios("theme", [("light", c["bkLight"]), ("sepia", c["bkSepia"]), ("dark", c["bkDark"])])}</fieldset>
          <fieldset><legend>{e(c["bkView"])}</legend>{radios("view", [("book", c["bkViewBook"]), ("study", c["bkViewStudy"])])}</fieldset>
          <label class="rd-field">{e(c["bkTarget"])} <select name="target">{target_options}</select></label>
          <label class="rd-check"><input type="checkbox" name="autoTr"> {e(c["bkAutoTr"])}</label>
        </form></dialog>
        <dialog class="rd-sheet" id="rd-words"><div class="rd-sheet-in">
          <button type="button" class="rd-x" data-close aria-label="{e(c["bkClose"])}">×</button>
          <h2>{e(c["bkWords"])}</h2>
          <input class="discover-search" type="search" id="rd-wq" placeholder="{e(c["bkWordsSearch"])}" aria-label="{e(c["bkWordsSearch"])}" autocomplete="off">
          <ul class="rd-wlist" id="rd-wlist"></ul>
          <p class="discover-empty" id="rd-wempty">{e(c["bkWordsEmpty"])}</p>
        </div></dialog>
      </div>
      <script type="application/json" id="rd-data">{script_json(data)}</script>
      <script src="/assets/reader.js?v={asset_version("reader.js")}" defer></script>
"""
    jsonld = script_json(book.get("jsonld") or {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": book["title"],
        "author": {"@type": "Person", "name": book["author"]},
        "inLanguage": book.get("language") or "en",
        "educationalLevel": book["level"],
        "isAccessibleForFree": True,
        "url": f"{BASE_URL}{pre}/books/{slug}/",
    })
    description = book.get("description") or c["bkReaderMeta"].replace("{title}", str(book["title"])).replace("{author}", str(book["author"]))
    return book.get("pageTitle") or f'{book["title"]} — {c["bkTitle"]}', description, body, jsonld, reader_css()
