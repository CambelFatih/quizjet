"""Self-check for build_discover.py — run: python scripts/test_build_discover.py"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import build_discover as bd

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
    ],
}


def run() -> None:
    original_blocklist = bd.BLOCKLIST_PATH
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        blocklist = tmp_path / "blocklist.json"
        blocklist.write_text('["blocked1"]', encoding="utf-8")
        bd.BLOCKLIST_PATH = blocklist
        try:
            export = tmp_path / "export.json"
            export.write_text(json.dumps(EXPORT), encoding="utf-8")
            out = tmp_path / "site"

            message = bd.build(out, export)
            assert "2 quizzes in 18 languages" in message and "Skipped 1" in message, message

            # static pages are copied, private build inputs are not
            assert (out / "index.html").is_file() and (out / "tr" / "privacy.html").is_file()
            assert not (out / "scripts").exists() and not (out / "_data").exists() and not (out / ".github").exists()

            catalog = (out / "kesfet" / "index.html").read_text(encoding="utf-8")
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

            sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
            assert "https://quizjet.app/kesfet/" in sitemap and "https://quizjet.app/q/good_key-1/" in sitemap
            assert "https://quizjet.app/tr/" in sitemap, "static pages stay in the sitemap"

            data = json.loads((out / "discover.json").read_text(encoding="utf-8"))
            assert data["count"] == 2 and data["items"][0]["startGroup"].endswith("?startgroup=good_key-1")

            # every Discover page exists in all 18 languages and keeps the visitor in theirs
            tr_catalog = (out / "tr" / "kesfet" / "index.html").read_text(encoding="utf-8")
            assert '<html lang="tr" dir="ltr">' in tr_catalog and "<h1>Quiz&#x27;leri keşfet</h1>" in tr_catalog
            assert 'href="/tr/q/good_key-1/"' in tr_catalog and 'href="/q/good_key-1/"' not in tr_catalog
            assert 'href="/de/kesfet/"' in tr_catalog, "language menu switches to the same page in another language"
            assert tr_catalog.count('rel="alternate" hreflang=') == 19, "18 languages + x-default"
            tr_quiz = (out / "tr" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert 'href="/tr/kesfet/"' in tr_quiz and "Özel sohbette başlat" in tr_quiz
            assert "Soru: 1 · AI ile hazırlandı · Oynanma: 3 · Oyuncu: 12" in tr_quiz
            assert 'href="/tr/privacy.html"' in tr_quiz, "Turkish keeps its own legal pages"
            assert '<bdi dir="ltr">@QuizJetBot</bdi>' in tr_quiz and "{bot}" not in tr_quiz
            de_quiz = (out / "de" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert 'href="/privacy.html"' in de_quiz and 'href="/de/privacy.html"' not in de_quiz, "legal pages stay EN/TR"
            ar_quiz = (out / "ar" / "q" / "good_key-1" / "index.html").read_text(encoding="utf-8")
            assert '<html lang="ar" dir="rtl">' in ar_quiz and "→ " in ar_quiz, "RTL back arrow points right"
            assert sum(1 for _ in out.rglob("q/good_key-1/index.html")) == 18
            landing_tr = (out / "tr" / "index.html").read_text(encoding="utf-8")
            assert 'href="/tr/kesfet/"' in landing_tr and 'href="/kesfet/"' not in landing_tr
            assert sitemap.count("<loc>") == 18 + 18 * (1 + 2), "landing + per language: catalog + 2 quizzes"
            assert "https://quizjet.app/tr/q/good_key-1/" in sitemap

            # the landing band shows the real count, in every language, with no placeholder left
            for landing in ("index.html", "tr/index.html", "ar/index.html"):
                html_text = (out / landing).read_text(encoding="utf-8")
                assert 'id="discover-band"' in html_text and "{count}" not in html_text, landing
            assert "Ready-made quizzes: 2" in (out / "index.html").read_text(encoding="utf-8")
            assert "Hazır quiz&#x27;ler: 2" in (out / "tr" / "index.html").read_text(encoding="utf-8") or "Hazır quiz'ler: 2" in (out / "tr" / "index.html").read_text(encoding="utf-8")

            # no export: placeholder page, not indexed, sitemap untouched
            bare = tmp_path / "bare"
            bd.build(bare, None)
            placeholder = (bare / "kesfet" / "index.html").read_text(encoding="utf-8")
            assert (bare / "uz" / "kesfet" / "index.html").is_file(), "placeholder exists in every language"
            assert '<meta name="robots" content="noindex">' in placeholder
            for landing in ("index.html", "tr/index.html"):
                bare_html = (bare / landing).read_text(encoding="utf-8")
                assert 'id="discover-band"' not in bare_html and "{count}" not in bare_html, "band is removed without a catalog"
                assert ('href="/tr/kesfet/"' if landing.startswith("tr/") else 'href="/kesfet/"') in bare_html, "hero and menu links stay, in the page's language"
            assert "kesfet" not in (bare / "sitemap.xml").read_text(encoding="utf-8")

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
    print("build_discover self-check passed.")


if __name__ == "__main__":
    run()
