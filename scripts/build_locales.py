from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "_includes" / "index.template.html"
LOCALES_PATH = ROOT / "_data" / "locales.json"
LEGAL_META_PATH = ROOT / "_data" / "legal.json"
LEGAL_DIR = ROOT / "_legal"
BASE_URL = "https://quizjet.app"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
LOCALES_BY_CODE = {locale["code"]: locale for locale in json.loads(LOCALES_PATH.read_text(encoding="utf-8"))}
ICON_LINKS = (
    '    <link rel="icon" type="image/png" href="/assets/logo-96.png">\n'
    '    <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">\n'
    '    <link rel="manifest" href="/manifest.webmanifest">\n'
)
# The two faces every page paints first (body text, headings); the other subsets load by unicode-range when needed.
FONT_PRELOADS = "".join(
    f'    <link rel="preload" href="/assets/fonts/{name}" as="font" type="font/woff2" crossorigin>\n'
    for name in ("dm-sans-latin.woff2", "manrope-700-latin.woff2")
)
# Deep-link payloads the bot maps to commands (quizjet-bot MiniAppCommandDeepLink.Map). Any other ?start= payload is
# looked up as a quiz key and answers "Quiz not found."; ?startgroup= treats its payload the same way. check_seo pins this.
START_PAYLOADS = {"cmd_newquiz", "cmd_aiquiz", "cmd_premium"}


def head_links() -> str:
    return ICON_LINKS + FONT_PRELOADS


# The phone menu is a <details> that needs no script to open; these lines close it when a link, the backdrop or
# Escape is used, so an in-page link (#features) does not leave the panel over the section it scrolled to.
NAV_MENU_SCRIPT = (
    "    <script>document.querySelectorAll('.nav-menu').forEach(function (m) {"
    " m.addEventListener('click', function (e) { if (e.target === m || e.target.closest('a')) m.removeAttribute('open'); }); });"
    " addEventListener('keydown', function (e) { if (e.key === 'Escape')"
    " document.querySelectorAll('.nav-menu[open]').forEach(function (m) { m.removeAttribute('open'); }); });</script>\n"
)


def absolute_position(line_offsets: list[int], position: tuple[int, int]) -> int:
    line, column = position
    return line_offsets[line - 1] + column


class CopyReplacer(HTMLParser):
    def __init__(self, source: str, copy: dict[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.copy = copy
        self.line_offsets = [0]
        for match in re.finditer("\n", source):
            self.line_offsets.append(match.end())
        self.stack: list[dict[str, object]] = []
        self.content_edits: list[tuple[int, int, str]] = []
        self.tag_edits: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        position = absolute_position(self.line_offsets, self.getpos())
        raw_tag = self.get_starttag_text()
        values = dict(attrs)
        copy_key = values.get("data-copy")
        aria_key = values.get("data-aria-copy")

        updated_tag = raw_tag
        if aria_key:
            if aria_key not in self.copy:
                raise ValueError(f"Missing translation for aria-copy key {aria_key!r}")
            label = html.escape(re.sub(r"<[^>]*>", "", self.copy[aria_key]), quote=True)
            if re.search(r'\baria-label="[^"]*"', updated_tag):
                updated_tag = re.sub(r'\baria-label="[^"]*"', f'aria-label="{label}"', updated_tag)
            else:
                updated_tag = updated_tag[:-1] + f' aria-label="{label}">'
        updated_tag = re.sub(r'\sdata-(?:aria-copy|copy)="[^"]*"', "", updated_tag)
        if updated_tag != raw_tag:
            self.tag_edits.append((position, position + len(raw_tag), updated_tag))

        marker = None
        if copy_key:
            if copy_key not in self.copy:
                raise ValueError(f"Missing translation for copy key {copy_key!r}")
            marker = {
                "tag": tag,
                "start": position + len(raw_tag),
                "key": copy_key,
            }

        if tag not in VOID_ELEMENTS:
            self.stack.append({"tag": tag, "marker": marker})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        end = absolute_position(self.line_offsets, self.getpos())
        matching_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1)
             if self.stack[index]["tag"] == tag),
            None,
        )
        if matching_index is None:
            return
        closed = self.stack[matching_index:]
        del self.stack[matching_index:]
        for element in closed:
            marker = element["marker"]
            if marker is not None:
                start = marker["start"]
                key = marker["key"]
                self.content_edits.append((start, end, self.copy[key]))

    def localized_source(self) -> str:
        self.feed(self.source)
        edits = self.content_edits[:]
        replaced_ranges = [(start, end) for start, end, _ in self.content_edits]
        for start, end, replacement in self.tag_edits:
            if not any(range_start <= start < range_end for range_start, range_end in replaced_ranges):
                edits.append((start, end, replacement))
        result = self.source
        for start, end, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[end:]
        return result


