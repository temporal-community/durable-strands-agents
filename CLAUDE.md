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

**`TemporalAgent`'s implicit `model="bedrock"` default only applies when `StrandsPlugin()` is
constructed with `models=None`.** The moment `worker.py` passes a custom `models={...}` dict
(the proxy-aware path), that implicit default disappears. `agent_workflow.py` therefore passes
`model="bedrock"` to `TemporalAgent` explicitly — don't remove it, even though it looks
redundant when reading `worker.py`'s non-proxy branch in isolation.

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
in the drawer). Nothing else is currently planned as a follow-up.
