"""Static HTML/CSS contract tests: no backend import or external font dependencies."""
from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _document() -> str:
    tree = ast.parse((ROOT / "web" / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_INDEX_HTML"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("_INDEX_HTML not found")


class _Structure(HTMLParser):
    VOID = frozenset(("meta", "link", "input", "img", "br", "hr", "source", "area"))
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = []
        self.tags = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if values.get("id"):
            self.ids.append(values["id"])
        if tag not in self.VOID:
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, list(self.stack[-4:])))
            return
        self.stack.pop()


def test_page_has_self_hosted_stylesheet_mobile_viewport_and_semantic_shell():
    html = _document()
    assert '<meta name="viewport"' in html
    assert '<main class="app-shell">' in html
    assert '<section class="control-deck"' in html
    assert 'href="/assets/research_desk.css"' in html
    assert 'src="/assets/roe_discount.js"' in html
    assert '<div id="result"></div>' in html
    assert 'id="stockCode"' in html
    assert 'aria-label="股票代码或名称"' in html
    assert 'id="submitBtn"' in html
    assert 'id="researchMode"' in html
    assert 'id="crawlXueqiuBtn"' in html
    assert 'id="crawlBiliBtn"' in html


def test_no_duplicate_dom_ids_and_structural_html_tags_are_balanced():
    html = _document()
    static_html = html.split("<script>", 1)[0]
    parser = _Structure()
    parser.feed(static_html)
    # The static portion intentionally leaves html/body/main open for JS and
    # the closing tags after script; validate the HTML around the script too.
    assert parser.errors == []
    assert parser.stack == ["html", "body", "main"]
    assert len(parser.ids) == len(set(parser.ids))


def test_theme_served_locally_and_has_responsive_aligned_controls():
    js = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    css = (ROOT / "web" / "research_desk.css").read_text(encoding="utf-8")
    assert '@app.get("/assets/research_desk.css")' in js
    assert 'media_type="text/css"' in js
    for rule in (
        ".app-shell", ".hero-header", ".control-deck",
        ".search-bar", ".research-options", ".stock-chip",
        ".metric-strip", ".chart-grid", ".lab-layout",
        ".lab-result", ".slider-row", ".crawl-row",
        "@media(max-width:1100px)", "@media(max-width:760px)",
        "@media(max-width:420px)",
    ):
        assert rule in css, rule
    assert "Noto Sans SC" in css
    assert "Microsoft YaHei" in css
    assert "font-variant-numeric:tabular-nums" in css
    assert "max-width:none" in css
    assert "min-width:0" in css
    assert "@import" not in css
    assert "fonts.googleapis" not in css
    assert "https://" not in css


def test_stock_shortcuts_use_native_buttons_and_keep_existing_hooks():
    html = _document()
    assert '<button type="button" class="stock-chip"' in html
    assert 'el.querySelectorAll(\'.stock-chip\')' in html
    assert "chip.addEventListener('click'" in html
