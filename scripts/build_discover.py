"""Build the deployable site: the committed static pages plus the public Discover catalog.

    python scripts/build_discover.py --out /tmp/site [--export discover-export.json]

The export comes from the bot (GET https://api.quizjet.app/public/discover/export, see
quizjet-bot docs/decisions/2026-11-02-kesfet-web-yayini.md). Every Discover page exists in all
18 site languages (/kesfet/ and /q/<key>/ for English, /<route>/kesfet/ and /<route>/q/<key>/ for
the others), so a visitor stays in the language they picked. The interface text comes from
_data/locales.json; quiz titles and descriptions stay in the language their creator wrote them in.
Without --export (the token is not configured yet) every /kesfet/ page is a "coming soon" page and
the sitemap only lists the static pages. A truncated or malformed export fails the build so a
partial catalog is never published.
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

from build_locales import language_menu, stylesheet_version

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://quizjet.app"
BLOCKLIST_PATH = ROOT / "_data" / "discover-blocklist.json"
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))
# Legal pages exist only in English and Turkish (owner decision); other languages link to English.
LEGAL_ROUTES = {
    language["route"]
    for language in json.loads((ROOT / "_data" / "legal.json").read_text(encoding="utf-8"))["languages"].values()
    if language["route"]
}
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
# Telegram's deep-link payload alphabet; a key outside it cannot start the quiz anyway.
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BOT_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
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
        items.append({
            "key": key,
            "title": title,
            "description": " ".join(str(raw.get("description") or "").split()),
            "questionCount": int(raw.get("questionCount") or 0),
            "aiGenerated": bool(raw.get("isAiGenerated")),
            "createdAtUtc": str(raw.get("createdAtUtc") or ""),
            "plays": int(raw.get("executionCount") or 0),
            "participants": int(raw.get("participantCount") or 0),
        })
    return bot, items, skipped


def shell(locale: dict[str, object], title: str, description: str, suffix: str, body: str,
          *, noindex: bool = False, jsonld: str = "") -> str:
    """Page frame in ``locale``; ``suffix`` is the path after the language prefix (e.g. "kesfet/")."""
    c = locale["copy"]
    pre = prefix(locale)
    url = f"{BASE_URL}{pre}/{suffix}"
    legal = pre if locale["route"] in LEGAL_ROUTES else ""
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    alternates = "".join(
        f'    <link rel="alternate" hreflang="{e(other["code"])}" href="{BASE_URL}{prefix(other)}/{suffix}">\n'
        for other in LOCALES
    ) + f'    <link rel="alternate" hreflang="x-default" href="{BASE_URL}/{suffix}">\n'
    robots = '    <meta name="robots" content="noindex">\n' if noindex else ""
    ld = f'    <script type="application/ld+json">{jsonld}</script>\n' if jsonld else ""
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
        f'    <meta property="og:image" content="{BASE_URL}/assets/logo.png">\n'
        '    <meta name="twitter:card" content="summary">\n'
        '    <link rel="icon" type="image/png" href="/assets/logo.png">\n'
        f'    <link rel="stylesheet" href="/styles.css?v={stylesheet_version()}">\n'
        f"{ld}"
        "  </head>\n"
        "  <body>\n"
        '    <header class="site-header">\n'
        '      <nav class="navigation page-width">\n'
        f'        <a class="brand" href="{pre}/"><img src="/assets/logo.png" alt="" width="44" height="44"><span>quizjet</span></a>\n'
        f'        <div class="nav-actions"><div class="nav-links"><a href="{pre}/kesfet/">{e(c["discAll"])}</a></div>'
        f"{language_menu(locale, LOCALES, suffix)}"
        f'<a class="button button-small button-light" href="https://t.me/QuizJetBot" target="_blank" rel="noopener noreferrer">'
        f'{e(c["navStart"])} <span aria-hidden="true">↗</span></a></div>\n'
        "      </nav>\n"
        "    </header>\n"
        f'    <main class="discover page-width">\n{body}    </main>\n'
        '    <footer class="site-footer">\n'
        '      <div class="footer-inner page-width">\n'
        f'        <a class="brand footer-brand" href="{pre}/"><img src="/assets/logo.png" alt="" width="36" height="36"><span>quizjet</span></a>\n'
        f'        <nav class="footer-legal"><a href="{legal}/privacy.html">{e(c["footerPrivacy"])}</a>'
        f'<a href="{legal}/terms.html">{e(c["footerTerms"])}</a><a href="{legal}/support.html">{e(c["footerSupport"])}</a></nav>\n'
        "      </div>\n"
        "    </footer>\n"
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


SEARCH_SCRIPT = """    <script>
(function () {
  var input = document.getElementById("q"), count = document.getElementById("count"), none = document.getElementById("none");
  var items = [].slice.call(document.querySelectorAll(".quiz-list li"));
  function fold(s) { return s.toLowerCase().replace(/ı/g, "i").normalize("NFKD").replace(/[\\u0300-\\u036f]/g, ""); }
  input.addEventListener("input", function () {
    var term = fold(input.value.trim()), shown = 0;
    items.forEach(function (li) { var hit = !term || li.getAttribute("data-search").indexOf(term) > -1; li.hidden = !hit; if (hit) shown++; });
    count.textContent = shown;
    none.hidden = shown > 0;
  });
})();
    </script>
