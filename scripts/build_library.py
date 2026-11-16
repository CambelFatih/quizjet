"""Library pages: /library/ (the shelves, in all 18 languages) and one page per pack, in the pack's language.

build_discover.build() calls this and wraps each page in the site frame. The data is _data/library.json, the same
bundle the bot embeds (quizjet-bot Presentation/WebHook/Shelves/shelves.json), written by quizjet-sentetik-veri:

    python scripts/build_packs.py --export ../quizjet/_data/library.json

The pages follow the miniapp's Library (quizjet-bot wwwroot/miniapp/kutuphane.html and oyna.html):
- /library/ shows the shelves; a shelf (/library/#history) lists its packs with a level filter and, when the shelf
  has several, a topic filter. Without JavaScript every shelf is one long list.
- A pack page exists ONCE, like a Discover quiz: /library/<slug>/ for English, /<route>/library/<slug>/ for another
  language (slug = the pack id without its "<language>-" prefix). It is the quiz's home: description, facts and three
  ways to play — here in the browser, privately in Telegram, in a group.
- The browser player (assets/library.js) is the miniapp's practice run without a server: 30 s per question (the bot
  imports every pack with 30 s), a timed-out question passes unanswered, the right answer shows after each pick and
  the next question follows by itself, Previous/Next review earlier questions or skip, the result lists every
  question. Nothing is sent or stored.
- The Telegram buttons use the key the bot's import gives the pack's quiz (ShelfImportService.QuizIdFor →
  ExternalKeyGenerator.Generate); they open the quiz once the admin has imported the pack.
The questions are in the page as plain HTML too (search engines, readers without JavaScript; no answer marked).
Questions are never reworded (CC BY-SA, as is); each source is credited on its pages.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from build_books import asset_version, e, script_json

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = ROOT / "_data" / "library.json"
BASE_URL = "https://quizjet.app"
BOT = "QuizJetBot"
QUESTION_SECONDS = 30  # ShelfImportService creates every pack's quiz with 30 s per question
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))
# quizjet-sentetik-veri scripts/check_data.py SHELVES, in the bot's order (ShelfBundle.ShelfOrder) with the miniapp's
# icons; each has an "lbShelf_<key>" copy key.
SHELVES = {"general": "🌍", "history": "🏛️", "geography": "🗺️", "science": "🔬", "sports": "⚽", "arts": "🎨", "pop-culture": "🎮",
           "mkqa": "🔎"}
# Two tabs, as in the miniapp (owner decision 2026-11-16, option C): "Trivia" holds Open Trivia DB's seven shelves,
# "Curious Questions" holds MKQA alone and lists its topics where the other tab lists shelves (#mkqa-<topic>).
CURIOUS = "mkqa"
# MKQA's topics are English labels in the bundle; each has an icon and an "lbTopic_<id>" copy key.
TOPICS = {"Songs & Singers": ("songs", "🎤"), "Movies": ("movies", "🎬"), "TV Shows": ("tv", "📺"),
          "History & Government": ("history", "🏛️"), "General Knowledge": ("general", "💡"),
          "Books & Authors": ("books", "📚"), "Geography": ("geography", "🗺️"), "Sports": ("sports", "⚽")}
LEVELS = {"easy": "lbEasy", "medium": "lbMedium", "hard": "lbHard"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCALE_BY_CODE = {str(l["code"]).lower(): l for l in LOCALES}


def telegram_key(pack_id: str) -> str:
    """The bot's start code for an imported pack: the quiz id is the first 16 bytes of SHA-256("quizjet-shelf:" + id),
    the key the first 8 base64 characters of those bytes with + and / folded to _ (a .NET Guid built from bytes gives
    the same bytes back from ToByteArray, so no byte reordering)."""
    quiz_id = hashlib.sha256(("quizjet-shelf:" + pack_id).encode("utf-8")).digest()[:16]
    return base64.b64encode(quiz_id).decode("ascii")[:8].replace("+", "_").replace("/", "_")


def load_library(path: Path | None = None) -> tuple[dict[str, dict], list[dict]]:
    """(sources, packs); a malformed pack fails the build instead of publishing a broken player."""
    data = json.loads((path or LIBRARY_PATH).read_text(encoding="utf-8"))
    sources, packs, seen = data["sources"], [], set()
    for p in data["packs"]:
        language, pid = str(p["language"]).lower(), str(p["id"])
        slug = pid.removeprefix(language + "-")
        problems = [
            language not in LOCALE_BY_CODE and f"language {language!r} is not a site language",
            (slug == pid or not SLUG_RE.match(slug)) and "id is not <language>-<slug>",
            (language, slug) in seen and "duplicate id",
            p["shelf"] not in SHELVES and f"unknown shelf {p['shelf']!r}",
            p["shelf"] == CURIOUS and p["topic"] not in TOPICS and f"unknown {CURIOUS} topic {p['topic']!r}",
            p["difficulty"] not in LEVELS and f"unknown difficulty {p['difficulty']!r}",
            p["source"] not in sources and f"unknown source {p['source']!r}",
            not p["questions"] and "no questions",
            any(len(q["o"]) < 2 or not (isinstance(q["a"], int) and 0 <= q["a"] < len(q["o"])) for q in p["questions"]) and "bad answer index",
        ]
        if any(problems):
            raise ValueError(f"library pack {pid!r}: " + "; ".join(x for x in problems if x))
        seen.add((language, slug))
        packs.append({**p, "language": language, "slug": slug, "key": telegram_key(pid)})
    return sources, packs


def tab_of(shelf: str) -> str:
    return "curious" if shelf == CURIOUS else "trivia"


def topic_section(topic: str) -> str:
    """The section id of an MKQA topic, the hash that opens it: #mkqa-songs."""
    return f"{CURIOUS}-{TOPICS[topic][0]}"


