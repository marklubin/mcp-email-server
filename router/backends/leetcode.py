"""LeetCode MCP backend - Query company-tagged problems and interview data."""

import os
import httpx
from fastmcp import FastMCP

mcp = FastMCP("leetcode")

LEETCODE_SESSION = os.environ.get("LEETCODE_SESSION", "")
GRAPHQL_URL = "https://leetcode.com/graphql"


def _get_headers():
    """Get headers for LeetCode API requests."""
    return {
        "Content-Type": "application/json",
        "Cookie": f"LEETCODE_SESSION={LEETCODE_SESSION}",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }


async def _graphql_query(query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query against LeetCode."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=_get_headers(),
            timeout=60,
        )
        data = resp.json()
        # Return data even on 400 - GraphQL errors are in the response
        if resp.status_code >= 500:
            resp.raise_for_status()
        return data


@mcp.tool()
async def get_user_status() -> dict:
    """Check LeetCode authentication status and premium membership.

    Returns:
        Username and premium status.
    """
    query = """
    query {
        userStatus {
            username
            isPremium
        }
    }
    """
    result = await _graphql_query(query)
    return result.get("data", {}).get("userStatus", {})


@mcp.tool()
async def get_company_problems(company_slug: str, limit: int = 50) -> dict:
    """Get problems tagged for a specific company (requires Premium).

    Args:
        company_slug: Company identifier (e.g., 'google', 'amazon', 'meta', 'apple')
        limit: Maximum problems to return (default: 50)

    Returns:
        List of problems with title, difficulty, and URL.
    """
    # Use the v2 API with company filter
    query = f"""
    query {{
        problemsetQuestionListV2(filters: {{
            filterCombineType: ALL
            companyFilter: {{companySlugs: ["{company_slug.lower()}"]}}
        }}) {{
            questions {{
                questionId
                title
                titleSlug
                difficulty
                acRate
                topicTags {{
                    name
                }}
            }}
        }}
    }}
    """
    result = await _graphql_query(query)

    if "errors" in result:
        return {"error": result["errors"][0].get("message", "Unknown error")}

    questions = result.get("data", {}).get("problemsetQuestionListV2", {}).get("questions", [])

    return {
        "company": company_slug,
        "total_problems": len(questions),
        "problems": [
            {
                "id": q.get("questionId"),
                "title": q.get("title"),
                "slug": q.get("titleSlug"),
                "url": f"https://leetcode.com/problems/{q.get('titleSlug')}/",
                "difficulty": q.get("difficulty"),
                "acceptance_rate": round(q.get("acRate", 0), 1),
                "topics": [t.get("name") for t in q.get("topicTags", [])],
            }
            for q in questions[:limit]
        ],
    }


@mcp.tool()
async def get_problem_detail(title_slug: str) -> dict:
    """Get detailed information about a specific problem.

    Args:
        title_slug: Problem identifier (e.g., 'two-sum', 'lru-cache')

    Returns:
        Problem details including description, difficulty, tags, and company tags.
    """
    query = """
    query questionData($titleSlug: String!) {
        question(titleSlug: $titleSlug) {
            questionId
            title
            titleSlug
            difficulty
            acRate
            content
            topicTags {
                name
                slug
            }
            companyTags {
                name
                slug
            }
            hints
        }
    }
    """
    result = await _graphql_query(query, {"titleSlug": title_slug.lower()})

    if "errors" in result:
        return {"error": result["errors"][0].get("message", "Unknown error")}

    q = result.get("data", {}).get("question")

    if not q:
        return {"error": f"Problem '{title_slug}' not found"}

    return {
        "id": q.get("questionId"),
        "title": q.get("title"),
        "slug": q.get("titleSlug"),
        "url": f"https://leetcode.com/problems/{q.get('titleSlug')}/",
        "difficulty": q.get("difficulty"),
        "acceptance_rate": round(q.get("acRate", 0), 1),
        "content": q.get("content"),  # HTML description
        "topics": [t.get("name") for t in q.get("topicTags", [])],
        "companies": [c.get("name") for c in q.get("companyTags", [])],
        "hints": q.get("hints", []),
    }


@mcp.tool()
async def search_problems(keyword: str, difficulty: str = None, limit: int = 20) -> dict:
    """Search for problems by keyword and optional difficulty.

    Args:
        keyword: Search term (e.g., 'binary search', 'dynamic programming')
        difficulty: Filter by difficulty ('EASY', 'MEDIUM', 'HARD') - optional
        limit: Maximum results (default: 20)

    Returns:
        List of matching problems.
    """
    # Build filters
    filters = "filterCombineType: ALL"
    if difficulty and difficulty.upper() in ["EASY", "MEDIUM", "HARD"]:
        filters += f", difficultyFilter: {{difficulties: [{difficulty.upper()}]}}"

    query = f"""
    query {{
        problemsetQuestionListV2(filters: {{ {filters} }}) {{
            questions {{
                questionId
                title
                titleSlug
                difficulty
                acRate
                topicTags {{
                    name
                }}
            }}
        }}
    }}
    """

    result = await _graphql_query(query)

    if "errors" in result:
        return {"error": result["errors"][0].get("message", "Unknown error")}

    questions = result.get("data", {}).get("problemsetQuestionListV2", {}).get("questions", [])

    # Filter by keyword in title or topics
    keyword_lower = keyword.lower()
    filtered = [
        q for q in questions
        if keyword_lower in q.get("title", "").lower()
        or any(keyword_lower in t.get("name", "").lower() for t in q.get("topicTags", []))
    ]

    return {
        "query": keyword,
        "difficulty": difficulty,
        "count": len(filtered),
        "problems": [
            {
                "id": q.get("questionId"),
                "title": q.get("title"),
                "slug": q.get("titleSlug"),
                "url": f"https://leetcode.com/problems/{q.get('titleSlug')}/",
                "difficulty": q.get("difficulty"),
                "acceptance_rate": round(q.get("acRate", 0), 1),
                "topics": [t.get("name") for t in q.get("topicTags", [])],
            }
            for q in filtered[:limit]
        ],
    }


@mcp.tool()
async def get_problems_by_topic(topic_slug: str, limit: int = 30) -> dict:
    """Get problems filtered by topic/tag.

    Args:
        topic_slug: Topic identifier (e.g., 'dynamic-programming', 'binary-search', 'tree')
        limit: Maximum problems to return (default: 30)

    Returns:
        List of problems for the given topic.
    """
    query = f"""
    query {{
        problemsetQuestionListV2(filters: {{
            filterCombineType: ALL
            topicFilter: {{topicSlugs: ["{topic_slug.lower()}"]}}
        }}) {{
            questions {{
                questionId
                title
                titleSlug
                difficulty
                acRate
            }}
        }}
    }}
    """

    result = await _graphql_query(query)

    if "errors" in result:
        return {"error": result["errors"][0].get("message", "Unknown error")}

    questions = result.get("data", {}).get("problemsetQuestionListV2", {}).get("questions", [])

    return {
        "topic": topic_slug,
        "count": len(questions),
        "problems": [
            {
                "id": q.get("questionId"),
                "title": q.get("title"),
                "slug": q.get("titleSlug"),
                "url": f"https://leetcode.com/problems/{q.get('titleSlug')}/",
                "difficulty": q.get("difficulty"),
                "acceptance_rate": round(q.get("acRate", 0), 1),
            }
            for q in questions[:limit]
        ],
    }
