"""Build the deployable site: the committed static pages, the Books pages and the public Discover catalog.

    python scripts/build_discover.py --out /tmp/site [--export discover-export.json]

The export comes from the bot (GET https://api.quizjet.app/public/discover/export, see
quizjet-bot docs/decisions/2026-11-02-kesfet-web-yayini.md). The catalog page exists in all 18 site
languages (/discover/, /<route>/discover/) and shows the quizzes written in that language first; the
others are one click away ("All languages"). A quiz has ONE page, in its own language: /<route>/q/<key>/,
or /q/<key>/ (English interface) for English, for a language the site does not have and for an unknown one.
/q/<key>/ always exists, as the page or as a redirect to it, and the 404 page forwards an old copy under
another language prefix there. The quiz language comes from the export (quizjet-bot
docs/decisions/2026-11-08-quiz-dili.md); an export without it is built the old way, every page in every
language. Interface text comes from _data/locales.json; quiz titles and descriptions are never translated.
Without --export (the token is not configured yet) every /discover/ page is a "coming soon" page and
the sitemap only lists the static and Books pages. A truncated or malformed export fails the build
so a partial catalog is never published. The Books pages (/books/) need no export: see
scripts/build_books.py.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import build_books
import build_news
from build_locales import NAV_MENU_SCRIPT, head_links, language_menu, legacy_redirect_page, legal_url, stylesheet_version

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://quizjet.app"
BLOCKLIST_PATH = ROOT / "_data" / "discover-blocklist.json"
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))
# Legal pages exist only in English and Turkish (owner decision); other languages link to English.
LEGAL_META = json.loads((ROOT / "_data" / "legal.json").read_text(encoding="utf-8"))
LEGAL_ROUTES = {language["route"] for language in LEGAL_META["languages"].values() if language["route"]}
LEGAL_URLS = [legal_url(language["route"], page) for language in LEGAL_META["languages"].values() for page in LEGAL_META["pages"]]
# A creator's description is published as the page's meta description; links in it (channel invites, mostly) are not.
URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.IGNORECASE)
TITLE_LIMIT = 70  # <title> only; the h1 keeps the full title
PAGE_SIZE = 30  # catalog cards shown before "Show more" (the script; without it every card is on the page)
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
# URL segments are English and never localized: UI text comes from _data/locales.json, the address of a page
# is the same in every language apart from its /<route>/ prefix (the way /q/<key>/ always was). A new section
# gets one English name here and nowhere else; test_build_discover pins the built tree against this set.
ROUTES = {"discover", "books", "news", "q"}
# The site launched with Turkish names. Every page that had one keeps a redirect there (search engines and
# shared links); never reuse these names for anything new and never drop the redirects.
LEGACY_ROUTES = {"kesfet": "discover", "kitaplar": "books", "haberler": "news"}
# Telegram's deep-link payload alphabet; a key outside it cannot start the quiz anyway.
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BOT_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
# The bot's quiz language: a short code and an optional two-letter region (tr, en, pt-BR, hi). Anything else is unknown.
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$")
SUPPORT = "support@quizjet.app"


def fold(text: str) -> str:
    """Case- and accent-insensitive search key; the inline script applies the same folding."""
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def prefix(locale: dict[str, object]) -> str:
    return f"/{locale['route']}" if locale["route"] else ""


def home_locale(item: dict[str, object]) -> dict[str, object] | None:
    """The site language a quiz is written in, if the site has it: the exact code, else its primary subtag (en-US → en)."""
    language = str(item.get("language") or "").lower()
    if not language:
        return None
    codes = {str(locale["code"]).lower(): locale for locale in LOCALES}
    return codes.get(language) or codes.get(language.split("-")[0])


def quiz_path(item: dict[str, object]) -> str:
    """The one address of a quiz page: in its own language, else under the English interface (the x-default)."""
    return f"{prefix(home_locale(item) or LOCALES[0])}/q/{item['key']}/"


def load_catalog(path: Path) -> tuple[str, list[dict[str, object]], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("truncated"):
        raise ValueError("export is truncated; refusing to publish a partial catalog")
    bot = str(data.get("botUsername") or "")
    if not BOT_RE.match(bot):
        raise ValueError(f"unexpected bot username {bot!r}")
    blocked = set(json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8")))
    items, skipped = [], []
    for raw in data["items"]:
        key = str(raw.get("externalId") or "")
        title = " ".join(str(raw.get("title") or "").split())
        if not KEY_RE.match(key) or not title:
            skipped.append(key)
            continue
        if key in blocked:
            continue
        language = str(raw.get("language") or "")
        items.append({
            "key": key,
            "title": title,
            "language": language if LANGUAGE_RE.match(language) else "",
            "description": " ".join(URL_RE.sub(" ", str(raw.get("description") or "")).split()),
            "questionCount": int(raw.get("questionCount") or 0),
            "aiGenerated": bool(raw.get("isAiGenerated")),
            "createdAtUtc": str(raw.get("createdAtUtc") or ""),
            "plays": int(raw.get("executionCount") or 0),
            "participants": int(raw.get("participantCount") or 0),
        })
    return bot, items, skipped


def shell(locale: dict[str, object], title: str, description: str, suffix: str, body: str,
          *, noindex: bool = False, jsonld: str = "", head: str = "", single: bool = False) -> str:
    """Page frame in ``locale``; ``suffix`` is the path after the language prefix (e.g. "discover/").

    ``single``: the page exists in this language only (a quiz page), so it names no other-language alternates and its
    language menu leads to the catalog in the other language rather than to a page that does not exist."""
    c = locale["copy"]
    pre = prefix(locale)
    url = f"{BASE_URL}{pre}/{suffix}"
    legal = pre if locale["route"] in LEGAL_ROUTES else ""
    en = "" if legal or locale["code"] == "en" else " (EN)"  # the legal pages are English and Turkish only; say so elsewhere
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    alternates = "" if single else "".join(
        f'    <link rel="alternate" hreflang="{e(other["code"])}" href="{BASE_URL}{prefix(other)}/{suffix}">\n'
        for other in LOCALES
    ) + f'    <link rel="alternate" hreflang="x-default" href="{BASE_URL}/{suffix}">\n'
    robots = '    <meta name="robots" content="noindex">\n' if noindex else ""
    ld = f'    <script type="application/ld+json">{jsonld}</script>\n' if jsonld else ""
    links = (f'<a href="{pre}/discover/">{e(c["discAll"])}</a><a href="{pre}/books/">{e(c["navBooks"])}</a>'
             f'<a href="{pre}/news/">{e(c["navNews"])}</a>')
    return (
        "<!doctype html>\n"
        f'<html lang="{e(locale["code"])}" dir="{e(locale["dir"])}">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '    <meta name="theme-color" content="#101f37">\n'
        f"    <title>{e(title)}</title>\n"
        f'    <meta name="description" content="{e(description)}">\n'
        f"{robots}"
        f'    <link rel="canonical" href="{url}">\n'
        f"{alternates}"
        f'    <meta property="og:locale" content="{e(locale["ogLocale"])}">\n'
        '    <meta property="og:site_name" content="QuizJet">\n'
        '    <meta property="og:type" content="website">\n'
        f'    <meta property="og:title" content="{e(title)}">\n'
        f'    <meta property="og:description" content="{e(description)}">\n'
        f'    <meta property="og:url" content="{url}">\n'
        f'    <meta property="og:image" content="{BASE_URL}/assets/og-image.png">\n'
        '    <meta property="og:image:width" content="1200">\n'
        '    <meta property="og:image:height" content="630">\n'
        '    <meta name="twitter:card" content="summary_large_image">\n'
        f'    <meta name="twitter:image" content="{BASE_URL}/assets/og-image.png">\n'
        f"{head_links()}"
        f'    <link rel="stylesheet" href="/styles.css?v={stylesheet_version()}">\n'
        f"{head}{ld}"
        "  </head>\n"
        "  <body>\n"
        f'    <a class="skip-link" href="#top">{e(c["skipToContent"])}</a>\n'
        '    <header class="site-header">\n'
        f'      <nav class="navigation page-width" aria-label="{e(c["navMainLabel"])}">\n'
        f'        <a class="brand" href="{pre}/"><img src="/assets/logo-96.png" alt="" width="44" height="44"><span>quizjet</span></a>\n'
        f'        <div class="nav-links">{links}</div>\n'
        f'        <div class="nav-actions">{language_menu(locale, LOCALES, "discover/" if single else suffix)}'
        f'<details class="nav-menu"><summary aria-label="{e(c["navMenu"])}"></summary><div class="nav-panel">'
        f'<a class="brand nav-panel-brand" href="{pre}/"><img src="/assets/logo-96.png" alt="" width="44" height="44"><span>quizjet</span></a>'
        f'{links}</div></details></div>\n'
        "      </nav>\n"
        "    </header>\n"
        f'    <main class="discover page-width" id="top">\n{body}    </main>\n'
        '    <footer class="site-footer">\n'
        '      <div class="footer-inner page-width">\n'
        f'        <a class="brand footer-brand" href="{pre}/"><img src="/assets/logo-96.png" alt="" width="36" height="36"><span>quizjet</span></a>\n'
        f'        <nav class="footer-legal" aria-label="{e(c["footerLegalLabel"])}"><a href="{legal}/privacy.html">{e(c["footerPrivacy"])}{en}</a>'
        f'<a href="{legal}/terms.html">{e(c["footerTerms"])}{en}</a><a href="{legal}/support.html">{e(c["footerSupport"])}{en}</a></nav>\n'
        "      </div>\n"
        "    </footer>\n"
        f"{NAV_MENU_SCRIPT}"
        "  </body>\n"
        "</html>\n"
    )


def facts(item: dict[str, object], c: dict[str, str]) -> str:
    # "Label: N" keeps every language free of plural forms (ru, pl, uk, ar inflect the noun).
    return " · ".join([
        f'{c["discQuestions"]}: {int(item["questionCount"])}',
        c["discAi"] if item["aiGenerated"] else c["discManual"],
        f'{c["discPlays"]}: {int(item["plays"])}',
    ])


# Search, sort, language scope, AI/by-hand filter and "show more", all on the cards already in the page: no request, no
# re-render on the server. Without script every card is visible, the page's own language first (the controls stay hidden).
SEARCH_SCRIPT = """    <script>
(function () {
  var input = document.getElementById("q"), count = document.getElementById("count"), none = document.getElementById("none");
  var list = document.querySelector(".quiz-list"), items = [].slice.call(list.children), more = document.getElementById("more");
  var sort = document.getElementById("sort"), filters = [].slice.call(document.querySelectorAll("#filter button"));
  var lang = document.getElementById("lang");
  var PAGE = %d, limit = PAGE, kind = "all";
  items.forEach(function (li, i) { li.setAttribute("data-i", i); });
  function fold(s) { return s.toLowerCase().replace(/ı/g, "i").normalize("NFKD").replace(/[\\u0300-\\u036f]/g, ""); }
  function num(li, a) { return Number(li.getAttribute(a)) || 0; }
  function apply() {
    var term = fold(input.value.trim()), mode = sort.value, matched = 0;
    var order = items.slice().sort(function (a, b) {
      if (mode === "plays") return num(b, "data-plays") - num(a, "data-plays") || num(a, "data-i") - num(b, "data-i");
      if (mode === "q") return num(b, "data-q") - num(a, "data-q") || num(a, "data-i") - num(b, "data-i");
      return num(a, "data-i") - num(b, "data-i");
    });
    order.forEach(function (li) {
      var hit = (!term || li.getAttribute("data-search").indexOf(term) > -1) && (kind === "all" || li.getAttribute("data-ai") === kind)
        && (!lang || lang.value === "all" || li.getAttribute("data-own") === "1");
      if (hit) matched++;
      li.hidden = !(hit && matched <= limit);
      list.appendChild(li);
    });
    count.textContent = matched;
    none.hidden = matched > 0;
    more.hidden = matched <= limit;
  }
  function reset() { limit = PAGE; apply(); }
  input.addEventListener("input", reset);
  sort.addEventListener("change", reset);
  if (lang) lang.addEventListener("change", reset);
  filters.forEach(function (b) {
    b.addEventListener("click", function () {
      kind = b.getAttribute("data-kind");
      filters.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      reset();
    });
  });
  more.addEventListener("click", function () { limit += PAGE; apply(); });
  document.getElementById("controls").hidden = false;
  apply();
})();
    </script>
