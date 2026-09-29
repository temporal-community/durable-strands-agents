# CLAUDE.md

Context for working on this repo that isn't obvious from reading the code.

## What this is

A live-demo app for an AWS Community Day Colombo talk on Temporal's `StrandsPlugin` /
`TemporalAgent` integration (experimental, shipped 2026 — see
`.venv/lib/*/site-packages/temporalio/contrib/strands/README.md` for the authoritative API
docs, since this is too new for blog posts/training data to be reliable). The point of every
piece here is to make a specific "aha" visible on stage: kill something mid-run, show Temporal
resume without redoing finished work.

## Gotchas that will bite you if you don't know them

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

## Running on Bedrock AgentCore (`agentcore_worker.py`)

There is a second, independent way to run `DemoAgentWorkflow`: as a [Temporal Serverless
Worker](https://docs.temporal.io/serverless-workers) hosted inside an [Amazon Bedrock
AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html) Runtime
session, adapted from Temporal's own reference sample
(`temporalio/samples-python#360`, `bedrock_agentcore/strands_agent/`). See the README's
"Running on Amazon Bedrock AgentCore" section for the deploy steps. A few things that will bite
you if you don't know them:

**The network kill-switch demo does not exist here, on purpose.** AgentCore Runtime is a
managed sandbox; there's no equivalent of threading `HTTPS_PROXY` through a proxy you control.
`agentcore_worker.py` still calls `make_strands_plugin()` from `worker.py` (so the three
Bedrock tiers stay registered the same way), but it never sets `HTTPS_PROXY` in that
environment, so the proxy branch is always a no-op there — this is expected, not a bug to "fix"
by porting `proxy.py` into AgentCore.

**`agentcore_worker.py`'s `@app.entrypoint` must not block on the whole polling session.** It
starts the Temporal `Worker` as a background `asyncio.Task` via `app.add_async_task(...)` and
returns immediately. If you ever see it awaiting `run_worker()` directly inside `invoke()`,
that's wrong — AgentCore would hold the invocation open for the entire time the Worker polls
instead of just acknowledging that it started.