"""


def render_catalog(locale: dict[str, object], items: list[dict[str, object]]) -> str:
    c, pre, e = locale["copy"], prefix(locale), html.escape
    rows = "".join(
        f'        <li data-search="{e(fold(str(i["title"]) + " " + str(i["description"])), quote=True)}">'
        f'<a href="{pre}/q/{i["key"]}/"><strong>{e(str(i["title"]))}</strong>'
        + (f'<span>{e(clip(str(i["description"]), 140))}</span>' if i["description"] else "")
        + f"<small>{e(facts(i, c))}</small></a></li>\n"
        for i in items
    )
    body = (
        f'      <h1>{e(c["discTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["discLead"])}</p>\n'
        f'      <label class="sr-only" for="q">{e(c["discSearch"])}</label>\n'
        f'      <input class="discover-search" id="q" type="search" placeholder="{e(c["discSearch"], quote=True)}" autocomplete="off">\n'
        f'      <p class="discover-count">{e(c["discCount"])}: <span id="count">{len(items)}</span></p>\n'
        f'      <ul class="quiz-list">\n{rows}      </ul>\n'
        f'      <p id="none" class="discover-empty" hidden>{e(c["discEmpty"])}</p>\n'
        + SEARCH_SCRIPT
    )
    return shell(locale, f'{c["discTitle"]} — QuizJet', c["discMeta"], "kesfet/", body)


def render_placeholder(locale: dict[str, object]) -> str:
    c, e = locale["copy"], html.escape
    body = (
        f'      <h1>{e(c["discTitle"])}</h1>\n'
        f'      <p class="discover-lead">{e(c["discSoon"])} '
        f'<a href="https://t.me/QuizJetBot">{e(c["playNow"])}</a></p>\n'
    )
    return shell(locale, f'{c["discTitle"]} — QuizJet', c["discSoon"], "kesfet/", body, noindex=True)


def render_quiz(locale: dict[str, object], item: dict[str, object], bot: str) -> str:
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
        "isAccessibleForFree": True,
        "provider": {"@type": "Organization", "name": "QuizJet", "url": BASE_URL + "/"},
    }, ensure_ascii=False).replace("</", "<\\/")
    report = f"mailto:{SUPPORT}?subject=" + "Report%20quiz%20" + key
    back = "→" if locale["dir"] == "rtl" else "←"
    # The handle and the date are left-to-right islands; inside Arabic/Persian text they would flip.
    note = e(c["discGroupNote"]).replace("{bot}", f'<bdi dir="ltr">@{e(bot)}</bdi>')
    body = (
        f'      <p class="quiz-back"><a href="{pre}/kesfet/">{back} {e(c["discAll"])}</a></p>\n'
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
    return shell(locale, f'{title} — {c["discQuizSuffix"]}', meta, suffix, body, jsonld=jsonld)


def catalog_json(items: list[dict[str, object]], bot: str, generated: str) -> str:
    out = [{
        **item,
        "url": f"{BASE_URL}/q/{item['key']}/",
        "startPrivate": f"https://t.me/{bot}?start={item['key']}",
        "startGroup": f"https://t.me/{bot}?startgroup={item['key']}",
    } for item in items]
    return json.dumps({"generatedAtUtc": generated, "botUsername": bot, "count": len(out), "items": out},
                      ensure_ascii=False, indent=1) + "\n"


def sitemap(extra_paths: list[str]) -> str:
    ET.register_namespace("", SITEMAP_NS)
    base = ET.parse(ROOT / "sitemap.xml").getroot()
    urls = [loc.text for loc in base.iter(f"{{{SITEMAP_NS}}}loc")]
    urls += [BASE_URL + path for path in extra_paths]
    urlset = ET.Element(f"{{{SITEMAP_NS}}}urlset")
    for url in urls:
        ET.SubElement(ET.SubElement(urlset, f"{{{SITEMAP_NS}}}url"), f"{{{SITEMAP_NS}}}loc").text = url
    ET.indent(urlset, space="  ")
    return ET.tostring(urlset, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


BAND_RE = re.compile(r'[ \t]*<section class="discover-band[^"]*" id="discover-band">.*?</section>\n\n?', re.DOTALL)


def apply_band(out: Path, count: int | None) -> None:
    """Fill the landing pages' catalog band with the real quiz count, or drop it.

    The committed pages carry the band with a {count} placeholder (translated per language). Only
    this build knows the catalog size, so it is filled here; without a catalog the band is removed
    rather than shown with a placeholder or an invented number.
    """
    for page in out.rglob("index.html"):
        text = page.read_text(encoding="utf-8")
        if 'id="discover-band"' not in text:
            continue
        text = BAND_RE.sub("", text) if count is None else text.replace("{count}", str(count))
        write(page, text)


def publishable(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return parts[0] != "scripts" and not any(part.startswith((".", "_")) for part in parts)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def locale_dir(out: Path, locale: dict[str, object]) -> Path:
    return out / str(locale["route"]) if locale["route"] else out


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

    if export is None:
        apply_band(out, None)
        for locale in LOCALES:
            write(locale_dir(out, locale) / "kesfet" / "index.html", render_placeholder(locale))
        return "Built without the Discover catalog (no export given)."

    bot, items, skipped = load_catalog(export)
    generated = str(json.loads(export.read_text(encoding="utf-8")).get("generatedAtUtc") or "")
    apply_band(out, len(items))
    paths: list[str] = []
    for locale in LOCALES:
        base, pre = locale_dir(out, locale), prefix(locale)
        write(base / "kesfet" / "index.html", render_catalog(locale, items))
        paths.append(f"{pre}/kesfet/")
        for item in items:
            write(base / "q" / str(item["key"]) / "index.html", render_quiz(locale, item, bot))
            paths.append(f"{pre}/q/{item['key']}/")
    write(out / "discover.json", catalog_json(items, bot, generated))
    write(out / "sitemap.xml", sitemap(paths))
    note = f" Skipped {len(skipped)} item(s) with an unusable key or empty title." if skipped else ""
    return f"Built the Discover catalog with {len(items)} quizzes in {len(LOCALES)} languages.{note}"


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