""" % PAGE_SIZE


def render_catalog(locale: dict[str, object], items: list[dict[str, object]], generated: str, by_language: bool) -> str:
    """``by_language``: the export says each quiz's language, so this language's quizzes come first and the rest wait
    behind "All languages"; without it every quiz is listed with its copy in this language (the old build)."""
    c, pre, e = locale["copy"], prefix(locale), html.escape
    is_own = lambda i: by_language and home_locale(i) is locale  # noqa: E731
    own = sum(1 for i in items if is_own(i))
    ordered = sorted(items, key=lambda i: not is_own(i))  # stable: creation order inside each group
    rows = "".join(
        f'        <li data-search="{e(fold(str(i["title"]) + " " + str(i["description"])), quote=True)}"'
        f' data-plays="{int(i["plays"])}" data-q="{int(i["questionCount"])}" data-ai="{"ai" if i["aiGenerated"] else "manual"}"'
        + (f' data-own="{1 if is_own(i) else 0}"' if by_language else "") + ">"
        f'<a href="{quiz_path(i) if by_language else pre + "/q/" + str(i["key"]) + "/"}"><strong>{e(str(i["title"]))}</strong>'
        + (f'<span>{e(clip(str(i["description"]), 140))}</span>' if i["description"] else "")
        + f"<small>{e(facts(i, c))}</small></a></li>\n"
        for i in ordered
    )
    scope = (
        f'        <label for="lang">{e(c["discLangLabel"])} <select id="lang">'
        f'<option value="own"{" selected" if own else " disabled"}>{e(str(locale["name"]))} ({own})</option>'
        f'<option value="all"{"" if own else " selected"}>{e(c["discLangAll"])} ({len(items)})</option></select></label>\n'
    ) if by_language else ""
    none_here = f'      <p class="discover-empty">{e(c["discLangNone"])}</p>\n' if by_language and not own else ""
    day = generated[:10]
    updated = e(c["discUpdated"]).replace("{date}", f'<time datetime="{e(day)}" dir="ltr">{e(day)}</time>') if day else ""
    body = (
        f'      <h1>{e(c["discTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["discLead"])}</p>\n'
        f'      <label class="sr-only" for="q">{e(c["discSearch"])}</label>\n'
        f'      <input class="discover-search" id="q" type="search" placeholder="{e(c["discSearch"], quote=True)}" autocomplete="off">\n'
        '      <div class="discover-controls" id="controls" hidden>\n'
        f"{scope}"
        f'        <label for="sort">{e(c["discSort"])} <select id="sort"><option value="new">{e(c["discSortNew"])}</option>'
        f'<option value="plays">{e(c["discSortPlays"])}</option><option value="q">{e(c["discSortQuestions"])}</option></select></label>\n'
        f'        <div class="discover-filter" id="filter" role="group"><button type="button" data-kind="all" aria-pressed="true">{e(c["discFilterAll"])}</button>'
        f'<button type="button" data-kind="ai" aria-pressed="false">{e(c["discAi"])}</button>'
        f'<button type="button" data-kind="manual" aria-pressed="false">{e(c["discManual"])}</button></div>\n'
        "      </div>\n"
        f"{none_here}"
        f'      <p class="discover-count" aria-live="polite">{e(c["discCount"])}: <span id="count">{len(items)}</span></p>\n'
        f'      <ul class="quiz-list">\n{rows}      </ul>\n'
        f'      <p id="none" class="discover-empty" hidden>{e(c["discEmpty"])}</p>\n'
        f'      <button type="button" class="button button-dark discover-more" id="more" hidden>{e(c["discShowMore"])}</button>\n'
        + (f'      <p class="discover-updated">{updated}</p>\n' if updated else "")
        + SEARCH_SCRIPT
    )
    return shell(locale, f'{c["discTitle"]} — QuizJet', c["discMeta"], "discover/", body)


def render_placeholder(locale: dict[str, object]) -> str:
    c, e = locale["copy"], html.escape
    body = (
        f'      <h1>{e(c["discTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["discSoon"])} '
        f'<a href="https://t.me/QuizJetBot">{e(c["playNow"])}</a></p>\n'
    )
    return shell(locale, f'{c["discTitle"]} — QuizJet', c["discSoon"], "discover/", body, noindex=True)


def render_quiz(locale: dict[str, object], item: dict[str, object], bot: str, single: bool = False) -> str:
    c, pre, e = locale["copy"], prefix(locale), html.escape
    key, title, description = str(item["key"]), str(item["title"]), str(item["description"])
    created = str(item["createdAtUtc"])[:10]
    suffix = f"q/{key}/"
    start = f"https://t.me/{bot}?start={key}"
    group = f"https://t.me/{bot}?startgroup={key}"
    meta = clip(description, 155) if description else (
        f'{c["discQuizMeta"]} {c["discQuestions"]}: {int(item["questionCount"])}.'
    )
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Quiz",
        "name": title,
        **({"description": description} if description else {}),
        "url": f"{BASE_URL}{pre}/{suffix}",
        **({"dateCreated": created} if created else {}),
        **({"inLanguage": str(item["language"])} if item.get("language") else {}),
        "isAccessibleForFree": True,
        "provider": {"@type": "Organization", "name": "QuizJet", "url": BASE_URL + "/"},
    }, ensure_ascii=False).replace("</", "<\\/")
    report = f"mailto:{SUPPORT}?subject=" + "Report%20quiz%20" + key
    back = "→" if locale["dir"] == "rtl" else "←"
    # The handle and the date are left-to-right islands; inside Arabic/Persian text they would flip.
    note = e(c["discGroupNote"]).replace("{bot}", f'<bdi dir="ltr">@{e(bot)}</bdi>')
    body = (
        f'      <p class="quiz-back"><a href="{pre}/discover/">{back} {e(c["discAll"])}</a></p>\n'
        f"      <h1>{e(title)}</h1>\n"
        + (f'      <p class="quiz-description">{e(description)}</p>\n' if description else "")
        + f'      <p class="quiz-facts">{e(facts(item, c))} · {e(c["discPlayers"])}: {int(item["participants"])}'
        + (f' · {e(c["discPublished"])}: <time datetime="{e(created)}" dir="ltr">{e(created)}</time>' if created else "")
        + "</p>\n"
        '      <div class="quiz-actions">\n'
        f'        <a class="button button-primary" href="{e(start, quote=True)}">{e(c["discStartPrivate"])} <span aria-hidden="true">↗</span></a>\n'
        f'        <a class="button button-dark" href="{e(group, quote=True)}">{e(c["discStartGroup"])} <span aria-hidden="true">↗</span></a>\n'
        "      </div>\n"
        f'      <p class="quiz-note">{note}</p>\n'
        f'      <p class="quiz-report"><a href="{e(report, quote=True)}">{e(c["discReport"])}</a></p>\n'
    )
    return shell(locale, f'{clip(title, TITLE_LIMIT)} — {c["discQuizSuffix"]}', meta, suffix, body, jsonld=jsonld, single=single)


def catalog_json(items: list[dict[str, object]], bot: str, generated: str) -> str:
    out = [{
        **item,
        "url": f"{BASE_URL}{quiz_path(item)}",
        "startPrivate": f"https://t.me/{bot}?start={item['key']}",
        "startGroup": f"https://t.me/{bot}?startgroup={item['key']}",
    } for item in items]
    return json.dumps({"generatedAtUtc": generated, "botUsername": bot, "count": len(out), "items": out},
                      ensure_ascii=False, indent=1) + "\n"


Entry = tuple[str, str | None]  # (path, lastmod date or None): a date only where one is known, never a guess


def sitemap(entries: list[Entry]) -> str:
    """The committed sitemap (landing pages) plus the generated pages and the legal pages; <lastmod> only with a date."""
    ET.register_namespace("", SITEMAP_NS)
    base = ET.parse(ROOT / "sitemap.xml").getroot()
    urls: list[Entry] = [(loc.text or "", None) for loc in base.iter(f"{{{SITEMAP_NS}}}loc")]
    urls += [(BASE_URL + path, lastmod) for path, lastmod in entries]
    urls += [(url, None) for url in LEGAL_URLS]
    urlset = ET.Element(f"{{{SITEMAP_NS}}}urlset")
    for url, lastmod in urls:
        entry = ET.SubElement(urlset, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(entry, f"{{{SITEMAP_NS}}}loc").text = url
        if lastmod:
            ET.SubElement(entry, f"{{{SITEMAP_NS}}}lastmod").text = lastmod
    ET.indent(urlset, space="  ")
    return ET.tostring(urlset, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def band_re(band_id: str) -> re.Pattern[str]:
    return re.compile(rf'[ \t]*<section class="discover-band[^"]*" id="{band_id}">.*?</section>\n\n?', re.DOTALL)


BAND_RE = band_re("discover-band")


def drop_band(out: Path, band_id: str) -> None:
    """Remove one landing band from every language page (a band that would lead to an empty page)."""
    for page in out.rglob("index.html"):
        text = page.read_text(encoding="utf-8")
        if f'id="{band_id}"' in text:
            write(page, band_re(band_id).sub("", text))


def thousands(n: int) -> str:
    """23500 → "23 500" with a narrow no-break space: readable in every language without locale rules."""
    return f"{n:,}".replace(",", "\u202f")


Stats = tuple[int, int, int]  # quizzes, plays, players


def band_stats(items: list[dict[str, object]], by_language: bool) -> dict[str, Stats]:
    """The catalog band's numbers per landing route ("" = English): the quizzes in that language, or the whole catalog
    on every page when the export does not say the language."""
    stats: dict[str, Stats] = {}
    for locale in LOCALES:
        mine = [i for i in items if home_locale(i) is locale] if by_language else items
        stats[str(locale["route"])] = (len(mine), sum(int(i["plays"]) for i in mine), sum(int(i["participants"]) for i in mine))
    return stats


def apply_band(out: Path, stats: dict[str, Stats] | None) -> None:
    """Fill the landing pages' catalog band with the real numbers, or drop it.

    The committed pages carry the band with {count}, {plays} and {participants} placeholders (labels translated per
    language). Only this build knows the catalog, so they are filled here; without a catalog, or with no quiz in the
    page's language, the band is removed rather than shown with a placeholder, a zero or another language's quizzes.
    """
    for page in out.rglob("index.html"):
        text = page.read_text(encoding="utf-8")
        if 'id="discover-band"' not in text:
            continue
        route = page.parent.relative_to(out).as_posix().strip(".")
        count, plays, participants = (stats or {}).get(route, (0, 0, 0))
        if not count:
            text = BAND_RE.sub("", text)
        else:
            text = (text.replace("{count}", str(count)).replace("{plays}", thousands(plays))
                    .replace("{participants}", thousands(participants)))
        write(page, text)


def publishable(path: Path) -> bool:
    """What the artifact carries: not the build inputs (scripts/, _data, _legal, _includes, dotfiles) and not the
    repo's own documents (CLAUDE.md), which are for people working here, not for visitors."""
    parts = path.relative_to(ROOT).parts
    return parts[0] != "scripts" and path.suffix != ".md" and not any(part.startswith((".", "_")) for part in parts)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def locale_dir(out: Path, locale: dict[str, object]) -> Path:
    return out / str(locale["route"]) if locale["route"] else out


