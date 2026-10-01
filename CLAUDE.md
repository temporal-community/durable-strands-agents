# CLAUDE.md

Context for working on this repo that isn't obvious from reading the code.

## What this is

A demo app showcasing Temporal's `StrandsPlugin`/`TemporalAgent` integration (experimental,
shipped 2026 — see `.venv/lib/*/site-packages/temporalio/contrib/strands/README.md` for the
authoritative API docs, since this is too new for blog posts/training data to be reliable). The
point of every piece here is to make a specific "aha" visible: kill something mid-run, show
Temporal resume without redoing finished work.

## Gotchas that will bite you if you don't know them

**Temporal's task-queue poller list is NOT a usable "is the worker alive right now" signal —
it's stale for a very long time.** `web.py`'s GUI has a Worker status pill
(`/api/worker`/`#workerPill`) that needs to flip within ~1-2 seconds of `Ctrl+C`-ing the worker
for the crash demo to land. The obvious approach — `client.workflow_service.describe_task_queue(...)`
and checking `response.pollers` — was tried and measured live: it still reported a poller present
90+ seconds after the worker process was confirmed dead. Don't reach for it as a liveness check.
Instead, `worker.py` opens a trivial TCP beacon (`WORKER_HEALTH_PORT`, default 8787,
`asyncio.start_server` that just accepts and closes) and `web.py` does a short-timeout
`asyncio.open_connection` against it — this is near-instant in both directions because it's
tied to the OS actually holding the port open, not to any Temporal-side bookkeeping.

**`description.raw_description.pending_activities` is the only way to see an Activity retrying
in progress, and it's not exposed as a first-class field on `WorkflowExecutionDescription`.**
`web.py`'s `/api/status/{workflow_id}` reads this raw proto field (via `handle.describe()`'s
`.raw_description`, the underlying `DescribeWorkflowExecutionResponse`) to surface a live
"retrying" step in the GUI during the network kill-switch demo. Each `PendingActivityInfo` has
`attempt`, `last_failure`, `next_attempt_schedule_time` — `next_attempt_schedule_time` is only
populated during the backoff gap between attempts, not while an attempt is actively executing,
so the GUI falls back to a plain "retrying…" when it's null rather than assuming it's always set.

**botocore does NOT read `HTTPS_PROXY`/`HTTP_PROXY` env vars on its own.** `feedparser`/`urllib`
do (that's why `activities.py` needed zero changes for the kill-switch demo to work on the RSS
tool). Bedrock calls go through `boto3`/`botocore`, which only honors a proxy passed explicitly
via `Config(proxies=...)`. This is why `worker.py` has `make_strands_plugin()` instead of just
`StrandsPlugin()` — it reads `HTTPS_PROXY` itself and threads it into `BedrockModel(boto_client_config=...)`.
If you ever "fix" that function away, the network kill-switch demo will silently stop affecting
Bedrock while still working on the RSS tool, which is a confusing bug to rediscover.

**`worker.py`'s `make_strands_plugin()` always passes an explicit `models={...}` dict, proxied
or not — it never leaves `models=None`.** Before the model-routing feature this repo relied on
`TemporalAgent`'s implicit `model="bedrock"` default, which only exists when `StrandsPlugin()`
is constructed with `models=None`. Now `agent_workflow.py` passes `model=<size>` (`small`/
`medium`/`big`, whichever the Jev classification + `model_router.pick_model_id` resolved to),
so all three tiers must be registered keys in `worker.py`'s plugin regardless of whether the
proxy env var is set — there's no implicit default to fall back on anymore. If you ever see
`StrandsPlugin()` with no `models=` argument reintroduced here, the model router will break for
every tier except whatever `TemporalAgent` implicitly defaults to.

**The kill-switch proxy (`proxy.py`) must sever already-open tunnels, not just refuse new
ones.** boto3/urllib3 keep a persistent connection pool; if you cut the network *after* a
tunnel to Bedrock is already established, the pooled connection keeps working right through the
"outage" unless the proxy actively closes it. This was a real bug found live — see
`_active_tunnels` / `_kill_matching_tunnels()`. `tests/test_proxy.py` guards this regression
(temporarily gut `_kill_matching_tunnels()` and two tests fail immediately if you want to
confirm the test actually catches it).

**Both `worker.py` and the GUI's client (`web.py`) must use matching `StrandsPlugin()`
instances for the Pydantic data converter to agree** — this is a hard requirement from the
Temporal integration itself, not a style choice. Don't construct a plain `Client.connect(...)`
without `plugins=[StrandsPlugin()]` anywhere that touches these workflows.

**Piping a long-running Python process's stdout to a file via `nohup ... > log 2>&1 &` fully
buffers it** (no TTY attached), so `worker.py`'s startup print and any errors can appear to
vanish until the process exits. Set `PYTHONUNBUFFERED=1` when you need to `tail -f` a
backgrounded worker/web process during debugging.

