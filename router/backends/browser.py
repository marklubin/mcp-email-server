"""Browser automation backend using Playwright via CDP."""

import os
import base64
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
async def get_content(selector: Optional[str] = None) -> dict:
    """Get text content from page or element.

    Args:
        selector: CSS selector of element (optional, defaults to body)

    Returns:
        Text content and HTML
    """
    page = await get_page()

    if selector:
        element = await page.query_selector(selector)
        if not element:
            return {'error': f'Element not found: {selector}'}
        text = await element.text_content()
        html = await element.inner_html()
    else:
        text = await page.text_content('body')
        html = await page.content()

    return {
        'text': text,
        'html': html[:10000] if len(html) > 10000 else html,
        'url': page.url,
    }


@mcp.tool()
async def click(selector: str) -> dict:
    """Click an element.

    Args:
        selector: CSS selector of element to click

    Returns:
        Success status
    """
    page = await get_page()

    try:
        await page.click(selector, timeout=5000)
        return {
            'status': 'clicked',
            'selector': selector,
            'url': page.url,
        }
    except Exception as e:
        return {'error': str(e), 'selector': selector}


@mcp.tool()
async def type_text(selector: str, text: str, clear: bool = True) -> dict:
    """Type text into an input field.

    Args:
        selector: CSS selector of input element
        text: Text to type
        clear: Clear existing text first (default: True)

    Returns:
        Success status
    """
    page = await get_page()

    try:
        if clear:
            await page.fill(selector, text, timeout=5000)
        else:
            await page.type(selector, text, timeout=5000)

        return {
            'status': 'typed',
            'selector': selector,
            'text': text,
        }
    except Exception as e:
        return {'error': str(e), 'selector': selector}


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
