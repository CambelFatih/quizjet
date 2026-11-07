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

Selection: every desk in TOPICS carries an editorial brief (what leads it, in order, and what never does;
sport is football first). A story taken by an earlier desk today or by an edition of the last COVERED_DAYS
is named to the model as covered, and a report link already used is dropped in research(), so the same
story cannot come back under another desk with the same reports.
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
import xml.etree.ElementTree as ET
from pathlib import Path

import build_books
from import_gutenberg import clean, split_sentences

ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "_data" / "news"
BASE_URL = "https://quizjet.app"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVEL = "B1"
# One editorial brief per desk: what an international editor leads it with, in order, and what never makes it.
# The readers are adults learning English all over the world, so "important" means important in many countries.
TOPICS = [
    ("politics", "World politics and international affairs. Prefer: elections and changes of government in major "
                 "countries, wars, ceasefires and peace talks, summits and decisions of the UN, EU, NATO, G7 or G20, "
                 "sanctions and major diplomatic agreements. Avoid: local or state politics, polls, campaign gossip."),
    ("business", "Global economy, markets and finance. Prefer: central-bank decisions (Federal Reserve, ECB, Bank of "
                 "England, Bank of Japan), inflation, jobs and growth figures of major economies, big moves in stock "
                 "markets, oil, gold or currencies, and deals or results of the world's largest companies. "
                 "Avoid: investment tips, small companies."),
    ("sports", "Sport, the world's most followed competitions, football first. Prefer, in this order: (1) European club "
               "football: UEFA Champions League, Premier League, La Liga, Serie A, Bundesliga, Ligue 1, and the biggest "
               "clubs (Real Madrid, Barcelona, Manchester City, Liverpool, Arsenal, Manchester United, Bayern Munich, "
               "Paris Saint-Germain, Inter, Juventus), then international football (World Cup, European Championship, "
               "qualifiers, Ballon d'Or); (2) when football has no strong story or a bigger global event dominates: "
               "Formula 1, tennis Grand Slams and ATP/WTA Finals, the NBA, the Olympic Games, world athletics, "
               "heavyweight boxing. A result, record, title race, confirmed transfer or coach change beats a preview. "
               "Avoid: golf, American football, baseball, ice hockey, college sport, horse racing, betting, injury lists, "
               "transfer rumours."),
    ("science", "Science, health and technology. Prefer: discoveries, space missions, medicine and health, climate and "
                "environment, and new technology such as AI, chips or phones: the research or the product, never its "
                "share price. Avoid: stock-market and company-results stories (they belong to business), press releases."),
]
COVERED_DAYS = 3  # stories (and their reports) of the editions this many days back are not picked again
RESEARCH_MODEL = os.environ.get("NEWS_RESEARCH_MODEL", "gpt-5.6-luna")  # the bot's grounding tier: searches, lists facts
MODEL = os.environ.get("NEWS_MODEL", "gpt-5.6-sol")  # the bot's quality tier writes the text; ~4 a day cost cents
MAX_FACT_WORDS = 12  # a "fact" longer than this is a sentence lifted from a page, not a fact
MIN_WORDS = 150  # the writer is asked for 180–230; a text this far below is thin and is skipped, like a thin quiz
FEED_PATH = "news/feed.xml"
FEED_LINK = f'    <link rel="alternate" type="application/atom+xml" href="/{FEED_PATH}" title="QuizJet news lessons">\n'

FRESH_DAYS = 2
# The model has no idea what day it is: the date comes from the runner's clock and goes into the prompt,
# and every report's publication date comes back so research() can drop the stale ones itself.
RESEARCH = f"""You are the desk editor of an English-learning site read by adults all over the world. Today is {{today}} (UTC).
Search the web for news of the given desk published on {{earliest}} or later; check each report's publication
date and ignore older reports and undated evergreen pages. Pick the ONE story an international editor would
lead this desk with: first what the desk brief prefers, in its order; then the story that matters to the most
people in the most countries, not in one country only; then the one the most independent major outlets cover
(international agencies and broadcasters such as Reuters, AP, AFP and the BBC count most). It must be covered
by at least two independent reports from that period and still make sense tomorrow. Never pick a story listed
as already covered, an opinion piece, a rumour or a press release. Report it as FACTS ONLY: 8 to 14 short
fragments of at most {MAX_FACT_WORDS} words each (who, what, where, when, numbers, what happens next), each in
your own words, never a sentence or phrase copied from a page. Name the competition, institution or country the
story is about in 1 to 3 words as its tag (for example "Premier League", "Federal Reserve", "European Union").
List every report you used, with its title, URL and publication date as YYYY-MM-DD (empty if the page shows none)."""
MAX_TAG_WORDS = 4

FACTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["story", "tag", "facts", "sources"],
    "properties": {
        "story": {"type": "string"},
        "tag": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "url", "published"],
                                               "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "published": {"type": "string"}}}},
    },
}

WRITER = f"""You write short news texts for adult English learners (CEFR {LEVEL}). You get a list of facts about one
news story and nothing else. Write an ORIGINAL text from these facts only: no claim the facts do not
support, no quotations, neutral tone, names of people, places and organisations as given. at least 180 and at most 230
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


def research(query: str, key: str, date: str, covered: list[str] | None = None, seen: set[str] | None = None) -> dict[str, object]:
    """Call 1: the hosted web_search reads the reports and hands back facts and links, never their wording.

    Shape as the bot's OpenAiWebSearchProvider sends it (tool + strict schema, tool_choice required, low
    effort); a hosted search is a model turn, so the budget is minutes, not the seconds of a search API.
    ``covered`` (titles) is told to the model; ``seen`` (report links already used) is enforced here: on
    2026-11-07 the science desk returned the previous day's business story with the very same reports."""
    earliest = (dt.date.fromisoformat(date) - dt.timedelta(days=FRESH_DAYS)).isoformat()
    avoid = "".join(f"\n- {t}" for t in covered or [])
    out = responses({
        "model": RESEARCH_MODEL, "instructions": RESEARCH.replace("{today}", date).replace("{earliest}", earliest),
        "input": f"Desk brief: {query}" + (f"\nAlready covered, pick a different story:{avoid}" if avoid else ""),
        "tools": [{"type": "web_search", "search_context_size": "low"}], "tool_choice": "required",
        "reasoning": {"effort": "low"}, "max_output_tokens": 4000,
        "text": {"format": {"type": "json_schema", "name": "news_facts", "strict": True, "schema": FACTS_SCHEMA}},
    }, key, timeout=180)
    facts = [clean(str(f)) for f in out["facts"] if 1 <= len(str(f).split()) <= MAX_FACT_WORDS]
    sources, used = [], set()
    for src in out["sources"]:
        url, published = str(src.get("url") or ""), str(src.get("published") or "").strip()
        if DATE_RE.match(published) and published < earliest:
            continue  # the model was told, but a stale report is dropped here too, not trusted to the prompt
        if url.startswith(("http://", "https://")) and url not in used and url not in (seen or ()):  # published as a link
            used.add(url)
            sources.append({"t": clean(str(src.get("title") or "")), "u": url})
    tag = clean(str(out["tag"]))
    return {"story": clean(str(out["story"])), "tag": tag if len(tag.split()) <= MAX_TAG_WORDS else "", "facts": facts, "sources": sources}


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
    words = sum(len(p.split()) for p in paragraphs)
    if len(paragraphs) < 3 or len(found["sources"]) < 2 or words < MIN_WORDS:
        raise ValueError(f"thin lesson: {len(paragraphs)} paragraphs, {words} words, {len(found['sources'])} reports")
    quiz = []
    for q in lesson["quiz"]:
        options = [clean(str(o)) for o in q["o"]]
        if len(options) == 4 and clean(str(q["answer"])) in options:
            quiz.append({"q": clean(str(q["q"])), "o": options, "a": options.index(clean(str(q["answer"]))), "why": clean(str(q["why"]))})
    words = [{"w": clean(str(w["w"])), "d": clean(str(w["d"]))} for w in lesson["words"] if str(w["w"]).strip()]
    if len(quiz) < 3 or len(words) < 3:
        raise ValueError("thin quiz")
    return {"topic": topic, **({"tag": found["tag"]} if found.get("tag") else {}), "title": clean(str(lesson["title"])), "level": LEVEL,
            "p": [split_sentences(p) for p in paragraphs], "words": words[:8], "quiz": quiz[:4],
            "sources": found["sources"]}


