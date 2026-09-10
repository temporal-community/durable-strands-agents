"""Unit test for the RSS tool activity — no real network calls."""

from types import SimpleNamespace

import feedparser
import pytest

from activities import get_recent_aws_announcements


@pytest.mark.asyncio
async def test_returns_top_n_entries_in_feed_order(monkeypatch):
    entries = [
        SimpleNamespace(title=f"Announcement {i}", published="2026-09-10", link=f"https://x/{i}")
        for i in range(5)
    ]
    monkeypatch.setattr(feedparser, "parse", lambda url: SimpleNamespace(entries=entries))

    result = await get_recent_aws_announcements(limit=2)

    assert result == [
        {"title": "Announcement 0", "published": "2026-09-10", "link": "https://x/0"},
        {"title": "Announcement 1", "published": "2026-09-10", "link": "https://x/1"},
    ]