## Running two processes in sync

`worker.py` and `web.py` are independent processes, but the network kill-switch only works if
**both** are pointed at the same proxy: `web.py` hosts `proxy.py` (started in its FastAPI
lifespan, default port `8899`, override with `PROXY_PORT`), and `worker.py` must be launched
with `HTTPS_PROXY`/`HTTP_PROXY` set to that same address. If the worker is launched without
those env vars, it talks to Bedrock directly — the CLI (`cli.py`) still works fine, but the
kill switch's Bedrock toggle becomes a no-op (the AWS-feed toggle still works either way, since
that path is `urllib`-based and always proxy-aware once the env var is set on the worker
process — just not by default here).

The same kind of mismatch applies to `WORKER_HEALTH_PORT` (default `8787`): `worker.py` binds it
for its liveness beacon, and `web.py` reads the same env var independently to know which port to
probe for the Worker pill. If you ever need to run a second worker on the same host and override
`WORKER_HEALTH_PORT` on its command line, you must set the identical value when launching
`web.py` too — otherwise the pill probes the wrong (or default) port forever and reads "offline"
for a worker that's actually healthy, with no error to point at why.

## AWS account gotchas hit during development

- SSO-based AWS profiles (`AWS_PROFILE` using the "login" credential provider) need
  `botocore[crt]` installed — plain `botocore` raises `MissingDependencyException` at Bedrock
  client construction time. Already in `pyproject.toml`; don't remove it.
- A fresh AWS account/region can hit `ResourceNotFoundException: Model use case details have
  not been submitted for this account` on the first Bedrock Claude call — that's an
  AWS-Marketplace-side form (Bedrock console → Model access), not a code or auth problem.

## Screenshots

`docs/screenshots/*.png` were captured with Playwright against a live `localhost:8090` (real
worker, real Bedrock call, real kill-switch toggle — not mocked). Regenerate by running the app
normally, then driving it headlessly, e.g.:

```bash
uv run --with playwright playwright install chromium
uv run --with playwright python - <<'EOF'
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        page = await (await p.chromium.launch()).new_page(viewport={"width": 1440, "height": 860})
        await page.goto("http://localhost:8090", wait_until="networkidle")
        await page.screenshot(path="docs/screenshots/idle.png")

asyncio.run(main())
EOF
```

No screenshot script is checked in — it's a one-off tool invocation, not part of the app.

## Testing philosophy (see also README's Tests section)

Tests cover logic this repo owns (`proxy.py` policy, `activities.py` parsing, the `ProgressHook`
in `agent_workflow.py`) and run fully offline. There's deliberately no Temporal workflow-replay
test for `DemoAgentWorkflow` itself — that would require hand-building a fake Strands `Model` to
stand in for Bedrock, which is more code than the workflow it would be testing. That path is
covered by manual end-to-end runs against real Bedrock instead.

## Not-yet-built

The plan (see conversation history / `~/.claude/plans` on the machine this was built on) called
out a Phase 2 with per-service network toggles — that's built (`Bedrock` / `AWS feed` switches
in the drawer). Jev-based model routing is also built (`model_router.py`,
`classify_prompt_difficulty` in `activities.py`, wired into `agent_workflow.py`/`worker.py`,
surfaced as a tier badge in the GUI) — see the "Model routing" section below. Nothing else is
currently planned as a follow-up.

## Model routing (Jev-classified small/medium/big)

Each run classifies the prompt's difficulty with Jev (TypeSafe's classifier, called over
OpenRouter) as a durable Temporal activity (`classify_prompt_difficulty`), then
`model_router.pick_model_id` maps the result to one of three Bedrock models
(`MODELS_BY_SIZE`). Below `CONFIDENCE_FLOOR` (0.6), it biases *up* to the big model rather
than trusting an unsure classification — this mirrors the reference demo
([mikegc-aws/jev-strands-video](https://github.com/mikegc-aws/jev-strands-video/tree/main/demos/model_switching))'s
own stated gap. If the classify activity fails after its bounded retry
(`CLASSIFY_RETRY_POLICY`, `agent_workflow.py`), the workflow falls back to
`FALLBACK_CLASSIFICATION` (confidence 0.0) instead of failing the whole run — zero confidence
trips the same floor, so an unclassified prompt still lands on the safest (big) model rather
than guessing. **This requires `OPENROUTER_API_KEY` to actually be loaded** — `uv run` does
*not* auto-load `.env`; use `uv run --env-file .env worker.py` (same for `web.py`/`cli.py` if
you rely on `.env` for AWS credentials too), or the classifier will always fail over to the
fallback path and every prompt silently runs on the big model regardless of actual difficulty.
