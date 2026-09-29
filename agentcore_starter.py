"""Starts a DemoAgentWorkflow execution against the Temporal Cloud namespace
that the AgentCore-hosted Worker (agentcore_worker.py) polls.

Reads the same TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE / TEMPORAL_API_KEY
environment variables the Worker's runtime config sets, so run this with those
exported to talk to the same namespace/task queue the AgentCore runtime polls.
"""

import asyncio
import os
import sys
import uuid

from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin

from agent_workflow import DemoAgentWorkflow
from agentcore_worker import TASK_QUEUE

DEFAULT_PROMPT = "What are the most recent AWS announcements?"


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROMPT

    api_key = os.environ.get("TEMPORAL_API_KEY") or None
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        api_key=api_key,
        tls=bool(api_key),
        plugins=[StrandsPlugin()],
    )

    workflow_id = f"agentcore-durable-strands-agents-{uuid.uuid4()}"
    print(f"Starting workflow: {workflow_id}")
    result = await client.execute_workflow(
        DemoAgentWorkflow.run,
        prompt,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
