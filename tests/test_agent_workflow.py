"""Unit test for ProgressHook — the deterministic hook that feeds the GUI's live step list."""

from types import SimpleNamespace

from strands.hooks.events import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)

from agent_workflow import ProgressHook


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
