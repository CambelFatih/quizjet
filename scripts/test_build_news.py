"""Self-check for build_news.py — run: python scripts/test_build_news.py (no network, no keys)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import build_discover as bd
import build_news as bn

SNIPPETS = [
    {"t": "Central bank holds rates", "u": "https://example.com/a",
     "c": "The central bank kept its main interest rate unchanged on Wednesday, saying inflation was slowing but still above target."},
    {"t": "Markets react to rate decision", "u": "https://example.com/b",
     "c": "Shares rose after the decision, with investors betting on a cut early next year."},
    {"t": "Unrelated story", "u": "https://example.com/c", "c": "A football club signed a new striker."},
]
ORIGINAL = {
    "title": "Interest rates stay the same for now",
    "paragraphs": [
        "A big bank that controls money in the country decided not to change interest rates this week. Prices are rising more slowly than before, but they are still rising too fast.",
        "After the news, the stock market went up. Many investors think the bank will make borrowing cheaper at the start of next year.",
        "Experts say the next months will show if prices really calm down. If they do, loans and mortgages could become cheaper.",
    ],
    "words": [{"w": "interest rate", "d": "the extra money you pay when you borrow"}, {"w": "investor", "d": "a person who puts money into a business"},
              {"w": "borrow", "d": "to take money that you must pay back"}],
    "quiz": [
        {"q": "What did the bank decide?", "o": ["To raise rates", "To keep rates the same", "To cut rates", "To close"], "answer": "To keep rates the same", "why": "It did not change the rate this week."},
        {"q": "What happened to shares?", "o": ["They fell", "They rose", "Nothing", "They were sold"], "answer": "They rose", "why": "The market went up after the news."},
        {"q": "When might borrowing get cheaper?", "o": ["Tomorrow", "Never", "Early next year", "In ten years"], "answer": "Early next year", "why": "Investors expect a cut at the start of next year."},
    ],
    "sources": [1, 2, 9],
}


def run() -> None:
    # the overlap guard: an eight-word run copied from a snippet is caught, original wording passes
    copied = dict(ORIGINAL, paragraphs=["The central bank kept its main interest rate unchanged on Wednesday, and that is the news."] + ORIGINAL["paragraphs"][1:])
    assert bn.overlap("the central bank kept its main interest rate unchanged", [SNIPPETS[0]["c"]])
    assert not bn.overlap(" ".join(ORIGINAL["paragraphs"]), [s["c"] for s in SNIPPETS])
    try:
        bn.article(copied, SNIPPETS, "business")
        raise AssertionError("a copied sentence must be rejected")
    except ValueError as exc:
        assert "copies a source" in str(exc), exc
    try:
        bn.article(dict(ORIGINAL, sources=[1]), SNIPPETS, "business")
        raise AssertionError("one source is not enough")
    except ValueError:
        pass

    art = bn.article(ORIGINAL, SNIPPETS, "business")
    assert art["topic"] == "business" and art["level"] == "B1"
    assert art["p"][0] == ["A big bank that controls money in the country decided not to change interest rates this week.",
                           "Prices are rising more slowly than before, but they are still rising too fast."], art["p"][0]
    assert [q["a"] for q in art["quiz"]] == [1, 1, 2], "the answer index comes from the answer text"
    assert [s["u"] for s in art["sources"]] == ["https://example.com/a", "https://example.com/b"], "only the snippets the model used, and only real ones"

    original_dir = bn.NEWS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        news = tmp_path / "news"
        news.mkdir()
        (news / "2026-11-06.json").write_text(json.dumps({"date": "2026-11-06", "model": "test", "articles": [art, dict(art, topic="sports", title="A striker <b>moves</b>")]}), encoding="utf-8")
        bn.NEWS_DIR = news
        try:
            out = tmp_path / "site"
            bd.build(out, None)
        finally:
            bn.NEWS_DIR = original_dir

        assert sum(1 for _ in out.rglob("haberler/2026-11-06/index.html")) == 18, "one reader per language"
        assert (out / "haberler" / "2026-11-06" / "1.json").is_file() and not (out / "tr" / "haberler" / "2026-11-06" / "1.json").exists()
        chapter = json.loads((out / "haberler" / "2026-11-06" / "2.json").read_text(encoding="utf-8"))
        assert chapter["quiz"][0]["a"] == 1 and chapter["sources"][0]["u"] == "https://example.com/a" and chapter["words"][0]["w"] == "interest rate"

        tr_reader = (out / "tr" / "haberler" / "2026-11-06" / "index.html").read_text(encoding="utf-8")
        assert '<html lang="tr" dir="ltr">' in tr_reader and 'href="/tr/haberler/"' in tr_reader and 'href="/tr/kitaplar/"' not in tr_reader.split("<main")[1].split("rd-tools")[0]
        assert '"base":"/haberler/2026-11-06/"' in tr_reader and '"slug":"news-2026-11-06"' in tr_reader
        assert "<h1 lang=\"tr\" dir=\"ltr\">Haber dersi · 2026-11-06</h1>" in tr_reader
        assert 'id="rd-next-ch" href="#">Sonraki metin ' in tr_reader, "the news framing replaces the book framing on the button"
        assert '"nwCorrect":"Doğru!"' in tr_reader and '"@type":"LearningResource"' in tr_reader and "gutenberg.org" not in tr_reader
        assert "Spor: A striker &lt;b&gt;moves&lt;/b&gt;" in tr_reader, "chapter names are escaped and carry the topic"
        ar_reader = (out / "ar" / "haberler" / "2026-11-06" / "index.html").read_text(encoding="utf-8")
        assert '<h1 lang="ar" dir="rtl">' in ar_reader

        shelf = (out / "haberler" / "index.html").read_text(encoding="utf-8")
        assert 'href="/haberler/2026-11-06/"' in shelf and "Business, Sports" in shelf and "Interest rates stay the same for now · A striker &lt;b&gt;moves&lt;/b&gt;" in shelf
        assert '<meta name="robots" content="noindex">' not in shelf and "support@quizjet.app" in shelf
        sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
        assert "https://quizjet.app/tr/haberler/2026-11-06/" in sitemap and "https://quizjet.app/haberler/" in sitemap
        landing = (out / "tr" / "index.html").read_text(encoding="utf-8")
        assert 'href="/tr/haberler/"' in landing and 'href="/haberler/"' not in landing, "the landing menu links the lessons in its own language"
        assert 'id="news-band"' in landing and "Bugünün haberlerini oku" in landing, "the landing band is reachable on phones, where the menu links are hidden"
        book = (out / "tr" / "kitaplar" / "sherlock-holmes" / "index.html").read_text(encoding="utf-8")
        assert "gutenberg.org" in book and 'id="rd-next-ch" href="#">Sonraki bölüm ' in book and '"base":"/kitaplar/sherlock-holmes/"' in book, "books keep their framing"

        # no edition yet: the page exists for the menu link, unindexed and out of the sitemap
        empty = tmp_path / "empty"
        bd.build(empty, None)
        bare = (empty / "haberler" / "index.html").read_text(encoding="utf-8")
        assert '<meta name="robots" content="noindex">' in bare and "next daily update" in bare
        assert "haberler" not in (empty / "sitemap.xml").read_text(encoding="utf-8")
        for landing_page in ("index.html", "ar/index.html"):
            bare_landing = (empty / landing_page).read_text(encoding="utf-8")
            assert 'id="news-band"' not in bare_landing and 'id="books-band"' in bare_landing, "no edition: the News band leaves, the Books band stays"

        # a bad edition fails the build instead of publishing half a lesson
        (news / "2026-11-07.json").write_text('{"date": "2026-11-08", "articles": []}', encoding="utf-8")
        bn.NEWS_DIR = news
        try:
            bd.build(tmp_path / "bad", None)
            raise AssertionError("a mismatched date must fail")
        except ValueError:
            pass
        finally:
            bn.NEWS_DIR = original_dir
    # the generation path with both APIs faked: Tavily's results feed the model, the Responses API's
    # output_text is parsed, every topic that survives lands in one file, a second run bills nothing
    calls = []
    def fake_post(url, body, headers, timeout=120):
        calls.append(url)
        if "tavily" in url:
            assert body["api_key"] == "t-key" and body["topic"] == "news"
            return {"results": [dict(url=s["u"], title=s["t"], content=s["c"]) for s in SNIPPETS] + [{"url": "javascript:alert(1)", "title": "x", "content": "y"}]}
        assert headers["Authorization"] == "Bearer o-key" and body["text"]["format"]["strict"] is True
        return {"output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": json.dumps(ORIGINAL)}]}]}
    original_post, original_env = bn.post_json, dict(os.environ)
    bn.post_json = fake_post
    os.environ.update(OPENAI_API_KEY="o-key", TAVILY_API_KEY="t-key")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            written = bn.generate("2026-11-06", Path(tmp))
            edition = json.loads(written.read_text(encoding="utf-8"))
            assert edition["date"] == "2026-11-06" and [a["topic"] for a in edition["articles"]] == [t for t, _ in bn.TOPICS]
            assert len(calls) == 2 * len(bn.TOPICS), "one search and one generation per topic"
            assert all(src["u"].startswith("https://") for a in edition["articles"] for src in a["sources"]), "only http(s) sources are published"
            bn.generate("2026-11-06", Path(tmp))
            assert len(calls) == 2 * len(bn.TOPICS), "an existing edition is never regenerated"
    finally:
        bn.post_json = original_post
        os.environ.clear()
        os.environ.update(original_env)
    print("build_news: OK")


if __name__ == "__main__":
    run()
