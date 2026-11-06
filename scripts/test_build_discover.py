"""Self-check for build_discover.py — run: python scripts/test_build_discover.py"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import build_books
import build_discover as bd
import build_news
import import_gutenberg as ig

LONG_TITLE = "Hukukun Temel Kavramları Sınav Hazırlık 2 Başarılar Rüzgarın Sesinin (Q13) ve devamı"  # 82 characters
EXPORT = {
    "generatedAtUtc": "2026-11-02T02:41:00Z",
    "botUsername": "QuizJetBot",
    "totalCount": 4,
    "truncated": False,
    "items": [
        {"quizId": "a", "externalId": "good_key-1", "title": "Türkiye <b>coğrafyası</b>", "description": "İller & bölgeler",
         "questionCount": 1, "isAiGenerated": True, "createdAtUtc": "2026-10-30T10:00:00Z", "executionCount": 3, "participantCount": 12},
        {"quizId": "b", "externalId": "bad key/../x", "title": "Unsafe key", "description": "",
         "questionCount": 5, "isAiGenerated": False, "createdAtUtc": "", "executionCount": 0, "participantCount": 0},
        {"quizId": "c", "externalId": "blocked1", "title": "Blocked", "description": "",
         "questionCount": 5, "isAiGenerated": False, "createdAtUtc": "", "executionCount": 0, "participantCount": 0},
        {"quizId": "d", "externalId": "no_desc", "title": "No description</script><script>alert(1)</script>", "description": None,
         "questionCount": 7, "isAiGenerated": False, "createdAtUtc": "2026-10-01T00:00:00Z", "executionCount": 1, "participantCount": 2},
        {"quizId": "e", "externalId": "url_desc", "title": LONG_TITLE, "description": "Katıl: https://t.me/+secretInvite www.example.com/x",
         "questionCount": 3, "isAiGenerated": False, "createdAtUtc": "2026-10-20T00:00:00Z", "executionCount": 9, "participantCount": 4},
    ],
}


def run() -> None:
    original_blocklist, original_news = bd.BLOCKLIST_PATH, build_news.NEWS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        blocklist = tmp_path / "blocklist.json"
        blocklist.write_text('["blocked1"]', encoding="utf-8")
        bd.BLOCKLIST_PATH = blocklist
        build_news.NEWS_DIR = tmp_path / "no-news"  # the repo carries real editions once the workflow has run; this check counts pages without them
        try:
            export = tmp_path / "export.json"
            export.write_text(json.dumps(EXPORT), encoding="utf-8")
            out = tmp_path / "site"

            message = bd.build(out, export)
            assert "3 quizzes in 18 languages" in message and "Skipped 1" in message, message

            # URL segments are English: nothing else appears at the top of the tree, and the Turkish names the site
            # launched with hold nothing but redirects to the English address of the same page
            top = {p.name for p in out.iterdir() if p.is_dir()}
            assert top <= {l["route"] for l in bd.LOCALES if l["route"]} | bd.ROUTES | set(bd.LEGACY_ROUTES) | {"assets", "en"}, top  # en: the legacy English URL
            for old, new in bd.LEGACY_ROUTES.items():
                moved = (out / "tr" / old / "index.html").read_text(encoding="utf-8")
                assert f'content="0;url=/tr/{new}/"' in moved and 'content="noindex"' in moved, (old, new)
                assert f'<link rel="canonical" href="https://quizjet.app/tr/{new}/">' in moved
                assert {p.name for p in (out / old).rglob("*") if p.is_file()} == {"index.html"}, f"only redirects live under /{old}/"
            assert 'content="0;url=/books/sherlock-holmes/"' in (out / "kitaplar" / "sherlock-holmes" / "index.html").read_text(encoding="utf-8")
            assert 'content="0;url=/"' in (out / "en" / "index.html").read_text(encoding="utf-8"), "the old English URL still forwards to the root"

            # static pages are copied, private build inputs are not
            assert (out / "index.html").is_file() and (out / "tr" / "privacy.html").is_file()
            assert not (out / "scripts").exists() and not (out / "_data").exists() and not (out / ".github").exists()
            assert not (out / "CLAUDE.md").exists(), "repo documents are not published"

            catalog = (out / "discover" / "index.html").read_text(encoding="utf-8")
            assert "Türkiye &lt;b&gt;coğrafyası&lt;/b&gt;" in catalog, "titles must be escaped"
            assert "<b>coğrafyası</b>" not in catalog
            assert 'data-search="turkiye <b>cografyasi</b> iller & bolgeler"' not in catalog
            assert 'data-search="turkiye &lt;b&gt;cografyasi&lt;/b&gt; iller &amp; bolgeler"' in catalog, "search key folds case and accents"
            assert "Unsafe key" not in catalog and "Blocked" not in catalog

            quiz = (out / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert "https://t.me/QuizJetBot?start=good_key-1" in quiz
            assert "https://t.me/QuizJetBot?startgroup=good_key-1" in quiz
            assert "Questions: 1 · Made with AI · Plays: 3 · Players: 12" in quiz
            assert '<link rel="canonical" href="https://quizjet.app/q/good_key-1/">' in quiz

            injected = (out / "q" / "no_desc" / "index.html").read_text(encoding="utf-8")
            assert "</script><script>alert(1)" not in injected, "JSON-LD must not break out of its script tag"
            assert 'content="A quiz you can play on Telegram with QuizJet. Questions: 7."' in injected, "fallback description"
            assert not (out / "q" / "blocked1").exists() and not any("bad key" in str(p) for p in out.rglob("*"))

            # a creator's description is published, the links in it are not; a long title is clipped in <title> only
            linked = (out / "q" / "url_desc" / "index.html").read_text(encoding="utf-8")
            assert "secretInvite" not in linked and "example.com" not in linked and 'name="description" content="Katıl:"' in linked
            page_title = linked.split("<title>")[1].split("</title>")[0]
            assert page_title.startswith(LONG_TITLE[:60]) and "…" in page_title and len(page_title) < len(LONG_TITLE) + 20, page_title
            assert f"<h1>{LONG_TITLE}</h1>" in linked
            assert "secretInvite" not in catalog and 'data-plays="9"' in catalog and 'data-ai="manual"' in catalog
            assert 'id="sort"' in catalog and 'id="more"' in catalog and 'aria-live="polite"' in catalog, "sort, filter, show-more and a live count"
            assert 'Last update: <time datetime="2026-11-02" dir="ltr">2026-11-02</time>' in catalog

            sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
            assert "https://quizjet.app/discover/" in sitemap and "https://quizjet.app/q/good_key-1/" in sitemap
            assert "https://quizjet.app/tr/" in sitemap, "static pages stay in the sitemap"

            data = json.loads((out / "discover.json").read_text(encoding="utf-8"))
            assert data["count"] == 3 and data["items"][0]["startGroup"].endswith("?startgroup=good_key-1")

            # every Discover page exists in all 18 languages and keeps the visitor in theirs
            tr_catalog = (out / "tr" / "discover" / "index.html").read_text(encoding="utf-8")
            assert '<html lang="tr" dir="ltr">' in tr_catalog and "<h1>Quiz&#x27;leri keşfet</h1>" in tr_catalog
            assert 'href="/tr/q/good_key-1/"' in tr_catalog and 'href="/q/good_key-1/"' not in tr_catalog
            assert 'href="/de/discover/"' in tr_catalog, "language menu switches to the same page in another language"
            assert ('<details class="nav-menu"><summary aria-label="Menü"></summary><div class="nav-panel">'
                    '<a class="brand nav-panel-brand" href="/tr/">') in tr_catalog and '<div class="nav-panel"><a class="brand' in tr_catalog, "phone menu panel"
            assert tr_catalog.split('<div class="nav-panel">')[1].count('<a href="/tr/discover/">') == 1
            assert tr_catalog.count("querySelectorAll('.nav-menu')") == 1
            assert tr_catalog.count('rel="alternate" hreflang=') == 19, "18 languages + x-default"
            tr_quiz = (out / "tr" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert 'href="/tr/discover/"' in tr_quiz and "Özel sohbette başlat" in tr_quiz
            assert "Soru: 1 · AI ile hazırlandı · Oynanma: 3 · Oyuncu: 12" in tr_quiz
            assert 'href="/tr/privacy.html"' in tr_quiz, "Turkish keeps its own legal pages"
            assert '<bdi dir="ltr">@QuizJetBot</bdi>' in tr_quiz and "{bot}" not in tr_quiz
            de_quiz = (out / "de" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert 'href="/privacy.html"' in de_quiz and 'href="/de/privacy.html"' not in de_quiz, "legal pages stay EN/TR"
            assert "Datenschutz (EN)</a>" in de_quiz and " (EN)</a>" not in tr_quiz and " (EN)</a>" not in quiz, "and the other languages say so"
            assert " (EN)</a>" in (out / "de" / "index.html").read_text(encoding="utf-8")
            assert " (EN)</a>" not in (out / "tr" / "index.html").read_text(encoding="utf-8")
            ar_quiz = (out / "ar" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert '<html lang="ar" dir="rtl">' in ar_quiz and "→ " in ar_quiz, "RTL back arrow points right"
            assert sum(1 for _ in out.rglob("q/good_key-1/index.html")) == 18
            landing_tr = (out / "tr" / "index.html").read_text(encoding="utf-8")
            assert 'href="/tr/discover/"' in landing_tr and 'href="/discover/"' not in landing_tr
            assert sitemap.count("<loc>") == 18 + 18 * (1 + 3) + 18 * 3 + len(bd.LEGAL_URLS), \
                "landing + per language: catalog + 3 quizzes + shelf + 2 books, plus the legal pages"
            assert "https://quizjet.app/tr/q/good_key-1/" in sitemap and "https://quizjet.app/tr/privacy.html" in sitemap
            assert "<lastmod>2026-10-30</lastmod>" in sitemap and sitemap.count("<lastmod>") == 18 * (1 + 3), "dates only where known"

            # the landing band shows the real count, in every language, with no placeholder left
            for landing in ("index.html", "tr/index.html", "ar/index.html"):
                html_text = (out / landing).read_text(encoding="utf-8")
                assert 'id="discover-band"' in html_text and "{count}" not in html_text, landing
            index = (out / "index.html").read_text(encoding="utf-8")
            assert "Ready-made quizzes: 3" in index and "<span>Plays</span>: 13 · <span>Players</span>: 18" in index and "{plays}" not in index
            tr_index = (out / "tr" / "index.html").read_text(encoding="utf-8")
            assert ("Hazır quiz&#x27;ler: 3" in tr_index or "Hazır quiz'ler: 3" in tr_index) and "<span>Oynanma</span>: 13 · <span>Oyuncu</span>: 18" in tr_index
            assert '<a class="language-chip" href="/tr/" lang="tr"' in index, "the language grid links the language pages"
            assert '"@type":"SoftwareApplication"' in index and '"inLanguage":"tr"' in tr_index, "structured data on every landing page"
            assert (out / "manifest.webmanifest").is_file() and (out / "assets" / "fonts" / "dm-sans-latin.woff2").is_file()
            not_found = (out / "404.html").read_text(encoding="utf-8")
            assert '<html lang="en"' in not_found and "Sayfa bulunamadı" in not_found and '"tr":{"lang":"tr"' in not_found, "one 404, every language inside"
            assert not (out / "news" / "feed.xml").exists(), "no edition, no feed"

            # no export: placeholder page, not indexed, sitemap untouched
            bare = tmp_path / "bare"
            bd.build(bare, None)
            placeholder = (bare / "discover" / "index.html").read_text(encoding="utf-8")
            assert (bare / "uz" / "discover" / "index.html").is_file(), "placeholder exists in every language"
            assert '<meta name="robots" content="noindex">' in placeholder
            for landing in ("index.html", "tr/index.html"):
                bare_html = (bare / landing).read_text(encoding="utf-8")
                assert 'id="discover-band"' not in bare_html and "{count}" not in bare_html, "band is removed without a catalog"
                assert ('href="/tr/discover/"' if landing.startswith("tr/") else 'href="/discover/"') in bare_html, "hero and menu links stay, in the page's language"
            bare_sitemap = (bare / "sitemap.xml").read_text(encoding="utf-8")
            assert "discover" not in bare_sitemap and "https://quizjet.app/tr/books/sherlock-holmes/" in bare_sitemap

            # Books: shelf + reader in every language, chapters published once and language-neutral
            assert sum(1 for _ in bare.rglob("books/sherlock-holmes/index.html")) == 18
            chapter = json.loads((bare / "books" / "sherlock-holmes" / "1.json").read_text(encoding="utf-8"))
            assert chapter["title"] == "A Scandal in Bohemia" and chapter["p"][0][0] == "To Sherlock Holmes she is always THE woman."
            assert not (bare / "tr" / "books" / "sherlock-holmes" / "1.json").exists()
            tr_reader = (bare / "tr" / "books" / "sherlock-holmes" / "index.html").read_text(encoding="utf-8")
            assert '<html lang="tr" dir="ltr">' in tr_reader and 'href="/tr/books/"' in tr_reader
            assert '"lang":"tr"' in tr_reader and "Bu cümle" in tr_reader and "/assets/reader.js?v=" in tr_reader
            assert "To Sherlock Holmes she is always THE woman." in tr_reader, "the first chapter is in the HTML, not only in the JSON"
            assert '<section class="rd-quiz"' in tr_reader and "Bu kitabın quiz&#x27;i yolda" in tr_reader, "no quiz key: no chapter-quiz promise"
            assert 'href="/de/books/sherlock-holmes/"' in tr_reader, "language menu stays on the book"
            assert '"@type":"Book"' in tr_reader
            assert 'name="autoTr"' in tr_reader and 'id="rd-tip"' in tr_reader and "translate.google.com/?" not in tr_reader
            assert tr_reader.index('id="rd-tip"') < tr_reader.index('class="rd-tools"'), "the tip sits above the toolbar: hiding it never changes the page height"
            shelf = (bare / "books" / "index.html").read_text(encoding="utf-8")
            assert 'href="/books/sherlock-holmes/"' in shelf and "Chapters: 12" in shelf
            assert 'href="/books/frankenstein/"' in shelf and "Chapters: 28" in shelf
            assert 'href="/tr/books/"' in (bare / "tr" / "index.html").read_text(encoding="utf-8"), "landing links the shelf"

            # a truncated export is refused
            export.write_text(json.dumps({**EXPORT, "truncated": True}), encoding="utf-8")
            try:
                bd.build(tmp_path / "partial", export)
                raise AssertionError("truncated export must fail")
            except ValueError:
                pass

            # output inside the repository is refused (the page checks scan every index.html)
            try:
                bd.build(bd.ROOT / "_site", None)
                raise AssertionError("output inside the repository must fail")
            except ValueError:
                pass
        finally:
            bd.BLOCKLIST_PATH = original_blocklist
            build_news.NEWS_DIR = original_news
    check_books(Path(tempfile.mkdtemp()))
    print("build_discover self-check passed.")


def check_books(tmp: Path) -> None:
    # sentence splitting keeps abbreviations, initials and '"Seven!" I answered.' together
    assert ig.split_sentences('"Wedlock suits you," he remarked. "I think, Watson, that you have." Mr. St. Simon came.') == [
        '"Wedlock suits you," he remarked.', '"I think, Watson, that you have."', "Mr. St. Simon came."]
    assert ig.split_sentences('"Seven!" I answered. J. Smith left. Then?') == ['"Seven!" I answered.', "J. Smith left.", "Then?"]
    assert ig.clean("a--b _word_\n  c") == "a—b word c"
    assert ig.clean("“It’s _so_.”") == '"It\'s so."', "curly quotes become the ones the splitter knows"
    toc = "*** START OF X\n\nChapter 1\n\nChapter 2\n\nChapter 1\n\nHi.\n"
    assert [c["title"] for c in ig.parse(toc, r"^(Chapter [0-9]+)$")] == ["Chapter 1"], "contents entries are dropped"
    # metadata errors fail the build instead of publishing a broken link
    (tmp / "b.json").write_text('{"chapters": [{"title": "One", "p": [["Hi."]]}]}', encoding="utf-8")
    for bad in ({"quiz": "bad key"}, {"chapterQuizzes": {"2": "k"}}, {"slug": "Bad/Slug"}):
        meta = {"slug": "b", "title": "T", "author": "A", "level": "A1", "source": "https://x", **bad}
        (tmp / "books.json").write_text(json.dumps([meta]), encoding="utf-8")
        try:
            build_books.load_books(tmp / "books.json", tmp)
            raise AssertionError(f"{bad} must be refused")
        except (ValueError, FileNotFoundError):
            pass
    # the data blob cannot close its <script> tag
    meta = {"slug": "b", "title": "</script><script>alert(1)</script>", "author": "A", "level": "A1", "source": "https://x",
            "chapterQuizzes": {"1": "chapter_key"}}
    (tmp / "books.json").write_text(json.dumps([meta]), encoding="utf-8")
    book = build_books.load_books(tmp / "books.json", tmp)[0]
    _, _, body, jsonld, _ = build_books.reader_page(bd.LOCALES[0], "", book)
    assert "</script><script>alert(1)" not in body and "</script><script>alert(1)" not in jsonld
    assert '"chapterQuizzes":{"1":"chapter_key"}' in body


if __name__ == "__main__":
    run()