def replace_meta(html_text: str, attribute: str, name: str, value: str) -> str:
    pattern = re.compile(
        rf'(<meta\b(?=[^>]*\b{attribute}="{re.escape(name)}")[^>]*\bcontent=")[^"]*(")',
        re.IGNORECASE,
    )
    updated, count = pattern.subn(lambda match: match.group(1) + html.escape(value, quote=True) + match.group(2), html_text)
    if count != 1:
        raise ValueError(f"Expected exactly one {attribute}={name!r} metadata tag, found {count}")
    return updated


def language_menu(locale: dict[str, object], locales: list[dict[str, object]], suffix: str = "") -> str:
    """Language switcher; ``suffix`` keeps the visitor on the same page in the other language (e.g. "discover/")."""
    copy = locale["copy"]
    code = locale["code"]
    current_name = html.escape(str(locale["name"]))
    current_code = html.escape(str(code).upper())
    summary = (
        f'<details class="language-menu"><summary aria-label="{html.escape(copy["languageLabel"], quote=True)}">'
        f'<span class="language-code" aria-hidden="true" dir="ltr">{current_code}</span><span>{current_name}</span>'
        '<span class="language-menu-chevron" aria-hidden="true">⌄</span></summary>'
    )
    links = []
    for item in locales:
        active = ' aria-current="page"' if item["code"] == code else ""
        href = (f'/{item["route"]}/' if item["route"] else "/") + suffix
        display_code = html.escape(str(item["code"]).upper())
        selected = '<span class="language-current" aria-hidden="true">✓</span>' if active else ""
        links.append(
            f'<a href="{href}" lang="{html.escape(item["code"], quote=True)}"{active}>'
            f'<span class="language-code" aria-hidden="true" dir="ltr">{display_code}</span>'
            f'<span class="language-name">{html.escape(item["name"])}</span>{selected}</a>'
        )
    return (
        summary
        + f'<nav class="language-picker language-menu-list" aria-label="{html.escape(copy["languageLabel"], quote=True)}">'
        + "".join(links)
        + "</nav></details>"
    )


def language_grid(locales: list[dict[str, object]], current: str) -> str:
    """The "pick your language" grid: one link per language page (it used to be plain divs that did nothing on click)."""
    chips = []
    for locale in locales:
        code = html.escape(locale["code"], quote=True)
        href = f'/{locale["route"]}/' if locale["route"] else "/"
        active = ' aria-current="page"' if locale["code"] == current else ""
        chips.append(
            # dir on the name only: every chip follows the page's direction, so an Arabic chip on a left-to-right page
            # lines up with the others instead of hugging the right edge.
            f'<a class="language-chip" href="{href}" lang="{code}" hreflang="{code}"{active}>'
            f'<span class="language-flag" aria-hidden="true">{locale["flag"]}</span><span dir="{locale["dir"]}">{html.escape(locale["name"])}</span></a>'
        )
    return "\n          ".join(chips)


