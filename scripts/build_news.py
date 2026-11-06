"""News lessons: /haberler/ (the editions) and /haberler/<date>/ (the reader), in all 18 languages.

    python scripts/build_news.py --generate            # writes _data/news/<today>.json (needs the two keys)
    python scripts/build_news.py --generate --date 2026-11-06

build_discover.build() publishes whatever editions sit in _data/news/. One edition is one "book" for
assets/reader.js: every article is a chapter, published once, language-neutral, as
/haberler/<date>/<n>.json with its key words, its four questions (answered on the page) and the
reports it was written from.

Copyright posture — the code below enforces it, keep the two in step:
- Facts and the news of the day are not protected (Berne Convention art. 2(8)); the wording of a
  report is. So the model never rewrites one article: it gets short snippets from several reports
  (Tavily, the same search the bot grounds with) and writes an ORIGINAL learner text in its own words.
- Nothing is published that shares a run of eight words with any snippet (overlap() below). A text
  that fails is dropped, not retried: a retry bills a second generation for a story that read too close.
- Every lesson links the reports it was written from (a link is outside the EU press publishers'
  right, DSM Directive art. 15(1)); no photos, no quotes longer than a few words.
- Generation runs in the deploy workflow once a day, only when OPENAI_API_KEY and TAVILY_API_KEY are
  set and today's file does not exist yet; without the keys the site builds without news.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import build_books
from import_gutenberg import clean, split_sentences

ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "_data" / "news"
BASE_URL = "https://quizjet.app"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVEL = "B1"
TOPICS = [("politics", "world politics"), ("business", "business and finance"), ("sports", "sports"),
          ("science", "science and technology")]
MODEL = os.environ.get("NEWS_MODEL", "gpt-5.6-sol")  # the bot's quality tier; ~4 short texts a day cost cents
SHINGLE = 8

SYSTEM = f"""You write short news texts for adult English learners (CEFR {LEVEL}).
You get numbered snippets from several news reports on one topic. Pick the ONE story that at least two
snippets support and that will still make sense tomorrow. Write an ORIGINAL text in your own words:
never copy or lightly rephrase a sentence from the snippets, no quotation longer than six words, no
claim the snippets do not support, neutral tone, names of people, places and organisations as in the
snippets. 180 to 220 words in 4 or 5 short paragraphs of plain sentences ({LEVEL} vocabulary and
grammar, no headline fragments). The title is a plain sentence, not a headline pun.
Then: 6 key words or phrases from your text with simple English definitions; 4 multiple-choice
comprehension questions, each with 4 options, the correct option repeated in "answer" exactly, and a
one-sentence explanation; and the snippet numbers you used."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "paragraphs", "words", "quiz", "sources"],
    "properties": {
        "title": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "words": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["w", "d"],
                                             "properties": {"w": {"type": "string"}, "d": {"type": "string"}}}},
        "quiz": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["q", "o", "answer", "why"],
                                            "properties": {"q": {"type": "string"}, "o": {"type": "array", "items": {"type": "string"}},
                                                           "answer": {"type": "string"}, "why": {"type": "string"}}}},
        "sources": {"type": "array", "items": {"type": "integer"}},
    },
}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


# ---------- generation ----------

def post_json(url: str, body: dict[str, object], headers: dict[str, str], timeout: int = 120) -> dict[str, object]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def search(query: str, key: str) -> list[dict[str, str]]:
    """Fresh reports on ``query``: title, url and Tavily's snippet (input for the model, never published)."""
    body = {"api_key": key, "query": f"{query} news", "topic": "news", "days": 2, "search_depth": "basic",
            "max_results": 8, "include_answer": False}
    found = post_json("https://api.tavily.com/search", body, {}, timeout=30).get("results") or []
    return [{"t": clean(str(r.get("title") or "")), "u": str(r["url"]), "c": clean(str(r.get("content") or ""))[:1200]}
            for r in found if str(r.get("url") or "").startswith(("http://", "https://"))]  # the url is published as a link


