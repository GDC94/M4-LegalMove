"""Fill an HTML template with a Golden Case document and render it to a PNG page."""

from html import escape
from string import Template
from typing import Protocol

from playwright.sync_api import sync_playwright

from golden_text import Block, Document
from render_plain import PAGE_SIZE, PageOverflowError


class PageRenderer(Protocol):
    """Seam between the generator and the browser; unit tests pass a double."""

    def render_png(self, html: str) -> bytes: ...


def block_html(block: Block) -> str:
    lead = f'<span class="lead">{escape(block.lead or "")}</span>'
    return f'<p class="block">{lead}<span class="body">{escape(block.body)}</span></p>'


def render_html(document: Document, template: str) -> str:
    blocks = "\n".join(block_html(block) for block in document.blocks)
    return Template(template).substitute(title=escape(document.title), blocks=blocks)


class PlaywrightPageRenderer:
    """Headless Chromium screenshot of one A4 page at 150 dpi; overflowing text fails."""

    def render_png(self, html: str) -> bytes:
        width, height = PAGE_SIZE
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.set_content(html, wait_until="load")
                content_height = int(page.evaluate("document.documentElement.scrollHeight"))
                if content_height > height:
                    raise PageOverflowError(f"page content is {content_height}px, over {height}px")
                return page.screenshot(type="png")
            finally:
                browser.close()