def recent(news_dir: Path, date: str) -> tuple[list[str], set[str]]:
    """Titles and report links of the editions in the COVERED_DAYS before ``date``: not picked again."""
    earliest = (dt.date.fromisoformat(date) - dt.timedelta(days=COVERED_DAYS)).isoformat()
    covered, seen = [], set()
    for path in sorted(news_dir.glob("*.json")):
        if earliest <= path.stem < date:
            for a in json.loads(path.read_text(encoding="utf-8")).get("articles") or []:
                covered.append(str(a.get("title") or ""))
                seen.update(str(s.get("u") or "") for s in a.get("sources") or [])
    return [t for t in covered if t], seen - {""}


def generate(date: str, news_dir: Path | None = None) -> Path:
    news_dir = news_dir or NEWS_DIR  # resolved at call time so a test can point it elsewhere
    path = news_dir / f"{date}.json"
    if path.exists():
        print(f"{path.name} exists; nothing generated.")
        return path
    key = os.environ["OPENAI_API_KEY"]
    covered, seen = recent(news_dir, date)
    articles = []
    for topic, query in TOPICS:
        try:
            found = research(query, key, date, covered, seen)
            if len(found["facts"]) < 5 or len(found["sources"]) < 2:
                raise ValueError(f"only {len(found['facts'])} facts from {len(found['sources'])} new reports")
            articles.append(article(write_lesson(found["facts"], key), found, topic))
            covered.append(found["story"])  # the next desk never takes the story this one just took
            seen.update(s["u"] for s in found["sources"])
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


def card(locale: dict[str, object], pre: str, date: str, n: int, a: dict[str, object], lead: bool) -> str:
    """One article as a card that opens the reader on it (#n); the newest edition's cards show the first sentence."""
    topic = str(a["topic"])
    tag = f' · <span lang="en" dir="ltr">{e(a["tag"])}</span>' if a.get("tag") else ""
    first = f'<span class="nw-first" lang="en" dir="ltr">{e(a["p"][0][0])}</span>' if lead and a["p"] and a["p"][0] else ""
    return (f'          <li data-topic="{e(topic)}"><a class="nw-card nw-{e(topic)}" href="{pre}/news/{date}/#{n}">'
            f'<span class="nw-kicker">{e(topic_label(locale, topic))}{tag}</span>'
            f'<strong lang="en" dir="ltr">{e(a["title"])}</strong>{first}</a></li>\n')


def edition_block(locale: dict[str, object], pre: str, ed: dict[str, object], lead: bool) -> str:
    c, date = locale["copy"], str(ed["date"])
    cards = "".join(card(locale, pre, date, n, a, lead) for n, a in enumerate(ed["articles"], start=1))
    return (f'      <section class="nw-edition{" nw-lead" if lead else ""}">\n'
            f'        <h2><a href="{pre}/news/{date}/">{e(c["nwEdition"].replace("{date}", date))}</a></h2>\n'
            f'        <ul class="nw-grid">\n{cards}        </ul>\n      </section>\n')


# Topic filter: hidden until the script runs, so a page without JavaScript shows no dead buttons. An edition with
# nothing left under the chosen topic leaves too.
FILTER_SCRIPT = """    <script>
(function () {
  var buttons = [].slice.call(document.querySelectorAll("#nw-filter button"));
  buttons.forEach(function (b) {
    b.addEventListener("click", function () {
      var t = b.getAttribute("data-topic");
      buttons.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      document.querySelectorAll(".nw-edition").forEach(function (sec) {
        var any = false;
        sec.querySelectorAll("li[data-topic]").forEach(function (li) { li.hidden = t !== "all" && li.getAttribute("data-topic") !== t; any = any || !li.hidden; });
        sec.hidden = !any;
      });
    });
  });
  document.getElementById("nw-filter").hidden = false;
})();
    </script>
"""


