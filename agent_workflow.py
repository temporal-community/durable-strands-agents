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

from activities import classify_prompt_difficulty, get_recent_aws_announcements
from model_router import MODEL_ID_TO_SIZE, pick_model_id

TOOL_TIMEOUT = timedelta(seconds=30)
CLASSIFY_TIMEOUT = timedelta(seconds=30)

INSTRUCTIONS = (
    "You are a concise AWS assistant for a live conference demo. "
    "Use your tool to check recent AWS announcements when relevant."
)


def build_routing(size: str, confidence: float) -> dict:
    """Turn a Jev classification into the routing decision the workflow acts on and exposes.

    Jev only classifies (size + confidence); pick_model_id owns the size->model policy,
    including the confidence-floor safety net, so `resolved_size` (the tier that actually
    answers) can differ from `size` (what Jev classified) when confidence was low. This stays
    a plain function so it's testable without any Temporal or Strands machinery.
    """
    model_id = pick_model_id(size, confidence)
    return {
        "size": size,
        "confidence": confidence,
        "model_id": model_id,
        "resolved_size": MODEL_ID_TO_SIZE[model_id],
    }


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
        self.routing: dict = {}

    @workflow.query
    def get_progress(self) -> list[str]:
        return self.progress

    @workflow.query
    def get_routing(self) -> dict:
        return self.routing

    @workflow.run
    async def run(self, prompt: str) -> str:
        classification = await workflow.execute_activity(
            classify_prompt_difficulty, prompt, start_to_close_timeout=CLASSIFY_TIMEOUT
        )
        self.routing = build_routing(classification["size"], classification["confidence"])
        self.progress.append(f"Routing: {self.routing['size']} -> {self.routing['resolved_size']}")

        agent = TemporalAgent(
            model=self.routing["resolved_size"],  # matches the key worker.py registers, proxied or not
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
