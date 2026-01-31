"""Browser automation backend using Playwright via CDP.

This backend provides AI-agent-friendly browser automation tools. The key feature
is the `get_content` tool with `format="agent"` which returns:
1. Clean, readable page content (no HTML/JS cruft)
2. A map of interactive elements with simple refs for actions

Example workflow:
1. browser_navigate("https://example.com")
2. browser_get_content(format="agent")  # Returns content + interactive elements
3. browser_click(ref="btn-0")  # Click using the ref from step 2
"""

import os
import re
import base64
import json
from typing import Optional, Literal

from fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

CDP_HOST = os.environ.get('BROWSER_CDP_HOST', '127.0.0.1')
CDP_PORT = int(os.environ.get('BROWSER_CDP_PORT', '9222'))

mcp = FastMCP('browser')

# Global state for lazy connection
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_page: Optional[Page] = None
_playwright = None
_element_map: dict[str, str] = {}  # Maps refs to selectors


async def get_browser() -> Browser:
    """Get or create browser connection via CDP."""
    global _browser, _playwright

    if _browser is None or not _browser.is_connected():
        if _playwright is None:
            _playwright = await async_playwright().start()

        cdp_url = f'http://{CDP_HOST}:{CDP_PORT}'
        _browser = await _playwright.chromium.connect_over_cdp(cdp_url)

    return _browser


async def get_page() -> Page:
    """Get or create the current page."""
    global _context, _page

    browser = await get_browser()

    # Get existing context or create one
    contexts = browser.contexts
    if contexts:
        _context = contexts[0]
    else:
        _context = await browser.new_context()

    # Get existing page or create one
    pages = _context.pages
    if pages:
        _page = pages[0]
    else:
        _page = await _context.new_page()

    return _page


