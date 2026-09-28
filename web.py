from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.strands import StrandsPlugin

import proxy
from agent_workflow import DemoAgentWorkflow
from worker import TASK_QUEUE

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

    try:
        progress = await handle.query(DemoAgentWorkflow.get_progress)
    except Exception:
        progress = []

    try:
        routing = await handle.query(DemoAgentWorkflow.get_routing)
    except Exception:
        routing = {}

    if status_name == "COMPLETED":
        result = await handle.result()
        return {"status": status_name, "progress": progress, "routing": routing, "result": result}
    if status_name == "FAILED":
        try:
            await handle.result()
        except WorkflowFailureError as exc:
            return {
                "status": status_name,
                "progress": progress,
                "routing": routing,
                "error": str(exc.cause or exc),
            }
        except Exception as exc:
            return {"status": status_name, "progress": progress, "routing": routing, "error": str(exc)}
    return {"status": status_name, "progress": progress, "routing": routing}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8090)))
