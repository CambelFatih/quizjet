"""Build the deployable site: the committed static pages plus the public Discover catalog.

    python scripts/build_discover.py --out /tmp/site [--export discover-export.json]

The export comes from the bot (GET https://api.quizjet.app/public/discover/export, see
quizjet-bot docs/decisions/2026-11-02-kesfet-web-yayini.md). Without --export (the token is not
configured yet) /kesfet/ is a "coming soon" page and the sitemap only lists the static pages.
A truncated or malformed export fails the build so a partial catalog is never published.
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

from build_locales import stylesheet_version

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://quizjet.app"
BLOCKLIST_PATH = ROOT / "_data" / "discover-blocklist.json"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
# Telegram's deep-link payload alphabet; a key outside it cannot start the quiz anyway.
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BOT_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
SUPPORT = "support@quizjet.app"


def fold(text: str) -> str:
    """Case- and accent-insensitive search key; the inline script applies the same folding."""
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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


def shell(title: str, description: str, path: str, body: str, *, noindex: bool = False, jsonld: str = "") -> str:
    url = BASE_URL + path
    e = lambda value: html.escape(value, quote=True)  # noqa: E731
    robots = '    <meta name="robots" content="noindex">\n' if noindex else ""
    ld = f'    <script type="application/ld+json">{jsonld}</script>\n' if jsonld else ""
    return (
        "<!doctype html>\n"
        '<html lang="en" dir="ltr">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '    <meta name="theme-color" content="#101f37">\n'
        f"    <title>{e(title)}</title>\n"
        f'    <meta name="description" content="{e(description)}">\n'
        f"{robots}"
        f'    <link rel="canonical" href="{url}">\n'
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
        '        <a class="brand" href="/"><img src="/assets/logo.png" alt="" width="44" height="44"><span>quizjet</span></a>\n'
        '        <div class="nav-actions"><div class="nav-links"><a href="/kesfet/">All quizzes</a></div>'
        '<a class="button button-small button-light" href="https://t.me/QuizJetBot" target="_blank" rel="noopener noreferrer">Open bot <span aria-hidden="true">↗</span></a></div>\n'
        "      </nav>\n"
        "    </header>\n"
        f'    <main class="discover page-width">\n{body}    </main>\n'
        '    <footer class="site-footer">\n'
        '      <div class="footer-inner page-width">\n'
        '        <a class="brand footer-brand" href="/"><img src="/assets/logo.png" alt="" width="36" height="36"><span>quizjet</span></a>\n'
        '        <nav class="footer-legal"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a><a href="/support.html">Support</a></nav>\n'
        "      </div>\n"
        "    </footer>\n"
        "  </body>\n"
        "</html>\n"
    )


def facts(item: dict[str, object]) -> str:
    parts = [
        plural(int(item["questionCount"]), "question", "questions"),
        "made with AI" if item["aiGenerated"] else "made by hand",
        plural(int(item["plays"]), "play", "plays"),
    ]
    return " · ".join(parts)


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


def render_catalog(items: list[dict[str, object]]) -> str:
    e = html.escape
    rows = "".join(
        f'        <li data-search="{e(fold(str(i["title"]) + " " + str(i["description"])), quote=True)}">'
        f'<a href="/q/{i["key"]}/"><strong>{e(str(i["title"]))}</strong>'
        + (f'<span>{e(clip(str(i["description"]), 140))}</span>' if i["description"] else "")
        + f"<small>{e(facts(i))}</small></a></li>\n"
        for i in items
    )
    body = (
        "      <h1>Discover quizzes</h1>\n"
        '      <p class="discover-lead">Quizzes their creators made public in QuizJet. Start one in a private chat '
        "or in your Telegram group.</p>\n"
        '      <label class="sr-only" for="q">Search quizzes</label>\n'
        '      <input class="discover-search" id="q" type="search" placeholder="Search quizzes" autocomplete="off">\n'
        f'      <p class="discover-count"><span id="count">{len(items)}</span> quizzes</p>\n'
        f'      <ul class="quiz-list">\n{rows}      </ul>\n'
        '      <p id="none" class="discover-empty" hidden>No quiz matches your search.</p>\n'
        + SEARCH_SCRIPT
    )
    return shell(
        "Discover quizzes — QuizJet",
        f"Browse {len(items)} public quizzes made with QuizJet and start any of them on Telegram, "
        "in a private chat or in your group.",
        "/kesfet/",
        body,
    )


def render_placeholder() -> str:
    body = (
        "      <h1>Discover quizzes</h1>\n"
        '      <p class="discover-lead">The public quiz catalog will appear here soon. Meanwhile, '
        '<a href="https://t.me/QuizJetBot">open QuizJet on Telegram</a>.</p>\n'
    )
    return shell("Discover quizzes — QuizJet", "The public QuizJet quiz catalog is coming soon.", "/kesfet/", body, noindex=True)


def render_quiz(item: dict[str, object], bot: str) -> str:
    e = html.escape
    key, title, description = str(item["key"]), str(item["title"]), str(item["description"])
    created = str(item["createdAtUtc"])[:10]
    start = f"https://t.me/{bot}?start={key}"
    group = f"https://t.me/{bot}?startgroup={key}"
    meta = clip(description, 155) if description else (
        f"A {int(item['questionCount'])}-question quiz you can play on Telegram with QuizJet."
    )
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Quiz",
        "name": title,
        **({"description": description} if description else {}),
        "url": f"{BASE_URL}/q/{key}/",
        **({"dateCreated": created} if created else {}),
        "isAccessibleForFree": True,
        "provider": {"@type": "Organization", "name": "QuizJet", "url": BASE_URL + "/"},
    }, ensure_ascii=False).replace("</", "<\\/")
    report = f"mailto:{SUPPORT}?subject=" + "Report%20quiz%20" + key
    body = (
        '      <p class="quiz-back"><a href="/kesfet/">← All quizzes</a></p>\n'
        f"      <h1>{e(title)}</h1>\n"
        + (f'      <p class="quiz-description">{e(description)}</p>\n' if description else "")
        + f'      <p class="quiz-facts">{e(facts(item))} · '
        + plural(int(item["participants"]), "player", "players")
        + (f' · published <time datetime="{e(created)}">{e(created)}</time>' if created else "")
        + "</p>\n"
        '      <div class="quiz-actions">\n'
        f'        <a class="button button-primary" href="{e(start, quote=True)}">Start in a private chat <span aria-hidden="true">↗</span></a>\n'
        f'        <a class="button button-dark" href="{e(group, quote=True)}">Start in a group <span aria-hidden="true">↗</span></a>\n'
        "      </div>\n"
        f'      <p class="quiz-note">In a group, only group admins can start a quiz. If it does not start, make @{e(bot)} '
        "an admin of the group and try again.</p>\n"
        f'      <p class="quiz-report"><a href="{e(report, quote=True)}">Report this quiz</a></p>\n'
    )
    return shell(f"{title} — QuizJet quiz", meta, f"/q/{key}/", body, jsonld=jsonld)


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


def publishable(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return parts[0] != "scripts" and not any(part.startswith((".", "_")) for part in parts)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


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
        write(out / "kesfet" / "index.html", render_placeholder())
        return "Built without the Discover catalog (no export given)."

    bot, items, skipped = load_catalog(export)
    generated = str(json.loads(export.read_text(encoding="utf-8")).get("generatedAtUtc") or "")
    write(out / "kesfet" / "index.html", render_catalog(items))
    for item in items:
        write(out / "q" / str(item["key"]) / "index.html", render_quiz(item, bot))
    write(out / "discover.json", catalog_json(items, bot, generated))
    write(out / "sitemap.xml", sitemap(["/kesfet/"] + [f"/q/{item['key']}/" for item in items]))
    note = f" Skipped {len(skipped)} item(s) with an unusable key or empty title." if skipped else ""
    return f"Built the Discover catalog with {len(items)} quizzes.{note}"


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
