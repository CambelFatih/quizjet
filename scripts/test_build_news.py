"""Self-check for build_news.py — run: python scripts/test_build_news.py (no network, no keys)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import build_discover as bd
import build_news as bn

# What the research call (hosted web_search) hands back. The page sentence is what must never reach the writer.
PAGE_SENTENCE = "The central bank kept its main interest rate unchanged on Wednesday, saying inflation was slowing but still above target."
FOUND = {
    "story": "Central bank holds rates",
    "facts": ["central bank left its main rate unchanged", "decision came on Wednesday", "inflation slowing but above target",
              "shares rose after the decision", "investors expect a cut early next year", "next months will show if prices calm",
              PAGE_SENTENCE],  # a 20-word "fact" is a lifted sentence: dropped
    "sources": [{"title": "Central bank holds rates", "url": "https://example.com/a", "published": "2026-11-05"},
                {"title": "Markets react", "url": "https://example.com/b", "published": ""},
                {"title": "dup", "url": "https://example.com/a", "published": "2026-11-06"},
                {"title": "x", "url": "javascript:alert(1)", "published": "2026-11-06"},
                {"title": "last year's rate decision", "url": "https://example.com/old", "published": "2025-11-05"}],
}
# What the writer call hands back.
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
}


def fake_api(calls: list[dict[str, object]]):
    """Both OpenAI calls, told apart by the web_search tool; records every request body."""
    def post(url, body, headers, timeout=120):
        assert url.endswith("/v1/responses") and headers["Authorization"] == "Bearer o-key" and body["store"] is False
        assert body["text"]["format"]["strict"] is True
        calls.append(body)
        answer = FOUND if "tools" in body else ORIGINAL
        if "tools" in body:
            assert body["tool_choice"] == "required" and body["tools"][0]["type"] == "web_search"
        return {"output": [{"type": "web_search_call"}, {"type": "reasoning"},
                           {"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
    return post


def run() -> None:
    original_post, original_env, original_dir, original_min = bn.post_json, dict(os.environ), bn.NEWS_DIR, bn.MIN_WORDS
    calls: list[dict[str, object]] = []
    bn.post_json = fake_api(calls)
    os.environ["OPENAI_API_KEY"] = "o-key"
    bn.MIN_WORDS = 0  # the fixture lesson is deliberately short; the length rule has its own case below
    try:
        # the clean room: research keeps short facts and real links; the writer sees facts only
        found = bn.research("business and finance", "o-key", "2026-11-06")
        assert PAGE_SENTENCE not in found["facts"] and len(found["facts"]) == 6, "a fact longer than MAX_FACT_WORDS is a lifted sentence"
        assert [s["u"] for s in found["sources"]] == ["https://example.com/a", "https://example.com/b"], "deduplicated, http(s) only, nothing older than two days"
        assert "Today is 2026-11-06 (UTC)" in calls[-1]["instructions"] and "published on 2026-11-04 or later" in calls[-1]["instructions"], "the runner's date, not the model's guess"
        lesson = bn.write_lesson(found["facts"], "o-key")
        writer = calls[-1]
        assert "tools" not in writer and writer["model"] == bn.MODEL, "the writer searches nothing"
        assert "saying inflation was slowing" not in writer["input"] and PAGE_SENTENCE not in writer["input"], "no page text reaches the writer"
        assert writer["input"].startswith("Facts:\n- central bank left its main rate unchanged\n")

        art = bn.article(lesson, found, "business")
        assert art["topic"] == "business" and art["level"] == "B1"
        assert art["p"][0] == ["A big bank that controls money in the country decided not to change interest rates this week.",
                               "Prices are rising more slowly than before, but they are still rising too fast."], art["p"][0]
        assert [q["a"] for q in art["quiz"]] == [1, 1, 2], "the answer index comes from the answer text"
        assert [s["u"] for s in art["sources"]] == ["https://example.com/a", "https://example.com/b"]
        try:
            bn.article(lesson, dict(found, sources=found["sources"][:1]), "business")
            raise AssertionError("one report is not enough")
        except ValueError:
            pass
        bn.MIN_WORDS = 150
        try:
            bn.article(lesson, found, "business")
            raise AssertionError("a text far below the length the writer was asked for is thin and is skipped")
        except ValueError as exc:
            assert "words" in str(exc)
        finally:
            bn.MIN_WORDS = 0

        # the whole generation: one research and one writer call per topic, one file, a second run bills nothing
        calls.clear()
        with tempfile.TemporaryDirectory() as tmp:
            written = bn.generate("2026-11-06", Path(tmp))
            edition = json.loads(written.read_text(encoding="utf-8"))
            assert edition["date"] == "2026-11-06" and [a["topic"] for a in edition["articles"]] == [t for t, _ in bn.TOPICS]
            assert len(calls) == 2 * len(bn.TOPICS) and sum("tools" in c for c in calls) == len(bn.TOPICS)
            bn.generate("2026-11-06", Path(tmp))
            assert len(calls) == 2 * len(bn.TOPICS), "an existing edition is never regenerated"
    finally:
        bn.post_json = original_post
        bn.MIN_WORDS = original_min
        os.environ.clear()
        os.environ.update(original_env)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        news = tmp_path / "news"
        news.mkdir()
        (news / "2026-11-06.json").write_text(json.dumps({"date": "2026-11-06", "model": "test", "articles": [art, dict(art, topic="sports", title="A striker <b>moves</b>")]}), encoding="utf-8")
        (news / "2026-10-30.json").write_text(json.dumps({"date": "2026-10-30", "model": "test", "articles": [art]}), encoding="utf-8")
        bn.NEWS_DIR = news
        try:
            out = tmp_path / "site"
            bd.build(out, None)
        finally:
            bn.NEWS_DIR = original_dir

        assert sum(1 for _ in out.rglob("news/2026-11-06/index.html")) == 18, "one reader per language"
        assert (out / "news" / "2026-11-06" / "1.json").is_file() and not (out / "tr" / "news" / "2026-11-06" / "1.json").exists()
        chapter = json.loads((out / "news" / "2026-11-06" / "2.json").read_text(encoding="utf-8"))
        assert chapter["quiz"][0]["a"] == 1 and chapter["sources"][0]["u"] == "https://example.com/a" and chapter["words"][0]["w"] == "interest rate"

        tr_reader = (out / "tr" / "news" / "2026-11-06" / "index.html").read_text(encoding="utf-8")
        assert '<html lang="tr" dir="ltr">' in tr_reader and 'href="/tr/news/"' in tr_reader and 'href="/tr/books/"' not in tr_reader.split("<main")[1].split("rd-tools")[0]
        assert '"base":"/news/2026-11-06/"' in tr_reader and '"slug":"news-2026-11-06"' in tr_reader
        assert "<h1 lang=\"tr\" dir=\"ltr\">Haber dersi · 2026-11-06</h1>" in tr_reader
        assert 'id="rd-next-ch" href="#">Sonraki metin ' in tr_reader, "the news framing replaces the book framing on the button"
        assert '"nwCorrect":"Doğru!"' in tr_reader and '"@type":"LearningResource"' in tr_reader and "gutenberg.org" not in tr_reader
        assert "Spor: A striker &lt;b&gt;moves&lt;/b&gt;" in tr_reader, "chapter names are escaped and carry the topic"
        assert "A big bank that controls money in the country decided not to change interest rates this week." in tr_reader, \
            "the texts are in the HTML for crawlers and readers without JavaScript"
        assert 'href="https://example.com/a"' in tr_reader and 'type="application/atom+xml"' in tr_reader
        feed = (out / "news" / "feed.xml").read_text(encoding="utf-8")
        assert feed.count("<entry>") == 2 and "https://quizjet.app/news/2026-11-06/" in feed and "Interest rates stay the same for now" in feed
        assert "https://example.com/a" in feed and "<b>" not in feed, "the feed carries the text and its reports, escaped"
        ar_reader = (out / "ar" / "news" / "2026-11-06" / "index.html").read_text(encoding="utf-8")
        assert '<h1 lang="ar" dir="rtl">' in ar_reader

        shelf = (out / "news" / "index.html").read_text(encoding="utf-8")
        assert 'href="/news/2026-11-06/"' in shelf and "Business, Sports" in shelf and "Interest rates stay the same for now · A striker &lt;b&gt;moves&lt;/b&gt;" in shelf
        assert '<meta name="robots" content="noindex">' not in shelf and "support@quizjet.app" in shelf
        assert 'class="nw-month"' in shelf and ">2026-10<" in shelf and 'type="application/atom+xml"' in shelf, "two months: grouped by month, feed linked"
        sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
        assert "https://quizjet.app/tr/news/2026-11-06/" in sitemap and "https://quizjet.app/news/" in sitemap
        landing = (out / "tr" / "index.html").read_text(encoding="utf-8")
        assert 'href="/tr/news/"' in landing and 'href="/news/"' not in landing, "the landing menu links the lessons in its own language"
        assert 'id="news-band"' in landing and "Bugünün haberlerini oku" in landing, "the landing band is reachable on phones, where the menu links are hidden"
        book = (out / "tr" / "books" / "sherlock-holmes" / "index.html").read_text(encoding="utf-8")
        assert "gutenberg.org" in book and 'id="rd-next-ch" href="#">Sonraki bölüm ' in book and '"base":"/books/sherlock-holmes/"' in book, "books keep their framing"

        # no edition yet: the page exists for the menu link, unindexed and out of the sitemap; the landing band leaves
        empty = tmp_path / "empty"
        bn.NEWS_DIR = tmp_path / "no-news"  # the repo itself carries real editions once the workflow has run
        try:
            bd.build(empty, None)
        finally:
            bn.NEWS_DIR = original_dir
        bare = (empty / "news" / "index.html").read_text(encoding="utf-8")
        assert '<meta name="robots" content="noindex">' in bare and "next daily update" in bare
        assert "news" not in (empty / "sitemap.xml").read_text(encoding="utf-8")
        assert not (empty / "news" / "feed.xml").exists() and 'type="application/atom+xml"' not in bare
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
    print("build_news: OK")


if __name__ == "__main__":
    run()
