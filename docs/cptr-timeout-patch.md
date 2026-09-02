# cptr pi-agent timeout patch

## Problem

The [cptr](https://github.com/open-webui/computer-use-agent) (Open WebUI Computer) library hard-codes a 120-second timeout in `cptr/utils/agents/pi.py` when waiting for pi's event stream:

```python
event = await asyncio.wait_for(client.events.get(), timeout=120)
```

When cptr drives pi agents that interact with local GPU inference (e.g. llama.cpp via llama-swap), prompt processing can exceed 120 seconds. The pi-agent call then throws a `TimeoutError`, which cascades up and kills the pi agent mid-request — leaving the GPU inference running in the background.

## Solution

genesis-worker patches `pi.py` after installing cptr, replacing `timeout=120` with `timeout=900` (15 minutes). This is done in two places:

1. **At install time** — `CptrAcquireSession._post_run_hook()` patches after `uv tool install` completes successfully
2. **At startup time** — `start_cptr()` in `lifecycle.py` calls `patch_pi_timeout()` before launching the cptr process

The runtime guard (step 2) ensures already-installed copies of cptr receive the patch regardless of how they were installed or upgraded.

## Stream-level timeouts

In addition to the pi-agent timeout, cptr also has request/response stream timeouts controlled by environment variables. genesis-worker sets these when starting the cptr process:

```bash
export CPTR_STREAM_READ_TIMEOUT=1200
export CPTR_STREAM_WRITE_TIMEOUT=1200
```

These are applied via a shell prefix on the cptr command in `start_cptr()`:

```bash
export CPTR_STREAM_READ_TIMEOUT=1200 CPTR_STREAM_WRITE_TIMEOUT=1200 && cptr run --host ... --port ...
```

## Why not a PR upstream?

cptr is a general-purpose tool — 120 seconds is a reasonable default for cloud inference. Patching locally lets us tune for local GPU workloads without imposing a longer default on everyone. If/when cptr gains per-install or per-request timeout configuration, this patch can be removed.
