from __future__ import annotations

import asyncio
import os

from temporalio import activity

from model_router import MODELS_BY_SIZE

ANNOUNCEMENTS_FEED_URL = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"

# Jev over OpenRouter (matches mikegc-aws/jev-strands-video/demos/model_switching) — the
# leading "~" is intentional, it's how OpenRouter addresses the TypeSafe classifier model.
JEV_MODEL = "~typesafe/jev-latest"


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


@activity.defn
async def classify_prompt_difficulty(prompt: str) -> dict:
    """Ask Jev which t-shirt size (small/medium/big) this prompt needs.

    Args:
        prompt: The user's prompt to classify.
    """
    # Imported lazily, same reasoning as feedparser above: keeps the workflow sandbox clean
    # and this activity's real network dependency out of workflow-code import time.
    import typesafe_sdk

    jev = typesafe_sdk.TypeSafeClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api",
    )

    def call_jev():
        return jev.system_one(
            model=JEV_MODEL,
            state=f"A user said the following turn in a conversation with an AI assistant:\n\n{prompt}",
            questions={
                "size": typesafe_sdk.Choice(
                    instructions=(
                        "Which size model is needed to answer this turn *well*? "
                        "Pick the smallest one that can do the job."
                    ),
                    criteria={
                        "small": "Trivial: a greeting, a simple fact, or a one-step request answerable in a sentence.",
                        "medium": "Moderate: ordinary reasoning, explanation, or a routine coding task.",
                        "big": "Hard: multi-step reasoning, novel problem-solving, tricky design, or deep analysis.",
                    },
                ),
            },
        )

    response = await asyncio.to_thread(call_jev)
    answer = response.answers["size"]
    # Belt-and-suspenders: the criteria above only ever offer known sizes, but validate
    # anyway so an unexpected value fails clearly here rather than as a ValueError raised
    # deep inside pick_model_id, far from where the actual classification happened.
    if answer.choice not in MODELS_BY_SIZE:
        raise ValueError(f"Jev returned an unexpected size: {answer.choice!r}")
    return {"size": answer.choice, "confidence": answer.confidence}