def write_books(out: Path) -> list[Entry]:
    """The Books shelf and one reader per book, in every language; returns their sitemap entries."""
    books = build_books.load_books()
    entries: list[Entry] = []
    for locale in LOCALES:
        base, pre = locale_dir(out, locale), prefix(locale)
        title, description, body, head = build_books.list_page(locale, pre, books)
        write(base / "books" / "index.html", shell(locale, title, description, "books/", body, head=head))
        entries.append((f"{pre}/books/", None))
        for book in books:
            suffix = f"books/{book['slug']}/"
            title, description, body, jsonld, head = build_books.reader_page(locale, pre, book)
            write(base / suffix / "index.html", shell(locale, title, description, suffix, body, jsonld=jsonld, head=head))
            entries.append((f"{pre}/{suffix}", None))
    for book in books:
        for path, content in build_books.chapter_files(book).items():
            write(out / path, content)
    return entries


def write_news(out: Path) -> list[Entry]:
    """The news editions, one reader per edition in every language, and the Atom feed; returns the sitemap entries.

    With no edition yet (the keys are not configured, or nothing survived) the list page still exists,
    unindexed and out of the sitemap, so the menu link never 404s."""
    editions = build_news.load_editions()
    if not editions:
        drop_band(out, "news-band")
    feed = build_news.FEED_LINK if editions else ""
    entries: list[Entry] = []
    for locale in LOCALES:
        base, pre = locale_dir(out, locale), prefix(locale)
        title, description, body, head = build_news.list_page(locale, pre, editions)
        write(base / "news" / "index.html", shell(locale, title, description, "news/", body, head=head + feed, noindex=not editions))
        if editions:
            entries.append((f"{pre}/news/", str(editions[0]["date"])))
        for edition in editions:
            suffix = f"news/{edition['date']}/"
            book = build_news.edition_book(locale, pre, edition)
            title, description, body, jsonld, head = build_books.reader_page(locale, pre, book)
            write(base / suffix / "index.html", shell(locale, title, description, suffix, body, jsonld=jsonld, head=head + feed))
            entries.append((f"{pre}/{suffix}", str(edition["date"])))
    for edition in editions:
        for path, content in build_news.chapter_files(edition).items():
            write(out / path, content)
    if editions:
        write(out / "news" / "feed.xml", build_news.feed(editions, LOCALES[0]))
    return entries