def place(pack: dict, c: dict) -> tuple[str, str, str]:
    """(hash, icon, name) of where a pack sits on /library/: its shelf, or its topic for MKQA."""
    if pack["shelf"] == CURIOUS:
        tid, icon = TOPICS[pack["topic"]]
        return topic_section(pack["topic"]), icon, c["lbTopic_" + tid]
    return pack["shelf"], SHELVES[pack["shelf"]], c["lbShelf_" + pack["shelf"]]


def pack_locale(pack: dict) -> dict:
    return LOCALE_BY_CODE[pack["language"]]


def pack_path(pack: dict) -> str:
    route = pack_locale(pack)["route"]
    return f"{'/' + route if route else ''}/library/{pack['slug']}/"


def facts(pack: dict, c: dict) -> str:
    return f'{c["discQuestions"]}: {len(pack["questions"])} · {c[LEVELS[pack["difficulty"]]]}'


def attribution(sources: dict, keys: list[str], c: dict) -> str:
    links = ", ".join(
        f'<a href="{e(s["url"])}" rel="noopener noreferrer">{e(s["name"])}</a>'
        f' (<a href="{e(s["licenseUrl"])}" rel="license noopener noreferrer">{e(s["license"])}</a>)'
        for s in (sources[k] for k in sorted(set(keys)))
    )
    return e(c["lbSource"]).replace("{0}", links)


def chips(name: str, values: list[tuple[str, str]], c: dict) -> str:
    """A filter row (All + values), like the miniapp's; a row with one value filters nothing and is left out."""
    if len(values) < 2:
        return ""
    buttons = "".join(f'<button type="button" data-v="{e(v)}" aria-pressed="false">{e(label)}</button>' for v, label in values)
    return (f'        <div class="discover-filter lb-chips" data-f="{name}" role="group"><button type="button" data-v="" aria-pressed="true">'
            f'{e(c["lbAll"])}</button>{buttons}</div>\n')


