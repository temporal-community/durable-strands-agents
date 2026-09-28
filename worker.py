import asyncio
import os

from botocore.config import Config
from strands.models.bedrock import BedrockModel
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import Worker

from activities import classify_prompt_difficulty, get_recent_aws_announcements
from agent_workflow import DemoAgentWorkflow
from model_router import MODELS_BY_SIZE

TASK_QUEUE = os.environ.get("DEMO_TASK_QUEUE", "durable-strands-agents-tq")


def make_strands_plugin() -> StrandsPlugin:
    # botocore ignores HTTPS_PROXY/HTTP_PROXY on its own (unlike urllib, which the AWS RSS
    # tool relies on) — it only honors a proxy passed explicitly via boto_client_config. This
    # is what lets the network kill-switch demo (see proxy.py) affect Bedrock calls too.
    #
    # We always register explicit small/medium/big model factories (never leave `models=None`
    # to fall back on TemporalAgent's implicit "bedrock" default) — the workflow always passes
    # model=<size>, proxied or not, so every tier must be a registered key either way.
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    boto_client_config = Config(proxies={"https": https_proxy, "http": https_proxy}) if https_proxy else None

    # Construct every tier's BedrockModel right now, at worker startup, rather than lazily
    # inside each factory. A constructor-level problem (bad model_id, bad boto config) then
    # fails loudly for all three tiers immediately, instead of silently only surfacing later
    # when that specific tier happens to get selected by the model router at runtime.
    models = {
        size: BedrockModel(model_id=model_id, boto_client_config=boto_client_config)
        for size, model_id in MODELS_BY_SIZE.items()
    }
    return StrandsPlugin(models={size: (lambda model=model: model) for size, model in models.items()})


async def main() -> None:
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[make_strands_plugin()],
    )

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DemoAgentWorkflow],
        activities=[get_recent_aws_announcements, classify_prompt_difficulty],
    )
    print(f"Worker started. Listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