def write_lesson(query: str, snippets: list[dict[str, str]], key: str) -> dict[str, object]:
    numbered = "\n\n".join(f"[{i}] {s['t']}\n{s['c']}" for i, s in enumerate(snippets, start=1))
    body = {
        "model": MODEL,
        "input": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Topic: {query}\n\n{numbered}"}],
        "text": {"format": {"type": "json_schema", "name": "news_lesson", "strict": True, "schema": SCHEMA}},
        # Low effort: a 200-word text needs no long deliberation, and reasoning tokens are billed as output.
        # The cap must leave room for them too: a text cut off mid-JSON is a skipped topic, not a cheaper one.
        "reasoning": {"effort": "low"},
        "max_output_tokens": 8000,
    }
    resp = post_json("https://api.openai.com/v1/responses", body, {"Authorization": f"Bearer {key}"})
    text = "".join(part.get("text", "") for item in resp.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    return json.loads(text)


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def overlap(text: str, sources: list[str], n: int = SHINGLE) -> str:
    """The first run of ``n`` words that ``text`` shares with any source, or "" when it shares none."""
    mine = shingles(text, n)
    for source in sources:
        shared = mine & shingles(source, n)
        if shared:
            return min(shared)
    return ""


def article(lesson: dict[str, object], snippets: list[dict[str, str]], topic: str) -> dict[str, object]:
    """Checked, reader-shaped article; raises ValueError for anything thin or too close to a source."""
    paragraphs = [clean(str(p)) for p in lesson["paragraphs"] if str(p).strip()]
    used = sorted({int(i) for i in lesson["sources"] if 1 <= int(i) <= len(snippets)})
    if len(paragraphs) < 3 or len(used) < 2:
        raise ValueError("thin lesson")
    title = clean(str(lesson["title"]))
    shared = overlap(f"{title} {' '.join(paragraphs)}", [f"{s['t']} {s['c']}" for s in snippets])
    if shared:
        raise ValueError(f"copies a source: {shared!r}")
    quiz = []
    for q in lesson["quiz"]:
        options = [clean(str(o)) for o in q["o"]]
        if len(options) == 4 and clean(str(q["answer"])) in options:
            quiz.append({"q": clean(str(q["q"])), "o": options, "a": options.index(clean(str(q["answer"]))), "why": clean(str(q["why"]))})
    words = [{"w": clean(str(w["w"])), "d": clean(str(w["d"]))} for w in lesson["words"] if str(w["w"]).strip()]
    if len(quiz) < 3 or len(words) < 3:
        raise ValueError("thin quiz")
    return {"topic": topic, "title": title, "level": LEVEL,
            "p": [split_sentences(p) for p in paragraphs], "words": words[:8], "quiz": quiz[:4],
            "sources": [{"t": snippets[i - 1]["t"], "u": snippets[i - 1]["u"]} for i in used]}


def generate(date: str, news_dir: Path | None = None) -> Path:
    news_dir = news_dir or NEWS_DIR  # resolved at call time so a test can point it elsewhere
    path = news_dir / f"{date}.json"
    if path.exists():
        print(f"{path.name} exists; nothing generated.")
        return path
    openai_key, tavily_key = os.environ["OPENAI_API_KEY"], os.environ["TAVILY_API_KEY"]
    articles = []
    for topic, query in TOPICS:
        try:
            snippets = search(query, tavily_key)
            if len(snippets) < 3:
                raise ValueError(f"only {len(snippets)} reports")
            articles.append(article(write_lesson(query, snippets, openai_key), snippets, topic))
        except Exception as exc:  # noqa: BLE001 — one topic failing never loses the others
            print(f"{topic}: skipped ({exc})", file=sys.stderr)
    if not articles:
        raise SystemExit("no article survived; nothing written")
    news_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": date, "model": MODEL, "articles": articles}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"Wrote {path.name} with {len(articles)} article(s).")
    return path


# ---------- pages ----------

def load_editions(news_dir: Path | None = None) -> list[dict[str, object]]:
    """Every edition, newest first; a malformed file fails the build rather than publishing half a lesson."""
    editions = []
    for path in sorted((news_dir or NEWS_DIR).glob("*.json"), reverse=True):
        edition = json.loads(path.read_text(encoding="utf-8"))
        date = str(edition.get("date") or "")
        if not DATE_RE.match(date) or path.stem != date:
            raise ValueError(f"{path.name}: the file name and the \"date\" field must both be YYYY-MM-DD")
        if not edition.get("articles") or any(not a.get("p") or not a.get("title") for a in edition["articles"]):
            raise ValueError(f"{path.name}: an article has no title or no text")
        editions.append(edition)
    return editions


def topic_label(locale: dict[str, object], topic: str) -> str:
    return str(locale["copy"].get("nwTopic" + topic.capitalize()) or topic)


def chapter_files(edition: dict[str, object]) -> dict[str, str]:
    return {
        f"haberler/{edition['date']}/{n}.json": json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n"
        for n, a in enumerate(edition["articles"], start=1)
    }


def list_page(locale: dict[str, object], pre: str, editions: list[dict[str, object]]) -> tuple[str, str, str, str]:
    """(title, description, body, extra head) of /haberler/."""
    c = locale["copy"]
    rows = "".join(
        f'        <li><a href="{pre}/haberler/{ed["date"]}/">'
        f'{build_books.cover({"title": ed["date"], "author": "QuizJet", "cover": ["#2a4d3a", "#9ad3a8"]})}<span class="book-info">'
        f'<strong>{e(c["nwEdition"].replace("{date}", str(ed["date"])))}</strong>'
        f'<span lang="en" dir="ltr">{e(" · ".join(str(a["title"]) for a in ed["articles"]))}</span>'
        f'<small>{e(c["bkLevel"])}: {e(LEVEL)} · {e(", ".join(topic_label(locale, str(a["topic"])) for a in ed["articles"]))}</small>'
        f'<span class="book-read">{e(c["bkRead"])} <span aria-hidden="true">→</span></span></span></a></li>\n'
        for ed in editions
    )
    body = (
        f'      <h1>{e(c["nwTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["nwLead"])}</p>\n'
        + (f'      <ul class="book-list">\n{rows}      </ul>\n' if rows else f'      <p class="discover-empty">{e(c["nwEmpty"])}</p>\n')
        + f'      <p class="quiz-note">{e(c["nwNote"])}</p>\n'
    )
    return f'{c["nwTitle"]} — QuizJet', c["nwMeta"], body, build_books.reader_css()


def edition_book(locale: dict[str, object], pre: str, edition: dict[str, object]) -> dict[str, object]:
    """An edition as the book dict build_books.reader_page renders (its optional keys carry the news framing)."""
    c, date = locale["copy"], str(edition["date"])
    title = c["nwEdition"].replace("{date}", date)
    return {
        "slug": f"news-{date}", "base": f"/haberler/{date}/", "title": title, "titleLang": locale["code"],
        "author": "QuizJet", "level": LEVEL, "quiz": "", "chapterQuizzes": {},
        "chapters": [{"title": f"{topic_label(locale, str(a['topic']))}: {a['title']}", "p": a["p"]} for a in edition["articles"]],
        "back": (f"{pre}/haberler/", c["nwBack"]),
        "sourceHtml": e(c["nwSource"]),
        "quizCopy": {"title": c["nwQuizTitle"], "body": c["nwQuizBody"], "cta": c["nwQuizCta"], "next": c["nwNext"]},
        "pageTitle": f'{title} — {c["nwTitle"]}',
        "description": c["nwEditionMeta"].replace("{date}", date),
        "jsonld": {"@context": "https://schema.org", "@type": "LearningResource", "name": title, "inLanguage": "en",
                   "educationalLevel": LEVEL, "isAccessibleForFree": True, "datePublished": date,
                   "author": {"@type": "Organization", "name": "QuizJet"}, "url": f"{BASE_URL}{pre}/haberler/{date}/"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate today's news lesson.")
    parser.add_argument("--generate", action="store_true", help="write _data/news/<date>.json (needs OPENAI_API_KEY and TAVILY_API_KEY)")
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    args = parser.parse_args()
    if not DATE_RE.match(args.date):
        parser.error("--date must be YYYY-MM-DD")
    if args.generate:
        generate(args.date)
    else:
        print(f"{len(load_editions())} edition(s) in {NEWS_DIR.relative_to(ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
