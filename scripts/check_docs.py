"""Check repository Markdown links, heading anchors and canonical ADR identity offline.

Supports the repository's ATX headings, inline links and reference definitions. Fenced code
and inline code are excluded from link parsing. Remote URLs are deliberately not fetched.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ADR_ALIASES = {"0010-managed-runtime-lifetime.md": "0011-managed-runtime-lifetime.md"}
LINK = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+\"[^\"]*\")?\s*\)")
DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)


def prose(source: str) -> str:
    """Remove fenced blocks without changing line numbers."""
    lines: list[str] = []
    fence = ""
    for line in source.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker and not fence:
            fence = marker[1]
            lines.append("")
        elif fence:
            if re.fullmatch(
                r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line
            ):
                fence = ""
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines)


def anchors(source: str) -> set[str]:
    found: set[str] = set()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", prose(source), re.MULTILINE):
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        heading = html.unescape(re.sub(r"<[^>]+>", "", heading)).lower()
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        candidate = slug
        number = 0
        while candidate in found:
            number += 1
            candidate = f"{slug}-{number}"
        found.add(candidate)
    return found


def destinations(source: str) -> list[tuple[int, str]]:
    body = re.sub(r"(`+).*?\1", "", prose(source))
    matches = [(match.start(), match[1] or match[2]) for match in LINK.finditer(body)]
    matches.extend((match.start(), match[1]) for match in DEFINITION.finditer(body))
    return [(body.count("\n", 0, offset) + 1, target) for offset, target in matches]


def document_errors(root: Path) -> list[str]:
    root = root.resolve()
    documents = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
    documents += sorted((root / "applications").rglob("README.md"))
    errors: list[str] = []
    for document in documents:
        source = document.read_text(encoding="utf-8")
        for line, destination in destinations(source):
            url = urlsplit(destination)
            if url.scheme or url.netloc:
                continue
            path = unquote(url.path)
            target = (
                (
                    (root / path.lstrip("/")) if path.startswith("/") else document.parent / path
                ).resolve()
                if path
                else document
            )
            label = f"{document.relative_to(root).as_posix()}:{line}"
            if not target.is_relative_to(root):
                errors.append(f"{label}: link escapes repository: {destination}")
            elif not target.exists():
                errors.append(f"{label}: missing link target: {destination}")
            elif url.fragment and target.suffix.lower() == ".md":
                if unquote(url.fragment) not in anchors(target.read_text(encoding="utf-8")):
                    errors.append(f"{label}: missing heading anchor: {destination}")
    errors.extend(adr_errors(root))
    return errors


def adr_errors(root: Path) -> list[str]:
    folder = root / "docs" / "adr"
    index = folder / "README.md"
    errors: list[str] = []
    seen: dict[str, str] = {}
    indexed = (
        [target for _, target in destinations(index.read_text(encoding="utf-8"))]
        if index.exists()
        else []
    )
    for path in sorted(folder.glob("[0-9]*.md")):
        source = path.read_text(encoding="utf-8")
        title = re.search(r"^# ADR (\d{4}):", source, re.MULTILINE)
        if path.name in ADR_ALIASES:
            target = ADR_ALIASES[path.name]
            if (
                title
                or target not in [link for _, link in destinations(source)]
                or not (folder / target).is_file()
            ):
                errors.append(f"{path.name}: invalid historical ADR alias")
            continue
        identity = path.name[:4]
        if not title or title[1] != identity:
            errors.append(f"{path.name}: canonical ADR title must match filename ID")
        if identity in seen:
            errors.append(f"{path.name}: duplicate canonical ADR ID {identity} ({seen[identity]})")
        seen[identity] = path.name
        if indexed.count(path.name) != 1:
            errors.append(f"{path.name}: canonical ADR must appear once in docs/adr/README.md")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    errors = document_errors(parser.parse_args().root)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Documentation links, anchors and ADR identity checks passed")


if __name__ == "__main__":
    main()