async def _extract_text_content(page: Page) -> str:
    """Extract clean visible text from page, stripping scripts/styles/JSON."""
    text = await page.evaluate("""() => {
        const clone = document.body.cloneNode(true);
        clone.querySelectorAll("script, style, noscript, code, pre, svg").forEach(e => e.remove());
        return clone.innerText;
    }""")

    # Filter out JSON-like lines and very long lines
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") or line.startswith("["):
            continue
        if len(line) > 300:
            continue
        lines.append(line)

    return "\n".join(lines)


async def _extract_interactive_elements(page: Page) -> list[dict]:
    """Extract interactive elements with their selectors."""
    elements = await page.evaluate("""() => {
        const results = [];
        const seen = new Set();

        const interactives = document.querySelectorAll(
            'button, a[href], input, textarea, select, [role="button"], [role="link"], [role="textbox"], [onclick], [tabindex="0"]'
        );

        for (const el of interactives) {
            const style = getComputedStyle(el);
            if (style.display === "none" || style.visibility === "hidden") continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;

            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute("role") || tag;
            const rawText = (el.innerText || el.value || el.placeholder || el.getAttribute("aria-label") || "").trim();
            const text = rawText.split("\\n")[0].slice(0, 80);
            const href = el.getAttribute("href");
            const type = el.getAttribute("type");

            const key = role + ":" + text.slice(0, 30);
            if (seen.has(key) && text) continue;
            seen.add(key);

            let selector = "";
            if (el.id) {
                selector = "#" + CSS.escape(el.id);
            } else if (el.getAttribute("data-test-id")) {
                selector = '[data-test-id="' + el.getAttribute("data-test-id") + '"]';
            } else if (el.getAttribute("aria-label")) {
                const label = el.getAttribute("aria-label").replace(/"/g, '\\\\"');
                selector = '[aria-label="' + label + '"]';
            } else if (text && text.length < 50 && !text.includes("\\n")) {
                const escaped = text.replace(/"/g, '\\\\"').slice(0, 40);
                selector = tag + ':has-text("' + escaped + '")';
            }

            if (!selector) continue;

            results.push({
                role: role,
                text: text,
                selector: selector,
                type: type,
                href: href ? href.slice(0, 100) : null
            });
        }

        return results.slice(0, 50);
    }""")

    return elements


def _build_element_map(elements: list[dict]) -> tuple[str, dict[str, str]]:
    """Build a human-readable element list and a ref->selector map."""
    global _element_map
    _element_map = {}

    lines = []
    for i, el in enumerate(elements):
        role = el["role"]
        text = el["text"] or "(no label)"
        selector = el["selector"]

        # Determine action type
        if role in ["input", "textbox", "textarea"] or el.get("type") in ["text", "email", "password", "search"]:
            action = "fill"
            ref = f"input-{i}"
        elif role in ["a", "link"]:
            action = "click"
            ref = f"link-{i}"
        else:
            action = "click"
            ref = f"btn-{i}"

        _element_map[ref] = selector
        lines.append(f'[{ref}] {action}: "{text}"')

    return "\n".join(lines), _element_map


@mcp.tool()
async def navigate(url: str) -> dict:
    """Navigate to a URL.

    Args:
        url: The URL to navigate to

    Returns:
        Page title and status
    """
    page = await get_page()

    response = await page.goto(url, wait_until='domcontentloaded')

    return {
        'url': page.url,
        'title': await page.title(),
        'status': response.status if response else None,
    }


@mcp.tool()
async def screenshot(
    full_page: bool = False,
    selector: Optional[str] = None,
) -> dict:
    """Capture a screenshot of the page or element.

    Args:
        full_page: Capture the full scrollable page (default: viewport only)
        selector: CSS selector of element to capture (optional)

    Returns:
        Base64-encoded PNG image
    """
    page = await get_page()

    if selector:
        element = await page.query_selector(selector)
        if not element:
            return {'error': f'Element not found: {selector}'}
        image_bytes = await element.screenshot()
    else:
        image_bytes = await page.screenshot(full_page=full_page)

    return {
        'image': base64.b64encode(image_bytes).decode('utf-8'),
        'format': 'png',
        'url': page.url,
    }


@mcp.tool()
async def get_content(
    selector: Optional[str] = None,
    format: Literal["text", "html", "agent"] = "agent",
    max_length: int = 16000,
) -> dict:
    """Get page content in various formats.

    Args:
        selector: CSS selector to scope content extraction (optional)
        format: Output format:
            - "agent": Clean text + interactive elements with refs (recommended for AI agents)
            - "text": Plain visible text only
            - "html": Raw HTML (truncated)
        max_length: Maximum content length in characters (default: 16000)

    Returns:
        For format="agent":
            - content: Clean readable page text
            - elements: List of interactive elements with refs like [btn-0], [link-1], [input-2]
            - Use refs with click(ref="btn-0") or type_text(ref="input-0", text="...")

        For format="text":
            - text: Plain text content

        For format="html":
            - html: Raw HTML (truncated to max_length)
    """
    page = await get_page()

    if format == "agent":
        # Get clean text content
        text_content = await _extract_text_content(page)
        if len(text_content) > max_length:
            text_content = text_content[:max_length] + "\n\n[Content truncated...]"

        # Get interactive elements
        elements = await _extract_interactive_elements(page)
        element_list, _ = _build_element_map(elements)

        return {
            'url': page.url,
            'title': await page.title(),
            'content': text_content,
            'elements': element_list,
            'element_count': len(elements),
            'usage': 'Use click(ref="btn-0") or type_text(ref="input-0", text="...") to interact',
        }

    elif format == "text":
        text_content = await _extract_text_content(page)
        if len(text_content) > max_length:
            text_content = text_content[:max_length] + "\n\n[Content truncated...]"

        return {
            'url': page.url,
            'text': text_content,
        }

    else:  # html
        if selector:
            element = await page.query_selector(selector)
            if not element:
                return {'error': f'Element not found: {selector}'}
            html = await element.inner_html()
        else:
            html = await page.content()

        if len(html) > max_length:
            html = html[:max_length] + "\n<!-- truncated -->"

        return {
            'url': page.url,
            'html': html,
        }


@mcp.tool()
async def click(
    selector: Optional[str] = None,
    ref: Optional[str] = None,
) -> dict:
    """Click an element.

    Args:
        selector: CSS selector of element to click (use this OR ref)
        ref: Element reference from get_content(), e.g. "btn-0", "link-1" (use this OR selector)

    Returns:
        Success status and new page URL (in case of navigation)
    """
    global _element_map
    page = await get_page()

    # Resolve ref to selector
    if ref:
        if ref not in _element_map:
            return {'error': f'Unknown ref: {ref}. Call get_content(format="agent") first to get element refs.'}
        selector = _element_map[ref]

    if not selector:
        return {'error': 'Must provide either selector or ref'}

    try:
        await page.click(selector, timeout=5000)
        await page.wait_for_load_state('domcontentloaded', timeout=5000)
        return {
            'status': 'clicked',
            'ref': ref,
            'selector': selector,
            'url': page.url,
        }
    except Exception as e:
        return {'error': str(e), 'ref': ref, 'selector': selector}


@mcp.tool()
async def type_text(
    text: str,
    selector: Optional[str] = None,
    ref: Optional[str] = None,
    clear: bool = True,
    press_enter: bool = False,
) -> dict:
    """Type text into an input field.

    Args:
        text: Text to type
        selector: CSS selector of input element (use this OR ref)
        ref: Element reference from get_content(), e.g. "input-0" (use this OR selector)
        clear: Clear existing text first (default: True)
        press_enter: Press Enter after typing (default: False)

    Returns:
        Success status
    """
    global _element_map
    page = await get_page()

    # Resolve ref to selector
    if ref:
        if ref not in _element_map:
            return {'error': f'Unknown ref: {ref}. Call get_content(format="agent") first to get element refs.'}
        selector = _element_map[ref]

    if not selector:
        return {'error': 'Must provide either selector or ref'}

    try:
        if clear:
            await page.fill(selector, text, timeout=5000)
        else:
            await page.type(selector, text, timeout=5000)

        if press_enter:
            await page.press(selector, 'Enter')
            await page.wait_for_load_state('domcontentloaded', timeout=5000)

        return {
            'status': 'typed',
            'ref': ref,
            'selector': selector,
            'text': text,
        }
    except Exception as e:
        return {'error': str(e), 'ref': ref, 'selector': selector}


@mcp.tool()
async def wait_for(
    selector: str,
    state: Literal['attached', 'detached', 'visible', 'hidden'] = 'visible',
    timeout: int = 30000,
) -> dict:
    """Wait for an element to reach a specific state.

    Args:
        selector: CSS selector of element
        state: State to wait for (attached, detached, visible, hidden)
        timeout: Maximum time to wait in milliseconds (default: 30000)

    Returns:
        Success status
    """
    page = await get_page()

    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout)
        return {
            'status': 'found',
            'selector': selector,
            'state': state,
        }
    except Exception as e:
        return {'error': str(e), 'selector': selector, 'state': state}