**The `ActivityTracker` interceptor's idle debounce is load-bearing, not decorative.** Without
it, the Worker would either shut down the instant it has no Task to poll (killing an in-flight
model call in AgentCore's eyes, since nothing marks the session `HealthyBusy` after that) or
never shut down at all (defeating AgentCore's idle-timeout billing). `AGENTCORE_DEBOUNCE_SECONDS`
controls how long it waits after the last Activity finishes before draining.

**`TEMPORAL_DEPLOYMENT_NAME`/`TEMPORAL_BUILD_ID` have no defaults and `run_worker()` will raise
`KeyError` without them.** This is deliberate (matches the reference sample): a defaulted build
ID would let the Worker silently register a Worker Deployment Version that nothing is actually
routing Tasks to, which is a much more confusing failure than an immediate crash on startup.

**This deploys against Temporal Cloud, not the local dev server `worker.py`/`web.py`/`cli.py`
use.** Serverless Workers require Temporal Cloud because Temporal Cloud is the side that assumes
an IAM role and invokes the AgentCore runtime endpoint directly — there's no equivalent for a
self-hosted server reaching into a managed AWS sandbox. The namespace this repo targets is
`temporal-agentcore-devrel.a2dd6.tmprl.cloud:7233`.

**The IAM role's "External ID" is not issued anywhere in Temporal Cloud's UI — you generate it
yourself.** It's just a shared secret string: you pick it, bake it into the invoke role's trust
policy (`bin/mk-invoke-role.sh` / `iam-role-for-temporal-agentcore-invoke.yaml`'s
`AssumeRoleExternalId` parameter), and pass that same value to
`temporal worker deployment create-version --aws-agentcore-assume-role-external-id`. Temporal
Cloud only ever enforces a match against whatever you configured; it never allocates or displays
one. Confirmed live: `--aws-agentcore-skip-role-and-external-id` does **not** actually work
against this namespace either — the server still rejects it demanding a role, despite the flag
existing in `temporal worker deployment create-version --help`.

**`--aws-agentcore-*` flags on `temporal worker deployment create-version` need `temporal` CLI
1.9.1+.** Homebrew's default (1.7.1 as of this writing) only recognizes `--aws-lambda-*` for
compute-provider config and will error as if the flag doesn't exist. `brew upgrade temporal`
fixes it — this isn't a sign the feature is unsupported.

**AgentCore runtime names cap at 48 characters, and it's `<project>_<runtime-name>` combined**
(from `agentcore.json`'s top-level `"name"` and the runtime's own `"name"`), not just the
runtime's name alone. `"DurableStrandsAgents"` + `"durable_strands_agents_worker"` (50 chars
combined) fails `agentcore deploy` with "Runtime name too long" even though each half looks
reasonable on its own — this repo settled on the shorter `DSAgents` / `agentcore_worker`. Also
note `agentcore logs --runtime <name>` and `agentcore status` address it by the runtime's own
short name (`agentcore_worker`), not the combined form that shows up in the deployed ARN
(`DSAgents_agentcore_worker-<suffix>`).

**A freshly `create-version`'d Worker Deployment Version is not automatically current.** Nothing
routes Tasks to it until `temporal worker deployment set-current-version` is run too (with
`--allow-no-pollers --yes` for a version that has no poller yet, which is normal for a
scale-to-zero Serverless Worker that hasn't been invoked once).

**`TEMPORAL_BUILD_ID` is a static string (`"1.0.0"`) and nothing enforces bumping it.** Re-running
`bin/create-runtime.sh` alone redeploys new code to the *same* pinned build under Worker
Versioning's `PINNED` behavior — if a Workflow is in-flight when that happens, its later Task
attempts can replay against logic that no longer matches what it actually ran under. There is no
automation here to force this (a demo repo doesn't warrant a build-tagging pipeline); the
discipline is manual — bump the Build ID in `agentcore/agentcore.json` and re-run
`create-version`/`set-current-version` with it whenever `agentcore_worker.py`, `agent_workflow.py`,
`activities.py`, or `model_router.py` changes. See the README's AgentCore section for the exact
commands.

**Expect several seconds of cold-start latency on the first prompt after any gap longer than
`AGENTCORE_DEBOUNCE_SECONDS`.** Each idle-shutdown-then-reinvoke cycle re-runs Python process
startup, `make_strands_plugin()`'s three `BedrockModel` constructions, and a fresh
`Client.connect()` before the first Activity even starts — this is the scale-to-zero tradeoff
working as intended, not a bug to chase. Matters for live-demo pacing: back-to-back prompts
inside the debounce window stay warm; a prompt after a long pause (e.g. after Q&A) will visibly
lag.

**Verified against AWS's own AgentCore Runtime security-best-practices guidance (not just this
sample's own README) as of 2026-09-29, live against the deployed runtime — no changes needed:**
- `requireMMDSV2: true` is already set (confirmed via `aws bedrock-agentcore-control
  get-agent-runtime`) — the CLI's generated CDK stack handles the June 2026 MMDSv2 mandate on its
  own; nothing in this repo needs to request it.
- The runtime's execution role already includes `bedrock:InvokeModel` — confirmed empirically
  (a real Bedrock call succeeded with no `additionalPolicies` entry for it), matching AWS's
  documented default execution-role policy.
- `iam-role-for-temporal-agentcore-invoke.yaml`'s `Resource` is already scoped to this specific
  runtime ARN (plus a wildcard for its endpoints), not `*` — matches AWS's "avoid wildcard
  resource statements, scope to specific runtime ARNs" guidance exactly, no change needed.
- AWS's guidance to validate the `payload` your entrypoint receives (reject non-string
  `prompt` fields, etc.) does not apply here the way it does in AWS's own examples: this
  repo's `agentcore_worker.py` entrypoint never reads `payload` at all — the prompt travels
  through a Temporal Workflow argument (`agentcore_starter.py`'s `execute_workflow` call), not
  through AgentCore's invoke payload — so there is no untrusted-payload parsing path to harden.

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
