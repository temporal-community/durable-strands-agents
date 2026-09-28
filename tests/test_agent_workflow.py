"""Unit tests for agent_workflow.py's deterministic, I/O-free pieces.

No full workflow-replay test here (see CLAUDE.md's testing philosophy) — ProgressHook and
build_routing are plain Python, unit-tested directly, same as the rest of this test suite.
"""

from types import SimpleNamespace

from strands.hooks.events import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)

from agent_workflow import FALLBACK_CLASSIFICATION, ProgressHook, build_routing
from model_router import MODELS_BY_SIZE


class FakeRegistry:
    def __init__(self):
        self.callbacks = {}

    def add_callback(self, event_type, callback):
        self.callbacks[event_type] = callback


def test_records_a_readable_step_for_each_lifecycle_event():
    progress: list[str] = []
    registry = FakeRegistry()
    ProgressHook(progress).register_hooks(registry)

    registry.callbacks[BeforeModelCallEvent](SimpleNamespace())
    registry.callbacks[BeforeToolCallEvent](SimpleNamespace(tool_use={"name": "get_recent_aws_announcements"}))
    registry.callbacks[AfterToolCallEvent](SimpleNamespace(tool_use={"name": "get_recent_aws_announcements"}))
    registry.callbacks[AfterModelCallEvent](SimpleNamespace())

    assert progress == [
        "Thinking...",
        "Calling tool: get_recent_aws_announcements",
        "Tool finished: get_recent_aws_announcements",
        "Model responded",
    ]


def test_build_routing_resolves_the_model_id_and_carries_confidence():
    routing = build_routing(size="medium", confidence=0.87)

    assert routing == {
        "size": "medium",
        "confidence": 0.87,
        "model_id": MODELS_BY_SIZE["medium"],
        "resolved_size": "medium",
    }


def test_build_routing_biases_up_on_low_confidence():
    routing = build_routing(size="small", confidence=0.3)

    assert routing["model_id"] == MODELS_BY_SIZE["big"]
    assert routing["resolved_size"] == "big"


def test_fallback_classification_resolves_to_the_safest_model():
    """If Jev is unreachable, the workflow falls back to FALLBACK_CLASSIFICATION rather than
    failing the whole run. Its zero confidence must trip the confidence floor so the run still
    lands on the big/safest model instead of guessing on an unclassified prompt."""
    routing = build_routing(**FALLBACK_CLASSIFICATION)

    assert routing["resolved_size"] == "big"