def landing_jsonld(locale: dict[str, object], description: str) -> str:
    """WebSite + Organization + SoftwareApplication for a landing page; check_seo requires exactly this block."""
    url = BASE_URL + (f'/{locale["route"]}/' if locale["route"] else "/")
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": f"{BASE_URL}/#website", "url": f"{BASE_URL}/", "name": "QuizJet",
             "inLanguage": locale["code"], "publisher": {"@id": f"{BASE_URL}/#organization"}},
            {"@type": "Organization", "@id": f"{BASE_URL}/#organization", "name": "QuizJet", "url": f"{BASE_URL}/",
             "logo": f"{BASE_URL}/assets/logo.png", "email": "support@quizjet.app", "sameAs": ["https://t.me/QuizJetBot"]},
            {"@type": "SoftwareApplication", "name": "QuizJet", "url": url, "applicationCategory": "EducationalApplication",
             "operatingSystem": "Telegram", "inLanguage": locale["code"], "description": description,
             "isAccessibleForFree": True, "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
             "publisher": {"@id": f"{BASE_URL}/#organization"}},
        ],
    }
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def stylesheet_version() -> str:
    # Line endings are normalised so a Windows checkout (CRLF) and Linux CI (LF) agree on the hash.
    css = (ROOT / "styles.css").read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(css).hexdigest()[:12]


def legal_url(route: str, page: str) -> str:
    return f"{BASE_URL}/{route + '/' if route else ''}{page}.html"


def render_legal_page(meta: dict[str, object], code: str, page: str, body: str) -> str:
    language = meta["languages"][code]
    route = language["route"]
    info = language["pages"][page]
    home = f"/{route}/" if route else "/"
    title = f'{info["title"]} — QuizJet'
    ui = LOCALES_BY_CODE[code]["copy"]
    alternates = "".join(
        f'    <link rel="alternate" hreflang="{other}" href="{legal_url(data["route"], page)}">\n'
        for other, data in meta["languages"].items()
    )
    alternates += f'    <link rel="alternate" hreflang="x-default" href="{legal_url("", page)}">\n'
    switch = "".join(
        f'<a href="{legal_url(data["route"], page).removeprefix(BASE_URL)}" lang="{other}" hreflang="{other}">{other.upper()}</a>'
        for other, data in meta["languages"].items() if other != code
    )
    links = ""
    for name in meta["pages"]:
        current = ' aria-current="page"' if name == page else ""
        links += (
            f'<a href="{legal_url(route, name).removeprefix(BASE_URL)}"{current}>'
            f'{html.escape(language["nav"][name])}</a>'
        )
    return (
        "<!doctype html>\n"
        f'<html lang="{code}" dir="ltr">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '    <meta name="theme-color" content="#101f37">\n'
        f"    <title>{html.escape(title)}</title>\n"
        f'    <meta name="description" content="{html.escape(info["description"], quote=True)}">\n'
        f'    <link rel="canonical" href="{legal_url(route, page)}">\n'
        f"{alternates}"
        f"{head_links()}"
        f'    <link rel="stylesheet" href="/styles.css?v={stylesheet_version()}">\n'
        "  </head>\n"
        "  <body>\n"
        f'    <a class="skip-link" href="#top">{html.escape(ui["skipToContent"])}</a>\n'
        '    <header class="site-header">\n'
        f'      <nav class="navigation page-width" aria-label="{html.escape(ui["navMainLabel"], quote=True)}">\n'
        f'        <a class="brand" href="{home}"><img src="/assets/logo-96.png" alt="" width="44" height="44"><span>quizjet</span></a>\n'
        f'        <div class="nav-actions legal-switch">{switch}</div>\n'
        "      </nav>\n"
        "    </header>\n"
        '    <main class="legal page-width" id="top">\n'
        f'      <p class="legal-updated">{html.escape(language["updatedLabel"])}: '
        f'<time datetime="{meta["updated"]}">{meta["updated"]}</time></p>\n'
        f"{body.rstrip()}\n"
        "    </main>\n"
        '    <footer class="site-footer">\n'
        '      <div class="footer-inner page-width">\n'
        f'        <a class="brand footer-brand" href="{home}"><img src="/assets/logo-96.png" alt="" width="36" height="36"><span>quizjet</span></a>\n'
        f'        <nav class="footer-legal" aria-label="{html.escape(ui["footerLegalLabel"], quote=True)}">{links}</nav>\n'
        "      </div>\n"
        "    </footer>\n"
        "  </body>\n"
        "</html>\n"
    )


def legal_outputs(meta: dict[str, object]) -> dict[Path, str]:
    generated = {}
    for code, language in meta["languages"].items():
        route = language["route"]
        for page in meta["pages"]:
            body = (LEGAL_DIR / code / f"{page}.html").read_text(encoding="utf-8")
            target = (ROOT / route if route else ROOT) / f"{page}.html"
            generated[target] = render_legal_page(meta, code, page, body)
    return generated


