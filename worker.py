import asyncio
import os

from botocore.config import Config
from strands.models.bedrock import BedrockModel
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import Worker

from activities import get_recent_aws_announcements
from agent_workflow import DemoAgentWorkflow

TASK_QUEUE = os.environ.get("DEMO_TASK_QUEUE", "durable-strands-agents-tq")


def make_strands_plugin() -> StrandsPlugin:
    # botocore ignores HTTPS_PROXY/HTTP_PROXY on its own (unlike urllib, which the AWS RSS
    # tool relies on) — it only honors a proxy passed explicitly via boto_client_config. This
    # is what lets the network kill-switch demo (see proxy.py) affect Bedrock calls too.
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not https_proxy:
        return StrandsPlugin()
    config = Config(proxies={"https": https_proxy, "http": https_proxy})
    return StrandsPlugin(models={"bedrock": lambda: BedrockModel(boto_client_config=config)})


async def main() -> None:
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[make_strands_plugin()],
    )

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DemoAgentWorkflow],
        activities=[get_recent_aws_announcements],
    )
    print(f"Worker started. Listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
