from __future__ import annotations

from datetime import timedelta

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)
from temporalio import workflow
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.strands import workflow as strands_workflow

from activities import get_recent_aws_announcements

TOOL_TIMEOUT = timedelta(seconds=30)

INSTRUCTIONS = (
    "You are a concise AWS assistant for a live conference demo. "
    "Use your tool to check recent AWS announcements when relevant."
)


class ProgressHook(HookProvider):
    """Deterministic hook that records human-readable steps so the GUI can poll them live."""

    def __init__(self, progress: list[str]) -> None:
        self._progress = progress

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeModelCallEvent, lambda e: self._progress.append("Thinking..."))
        registry.add_callback(AfterModelCallEvent, lambda e: self._progress.append("Model responded"))
        registry.add_callback(
            BeforeToolCallEvent,
            lambda e: self._progress.append(f"Calling tool: {e.tool_use['name']}"),
        )
        registry.add_callback(
            AfterToolCallEvent,
            lambda e: self._progress.append(f"Tool finished: {e.tool_use['name']}"),
        )


@workflow.defn
class DemoAgentWorkflow:
    def __init__(self) -> None:
        self.progress: list[str] = []

    @workflow.query
    def get_progress(self) -> list[str]:
        return self.progress

    @workflow.run
    async def run(self, prompt: str) -> str:
        agent = TemporalAgent(
            model="bedrock",  # matches the key worker.py registers, proxied or not
            start_to_close_timeout=timedelta(seconds=60),
            system_prompt=INSTRUCTIONS,
            tools=[
                strands_workflow.activity_as_tool(
                    get_recent_aws_announcements, start_to_close_timeout=TOOL_TIMEOUT
                ),
            ],
            hooks=[ProgressHook(self.progress)],
        )
        result = await agent.invoke_async(prompt)
        return str(result)
