"""Check repository Markdown file links and local heading anchors without network I/O."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
REFERENCE = re.compile(r"^\[[^\]\n]+\]:\s*(\S+)", re.MULTILINE)


def markdown_body(text: str) -> str:
    """Ignore examples in fenced code blocks and inline code spans."""
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return re.sub(r"`[^`\n]+`", "", "\n".join(lines))


def anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    # Preserve heading words wrapped in inline code when deriving GitHub anchors.
    text = text.replace("`", "")
    result = set(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    seen: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", "", match.group(1))
        title = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", title)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    paths = sorted({ROOT / item for item in tracked.stdout.split("\0") if (ROOT / item).is_file()})
    failures = []
    count = 0
    for source in paths:
        body = markdown_body(source.read_text(encoding="utf-8"))
        destinations = [m.group(1) for m in LINK.finditer(body)]
        destinations.extend(m.group(1) for m in REFERENCE.finditer(body))
        for value in destinations:
            if value.startswith("<"):
                value = value[1:value.index(">")]
            else:
                value = re.split(r'\s+["\']', value, maxsplit=1)[0]
            parsed = urlsplit(value)
            if parsed.scheme or value.startswith("//"):
                continue
            count += 1
            target = (ROOT / unquote(parsed.path.lstrip("/"))) if parsed.path.startswith("/") else (
                source.parent / unquote(parsed.path) if parsed.path else source
            )
            if not target.exists():
                failures.append(f"{source.relative_to(ROOT)}: missing {value}")
            elif parsed.fragment and target.suffix.lower() == ".md":
                if unquote(parsed.fragment) not in anchors(target):
                    failures.append(f"{source.relative_to(ROOT)}: missing anchor {value}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Documentation links passed: {len(paths)} Markdown files, {count} local links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
