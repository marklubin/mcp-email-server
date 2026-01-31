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
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def get_company_tags() -> dict:
    """List all companies with tagged problems on LeetCode.

    Returns:
        List of companies with problem counts.
    """
    query = """
    query companyTags {
        companyTags {
            name
            slug
            questionCount
        }
    }
    """
    result = await _graphql_query(query)
    tags = result.get("data", {}).get("companyTags", [])
    # Sort by question count descending
    tags.sort(key=lambda x: x.get("questionCount", 0), reverse=True)
    return {"companies": tags[:100]}  # Top 100


@mcp.tool()
async def get_company_problems(company_slug: str, limit: int = 50) -> dict:
    """Get problems tagged for a specific company.

    Args:
        company_slug: Company identifier (e.g., 'google', 'amazon', 'meta')
        limit: Maximum problems to return (default: 50)

    Returns:
        List of problems with title, difficulty, frequency, and acceptance rate.
    """
    query = """
    query companyTag($slug: String!) {
        companyTag(slug: $slug) {
            name
            questions {
                questionId
                title
                titleSlug
                difficulty
                freqBar
                acRate
                topicTags {
                    name
                    slug
                }
            }
        }
    }
    """
    result = await _graphql_query(query, {"slug": company_slug.lower()})
    company = result.get("data", {}).get("companyTag")

    if not company:
        return {"error": f"Company '{company_slug}' not found"}

    questions = company.get("questions", [])[:limit]

    # Sort by frequency (freqBar) descending
    questions.sort(key=lambda x: x.get("freqBar") or 0, reverse=True)

    return {
        "company": company.get("name"),
        "total_problems": len(company.get("questions", [])),
        "problems": [
            {
                "id": q.get("questionId"),
                "title": q.get("title"),
                "slug": q.get("titleSlug"),
                "url": f"https://leetcode.com/problems/{q.get('titleSlug')}/",
                "difficulty": q.get("difficulty"),
                "frequency": q.get("freqBar"),  # 0-100, higher = more frequent
                "acceptance_rate": round(q.get("acRate", 0), 1),
                "topics": [t.get("name") for t in q.get("topicTags", [])],
            }
            for q in questions
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
            similarQuestions
        }
    }
    """
    result = await _graphql_query(query, {"titleSlug": title_slug.lower()})
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
async def search_problems(query: str, limit: int = 20) -> dict:
    """Search for problems by keyword.

    Args:
        query: Search term (e.g., 'binary search', 'dynamic programming')
        limit: Maximum results (default: 20)

    Returns:
        List of matching problems.
    """
    gql = """
    query problemsetQuestionList($filters: QuestionListFilterInput, $limit: Int) {
        problemsetQuestionList(
            categorySlug: "all-code-essentials"
            filters: $filters
            limit: $limit
            skip: 0
        ) {
            questions {
                questionId
                title
                titleSlug
                difficulty
                acRate
                topicTags {
                    name
                }
            }
        }
    }
    """
    result = await _graphql_query(gql, {
        "filters": {"searchKeywords": query},
        "limit": limit
    })

    questions = result.get("data", {}).get("problemsetQuestionList", {}).get("questions", [])

    return {
        "query": query,
        "count": len(questions),
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
            for q in questions
        ],
    }


@mcp.tool()
async def get_discuss_posts(company: str = None, limit: int = 10) -> dict:
    """Get interview discussion posts, optionally filtered by company.

    Args:
        company: Company name to filter by (optional)
        limit: Maximum posts (default: 10)

    Returns:
        List of discussion posts about interviews.
    """
    # Search in interview-experience category
    query = """
    query discussQuestionTopicsList($first: Int!, $query: String, $categories: [String!]) {
        discussQuestionTopicsList(
            first: $first
            query: $query
            categories: $categories
            orderBy: MOST_VOTES
        ) {
            edges {
                node {
                    id
                    title
                    viewCount
                    voteCount
                    post {
                        creationDate
                    }
                }
            }
        }
    }
    """
    search_query = f"{company} interview" if company else "interview experience"

    result = await _graphql_query(query, {
        "first": limit,
        "query": search_query,
        "categories": ["interview-experience", "interview-question"]
    })

    edges = result.get("data", {}).get("discussQuestionTopicsList", {}).get("edges", [])

    return {
        "search": search_query,
        "count": len(edges),
        "posts": [
            {
                "id": e.get("node", {}).get("id"),
                "title": e.get("node", {}).get("title"),
                "views": e.get("node", {}).get("viewCount"),
                "votes": e.get("node", {}).get("voteCount"),
                "url": f"https://leetcode.com/discuss/interview-experience/{e.get('node', {}).get('id')}",
            }
            for e in edges
        ],
    }
