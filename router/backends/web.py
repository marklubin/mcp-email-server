"""Web search backend using Exa API.

Free tier: 1,000 searches/month. Get a key at https://dashboard.exa.ai
"""

import os
from typing import Optional, Literal

import httpx
from fastmcp import FastMCP

EXA_API_KEY = os.environ.get('EXA_API_KEY', '')
BASE_URL = 'https://api.exa.ai'

mcp = FastMCP('web')


@mcp.tool()
async def search(
    query: str,
    num_results: int = 10,
    search_type: Literal['auto', 'neural', 'keyword'] = 'auto',
    include_text: bool = False,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
) -> dict:
    """Search the web via Exa.

    Args:
        query: Natural-language or keyword query.
        num_results: Number of results to return (1-25).
        search_type: 'auto' lets Exa pick; 'neural' for semantic; 'keyword' for literal matches.
        include_text: If true, include a text excerpt of each result page in the response.
        include_domains: Only return results from these domains.
        exclude_domains: Drop results from these domains.
    """
    if not EXA_API_KEY:
        return {'error': 'EXA_API_KEY not configured'}

    body: dict = {
        'query': query,
        'numResults': max(1, min(num_results, 25)),
        'type': search_type,
    }
    if include_text:
        body['contents'] = {'text': {'maxCharacters': 2000}}
    if include_domains:
        body['includeDomains'] = include_domains
    if exclude_domains:
        body['excludeDomains'] = exclude_domains

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f'{BASE_URL}/search',
            headers={'x-api-key': EXA_API_KEY, 'Content-Type': 'application/json'},
            json=body,
        )
        if r.status_code != 200:
            return {'error': f'Exa API {r.status_code}: {r.text[:500]}'}
        data = r.json()

    results = []
    for item in data.get('results', []):
        entry = {
            'title': item.get('title'),
            'url': item.get('url'),
            'published': item.get('publishedDate'),
            'author': item.get('author'),
            'score': item.get('score'),
        }
        if include_text and item.get('text'):
            entry['text'] = item['text']
        results.append(entry)

    return {'query': query, 'results': results, 'count': len(results)}


@mcp.tool()
async def get_contents(urls: list[str], max_chars: int = 4000) -> dict:
    """Fetch clean page contents for one or more URLs via Exa's contents API.

    Args:
        urls: List of URLs to fetch.
        max_chars: Max characters of text to return per URL.
    """
    if not EXA_API_KEY:
        return {'error': 'EXA_API_KEY not configured'}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f'{BASE_URL}/contents',
            headers={'x-api-key': EXA_API_KEY, 'Content-Type': 'application/json'},
            json={'urls': urls, 'text': {'maxCharacters': max_chars}},
        )
        if r.status_code != 200:
            return {'error': f'Exa API {r.status_code}: {r.text[:500]}'}
        data = r.json()

    return {
        'results': [
            {
                'url': item.get('url'),
                'title': item.get('title'),
                'text': item.get('text'),
            }
            for item in data.get('results', [])
        ]
    }