def write_legacy_redirects(out: Path, new_paths: list[str]) -> None:
    """A redirect page at the Turkish-named address of every page that had one (LEGACY_ROUTES)."""
    for new in new_paths:
        for old_name, new_name in LEGACY_ROUTES.items():
            old = new.replace(f"/{new_name}/", f"/{old_name}/", 1)
            if old != new:
                write(out / old.strip("/") / "index.html", legacy_redirect_page(new, BASE_URL + new))


def build(out: Path, export: Path | None) -> str:
    if out.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("--out must be outside the repository (the page checks scan every index.html in it)")
    if out.exists():
        shutil.rmtree(out)
    for path in ROOT.rglob("*"):
        if path.is_file() and publishable(path):
            target = out / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    book_entries = write_books(out) + write_news(out)
    # /discover/ and /news/ exist in every build (placeholder or not), so their old names always forward.
    write_legacy_redirects(out, [f"{prefix(l)}/discover/" for l in LOCALES] + [f"{prefix(l)}/news/" for l in LOCALES]
                           + [path for path, _ in book_entries])
    if export is None:
        apply_band(out, None)
        for locale in LOCALES:
            write(locale_dir(out, locale) / "discover" / "index.html", render_placeholder(locale))
        write(out / "sitemap.xml", sitemap(book_entries))
        return "Built without the Discover catalog (no export given)."

    bot, items, skipped = load_catalog(export)
    generated = str(json.loads(export.read_text(encoding="utf-8")).get("generatedAtUtc") or "")
    # ponytail: the old every-page-in-every-language build stays for an export from a bot older than the language field
    # (merged ≠ deployed there); drop it, and this flag, once every export carries "language".
    by_language = any(item["language"] for item in items)
    apply_band(out, band_stats(items, by_language))
    entries: list[Entry] = []
    for locale in LOCALES:
        base, pre = locale_dir(out, locale), prefix(locale)
        write(base / "discover" / "index.html", render_catalog(locale, items, generated, by_language))
        entries.append((f"{pre}/discover/", generated[:10] or None))
        for item in ([] if by_language else items):
            write(base / "q" / str(item["key"]) / "index.html", render_quiz(locale, item, bot))
            # the creation date is the one change we know of; a build date would claim every page changed daily
            entries.append((f"{pre}/q/{item['key']}/", str(item["createdAtUtc"])[:10] or None))
    for item in (items if by_language else []):
        path = quiz_path(item)
        write(out / path.strip("/") / "index.html", render_quiz(home_locale(item) or LOCALES[0], item, bot, single=True))
        entries.append((path, str(item["createdAtUtc"])[:10] or None))
        if path != f"/q/{item['key']}/":  # the shared, language-free address keeps working
            write(out / "q" / str(item["key"]) / "index.html", legacy_redirect_page(path, BASE_URL + path))
    write(out / "discover.json", catalog_json(items, bot, generated))
    write(out / "sitemap.xml", sitemap(entries + book_entries))
    note = f" Skipped {len(skipped)} item(s) with an unusable key or empty title." if skipped else ""
    shape = "one page per quiz, in its own language" if by_language else f"{len(LOCALES)} languages"
    return f"Built the Discover catalog with {len(items)} quizzes ({shape}).{note}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the deployable QuizJet site.")
    parser.add_argument("--out", required=True, type=Path, help="output directory, outside the repository")
    parser.add_argument("--export", type=Path, help="JSON from GET /public/discover/export")
    args = parser.parse_args()
    try:
        print(build(args.out, args.export))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
