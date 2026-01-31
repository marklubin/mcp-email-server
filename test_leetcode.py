#!/usr/bin/env python3
"""Test LeetCode API access."""

import asyncio
import os
import httpx

LEETCODE_SESSION = os.environ.get("LEETCODE_SESSION", "")
GRAPHQL_URL = "https://leetcode.com/graphql"


def get_headers():
    return {
        "Content-Type": "application/json",
        "Cookie": f"LEETCODE_SESSION={LEETCODE_SESSION}",
        "Referer": "https://leetcode.com",
    }


async def test():
    if not LEETCODE_SESSION:
        print("ERROR: LEETCODE_SESSION not set")
        return

    print(f"Session token: {LEETCODE_SESSION[:50]}...")

    async with httpx.AsyncClient() as client:
        # First check user status
        resp = await client.post(
            GRAPHQL_URL,
            json={"query": "query { userStatus { username isPremium }}"},
            headers=get_headers(),
            timeout=60,
        )
        print(f"User: {resp.json()}")

        # Introspect CompanyFilter type
        print("\\nIntrospecting CompanyFilter...")
        query = """
        query {
            __type(name: "CompanyFilter") {
                name
                inputFields {
                    name
                    type { name kind ofType { name kind } }
                }
            }
        }
        """
        resp = await client.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=get_headers(),
            timeout=60,
        )
        data = resp.json()
        fields = data.get("data", {}).get("__type", {}).get("inputFields", [])
        print("CompanyFilter fields:")
        for f in fields:
            print(f"  - {f}")

        # Get enum values
        print("\\nGetting QuestionFilterCombineTypeEnum...")
        query_enum = """
        query {
            __type(name: "QuestionFilterCombineTypeEnum") {
                enumValues { name }
            }
        }
        """
        resp = await client.post(
            GRAPHQL_URL,
            json={"query": query_enum},
            headers=get_headers(),
            timeout=60,
        )
        data = resp.json()
        vals = data.get("data", {}).get("__type", {}).get("enumValues", [])
        print(f"Enum values: {[v.get('name') for v in vals]}")

        # Try with enum value directly in query (not as variable)
        print("\\nTrying with hardcoded enum...")
        query2 = """
        query {
            problemsetQuestionListV2(filters: {
                filterCombineType: ALL
                companyFilter: {companySlugs: ["google"]}
            }) {
                questions {
                    title
                    titleSlug
                    difficulty
                }
            }
        }
        """
        resp = await client.post(
            GRAPHQL_URL,
            json={"query": query2},
            headers=get_headers(),
            timeout=60,
        )
        print(f"Status: {resp.status_code}")
        data = resp.json()
        if "errors" in data:
            print(f"Errors: {data['errors']}")
        else:
            qs = data.get("data", {}).get("problemsetQuestionListV2", {}).get("questions", [])
            print(f"Found {len(qs)} problems")
            for q in qs[:5]:
                print(f"  - {q.get('title')} ({q.get('difficulty')})")


if __name__ == "__main__":
    asyncio.run(test())
