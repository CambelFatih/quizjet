"""Library pages: /library/ (the shelves, in all 18 languages) and one playable page per pack, in the pack's language.

build_discover.build() calls this and wraps each page in the site frame. The data is _data/library.json, the same
bundle the bot embeds (quizjet-bot Presentation/WebHook/Shelves/shelves.json), written by quizjet-sentetik-veri:

    python scripts/build_packs.py --export ../quizjet/_data/library.json

A pack page exists ONCE, like a Discover quiz: /library/<slug>/ for English, /<route>/library/<slug>/ for another
language (slug = the pack id without its "<language>-" prefix). A pack retired there disappears here with the next
export. The questions are in the page as plain HTML (search engines, readers without JavaScript; no answer marked)
and as JSON for assets/library.js, which turns them into a player: one question at a time, the right answer shown
after each pick, the score at the end. Nothing is sent anywhere. Questions are never reworded (CC BY-SA, as is);
the attribution of each pack's source is on its page and under the shelves.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from build_books import asset_version, e, script_json

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = ROOT / "_data" / "library.json"
BASE_URL = "https://quizjet.app"
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))
# quizjet-sentetik-veri scripts/check_data.py SHELVES, in display order; each has an "lbShelf_<key>" copy key.
SHELVES = ["general", "history", "geography", "science", "sports", "arts", "pop-culture"]
LEVELS = {"easy": "lbEasy", "medium": "lbMedium", "hard": "lbHard"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCALE_BY_CODE = {str(l["code"]).lower(): l for l in LOCALES}


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
            p["difficulty"] not in LEVELS and f"unknown difficulty {p['difficulty']!r}",
            p["source"] not in sources and f"unknown source {p['source']!r}",
            not p["questions"] and "no questions",
            any(len(q["o"]) < 2 or not (isinstance(q["a"], int) and 0 <= q["a"] < len(q["o"])) for q in p["questions"]) and "bad answer index",
        ]
        if any(problems):
            raise ValueError(f"library pack {pid!r}: " + "; ".join(x for x in problems if x))
        seen.add((language, slug))
        packs.append({**p, "language": language, "slug": slug})
    return sources, packs


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


LANG_SCRIPT = """      <script>
(function () {
  var pick = document.getElementById("lb-lang"), shelves = [].slice.call(document.querySelectorAll(".lb-shelf"));
  function apply() {
    shelves.forEach(function (s) {
      var shown = 0;
      [].forEach.call(s.querySelectorAll("li"), function (li) { li.hidden = li.getAttribute("data-lang") !== pick.value; shown += !li.hidden; });
      s.hidden = !shown;
    });
    try { localStorage.setItem("lb-lang", pick.value); } catch (x) {}
  }
  try { var saved = localStorage.getItem("lb-lang"); if (saved && pick.querySelector('option[value="' + saved + '"]')) pick.value = saved; } catch (x) {}
  pick.addEventListener("change", apply);
  document.getElementById("lb-langs").hidden = false;
  apply();
})();
      </script>
"""


def list_page(locale: dict, pre: str, sources: dict, packs: list[dict]) -> tuple[str, str, str]:
    """(title, description, body) of /library/. With packs in several languages a picker shows one language at a time:
    the page's own if it has packs, else the one with the most (the miniapp's rule); without script all are listed."""
    c = locale["copy"]
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
    sections = ""
    for shelf in SHELVES:
        mine = [p for p in packs if p["shelf"] == shelf]
        if not mine:
            continue
        rows = "".join(
            f'          <li data-lang="{e(p["language"])}"><a href="{pack_path(p)}" lang="{e(pack_locale(p)["code"])}" dir="{e(pack_locale(p)["dir"])}">'
            f'<strong>{e(p["title"])}</strong><span>{e(p["description"])}</span>'
            f'<small lang="{e(locale["code"])}" dir="{e(locale["dir"])}">{e(facts(p, c))}</small></a></li>\n'
            for p in mine
        )
        sections += (f'      <section class="lb-shelf"><h2>{e(c["lbShelf_" + shelf])}</h2>\n'
                     f'        <ul class="quiz-list">\n{rows}        </ul>\n      </section>\n')
    body = (
        f'      <h1>{e(c["navLibrary"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["lbLead"])}</p>\n'
        f"{picker}{sections}"
        f'      <p class="quiz-note">{attribution(sources, [p["source"] for p in packs], c)}</p>\n'
        + (LANG_SCRIPT if picker else "")
    )
    shelf_names = ", ".join(c["lbShelf_" + s] for s in SHELVES if any(p["shelf"] == s for p in packs))
    return f'{c["navLibrary"]} — QuizJet', f'{c["lbLead"]}: {shelf_names}.', body


def pack_page(sources: dict, pack: dict) -> tuple[str, str, str, str]:
    """(title, description, body, jsonld) of a pack page, in the pack's own language."""
    locale = pack_locale(pack)
    c, route = locale["copy"], locale["route"]
    pre = f"/{route}" if route else ""
    back = "→" if locale["dir"] == "rtl" else "←"
    static = "".join(
        f'          <li><p>{e(q["q"])}</p><ul>{"".join(f"<li>{e(o)}</li>" for o in q["o"])}</ul></li>\n'
        for q in pack["questions"]
    )
    data = {
        "questions": [{"q": q["q"], "o": q["o"], "a": q["a"]} for q in pack["questions"]],
        "copy": {k: c[k] for k in ("lbQuestionOf", "lbCorrect", "lbWrong", "lbNext", "lbFinish", "lbScore")},
    }
    body = f"""      <p class="quiz-back"><a href="{pre}/library/">{back} {e(c["navLibrary"])}</a></p>
      <h1>{e(pack["title"])}</h1>
      <p class="quiz-description">{e(pack["description"])}</p>
      <p class="quiz-facts">{e(facts(pack, c))} · {e(c["lbShelf_" + pack["shelf"]])}</p>
      <div class="lb-player" id="lb-player" hidden>
        <p class="lb-progress" id="lb-pos"></p>
        <h2 class="lb-q" id="lb-q" tabindex="-1"></h2>
        <div class="lb-options" id="lb-o"></div>
        <p class="lb-feedback" id="lb-fb" aria-live="polite"></p>
        <button type="button" class="button button-primary" id="lb-next" hidden></button>
      </div>
      <div class="lb-player" id="lb-done" hidden>
        <h2>{e(c["lbResult"])}</h2>
        <p class="lb-score" id="lb-score" aria-live="polite"></p>
        <div class="quiz-actions">
          <button type="button" class="button button-primary" id="lb-retry">{e(c["lbRetry"])}</button>
          <a class="button button-dark" href="https://t.me/QuizJetBot">{e(c["playNow"])} <span aria-hidden="true">↗</span></a>
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
        "isAccessibleForFree": True,
        "license": source["licenseUrl"],
        "isBasedOn": source["url"],
        "provider": {"@type": "Organization", "name": "QuizJet", "url": BASE_URL + "/"},
    })
    return f'{pack["title"]} — {c["navLibrary"]}', pack["description"], body, jsonld
