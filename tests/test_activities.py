"""Unit tests for activities.py — no real network calls."""

from types import SimpleNamespace

import feedparser
import pytest
import typesafe_sdk

from activities import classify_prompt_difficulty, get_recent_aws_announcements


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


class FakeChoiceAnswer:
    def __init__(self, choice: str, confidence: float):
        self.choice = choice
        self.confidence = confidence


class FakeSystemOneResponse:
    def __init__(self, choice: str, confidence: float):
        self.answers = {"size": FakeChoiceAnswer(choice, confidence)}


class FakeTypeSafeClient:
    """Records the call it received and returns a scripted response."""

    last_kwargs: dict | None = None

    def __init__(self, *, api_key=None, base_url=None):
        self.api_key = api_key
        self.base_url = base_url

    def system_one(self, **kwargs):
        FakeTypeSafeClient.last_kwargs = kwargs
        return FakeSystemOneResponse("medium", 0.87)


@pytest.mark.asyncio
async def test_classify_prompt_difficulty_extracts_size_and_confidence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", FakeTypeSafeClient)

    result = await classify_prompt_difficulty("Explain the difference between a process and a thread.")

    assert result == {"size": "medium", "confidence": 0.87}


@pytest.mark.asyncio
async def test_classify_prompt_difficulty_sends_the_prompt_as_state(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", FakeTypeSafeClient)

    await classify_prompt_difficulty("What's the capital of France?")

    assert "What's the capital of France?" in FakeTypeSafeClient.last_kwargs["state"]
    assert "size" in FakeTypeSafeClient.last_kwargs["questions"]
