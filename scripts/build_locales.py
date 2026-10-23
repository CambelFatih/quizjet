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
BASE_URL = "https://quizjet.app"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


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


def language_menu(locale: dict[str, object], locales: list[dict[str, object]]) -> str:
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
        href = f'/{item["route"]}/' if item["route"] else "/"
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


def language_grid(locales: list[dict[str, object]]) -> str:
    chips = []
    for locale in locales:
        direction = locale["dir"]
        code = html.escape(locale["code"], quote=True)
        name = html.escape(locale["name"])
        chips.append(
            f'<div class="language-chip" lang="{code}" dir="{direction}">'
            f'<span class="language-flag" aria-hidden="true">{locale["flag"]}</span><span>{name}</span></div>'
        )
    return "\n          ".join(chips)


def render_page(template: str, locale: dict[str, object], locales: list[dict[str, object]]) -> str:
    copy = locale["copy"]
    code = locale["code"]
    route = locale["route"]
    title_text = html.unescape(re.sub(r"<[^>]*>", " ", copy["heroTitle"]))
    title_text = " ".join(title_text.split())
    title = f"Quizjet — {title_text}"
    description = copy["heroLead"]

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

    page = re.sub(
        r'<nav class="language-picker"[^>]*>.*?</nav>',
        language_menu(locale, locales),
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = re.sub(
        r'(<div class="language-grid" id="language-grid"[^>]*>).*?(</div>)',
        lambda match: match.group(1) + "\n          " + language_grid(locales) + "\n        " + match.group(2),
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = re.sub(
        r'(<div class="language-grid" id="language-grid"[^>]*aria-label=")[^"]*(")',
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
    page = re.sub(r'\s*<script src="script\.js" defer></script>', "", page)
    if route:
        page = re.sub(r'((?:href|src)=")(assets/|styles\.css)', r"\1/\2", page)
    css_version = hashlib.sha256((ROOT / "styles.css").read_bytes()).hexdigest()[:12]
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


def legacy_redirect_page(target: str) -> str:
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '  <head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="robots" content="noindex">\n'
        f'    <link rel="canonical" href="{BASE_URL}/">\n'
        f'    <meta http-equiv="refresh" content="0;url={target}">\n'
        '    <title>Quizjet page moved</title>\n'
        '  </head>\n'
        '  <body>\n'
        f'    <p>This page has moved. <a href="{target}">Continue to Quizjet</a>.</p>\n'
        '  </body>\n'
        '</html>\n'
    )


def outputs(template: str, locales: list[dict[str, object]]) -> dict[Path, str]:
    generated = {}
    for locale in locales:
        target = ROOT / locale["route"] / "index.html" if locale["route"] else ROOT / "index.html"
        generated[target] = render_page(template, locale, locales)
    generated[ROOT / "en" / "index.html"] = legacy_redirect_page("/")
    generated[ROOT / "sitemap.xml"] = sitemap_content(locales)
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

        generated = outputs(template, locales)
        errors = []
        generated_paths = set(generated)
        current_pages = set(ROOT.glob("*/index.html")) | {ROOT / "index.html"}
        if args.check and current_pages != generated_paths - {ROOT / "sitemap.xml"}:
            missing = sorted(path.relative_to(ROOT).as_posix() for path in generated_paths - current_pages - {ROOT / "sitemap.xml"})
            stale = sorted(path.relative_to(ROOT).as_posix() for path in current_pages - generated_paths)
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