@mcp.tool()
async def evaluate(script: str) -> dict:
    """Execute JavaScript in the page context.

    Args:
        script: JavaScript code to execute

    Returns:
        Result of the script execution
    """
    page = await get_page()

    try:
        result = await page.evaluate(script)
        return {
            'result': result,
            'url': page.url,
        }
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
async def get_page_info() -> dict:
    """Get information about all open pages/tabs.

    Returns:
        List of contexts and pages with their URLs and titles
    """
    browser = await get_browser()

    contexts_info = []
    for ctx_idx, context in enumerate(browser.contexts):
        pages_info = []
        for page in context.pages:
            pages_info.append({
                'url': page.url,
                'title': await page.title(),
            })
        contexts_info.append({
            'context_index': ctx_idx,
            'pages': pages_info,
        })

    return {
        'contexts': contexts_info,
        'total_pages': sum(len(c['pages']) for c in contexts_info),
    }


@mcp.tool()
async def new_page(url: Optional[str] = None) -> dict:
    """Open a new tab/page.

    Args:
        url: URL to navigate to (optional)

    Returns:
        New page info
    """
    global _page

    browser = await get_browser()
    contexts = browser.contexts

    if contexts:
        context = contexts[0]
    else:
        context = await browser.new_context()

    _page = await context.new_page()

    if url:
        await _page.goto(url, wait_until='domcontentloaded')

    return {
        'url': _page.url,
        'title': await _page.title(),
    }