def list_page(locale: dict, pre: str, sources: dict, packs: list[dict]) -> tuple[str, str, str]:
    """(title, description, body) of /library/: the shelves, then one section per shelf. assets/library.js shows one
    of them at a time (the URL hash names the shelf) with its level and topic filters; with packs in several languages a
    picker shows one language: the page's own if it has packs, else the one with the most (the miniapp's rule)."""
    c, back = locale["copy"], "→" if locale["dir"] == "rtl" else "←"
    counts: dict[str, int] = {}
    for p in packs:
        counts[p["language"]] = counts.get(p["language"], 0) + 1
    own = str(locale["code"]).lower()
    default = own if own in counts else max(counts, key=lambda k: counts[k], default="")
    picker = ""
    if len(counts) > 1:
        options = "".join(f'<option value="{e(k)}"{" selected" if k == default else ""}>{e(LOCALE_BY_CODE[k]["name"])} ({n})</option>'
                          for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        picker = (f'      <div class="discover-controls" id="lb-langs" hidden><label for="lb-lang">{e(c["lbQuizLang"])} '
                  f'<select id="lb-lang">{options}</select></label></div>\n')
    def section(sid: str, tab: str, icon: str, name: str, mine: list[dict], up: str, topic_filter: bool) -> tuple[str, str]:
        levels = [(lv, c[LEVELS[lv]]) for lv in LEVELS if any(p["difficulty"] == lv for p in mine)]
        topics = list(dict.fromkeys(p["topic"] for p in mine)) if topic_filter else []
        rows = "".join(
            f'          <li data-lang="{e(p["language"])}" data-level="{e(p["difficulty"])}" data-topic="{e(p["topic"])}">'
            f'<a href="{pack_path(p)}"><strong lang="{e(pack_locale(p)["code"])}" dir="{e(pack_locale(p)["dir"])}">{e(p["title"])}</strong>'
            f'<span lang="{e(pack_locale(p)["code"])}" dir="{e(pack_locale(p)["dir"])}">{e(p["description"])}</span>'
            f'<small>{e(facts(p, c))}</small></a></li>\n'
            for p in mine
        )
        card = (f'        <li data-shelf="{sid}" data-tab="{tab}"><a href="#{sid}"><span class="lb-icon" aria-hidden="true">{icon}</span>'
                f'<strong>{e(name)}</strong><small>{e(c["lbPackCount"]).replace("{0}", f"<span>{len(mine)}</span>")}</small></a></li>\n')
        return card, (
            f'      <section class="lb-shelf" id="{sid}" data-tab="{tab}">\n'
            f'        <p class="quiz-back lb-up" hidden><a href="#{up}">{back} {e(c["navLibrary"])}</a></p>\n'
            f'        <h2><span aria-hidden="true">{icon}</span> {e(name)}</h2>\n'
            f'{chips("level", levels, c)}{chips("topic", [(t, t) for t in topics], c)}'
            f'        <ul class="quiz-list">\n{rows}        </ul>\n'
            f'        <p class="discover-empty" hidden>{e(c["lbNoneHere"])}</p>\n'
            "      </section>\n"
        )

    cards, sections = "", ""
    for shelf, icon in SHELVES.items():
        mine = [p for p in packs if p["shelf"] == shelf]
        if not mine:
            continue
        if shelf == CURIOUS:
            for topic, (tid, ticon) in TOPICS.items():
                theirs = [p for p in mine if p["topic"] == topic]
                if theirs:
                    card, sec = section(topic_section(topic), "curious", ticon, c["lbTopic_" + tid], theirs, "curious", False)
                    cards, sections = cards + card, sections + sec
        else:
            card, sec = section(shelf, "trivia", icon, c["lbShelf_" + shelf], mine, "", True)
            cards, sections = cards + card, sections + sec
    tabs = ""
    if {tab_of(p["shelf"]) for p in packs} == {"trivia", "curious"}:
        tabs = ('      <div class="lb-tabs" id="lb-tabs" role="tablist" hidden>'
                + "".join(f'<button type="button" role="tab" data-tab="{t}" aria-selected="false">{e(c[k])}<small></small></button>'
                          for t, k in (("trivia", "lbTabTrivia"), ("curious", "lbShelf_" + CURIOUS)))
                + "</div>\n")
    credits = "".join(
        f'      <p class="quiz-note" data-tab="{t}">{attribution(sources, keys, c)}</p>\n'
        for t in ("trivia", "curious")
        if (keys := [p["source"] for p in packs if tab_of(p["shelf"]) == t])
    )
    body = (
        f'      <h1>{e(c["navLibrary"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["lbLead"])}</p>\n'
        f"{picker}{tabs}"
        f'      <ul class="lb-shelves" id="lb-shelves">\n{cards}      </ul>\n'
        f"{sections}{credits}"
        f'      <script src="/assets/library.js?v={asset_version("library.js")}" defer></script>\n'
    )
    shelf_names = ", ".join(c["lbShelf_" + s] for s in SHELVES if any(p["shelf"] == s for p in packs))
    return f'{c["navLibrary"]} — QuizJet', f'{c["lbLead"]}: {shelf_names}.', body


def pack_page(sources: dict, pack: dict) -> tuple[str, str, str, str]:
    """(title, description, body, jsonld) of a pack page, in the pack's own language: the quiz's home with its three
    ways to play, the player and the result (assets/library.js), and the questions as plain HTML."""
    locale = pack_locale(pack)
    c, route = locale["copy"], locale["route"]
    pre = f"/{route}" if route else ""
    back = "→" if locale["dir"] == "rtl" else "←"
    start, group = f"https://t.me/{BOT}?start={pack['key']}", f"https://t.me/{BOT}?startgroup={pack['key']}"
    where, icon, where_name = place(pack, c)
    shelf_name = c["lbShelf_" + pack["shelf"]]
    topic_name = where_name if pack["shelf"] == CURIOUS else pack["topic"]
    note = e(c["discGroupNote"]).replace("{bot}", f'<bdi dir="ltr">@{BOT}</bdi>')
    static = "".join(
        f'          <li><p>{e(q["q"])}</p><ul>{"".join(f"<li>{e(o)}</li>" for o in q["o"])}</ul></li>\n'
        for q in pack["questions"]
    )
    data = {
        "seconds": QUESTION_SECONDS,
        "questions": [{"q": q["q"], "o": q["o"], "a": q["a"]} for q in pack["questions"]],
        "copy": {k: c[k] for k in ("lbQuestionOf", "lbCorrect", "lbWrong", "lbNext", "lbFinish", "lbScore", "lbTimeLeft",
                                   "lbTimedOut", "lbSkipped", "lbResultTime")},
    }
    body = f"""      <p class="quiz-back"><a href="{pre}/library/#{where}">{back} {e(where_name)}</a></p>
      <h1>{e(pack["title"])}</h1>
      <p class="quiz-description">{e(pack["description"])}</p>
      <p class="quiz-facts">{e(facts(pack, c))} · <span aria-hidden="true">{SHELVES[pack["shelf"]]}</span> {e(shelf_name)} · {e(topic_name)}</p>
      <div id="lb-home">
        <div class="quiz-actions">
          <button type="button" class="button button-primary" id="lb-start" hidden>{e(c["lbPlayHere"])} <span aria-hidden="true">▶</span></button>
          <a class="button button-dark" href="{e(start)}">{e(c["discStartPrivate"])} <span aria-hidden="true">↗</span></a>
          <a class="button button-dark" href="{e(group)}">{e(c["discStartGroup"])} <span aria-hidden="true">↗</span></a>
        </div>
        <p class="quiz-note">{note}</p>
      </div>
      <div class="lb-player" id="lb-player" hidden>
        <p class="lb-progress" id="lb-pos"></p>
        <p class="lb-review" id="lb-rv" hidden>{e(c["lbReviewing"])}</p>
        <h2 class="lb-q" id="lb-q" tabindex="-1"></h2>
        <div class="lb-timer" id="lb-tm"><i id="lb-tmb"></i></div>
        <p class="lb-time" id="lb-tmt" aria-hidden="true"></p>
        <div class="lb-options" id="lb-o"></div>
        <p class="lb-feedback" id="lb-fb" aria-live="polite"></p>
        <div class="lb-nav" dir="ltr">
          <button type="button" id="lb-prev">‹ {e(c["lbPrev"])}</button>
          <button type="button" id="lb-next">{e(c["lbNext"])} ›</button>
        </div>
        <p class="quiz-note">{e(c["lbFoot"])} {e(c["lbNote"])}</p>
      </div>
      <div class="lb-player" id="lb-done" hidden>
        <h2>{e(c["lbResult"])}</h2>
        <p class="lb-score" id="lb-score" aria-live="polite"></p>
        <p class="quiz-facts" id="lb-time"></p>
        <ol class="lb-rows" id="lb-rows"></ol>
        <div class="quiz-actions">
          <button type="button" class="button button-primary" id="lb-retry">{e(c["lbRetry"])}</button>
          <button type="button" class="button button-light" id="lb-back">{e(c["lbBack"])}</button>
        </div>
      </div>
      <ol class="lb-static" id="lb-static">
{static}      </ol>
      <p class="quiz-note">{attribution(sources, [pack["source"]], c)}</p>
      <script type="application/json" id="lb-data">{script_json(data)}</script>
      <script src="/assets/library.js?v={asset_version("library.js")}" defer></script>
"""
    source = sources[pack["source"]]
    jsonld = script_json({
        "@context": "https://schema.org",
        "@type": "Quiz",
        "name": pack["title"],
        "description": pack["description"],
        "url": BASE_URL + pack_path(pack),
        "inLanguage": locale["code"],
        "educationalLevel": pack["difficulty"],
        "about": pack["topic"],
        "isAccessibleForFree": True,
        "license": source["licenseUrl"],
        "isBasedOn": source["url"],
        "provider": {"@type": "Organization", "name": "QuizJet", "url": BASE_URL + "/"},
    })
    return f'{pack["title"]} — {c["navLibrary"]}', pack["description"], body, jsonld