def list_page(locale: dict[str, object], pre: str, editions: list[dict[str, object]]) -> tuple[str, str, str, str]:
    """(title, description, body, extra head) of /news/: a topic filter, the newest edition as cards with their first
    sentence, then the older ones as cards, under one heading per month once there is more than one month."""
    c = locale["copy"]
    lists, month = "", str(editions[0]["date"])[:7] if editions else ""
    several = len({str(ed["date"])[:7] for ed in editions}) > 1
    for i, ed in enumerate(editions):
        if several and (i == 0 or str(ed["date"])[:7] != month):
            month = str(ed["date"])[:7]
            lists += f'      <h2 class="nw-month" dir="ltr">{e(month)}</h2>\n'
        lists += edition_block(locale, pre, ed, lead=i == 0)
    if editions:
        topics = list(dict.fromkeys(t for t, _ in TOPICS))
        buttons = "".join(f'<button type="button" data-topic="{t}" aria-pressed="false">{e(topic_label(locale, t))}</button>' for t in topics)
        lists = (f'      <div class="discover-filter nw-filter" id="nw-filter" role="group" hidden>'
                 f'<button type="button" data-topic="all" aria-pressed="true">{e(c["discFilterAll"])}</button>{buttons}</div>\n'
                 + lists + FILTER_SCRIPT)
    else:
        lists = f'      <p class="discover-empty">{e(c["nwEmpty"])}</p>\n'
    body = (
        f'      <h1>{e(c["nwTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["nwLead"])}</p>\n'
        + lists
        + f'      <p class="quiz-note">{e(c["nwNote"])}</p>\n'
    )
    return f'{c["nwTitle"]} — QuizJet', c["nwMeta"], body, build_books.reader_css()


def feed(editions: list[dict[str, object]], locale: dict[str, object], limit: int = 30) -> str:
    """Atom feed of the newest editions, in English like the texts; each entry carries the full lesson text and its reports."""
    atom = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", atom)
    c = locale["copy"]
    root = ET.Element(f"{{{atom}}}feed")
    ET.SubElement(root, f"{{{atom}}}title").text = str(c["nwTitle"])
    ET.SubElement(root, f"{{{atom}}}subtitle").text = str(c["nwMeta"])
    ET.SubElement(root, f"{{{atom}}}id").text = f"{BASE_URL}/news/"
    ET.SubElement(root, f"{{{atom}}}link", rel="self", href=f"{BASE_URL}/{FEED_PATH}", type="application/atom+xml")
    ET.SubElement(root, f"{{{atom}}}link", rel="alternate", href=f"{BASE_URL}/news/", type="text/html")
    ET.SubElement(root, f"{{{atom}}}updated").text = f"{editions[0]['date']}T00:00:00Z"
    ET.SubElement(ET.SubElement(root, f"{{{atom}}}author"), f"{{{atom}}}name").text = "QuizJet"
    for ed in editions[:limit]:
        date = str(ed["date"])
        entry = ET.SubElement(root, f"{{{atom}}}entry")
        ET.SubElement(entry, f"{{{atom}}}id").text = f"{BASE_URL}/news/{date}/"
        ET.SubElement(entry, f"{{{atom}}}title").text = str(c["nwEdition"]).replace("{date}", date)
        ET.SubElement(entry, f"{{{atom}}}link", rel="alternate", href=f"{BASE_URL}/news/{date}/", type="text/html")
        ET.SubElement(entry, f"{{{atom}}}published").text = f"{date}T00:00:00Z"
        ET.SubElement(entry, f"{{{atom}}}updated").text = f"{date}T00:00:00Z"
        ET.SubElement(entry, f"{{{atom}}}summary").text = " · ".join(str(a["title"]) for a in ed["articles"])
        content = ET.SubElement(entry, f"{{{atom}}}content", type="text")
        content.text = "\n\n".join(
            f"{a['title']}\n\n" + "\n\n".join(" ".join(p) for p in a["p"])
            + ("\n\n" + str(c["nwSources"]) + ": " + ", ".join(str(s["u"]) for s in a.get("sources") or []) if a.get("sources") else "")
            for a in ed["articles"]
        )
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def edition_book(locale: dict[str, object], pre: str, edition: dict[str, object]) -> dict[str, object]:
    """An edition as the book dict build_books.reader_page renders (its optional keys carry the news framing)."""
    c, date = locale["copy"], str(edition["date"])
    title = c["nwEdition"].replace("{date}", date)
    return {
        "slug": f"news-{date}", "base": f"/news/{date}/", "title": title, "titleLang": locale["code"],
        "author": "QuizJet", "level": LEVEL, "quiz": "", "chapterQuizzes": {},
        "chapters": [{"title": f"{topic_label(locale, str(a['topic']))}: {a['title']}", "p": a["p"], "sources": a.get("sources") or []}
                     for a in edition["articles"]],
        "inlineAll": True,
        "tabs": [(str(a["topic"]), topic_label(locale, str(a["topic"]))) for a in edition["articles"]],
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
