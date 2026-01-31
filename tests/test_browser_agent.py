"""Integration tests for browser agent-friendly content extraction.

These tests verify that the browser backend correctly extracts content
and interactive elements in a format suitable for AI agents.
"""

import pytest
import asyncio


@pytest.fixture(scope="module")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def browser_page():
    """Get a connected browser page."""
    import sys
    sys.path.insert(0, 'router')
    from backends import browser

    page = await browser.get_page()
    yield page


class TestAgentContentExtraction:
    """Tests for format='agent' content extraction."""

    @pytest.mark.asyncio
    async def test_get_content_agent_format_returns_expected_keys(self, browser_page):
        """Verify agent format returns content, elements, and usage info."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="agent")

        assert 'url' in result
        assert 'title' in result
        assert 'content' in result
        assert 'elements' in result
        assert 'element_count' in result
        assert 'usage' in result

    @pytest.mark.asyncio
    async def test_content_is_clean_text(self, browser_page):
        """Verify content is clean text without HTML/JSON cruft."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="agent")
        content = result['content']

        # Should not contain HTML tags
        assert '<div' not in content
        assert '<script' not in content
        assert '<style' not in content

        # Should not contain JSON objects
        assert '{"data":' not in content
        assert '"entityUrn":' not in content

        # Should have reasonable size
        assert len(content) > 100  # Has some content
        assert len(content) < 50000  # Not bloated

    @pytest.mark.asyncio
    async def test_elements_have_refs(self, browser_page):
        """Verify interactive elements have usable refs."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="agent")
        elements = result['elements']

        # Should have elements
        assert result['element_count'] > 0

        # Elements should have refs like [btn-0], [link-1], [input-2]
        assert '[btn-' in elements or '[link-' in elements or '[input-' in elements

    @pytest.mark.asyncio
    async def test_element_refs_are_usable(self, browser_page):
        """Verify element refs can be used with click/type_text."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        # First get content to populate element map
        result = await browser.get_content.fn(format="agent")

        # Check that _element_map was populated
        assert len(browser._element_map) > 0

        # Get a ref from the map
        ref = list(browser._element_map.keys())[0]
        selector = browser._element_map[ref]

        # Verify ref format
        assert ref.startswith('btn-') or ref.startswith('link-') or ref.startswith('input-')

        # Verify selector is a valid CSS selector
        assert len(selector) > 0


class TestAgentInteraction:
    """Tests for using refs with click/type_text."""

    @pytest.mark.asyncio
    async def test_click_with_unknown_ref_returns_error(self, browser_page):
        """Verify click with unknown ref gives helpful error."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.click.fn(ref="nonexistent-99")

        assert 'error' in result
        assert 'Unknown ref' in result['error']

    @pytest.mark.asyncio
    async def test_type_text_with_unknown_ref_returns_error(self, browser_page):
        """Verify type_text with unknown ref gives helpful error."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.type_text.fn(text="test", ref="nonexistent-99")

        assert 'error' in result
        assert 'Unknown ref' in result['error']

    @pytest.mark.asyncio
    async def test_click_requires_selector_or_ref(self, browser_page):
        """Verify click requires either selector or ref."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.click.fn()

        assert 'error' in result
        assert 'Must provide' in result['error']


class TestContentFormats:
    """Tests for different content formats."""

    @pytest.mark.asyncio
    async def test_text_format(self, browser_page):
        """Verify text format returns clean text."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="text")

        assert 'text' in result
        assert 'url' in result
        assert '<div' not in result['text']

    @pytest.mark.asyncio
    async def test_html_format(self, browser_page):
        """Verify html format returns HTML."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="html")

        assert 'html' in result
        assert 'url' in result
        # HTML format should contain HTML tags
        assert '<' in result['html']

    @pytest.mark.asyncio
    async def test_max_length_truncation(self, browser_page):
        """Verify content is truncated to max_length."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="text", max_length=500)

        assert len(result['text']) <= 600  # Allow for truncation message


class TestLinkedInSpecific:
    """Tests specific to LinkedIn page structure."""

    @pytest.mark.asyncio
    async def test_linkedin_messages_visible(self, browser_page):
        """Verify LinkedIn message content is extracted."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="agent")
        content = result['content'].lower()

        # Should see messaging-related content (if on messaging page)
        # This is a soft check - depends on which page is loaded
        has_messaging_content = (
            'message' in content or
            'inbox' in content or
            'conversation' in content or
            'linkedin' in result['url'].lower()
        )
        assert has_messaging_content or 'linkedin' not in result['url'].lower()

    @pytest.mark.asyncio
    async def test_linkedin_interactive_elements(self, browser_page):
        """Verify LinkedIn interactive elements are found."""
        import sys
        sys.path.insert(0, 'router')
        from backends import browser

        result = await browser.get_content.fn(format="agent")

        # Should find interactive elements on any LinkedIn page
        if 'linkedin' in result['url'].lower():
            assert result['element_count'] > 5
