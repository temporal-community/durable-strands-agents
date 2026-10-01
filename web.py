from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncio

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.strands import StrandsPlugin

import proxy
from agent_workflow import DemoAgentWorkflow
from worker import TASK_QUEUE, WORKER_HEALTH_PORT

STATIC_DIR = Path(__file__).parent / "static"
PROXY_PORT = int(os.environ.get("PROXY_PORT", 8899))

client: Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[StrandsPlugin()],
    )
    proxy_server = await proxy.start_proxy_server(port=PROXY_PORT)
    print(
        f"Network kill-switch proxy on 127.0.0.1:{PROXY_PORT} — "
        f"launch the worker with HTTPS_PROXY=http://127.0.0.1:{PROXY_PORT} to route it through here."
    )
    async with proxy_server:
        yield
        proxy_server.close()


app = FastAPI(title="Durable Strands Agents", lifespan=lifespan)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


class ToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/network")
def get_network() -> dict:
    return proxy.get_state()


@app.get("/api/worker")
async def get_worker() -> dict:
    """Whether the Worker process is alive -- the signal behind the GUI's Worker pill.

    Checks worker.py's own TCP liveness beacon (WORKER_HEALTH_PORT) rather than Temporal's
    task-queue poller list: that was tried first and rejected during manual testing -- it stayed
    "online" for 90+ seconds after the worker process was killed outright, far too stale for a
    live crash demo that needs the pill to flip within ~1-2 seconds.
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", WORKER_HEALTH_PORT), timeout=0.5
        )
        writer.close()
        await writer.wait_closed()
        online = True
    except Exception:
        online = False
    return {"online": online}


def describe_pending_activities(pending_activities) -> list[dict]:
    """Map raw PendingActivityInfo protos (from a Workflow's raw DescribeWorkflowExecutionResponse)
    into the JSON shape the GUI renders as a live "retrying" step.

    Only surfaces activities on attempt > 1 (i.e. actually retrying) -- a healthy first attempt
    in flight isn't a retry and shouldn't be rendered as one.
    """
    return [
        {
            "activity_type": pa.activity_type.name,
            "attempt": pa.attempt,
            "last_failure": pa.last_failure.message if pa.HasField("last_failure") else None,
            "next_attempt_schedule_time": (
                pa.next_attempt_schedule_time.ToJsonString()
                if pa.HasField("next_attempt_schedule_time")
                else None
            ),
        }
        for pa in pending_activities
        if pa.attempt > 1
    ]


@app.post("/api/network/kill")
def set_kill_all(body: ToggleRequest) -> dict:
    return proxy.set_kill_all(body.enabled)


@app.post("/api/network/service/{key}")
def set_service(key: str, body: ToggleRequest) -> dict:
    try:
        return proxy.set_service(key, body.enabled)
    except KeyError:
        return {"error": f"unknown service: {key}"}


class RunRequest(BaseModel):
    prompt: str


@app.post("/api/run")
async def run(body: RunRequest) -> dict:
    workflow_id = f"durable-strands-agents-{uuid.uuid4()}"
    await client.start_workflow(
        DemoAgentWorkflow.run,
        body.prompt,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return {"workflow_id": workflow_id}


@app.get("/api/status/{workflow_id}")
async def status(workflow_id: str) -> dict:
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()
    status_name = description.status.name if description.status else "UNKNOWN"
    retrying = describe_pending_activities(description.raw_description.pending_activities)

    try:
        progress = await handle.query(DemoAgentWorkflow.get_progress)
    except Exception:
        progress = []

    try:
        routing = await handle.query(DemoAgentWorkflow.get_routing)
    except Exception:
        routing = {}

    base = {"status": status_name, "progress": progress, "routing": routing, "retrying": retrying}

    if status_name == "COMPLETED":
        result = await handle.result()
        return {**base, "result": result}
    if status_name == "FAILED":
        try:
            await handle.result()
        except WorkflowFailureError as exc:
            return {**base, "error": str(exc.cause or exc)}
        except Exception as exc:
            return {**base, "error": str(exc)}
    return base


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8090)))