def render_page(template: str, locale: dict[str, object], locales: list[dict[str, object]], legal_routes: set[str]) -> str:
    copy = locale["copy"]
    code = locale["code"]
    route = locale["route"]
    # <title> and the description are search copy of their own (seoTitle/seoDescription), not the hero slogan;
    # {languages} is the live count so the strings never carry a number that drifts.
    languages = str(len(locales))
    title = copy["seoTitle"].replace("{languages}", languages)
    description = copy["seoDescription"].replace("{languages}", languages)

    page = CopyReplacer(template, copy).localized_source()
    page = re.sub(r'<html lang="[^"]*">', f'<html lang="{html.escape(code, quote=True)}" dir="{locale["dir"]}">', page, count=1)
    page = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", page, count=1, flags=re.DOTALL)
    page = replace_meta(page, "name", "description", description)
    page = replace_meta(page, "property", "og:locale", locale["ogLocale"])
    page = replace_meta(page, "property", "og:title", title)
    page = replace_meta(page, "property", "og:description", description)
    page = replace_meta(page, "property", "og:url", BASE_URL + (f"/{route}/" if route else "/"))
    page = replace_meta(page, "name", "twitter:title", title)
    page = replace_meta(page, "name", "twitter:description", description)

    canonical = BASE_URL + (f"/{route}/" if route else "/")
    page = re.sub(
        r'<link rel="canonical" href="[^"]*">',
        f'<link rel="canonical" href="{canonical}">',
        page,
        count=1,
    )
    page = re.sub(r'^[ \t]*<link rel="alternate" hreflang="[^"]+" href="[^"]*">[ \t]*(?:\r?\n|$)', "", page, flags=re.MULTILINE)
    alternates = "".join(
        f'    <link rel="alternate" hreflang="{html.escape(item["code"], quote=True)}" href="{BASE_URL}{"/" + item["route"] + "/" if item["route"] else "/"}">\n'
        for item in locales
    )
    alternates += f'    <link rel="alternate" hreflang="x-default" href="{BASE_URL}/">\n'
    page = page.replace(f'<link rel="canonical" href="{canonical}">\n', f'<link rel="canonical" href="{canonical}">\n{alternates}', 1)
    page = page.replace(
        '    <meta property="og:locale"',
        f'    <script type="application/ld+json">{landing_jsonld(locale, description)}</script>\n    <meta property="og:locale"',
        1,
    )

    page = re.sub(
        r'<nav class="language-picker"[^>]*>.*?</nav>',
        language_menu(locale, locales),
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = re.sub(
        r'(<nav class="language-grid" id="language-grid"[^>]*>).*?(</nav>)',
        lambda match: match.group(1) + "\n          " + language_grid(locales, code) + "\n        " + match.group(2),
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = re.sub(
        r'(<nav class="language-grid" id="language-grid"[^>]*aria-label=")[^"]*(")',
        lambda match: match.group(1) + html.escape(copy["languageTitle"], quote=True) + match.group(2),
        page,
        count=1,
    )
    page = page.replace(">18</strong>", f'>{len(locales)}</strong>', 1)
    page = re.sub(
        r'(<span id="floating-language">).*?(</span>)',
        lambda match: match.group(1) + html.escape(copy["countLabel"].replace("{count}", str(len(locales)))) + match.group(2),
        page,
        count=1,
        flags=re.DOTALL,
    )
    # Root too: a relative favicon breaks wherever the home page is read from another base (crawlers, /index.html).
    page = re.sub(r'((?:href|src)=")(assets/|styles\.css)', r"\1/\2", page)
    if route:
        # Discover and Books pages exist in every language; keep the visitor in the language they chose.
        page = page.replace('href="/discover/"', f'href="/{route}/discover/"')
        page = page.replace('href="/books/"', f'href="/{route}/books/"')
        page = page.replace('href="/news/"', f'href="/{route}/news/"')
    if route in legal_routes:
        page = re.sub(r'href="/(privacy|terms|support)\.html"', rf'href="/{route}/\1.html"', page)
    elif route:
        # the legal pages exist in English and Turkish only: the other languages link the English text and say so
        page = re.sub(r'(<a href="/(?:privacy|terms|support)\.html">[^<]*)(</a>)', r"\1 (EN)\2", page)
    page = page.replace("  </body>", NAV_MENU_SCRIPT + "  </body>", 1)
    css_version = stylesheet_version()
    page = re.sub(
        r'(<link rel="stylesheet" href=")/?styles\.css(?:\?v=[^"]*)?(")',
        rf'\1/styles.css?v={css_version}\2',
        page,
        count=1,
    )
    return page


def sitemap_content(locales: list[dict[str, object]]) -> str:
    ET.register_namespace("", SITEMAP_NAMESPACE)
    urlset = ET.Element(f"{{{SITEMAP_NAMESPACE}}}urlset")
    for locale in locales:
        route = locale["route"]
        url = BASE_URL + (f"/{route}/" if route else "/")
        entry = ET.SubElement(urlset, f"{{{SITEMAP_NAMESPACE}}}url")
        ET.SubElement(entry, f"{{{SITEMAP_NAMESPACE}}}loc").text = url
    ET.indent(urlset, space="  ")
    return ET.tostring(urlset, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def legacy_redirect_page(target: str, canonical: str = BASE_URL + "/") -> str:
    """A page that only forwards to ``target``: the old English URL (/en/) and the Turkish path names the site launched with."""
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '  <head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="robots" content="noindex">\n'
        f'    <link rel="canonical" href="{canonical}">\n'
        f'    <meta http-equiv="refresh" content="0;url={target}">\n'
        '    <title>QuizJet page moved</title>\n'
        '  </head>\n'
        '  <body>\n'
        f'    <p>This page has moved. <a href="{target}">Continue to QuizJet</a>.</p>\n'
        '  </body>\n'
        '</html>\n'
    )


NOT_FOUND_SCRIPT = """(function () {
  // A quiz has one page, in its own language (2026-11-08). Its old copy under another prefix (/de/q/<key>/) forwards
  // to /q/<key>/, which is the quiz or a redirect to it; a quiz gone from Discover stays here.
  var p = location.pathname.split("/");
  if (p[1] && p[1] !== "q" && p[2] === "q" && /^[A-Za-z0-9_-]+$/.test(p[3] || "")) { location.replace("/q/" + p[3] + "/"); return; }
  var seg = location.pathname.split("/")[1] || "", all = JSON.parse(document.getElementById("nf-i18n").textContent), t = all[seg];
  if (!t) return;
  var pre = "/" + seg, root = document.documentElement;
  root.lang = t.lang; root.dir = t.dir; document.title = t.title + " — QuizJet";
  document.getElementById("nf-title").textContent = t.title;
  document.getElementById("nf-body").textContent = t.body;
  [["nf-quizzes", "quizzes", "/discover/"], ["nf-books", "books", "/books/"], ["nf-news", "news", "/news/"]].forEach(function (x) {
    var a = document.getElementById(x[0]); a.textContent = t[x[1]]; a.href = pre + x[2];
  });
})();"""


def not_found_page(locales: list[dict[str, object]]) -> str:
    """GitHub Pages serves one /404.html for every missing path: a quiz taken out of Discover, a mistyped URL.

    The HTML is English; a few lines of script switch it to the language of the path prefix (/tr/q/gone/ → Turkish,
    links kept in that language). Without script it stays English, which is what every visitor got before."""
    en = locales[0]
    c = en["copy"]
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    i18n = {
        str(locale["route"]): {"lang": locale["code"], "dir": locale["dir"], "title": locale["copy"]["nfTitle"],
                               "body": locale["copy"]["nfBody"], "quizzes": locale["copy"]["browseQuizzes"],
                               "books": locale["copy"]["bkBrowse"], "news": locale["copy"]["navNews"]}
        for locale in locales if locale["route"]
    }
    blob = json.dumps(i18n, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        "<!doctype html>\n"
        f'<html lang="{e(en["code"])}" dir="{e(en["dir"])}">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '    <meta name="theme-color" content="#101f37">\n'
        '    <meta name="robots" content="noindex">\n'
        f'    <title>{e(c["nfTitle"])} — QuizJet</title>\n'
        f"{head_links()}"
        f'    <link rel="stylesheet" href="/styles.css?v={stylesheet_version()}">\n'
        "  </head>\n"
        "  <body>\n"
        '    <header class="site-header">\n'
        f'      <nav class="navigation page-width" aria-label="{e(c["navMainLabel"])}">\n'
        '        <a class="brand" href="/"><img src="/assets/logo-96.png" alt="" width="44" height="44"><span>quizjet</span></a>\n'
        "      </nav>\n"
        "    </header>\n"
        '    <main class="discover page-width" id="top">\n'
        f'      <h1 id="nf-title">{e(c["nfTitle"])}</h1>\n'
        f'      <p class="discover-lead" id="nf-body">{e(c["nfBody"])}</p>\n'
        '      <div class="quiz-actions">\n'
        f'        <a class="button button-dark" id="nf-quizzes" href="/discover/">{e(c["browseQuizzes"])}</a>\n'
        f'        <a class="button button-light" id="nf-books" href="/books/">{e(c["bkBrowse"])}</a>\n'
        f'        <a class="button button-light" id="nf-news" href="/news/">{e(c["navNews"])}</a>\n'
        "      </div>\n"
        "    </main>\n"
        f'    <script type="application/json" id="nf-i18n">{blob}</script>\n'
        f"    <script>{NOT_FOUND_SCRIPT}</script>\n"
        "  </body>\n"
        "</html>\n"
    )


def outputs(template: str, locales: list[dict[str, object]], legal_meta: dict[str, object]) -> dict[Path, str]:
    legal_routes = {language["route"] for language in legal_meta["languages"].values() if language["route"]}
    generated = {}
    for locale in locales:
        target = ROOT / locale["route"] / "index.html" if locale["route"] else ROOT / "index.html"
        generated[target] = render_page(template, locale, locales, legal_routes)
    generated[ROOT / "en" / "index.html"] = legacy_redirect_page("/")
    generated[ROOT / "404.html"] = not_found_page(locales)
    generated[ROOT / "sitemap.xml"] = sitemap_content(locales)
    generated.update(legal_outputs(legal_meta))
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the localized static Quizjet pages.")
    parser.add_argument("--check", action="store_true", help="fail if generated pages are out of date")
    args = parser.parse_args()

    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        locales = json.loads(LOCALES_PATH.read_text(encoding="utf-8"))
        if not any(locale["code"] == "en" and locale["route"] == "" for locale in locales):
            raise ValueError("English must own the root URL")
        if not any(locale["code"] == "tr" and locale["route"] == "tr" for locale in locales):
            raise ValueError("Turkish must use the /tr/ route")
        if len({locale["route"] for locale in locales}) != len(locales):
            raise ValueError("Locale routes must be unique")

        expected_keys = set(locales[0]["copy"])
        if any(set(locale["copy"]) != expected_keys for locale in locales):
            raise ValueError("Every locale must provide the same translated fields")

        legal_meta = json.loads(LEGAL_META_PATH.read_text(encoding="utf-8"))
        generated = outputs(template, locales, legal_meta)
        errors = []
        index_pages = {path for path in generated if path.name == "index.html"}
        current_pages = set(ROOT.glob("*/index.html")) | {ROOT / "index.html"}
        if args.check and current_pages != index_pages:
            missing = sorted(path.relative_to(ROOT).as_posix() for path in index_pages - current_pages)
            stale = sorted(path.relative_to(ROOT).as_posix() for path in current_pages - index_pages)
            if missing:
                errors.append(f"Missing generated pages: {', '.join(missing)}")
            if stale:
                errors.append(f"Unexpected locale pages: {', '.join(stale)}")

        for path, content in generated.items():
            if args.check:
                if not path.is_file():
                    errors.append(f"Missing generated file: {path.relative_to(ROOT).as_posix()}")
                elif path.read_text(encoding="utf-8") != content:
                    errors.append(f"Generated file is out of date: {path.relative_to(ROOT).as_posix()}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")

        if errors:
            print("\n".join(f"ERROR: {error}" for error in errors))
            return 1
        print(f"Locale generation {'verified' if args.check else 'completed'} for {len(locales)} languages.")
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
