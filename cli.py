import asyncio
import os
import sys
import uuid

from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin

from agent_workflow import DemoAgentWorkflow
from worker import TASK_QUEUE


async def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: uv run cli.py \"<prompt>\"")
    prompt = sys.argv[1]

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[StrandsPlugin()],
    )

    workflow_id = f"durable-strands-agents-{uuid.uuid4()}"
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
