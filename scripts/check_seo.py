from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://quizjet.app"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
LOCALES = json.loads((ROOT / "_data" / "locales.json").read_text(encoding="utf-8"))
LOCALE_BY_ROUTE = {locale["route"]: locale for locale in LOCALES}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self.title_depth = 0
        self.h1_count = 0
        self.meta: dict[str, list[str]] = {}
        self.links: list[tuple[str, str, str, bool]] = []
        self.anchors: list[str] = []
        self.resources: list[str] = []
        self.ids: set[str] = set()
        self.jsonld: list[str] = []
        self.has_dynamic_copy = False
        self._jsonld_depth = 0
        self._jsonld_parts: list[str] = []
        self._in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if "data-copy" in values or "data-aria-copy" in values:
            self.has_dynamic_copy = True
        if tag == "html":
            self.lang = values.get("lang", "") or ""
        elif tag == "head":
            self._in_head = True
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.meta.setdefault(key.lower(), []).append(values.get("content", "") or "")
        elif tag == "title":
            self.title_depth += 1
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "link":
            rel = (values.get("rel", "") or "").lower()
            self.links.append((rel, values.get("hreflang", "") or "", values.get("href", "") or "", self._in_head))
            if rel in {"icon", "stylesheet"} and values.get("href"):
                self.resources.append(values["href"])
        elif tag == "a" and values.get("href"):
            self.anchors.append(values["href"])
        elif tag == "img" and values.get("src"):
            self.resources.append(values["src"])
        elif tag == "script":
            if values.get("src"):
                self.resources.append(values["src"])
            if (values.get("type", "") or "").lower() == "application/ld+json":
                self._jsonld_depth = 1
                self._jsonld_parts = []
        if values.get("id"):
            self.ids.add(values["id"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
        elif tag == "title" and self.title_depth:
            self.title_depth -= 1
        elif tag == "script" and self._jsonld_depth:
            self.jsonld.append("".join(self._jsonld_parts))
            self._jsonld_depth = 0

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title += data
        if self._jsonld_depth:
            self._jsonld_parts.append(data)


def fail(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path.relative_to(ROOT).as_posix()}: {message}")


def expected_route(path: Path) -> tuple[str, str]:
    relative = path.relative_to(ROOT)
    if relative == Path("index.html"):
        return "/", "en"
    route = relative.parent.as_posix().strip("/").lower()
    locale = LOCALE_BY_ROUTE.get(route)
    return "/" + route + "/", locale["code"] if locale else route


def internal_target(url: str) -> tuple[Path | None, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"", "http", "https"}:
        return None, ""
    host = parsed.netloc.lower()
    if host in {"www.quizjet.app"}:
        return None, "use the canonical apex hostname"
    if host and host != "quizjet.app":
        return None, ""
    if host == "quizjet.app" and parsed.scheme == "http":
        return None, "internal URL must use HTTPS"
    path = unquote(parsed.path)
    if path in {"", "/"}:
        target = ROOT / "index.html"
    else:
        target = ROOT / path.lstrip("/")
        if path.endswith("/"):
            target /= "index.html"
    try:
        target.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None, "path escapes the repository"
    return target, ""


def check() -> list[str]:
    errors: list[str] = []
    if LOCALE_BY_ROUTE.get("", {}).get("code") != "en":
        errors.append("Locale catalog must assign the root URL to English")
    if LOCALE_BY_ROUTE.get("tr", {}).get("code") != "tr":
        errors.append("Locale catalog must assign Turkish to /tr/")
    pages = sorted(ROOT.rglob("index.html"))
    if not pages:
        return ["No index.html pages found"]

    legacy_redirect = ROOT / "en" / "index.html"
    if not legacy_redirect.is_file():
        errors.append("en/index.html: missing redirect from the previous English URL")

    parsers: dict[Path, PageParser] = {}
    routes: dict[str, tuple[str, Path]] = {}
    all_hreflang: dict[Path, dict[str, str]] = {}

    for path in pages:
        if path.resolve() == legacy_redirect.resolve():
            redirect_html = path.read_text(encoding="utf-8")
            if 'http-equiv="refresh" content="0;url=/"' not in redirect_html:
                fail(errors, path, "previous English URL must immediately redirect to the English root")
            if '<meta name="robots" content="noindex">' not in redirect_html:
                fail(errors, path, "legacy redirect must not be indexed as a duplicate page")
            if f'<link rel="canonical" href="{BASE_URL}/">' not in redirect_html:
                fail(errors, path, "legacy English URL must point to the new canonical root")
            continue

        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8"))
        parsers[path] = parser
        route, lang = expected_route(path)
        url = BASE_URL + route
        routes[route] = (url, path)

        if route != "/" and route.strip("/") not in LOCALE_BY_ROUTE:
            fail(errors, path, f"unexpected language route {route!r}")
        if parser.lang != lang:
            fail(errors, path, f"html lang must be {lang!r}, got {parser.lang!r}")
        if not parser.title.strip():
            fail(errors, path, "missing page title")
        if parser.h1_count != 1:
            fail(errors, path, f"expected exactly one h1, found {parser.h1_count}")
        if len(parser.meta.get("description", [])) != 1 or not parser.meta["description"][0].strip():
            fail(errors, path, "expected exactly one non-empty meta description")
        if any("noindex" in value.lower() for value in parser.meta.get("robots", [])):
            fail(errors, path, "production page must not contain noindex")
        if parser.has_dynamic_copy:
            fail(errors, path, "localized content must be present in the static HTML")

        canonicals = [href for rel, _, href, in_head in parser.links if "canonical" in rel.split() and in_head]
        if canonicals != [url]:
            fail(errors, path, f"canonical must be exactly {url!r}, got {canonicals!r}")

        for key, expected in (
            ("og:url", url),
            ("og:title", parser.title.strip()),
            ("og:description", parser.meta.get("description", [""])[0]),
            ("twitter:title", parser.title.strip()),
            ("twitter:description", parser.meta.get("description", [""])[0]),
        ):
            if parser.meta.get(key) != [expected]:
                fail(errors, path, f"{key} must appear once and match page metadata")
        if parser.meta.get("twitter:card") != ["summary_large_image"]:
            fail(errors, path, "expected one twitter:card value")

        alternates: dict[str, str] = {}
        for rel, hreflang, href, in_head in parser.links:
            if "alternate" in rel.split() and hreflang:
                if not in_head:
                    fail(errors, path, "hreflang links must be in the head")
                if hreflang in alternates:
                    fail(errors, path, f"duplicate hreflang value {hreflang!r}")
                alternates[hreflang] = href
        all_hreflang[path] = alternates

        for jsonld in parser.jsonld:
            try:
                json.loads(jsonld)
            except json.JSONDecodeError as exc:
                fail(errors, path, f"invalid JSON-LD: {exc}")
        if "script.js" in parser.resources:
            fail(errors, path, "localized pages must not depend on client-side translation")
        stylesheets = [href for rel, _, href, in_head in parser.links if "stylesheet" in rel.split() and in_head]
        css_version = hashlib.sha256((ROOT / "styles.css").read_bytes()).hexdigest()[:12]
        if stylesheets != [f"/styles.css?v={css_version}"]:
            fail(errors, path, "stylesheet URL must be versioned against the current CSS content")
        page_html = path.read_text(encoding="utf-8")
        current_locale = LOCALE_BY_ROUTE["" if route == "/" else route.strip("/")]
        for locale in LOCALES:
            display_code = str(locale["code"]).upper()
            expected_label = (
                f'<span class="language-code" aria-hidden="true" dir="ltr">{display_code}</span>'
                f'<span class="language-name">{locale["name"]}</span>'
            )
            if expected_label not in page_html:
                fail(errors, path, f"language menu must show the {display_code} language code and native name")
        if page_html.count('aria-current="page"') != 1 or (
            '<span class="language-current" aria-hidden="true">✓</span>' not in page_html
        ):
            fail(errors, path, "language menu must mark exactly the current language with a check")
        current_code = str(current_locale["code"]).upper()
        if f'<span class="language-code" aria-hidden="true" dir="ltr">{current_code}</span>' not in page_html:
            fail(errors, path, "language selector summary must show the current language code")

    expected_alternates = {locale["code"]: BASE_URL + (f'/{locale["route"]}/' if locale["route"] else "/")
                           for locale in LOCALES}
    expected_alternates["x-default"] = BASE_URL + "/"
    expected_routes = {"/" if not locale["route"] else f'/{locale["route"]}/' for locale in LOCALES}
    if set(routes) != expected_routes:
        errors.append(f"Published routes must match the locale catalog: {sorted(expected_routes)!r}")
    for path, alternates in all_hreflang.items():
        if alternates != expected_alternates:
            fail(errors, path, f"hreflang set must match all published language pages: {expected_alternates!r}")
    if len({parser.title.strip() for parser in parsers.values()}) != len(parsers):
        errors.append("Every language page must have a unique title")
    if len({parser.meta.get("description", [""])[0] for parser in parsers.values()}) != len(parsers):
        errors.append("Every language page must have a unique meta description")

    page_by_path = {path.resolve(): parser for path, parser in parsers.items()}
    for path, parser in parsers.items():
        route, _ = expected_route(path)
        base = BASE_URL + route
        linked_routes: set[str] = set()
        for href in parser.anchors + parser.resources:
            target_url = urljoin(base, href)
            parsed_url = urlsplit(target_url)
            if parsed_url.netloc.lower() in {"", "quizjet.app"} and parsed_url.path in routes:
                linked_routes.add(parsed_url.path)
            target, issue = internal_target(target_url)
            if issue:
                fail(errors, path, f"invalid local reference {href!r}: {issue}")
            elif target is not None:
                if not target.is_file():
                    fail(errors, path, f"local reference does not exist: {href!r}")
                elif urlsplit(target_url).fragment and target.resolve() in page_by_path:
                    fragment = urlsplit(target_url).fragment
                    if fragment not in page_by_path[target.resolve()].ids:
                        fail(errors, path, f"local fragment does not exist: {href!r}")
        if set(routes) - linked_routes:
            fail(errors, path, f"missing direct links to language pages: {sorted(set(routes) - linked_routes)!r}")

    sitemap_path = ROOT / "sitemap.xml"
    try:
        sitemap = ET.parse(sitemap_path).getroot()
        if sitemap.tag != f"{{{SITEMAP_NAMESPACE}}}urlset":
            errors.append("sitemap.xml: invalid sitemap namespace or root element")
        sitemap_urls = [
            item.findtext(f"{{{SITEMAP_NAMESPACE}}}loc", default="")
            for item in sitemap.findall(f"{{{SITEMAP_NAMESPACE}}}url")
        ]
        canonical_urls = {url for url, _ in routes.values()}
        if len(sitemap_urls) != len(set(sitemap_urls)):
            errors.append("sitemap.xml: duplicate URL entries")
        if set(sitemap_urls) != canonical_urls:
            errors.append(f"sitemap.xml: URLs must exactly match canonical pages ({sorted(canonical_urls)!r})")
    except (OSError, ET.ParseError) as exc:
        errors.append(f"sitemap.xml: {exc}")

    robots_path = ROOT / "robots.txt"
    try:
        robots = robots_path.read_text(encoding="utf-8")
        if f"Sitemap: {BASE_URL}/sitemap.xml" not in robots:
            errors.append("robots.txt: missing sitemap directive")
    except OSError as exc:
        errors.append(f"robots.txt: {exc}")

    cname_path = ROOT / "CNAME"
    try:
        if cname_path.read_text(encoding="utf-8").strip() != "quizjet.app":
            errors.append("CNAME: expected quizjet.app")
    except OSError as exc:
        errors.append(f"CNAME: {exc}")

    return errors


if __name__ == "__main__":
    problems = check()
    if problems:
        print("\n".join(f"ERROR: {problem}" for problem in problems))
        sys.exit(1)
    print("SEO checks passed.")
