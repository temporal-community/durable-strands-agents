from __future__ import annotations

import asyncio

from temporalio import activity

ANNOUNCEMENTS_FEED_URL = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"


@activity.defn
async def get_recent_aws_announcements(limit: int = 5) -> list[dict]:
    """Fetch the most recent AWS "What's New" announcements.

    Args:
        limit: Maximum number of announcements to return.
    """
    # Imported lazily (not at module top level) so the workflow can import this activity
    # without pulling a non-deterministic module into the workflow sandbox.
    import feedparser

    feed = await asyncio.to_thread(feedparser.parse, ANNOUNCEMENTS_FEED_URL)
    return [
        {"title": entry.title, "published": entry.published, "link": entry.link}
        for entry in feed.entries[:limit]
    ]
