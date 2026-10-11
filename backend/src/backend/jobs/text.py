"""HTML to readable plain text, for LLM prompts and full-text search.

Keeps paragraph breaks and list bullets; drops markup, link targets, images, scripts and
styles. Greenhouse `content` is HTML-escaped (`&lt;p&gt;`): unescape it exactly once
before calling `html_to_text`.
"""

import re
from html.parser import HTMLParser

_BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "main", "nav", "p", "pre", "section", "summary", "table", "tbody",
    "tfoot", "thead", "tr",
})  # fmt: skip
_SKIP = frozenset({
    "script", "style", "noscript", "template", "head", "title", "svg", "iframe", "object",
})  # fmt: skip
_WS = re.compile(r"\s+")  # Unicode-aware, so it also matches NBSP
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_TRAILING_WS = re.compile(r"\s*$")
_MARKER = re.compile(r"- |\d+\. ")
_AFTER_MARKER = re.compile(r"(?:^|\n)[ \t]*(?:- |\d+\. )[ \t]*$")
_BULLET_ONLY = re.compile(r"^(?:[-*\u2022\u25cf\u25aa\u2013]|\d+\.)\s*$")


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0
        self.lists: list[tuple[str, list[int]]] = []  # (tag, [items so far])
        self.li_depth = 0

    def _close_item(self) -> None:
        """End a list item. One that ended without any text loses its dangling bullet."""
        if not _AFTER_MARKER.search("".join(self.out[-6:])):
            return
        while self.out and not self.out[-1].strip():
            self.out.pop()
        if self.out and _MARKER.fullmatch(self.out[-1]):
            self.out.pop()

    def _break(self, n: int) -> None:
        """End the output with at least `n` newlines. Inside a list item (`<li><p>`) a
        single newline is enough, and nothing breaks right after a fresh bullet."""
        tail = "".join(self.out[-6:])
        if not self.out or _AFTER_MARKER.search(tail):
            return
        if self.li_depth:
            n = 1
        trailing = _TRAILING_WS.search(tail)
        have = trailing.group().count("\n") if trailing else 0
        if have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "br":
            self.out.append("\n")
        elif tag in {"ul", "ol"}:
            self._break(1)
            self.lists.append((tag, [0]))
        elif tag == "li":
            self._break(1)
            self.li_depth += 1
            if self.lists and self.lists[-1][0] == "ol":
                counter = self.lists[-1][1]
                counter[0] += 1
                self.out.append(f"{counter[0]}. ")
            else:
                self.out.append("- ")
        elif tag in {"td", "th"}:
            self.out.append(" ")
        elif tag in _BLOCK:
            self._break(2)
            if tag == "pre":
                self.pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag in {"ul", "ol"}:
            self._close_item()
            if self.lists:
                self.lists.pop()
            self.li_depth = min(self.li_depth, len(self.lists))  # unclosed <li>s
            self._break(1 if self.lists else 2)
        elif tag == "li":
            self._close_item()
            self.li_depth = max(0, self.li_depth - 1)
            self._break(1)
        elif tag in _BLOCK:
            self._break(2)
            if tag == "pre":
                self.pre = max(0, self.pre - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data if self.pre else _WS.sub(" ", data))


def html_to_text(markup: str | None) -> str:
    """Plain text with blank lines between paragraphs and `- ` / `1. ` list items."""
    if not markup:
        return ""
    converter = _Converter()
    converter.feed(markup)
    converter.close()
    text = "".join(converter.out).translate(_ZERO_WIDTH)
    lines: list[str] = []
    blank = False
    for raw in text.split("\n"):
        line = _WS.sub(" ", raw).strip()
        if not line or _BULLET_ONLY.match(line):
            blank = bool(lines)
            continue
        if blank:
            lines.append("")
            blank = False
        lines.append(line)
    return "\n".join(lines)
