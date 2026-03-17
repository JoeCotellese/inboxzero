"""Extract and clean body text from Gmail API MIME payloads."""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser


def extract_body_text(payload: dict) -> str:
    """Walk a Gmail API payload MIME tree and return cleaned body text.

    Prefers text/plain over text/html.  Strips quoted replies, signatures,
    and HTML tags.  Truncates to 500 chars at a word boundary.
    """
    parts = _find_mime_parts(payload)

    if parts.get("text/plain"):
        text = parts["text/plain"]
    elif parts.get("text/html"):
        text = _strip_html(parts["text/html"])
    else:
        return ""

    text = _strip_quoted_replies(text)
    return _truncate(text, max_chars=500)


def _find_mime_parts(payload: dict) -> dict[str, str]:
    """Recursively walk a MIME tree and collect decoded text parts.

    Returns a dict mapping content-type ("text/plain", "text/html") to the
    decoded text of the first matching part found.
    """
    result: dict[str, str] = {}
    _walk(payload, result)
    return result


def _walk(node: dict, result: dict[str, str]) -> None:
    """Depth-first walk of the MIME tree."""
    mime_type = node.get("mimeType", "")

    # Leaf node with body data
    if mime_type in ("text/plain", "text/html"):
        body = node.get("body", {})
        data = body.get("data", "")
        if data and mime_type not in result:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            result[mime_type] = decoded
        return

    # Container node — recurse into parts
    for part in node.get("parts", []):
        _walk(part, result)


class _HTMLTextExtractor(HTMLParser):
    """HTMLParser subclass that strips tags and extracts visible text."""

    _SKIP_TAGS = frozenset({"style", "script"})

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    # Collapse runs of whitespace
    return re.sub(r"\s+", " ", text).strip()


# Patterns for quoted replies and signatures
_QUOTED_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE_RE = re.compile(r"^On .+wrote:\s*$", re.MULTILINE)
_SIGNATURE_RE = re.compile(r"^(?:-- |_{3,}|-{3,}).*", re.MULTILINE | re.DOTALL)


def _strip_quoted_replies(text: str) -> str:
    """Remove quoted reply lines, 'On ... wrote:' blocks, and signatures."""
    text = _SIGNATURE_RE.sub("", text)
    text = _ON_WROTE_RE.sub("", text)
    text = _QUOTED_LINE_RE.sub("", text)
    # Collapse blank lines left behind
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str, max_chars: int = 500) -> str:
    """Truncate text at a word boundary, appending '...' if truncated."""
    if len(text) <= max_chars:
        return text
    # Find last space before the limit
    cut = text.rfind(" ", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return text[:cut] + "..."
