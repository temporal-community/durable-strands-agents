"""Runs a Temporal Worker hosting DemoAgentWorkflow inside an Amazon Bedrock
AgentCore Runtime.

Mirrors the canonical pattern from temporalio/samples-python's
bedrock_agentcore/strands_agent sample. The AgentCore SDK provides the HTTP
contract: BedrockAgentCoreApp serves /ping and /invocations, and reports
"HealthyBusy" while an async task is registered.

The Temporal Worker only starts *after* invocation, and it runs in the
background: /invocations registers an async task and acknowledges immediately
rather than holding the response open for the whole polling window. AgentCore
keeps the session alive for as long as /ping reports "HealthyBusy", which is
exactly what add_async_task/complete_async_task drive.

The Worker polls until it has been idle for AGENTCORE_DEBOUNCE_SECONDS. Because
the plugin runs the agent's model calls as Activities, and the RSS/classify
tools are Activities too, the interceptor below sees the agent's whole turn,
so the Worker is not torn down mid-thought.

Unlike the local worker.py, there is no network kill-switch here — AgentCore
Runtime is a managed sandbox, and proxy.py's tunnel-killing has no equivalent
inside it. make_strands_plugin() is reused as-is: it only threads a proxy into
BedrockModel when HTTPS_PROXY is set, which it never is in this environment.
"""

import asyncio
import os
from datetime import timedelta

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from temporalio.client import Client
from temporalio.common import VersioningBehavior
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
    Worker,
    WorkerDeploymentConfig,
    WorkerDeploymentVersion,
)

from activities import classify_prompt_difficulty, get_recent_aws_announcements
from agent_workflow import DemoAgentWorkflow
from worker import make_strands_plugin

app = BedrockAgentCoreApp()
log = app.logger

# The background Worker, when one is running. Holds the strong reference that
# asyncio.create_task() does not keep, and doubles as the "already polling?" flag.
_worker: asyncio.Task[None] | None = None

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "agentcore-durable-strands-agents-tq")

# How long the Worker keeps polling after it goes idle.
DEBOUNCE = float(os.environ.get("AGENTCORE_DEBOUNCE_SECONDS", "60"))
# The first idle check waits at least this long, regardless of DEBOUNCE. Without this, a slow
# cold start (gRPC handshake to Temporal Cloud, workflow-task dispatch) that happens to take
# longer than DEBOUNCE looks identical to "genuinely idle" to wait_until_idle -- since inflight
# stays 0 until an Activity actually starts, the Worker could declare itself idle and drain
# before it ever picks up the very Task that caused AgentCore to invoke it in the first place.
STARTUP_GRACE = float(os.environ.get("AGENTCORE_STARTUP_GRACE_SECONDS", "30"))
# How long the drain waits for in-flight Activities (a model or tool call).
DRAIN_SECONDS = float(os.environ.get("AGENTCORE_DRAIN_SECONDS", "120"))


class ActivityTracker(Interceptor):
    """Tracks in-flight activities and blocks until AGENTCORE_DEBOUNCE_SECONDS elapses with no events."""

    def __init__(self) -> None:
        self.inflight = 0
        self.changed = asyncio.Event()

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _TrackedActivity(next, self)

    async def wait_until_idle(self, debounce: float, *, startup_grace: float = 0.0) -> None:
        """Return once no Activity has run for ``debounce`` seconds.

        The very first wait uses ``max(debounce, startup_grace)`` instead of ``debounce`` alone,
        so a slow-to-arrive first Task can't be mistaken for true idleness before any Activity
        has ever run. Every wait after that uses ``debounce`` as normal.
        """
        first_wait = True
        while True:
            self.changed.clear()
            timeout = max(debounce, startup_grace) if first_wait else debounce
            first_wait = False
            try:
                # Wake the moment an Activity starts or finishes; a timeout
                # instead means nothing has happened for the whole window.
                await asyncio.wait_for(self.changed.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                if self.inflight == 0:
                    return


class _TrackedActivity(ActivityInboundInterceptor):
    def __init__(
        self, next: ActivityInboundInterceptor, tracker: ActivityTracker
    ) -> None:
        super().__init__(next)
        self._tracker = tracker

    async def execute_activity(self, input: ExecuteActivityInput):
        self._tracker.inflight += 1
        self._tracker.changed.set()
        log.info("activity in flight: %d", self._tracker.inflight)
        try:
            return await self.next.execute_activity(input)
        finally:
            self._tracker.inflight -= 1
            self._tracker.changed.set()


async def run_worker() -> None:
    """Poll until idle, then drain."""
    # No default build ID: a defaulted one would silently strand Workflows on a
    # version nothing is polling, so this refuses to start without both set.
    deployment_name = os.environ["TEMPORAL_DEPLOYMENT_NAME"]
    build_id = os.environ["TEMPORAL_BUILD_ID"]

    api_key = os.environ.get("TEMPORAL_API_KEY") or None
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        api_key=api_key,
        tls=bool(api_key),
        plugins=[make_strands_plugin()],
    )

    tracker = ActivityTracker()
    log.info("polling %s as %s/%s", TASK_QUEUE, deployment_name, build_id)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DemoAgentWorkflow],
        activities=[get_recent_aws_announcements, classify_prompt_difficulty],
        interceptors=[tracker],
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=deployment_name, build_id=build_id
            ),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
        graceful_shutdown_timeout=timedelta(seconds=DRAIN_SECONDS),
    )
    async with worker:
        await tracker.wait_until_idle(DEBOUNCE, startup_grace=STARTUP_GRACE)
    log.info("worker idle for %ss; drained", DEBOUNCE)


async def _run_until_idle(task_id: int) -> None:
    """Own the Worker's whole life, and always release the async task."""
    try:
        await run_worker()
    except Exception:
        # Nothing awaits this task, so an error would otherwise be swallowed.
        log.exception("worker failed in async task")
    finally:
        # Without this the session stays HealthyBusy until MaxLifetime.
        app.complete_async_task(task_id)


@app.entrypoint
async def invoke(payload: dict) -> dict:
    """Start the Worker and acknowledge. The payload is unused."""
    # Prevent duplicate workers since we exit early
    global _worker
    if _worker is not None and not _worker.done():
        log.info("worker already polling %s", TASK_QUEUE)
        return {"message": "worker already polling", "task_queue": TASK_QUEUE}

    task_id = app.add_async_task("temporal-worker")
    _worker = asyncio.create_task(_run_until_idle(task_id))

    return {"message": "worker starting", "task_queue": TASK_QUEUE}


if __name__ == "__main__":
    app.run()
