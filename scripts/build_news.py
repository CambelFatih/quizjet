"""News lessons: /news/ (the editions) and /news/<date>/ (the reader), in all 18 languages.

    python scripts/build_news.py --generate            # writes _data/news/<today>.json (needs OPENAI_API_KEY)
    python scripts/build_news.py --generate --date 2026-11-06

build_discover.build() publishes whatever editions sit in _data/news/. One edition is one "book" for
assets/reader.js: every article is a chapter, published once, language-neutral, as
/news/<date>/<n>.json with its key words, its four questions (answered on the page) and the
reports it was written from.

Copyright posture — the code below enforces it, keep the two in step:
- Facts and the news of the day are not protected (Berne Convention art. 2(8)); the wording of a
  report is. So the text is written in a clean room, in two calls: research() lets OpenAI's hosted
  web_search read the reports and return FACTS as fragments of at most MAX_FACT_WORDS words plus the
  report links (a longer "fact" is dropped as a lifted sentence); write_lesson() then gets those
  fragments and nothing else, no tools, so the wording of a report never reaches what is published.
- Every lesson links the reports it was written from (a link is outside the EU press publishers'
  right, DSM Directive art. 15(1)); no photos, no quotations.
- Generation runs in the deploy workflow once a day, only when OPENAI_API_KEY is set and today's file
  does not exist yet; without the key the site builds without news. A topic with fewer than 5 facts
  or 2 reports is skipped, never padded.
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
RESEARCH_MODEL = os.environ.get("NEWS_RESEARCH_MODEL", "gpt-5.6-luna")  # the bot's grounding tier: searches, lists facts
MODEL = os.environ.get("NEWS_MODEL", "gpt-5.6-sol")  # the bot's quality tier writes the text; ~4 a day cost cents
MAX_FACT_WORDS = 12  # a "fact" longer than this is a sentence lifted from a page, not a fact

FRESH_DAYS = 2
# The model has no idea what day it is: the date comes from the runner's clock and goes into the prompt,
# and every report's publication date comes back so research() can drop the stale ones itself.
RESEARCH = f"""You are a research assistant for an English-learning site. Today is {{today}} (UTC). Search the web
for the main news on the given topic published on {{earliest}} or later; check each report's publication
date and ignore older reports and undated evergreen pages. Pick the ONE story that at least two
independent reports from that period cover and that will still make sense tomorrow. Report it as FACTS
ONLY: 8 to 14 short fragments of at most {MAX_FACT_WORDS} words each (who, what, where, when, numbers,
what happens next), each in your own words, never a sentence or phrase copied from a page. List every
report you used, with its title, URL and publication date as YYYY-MM-DD (empty if the page shows none)."""

FACTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["story", "facts", "sources"],
    "properties": {
        "story": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "url", "published"],
                                               "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "published": {"type": "string"}}}},
    },
}

WRITER = f"""You write short news texts for adult English learners (CEFR {LEVEL}). You get a list of facts about one
news story and nothing else. Write an ORIGINAL text from these facts only: no claim the facts do not
support, no quotations, neutral tone, names of people, places and organisations as given. 180 to 220
words in 4 or 5 short paragraphs of plain sentences ({LEVEL} vocabulary and grammar, no headline
fragments). The title is a plain sentence, not a headline pun.
Then: 6 key words or phrases from your text with simple English definitions, and 4 multiple-choice
comprehension questions, each with 4 options, the correct option repeated in "answer" exactly, and a
one-sentence explanation."""

LESSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "paragraphs", "words", "quiz"],
    "properties": {
        "title": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "words": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["w", "d"],
                                             "properties": {"w": {"type": "string"}, "d": {"type": "string"}}}},
        "quiz": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["q", "o", "answer", "why"],
                                            "properties": {"q": {"type": "string"}, "o": {"type": "array", "items": {"type": "string"}},
                                                           "answer": {"type": "string"}, "why": {"type": "string"}}}},
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


def responses(body: dict[str, object], key: str, timeout: int = 120) -> dict[str, object]:
    """One Responses API call with a strict JSON schema; returns the parsed JSON of the message text."""
    resp = post_json("https://api.openai.com/v1/responses", {"store": False, **body}, {"Authorization": f"Bearer {key}"}, timeout)
    text = "".join(part.get("text", "") for item in resp.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    return json.loads(text)


def research(query: str, key: str, date: str) -> dict[str, object]:
    """Call 1: the hosted web_search reads the reports and hands back facts and links, never their wording.

    Shape as the bot's OpenAiWebSearchProvider sends it (tool + strict schema, tool_choice required, low
    effort); a hosted search is a model turn, so the budget is minutes, not the seconds of a search API."""
    earliest = (dt.date.fromisoformat(date) - dt.timedelta(days=FRESH_DAYS)).isoformat()
    out = responses({
        "model": RESEARCH_MODEL, "instructions": RESEARCH.replace("{today}", date).replace("{earliest}", earliest),
        "input": f"Topic: {query}",
        "tools": [{"type": "web_search", "search_context_size": "low"}], "tool_choice": "required",
        "reasoning": {"effort": "low"}, "max_output_tokens": 4000,
        "text": {"format": {"type": "json_schema", "name": "news_facts", "strict": True, "schema": FACTS_SCHEMA}},
    }, key, timeout=180)
    facts = [clean(str(f)) for f in out["facts"] if 1 <= len(str(f).split()) <= MAX_FACT_WORDS]
    sources, seen = [], set()
    for src in out["sources"]:
        url, published = str(src.get("url") or ""), str(src.get("published") or "").strip()
        if DATE_RE.match(published) and published < earliest:
            continue  # the model was told, but a stale report is dropped here too, not trusted to the prompt
        if url.startswith(("http://", "https://")) and url not in seen:  # the url is published as a link
            seen.add(url)
            sources.append({"t": clean(str(src.get("title") or "")), "u": url})
    return {"story": clean(str(out["story"])), "facts": facts, "sources": sources}


def write_lesson(facts: list[str], key: str) -> dict[str, object]:
    """Call 2: the writer gets the facts and nothing else: no tools, no page text, so it cannot copy any."""
    return responses({
        "model": MODEL, "instructions": WRITER, "input": "Facts:\n" + "\n".join(f"- {f}" for f in facts),
        # Low effort: a 200-word text needs no long deliberation, and reasoning tokens are billed as output.
        # The cap must leave room for them too: a text cut off mid-JSON is a skipped topic, not a cheaper one.
        "reasoning": {"effort": "low"}, "max_output_tokens": 8000,
        "text": {"format": {"type": "json_schema", "name": "news_lesson", "strict": True, "schema": LESSON_SCHEMA}},
    }, key)


def article(lesson: dict[str, object], found: dict[str, object], topic: str) -> dict[str, object]:
    """Checked, reader-shaped article; raises ValueError for anything thin."""
    paragraphs = [clean(str(p)) for p in lesson["paragraphs"] if str(p).strip()]
    if len(paragraphs) < 3 or len(found["sources"]) < 2:
        raise ValueError("thin lesson")
    quiz = []
    for q in lesson["quiz"]:
        options = [clean(str(o)) for o in q["o"]]
        if len(options) == 4 and clean(str(q["answer"])) in options:
            quiz.append({"q": clean(str(q["q"])), "o": options, "a": options.index(clean(str(q["answer"]))), "why": clean(str(q["why"]))})
    words = [{"w": clean(str(w["w"])), "d": clean(str(w["d"]))} for w in lesson["words"] if str(w["w"]).strip()]
    if len(quiz) < 3 or len(words) < 3:
        raise ValueError("thin quiz")
    return {"topic": topic, "title": clean(str(lesson["title"])), "level": LEVEL,
            "p": [split_sentences(p) for p in paragraphs], "words": words[:8], "quiz": quiz[:4],
            "sources": found["sources"]}


def generate(date: str, news_dir: Path | None = None) -> Path:
    news_dir = news_dir or NEWS_DIR  # resolved at call time so a test can point it elsewhere
    path = news_dir / f"{date}.json"
    if path.exists():
        print(f"{path.name} exists; nothing generated.")
        return path
    key = os.environ["OPENAI_API_KEY"]
    articles = []
    for topic, query in TOPICS:
        try:
            found = research(query, key, date)
            if len(found["facts"]) < 5 or len(found["sources"]) < 2:
                raise ValueError(f"only {len(found['facts'])} facts from {len(found['sources'])} reports")
            articles.append(article(write_lesson(found["facts"], key), found, topic))
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
        f"news/{edition['date']}/{n}.json": json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n"
        for n, a in enumerate(edition["articles"], start=1)
    }


def list_page(locale: dict[str, object], pre: str, editions: list[dict[str, object]]) -> tuple[str, str, str, str]:
    """(title, description, body, extra head) of /news/."""
    c = locale["copy"]
    rows = "".join(
        f'        <li><a href="{pre}/news/{ed["date"]}/">'
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
        "slug": f"news-{date}", "base": f"/news/{date}/", "title": title, "titleLang": locale["code"],
        "author": "QuizJet", "level": LEVEL, "quiz": "", "chapterQuizzes": {},
        "chapters": [{"title": f"{topic_label(locale, str(a['topic']))}: {a['title']}", "p": a["p"]} for a in edition["articles"]],
        "back": (f"{pre}/news/", c["nwBack"]),
        "sourceHtml": e(c["nwSource"]),
        "quizCopy": {"title": c["nwQuizTitle"], "body": c["nwQuizBody"], "cta": c["nwQuizCta"], "next": c["nwNext"]},
        "pageTitle": f'{title} — {c["nwTitle"]}',
        "description": c["nwEditionMeta"].replace("{date}", date),
        "jsonld": {"@context": "https://schema.org", "@type": "LearningResource", "name": title, "inLanguage": "en",
                   "educationalLevel": LEVEL, "isAccessibleForFree": True, "datePublished": date,
                   "author": {"@type": "Organization", "name": "QuizJet"}, "url": f"{BASE_URL}{pre}/news/{date}/"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate today's news lesson.")
    parser.add_argument("--generate", action="store_true", help="write _data/news/<date>.json (needs OPENAI_API_KEY)")
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
