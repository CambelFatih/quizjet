"""Turn a Project Gutenberg plain-text book into the reader's chapter data.

    python scripts/import_gutenberg.py pg1661.txt _data/books/sherlock-holmes.json

Output: {"chapters": [{"title": ..., "p": [[sentence, ...], ...]}]}. Only the text between the
Gutenberg START/END markers is kept. A chapter starts at a line matching --chapter-re (group 1 is
the title); lines matching --skip-re (part numbers such as "II.") are dropped. Book metadata —
title, author, level, quiz keys — lives in _data/books.json and is never touched by this script,
so re-importing a corrected text keeps the quiz links.
"""
from __future__ import annotations

import argparse
import json
import re
import string
from pathlib import Path

# Defaults fit Gutenberg #1661 ("ADVENTURE I. A SCANDAL IN BOHEMIA", "VII. THE ADVENTURE OF ...").
CHAPTER_RE = r"^(?:ADVENTURE )?[IVXL]+\. ([A-Z][A-Z' -]+)$"
SKIP_RE = r"^[IVXL]+\.$"
SMALL_WORDS = {"a", "an", "and", "in", "of", "the", "with", "on", "at", "to", "for"}
# ponytail: fixed abbreviation list, enough for 19th-century English prose; add entries when a new
# book splits a sentence after one of its own abbreviations.
ABBREVIATIONS = {"mr", "mrs", "messrs", "dr", "st", "mme", "mlle", "no", "co", "vol", "rev", "capt", "col",
                 "gen", "lt", "sgt", "jr", "sr", "prof", "hon", "esq", "ft", "mt", "vs", "etc", "i.e", "e.g"}
# A sentence ends at . ! ? (plus closing quotes/brackets) followed by space and a capital or opening quote.
BOUNDARY_RE = re.compile(r"""[.!?]+["')\]]*(?=\s+["'(\[]?[A-Z0-9])""")


def title_case(text: str) -> str:
    words = string.capwords(text.lower()).split(" ")
    return " ".join(w.lower() if i and w.lower() in SMALL_WORDS else w for i, w in enumerate(words))


def clean(text: str) -> str:
    text = text.replace("--", "—").replace("_", "")  # Gutenberg dashes and _italics_
    return " ".join(text.split())


def split_sentences(paragraph: str) -> list[str]:
    sentences, start = [], 0
    for match in BOUNDARY_RE.finditer(paragraph):
        last_word = re.search(r"([A-Za-z.]+)\W*$", paragraph[start:match.start() + 1])
        word = last_word.group(1).rstrip(".").lower() if last_word else ""
        if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):  # "Mr." or an initial "J."
            continue
        if re.search(r"[!?]['\"]$", match.group()) and re.match(r"\s+I [a-z]", paragraph[match.end():]):
            continue  # '"Seven!" I answered.' is one sentence
        sentences.append(paragraph[start:match.end()].strip())
        start = match.end()
    tail = paragraph[start:].strip()
    return sentences + [tail] if tail else sentences


def parse(text: str, chapter_re: str = CHAPTER_RE, skip_re: str = SKIP_RE) -> list[dict[str, object]]:
    body = re.split(r"^\*\*\* ?START OF.*$", text, maxsplit=1, flags=re.MULTILINE)[-1]
    body = re.split(r"^\*\*\* ?END OF.*$", body, maxsplit=1, flags=re.MULTILINE)[0]
    chapter_pattern, skip_pattern = re.compile(chapter_re), re.compile(skip_re)
    chapters: list[dict[str, object]] = []
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
        first = block.strip()
        heading = chapter_pattern.match(first)
        if heading:
            chapters.append({"title": title_case(heading.group(1)), "p": []})
        elif chapters and first and not skip_pattern.match(first):
            chapters[-1]["p"].append(split_sentences(clean(block)))
    if chapters and chapters[-1]["p"]:
        # The end of the last chapter often carries a trailing "End of the Project Gutenberg..." line.
        last = chapters[-1]["p"]
        while last and last[-1][0].lower().startswith("end of"):
            last.pop()
    return chapters


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a Gutenberg .txt into reader chapter data.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--chapter-re", default=CHAPTER_RE)
    parser.add_argument("--skip-re", default=SKIP_RE)
    args = parser.parse_args()
    chapters = parse(args.source.read_text(encoding="utf-8"), args.chapter_re, args.skip_re)
    if not chapters:
        print("ERROR: no chapter heading matched --chapter-re")
        return 1
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(json.dumps({"chapters": chapters}, ensure_ascii=False, separators=(",", ":")) + "\n",
                           encoding="utf-8", newline="\n")
    sentences = sum(len(p) for c in chapters for p in c["p"])
    print(f"{len(chapters)} chapters, {sentences} sentences → {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
