"""Twitter/X read backend via twitterapi.io."""

import os
from typing import Optional

import aiohttp
from fastmcp import FastMCP

mcp = FastMCP("twitter")

TWITTERAPI_IO_BASE = "https://api.twitterapi.io"
TWITTERAPI_IO_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")


def _tweet_to_dict(tweet: dict) -> dict:
    """Normalize a twitterapi.io tweet to a standard format."""
    author_obj = tweet.get("author") or {}
    handle = author_obj.get("userName") or ""
    tweet_id = tweet.get("id") or ""
    url = f"https://x.com/{handle}/status/{tweet_id}" if handle and tweet_id else ""
    return {
        "id": tweet_id,
        "url": url,
        "author": {
            "handle": handle,
            "display_name": author_obj.get("name") or handle,
        },
        "text": tweet.get("text") or "",
        "created_at": tweet.get("createdAt"),
        "like_count": tweet.get("likeCount", 0),
        "retweet_count": tweet.get("retweetCount", 0),
        "reply_count": tweet.get("replyCount", 0),
    }


def _check_key():
    if not TWITTERAPI_IO_KEY:
        raise ValueError("TWITTERAPI_IO_KEY not set")


def _headers() -> dict:
    return {"x-api-key": TWITTERAPI_IO_KEY}


@mcp.tool()
async def search_tweets(query: str, limit: int = 25) -> dict:
    """Search recent tweets via twitterapi.io.

    Args:
        query: Search query (keywords, hashtags, from:user, etc.)
        limit: Max results to return (default 25)
    """
    _check_key()
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TWITTERAPI_IO_BASE}/twitter/tweet/advanced_search",
            params={"query": query, "limit": limit},
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            tweets = [_tweet_to_dict(t) for t in data.get("tweets", [])]
            return {"tweets": tweets, "count": len(tweets)}


@mcp.tool()
async def get_user_tweets(username: str, limit: int = 20, cursor: Optional[str] = None) -> dict:
    """Fetch recent tweets from a user.

    Args:
        username: Twitter handle (without @)
        limit: Max tweets to return (default 20)
        cursor: Pagination cursor from a previous response
    """
    _check_key()
    params: dict = {"userName": username, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TWITTERAPI_IO_BASE}/twitter/user/last_tweets",
            params=params,
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            tweets = [_tweet_to_dict(t) for t in data.get("tweets", [])]
            return {"tweets": tweets, "count": len(tweets), "next_cursor": data.get("next_cursor")}


@mcp.tool()
async def get_tweet(tweet_id: str) -> dict:
    """Fetch a single tweet by ID.

    Args:
        tweet_id: The tweet ID
    """
    _check_key()
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TWITTERAPI_IO_BASE}/twitter/tweets",
            params={"tweet_ids": tweet_id},
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            tweets = data.get("tweets", [])
            if not tweets:
                return {"error": f"Tweet {tweet_id} not found"}
            return {"tweet": _tweet_to_dict(tweets[0])}


@mcp.tool()
async def get_thread(tweet_id: str) -> dict:
    """Fetch a tweet thread (original + replies).

    Args:
        tweet_id: The tweet ID (or full URL — the ID will be extracted)
    """
    _check_key()
    if "/" in tweet_id:
        tweet_id = tweet_id.rstrip("/").split("/")[-1]
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TWITTERAPI_IO_BASE}/twitter/tweet/thread",
            params={"tweet_id": tweet_id},
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            tweets = [_tweet_to_dict(t) for t in data.get("tweets", [])]
            if not tweets:
                return {"error": "Thread not found"}
            return {"root": tweets[0], "replies": tweets[1:], "count": len(tweets)}


@mcp.tool()
async def get_profile(username: str) -> dict:
    """Fetch a Twitter user profile.

    Args:
        username: Twitter handle (without @)
    """
    _check_key()
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TWITTERAPI_IO_BASE}/twitter/user/info",
            params={"userName": username},
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            user = data.get("data") or data
            return {
                "id": user.get("id", ""),
                "handle": user.get("userName", username),
                "display_name": user.get("name", ""),
                "description": user.get("description", ""),
                "followers_count": user.get("followers", 0),
                "following_count": user.get("following", 0),
                "posts_count": user.get("statusesCount", 0),
            }
