from __future__ import annotations

import re
from collections.abc import Iterable

from markdown_it import MarkdownIt
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text


class MarkdownRenderer:
    """Terminal markdown renderer with explicit fenced-code highlighting."""

    def __init__(self, *, theme: str = "monokai") -> None:
        self.theme = theme
        self.parser = MarkdownIt("commonmark", {"html": False}).enable("table")

    def render(self, markdown: str) -> RenderableType:
        self.parser.parse(markdown)
        return Group(*list(self._render_parts(markdown)))

    def _render_parts(self, markdown: str) -> Iterable[RenderableType]:
        position = 0
        pattern = re.compile(r"```([a-zA-Z0-9_+.-]+)?\n(.*?)```", re.DOTALL)
        for match in pattern.finditer(markdown):
            prefix = markdown[position : match.start()].strip()
            if prefix:
                yield Markdown(prefix, hyperlinks=True, code_theme=self.theme)
            language = (match.group(1) or "text").lower()
            code = match.group(2).rstrip()
            yield Syntax(code, language, theme=self.theme, word_wrap=True)
            position = match.end()
        tail = markdown[position:].strip()
        if tail:
            yield Markdown(tail, hyperlinks=True, code_theme=self.theme)
        if not markdown.strip():
            yield Text("")
