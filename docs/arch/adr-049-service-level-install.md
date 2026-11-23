# ADR-049: Service-level install axis — one install covers everything the service needs

## Title

Service-level install axis — the Install button and the install endpoint act on the *service* (every artifact it needs to run), not on one installable, and the install outcome stays on screen until it is dismissed.

## Status

Accepted. Amends ADR-038 (the install endpoint's semantics).

> **Amended (2026-11-23, t-2vuyma-install-message).** The pre-install caption that named the still-missing images was removed. The Install button fetches the whole stack, so the list was noise on every card. The install axis, the composite session and the API contract are unchanged.

## Context

A declarative service exposes an **install axis**: a list of `ServiceInstall`s, one per artifact it needs. A single-container service has one (its image). A `kind: compose` service has one per image in the stack — ROMM has two (app + postgres), paperless-ngx has three (webserver + postgres + valkey broker).

The service's **availability** is a property of the whole set: `ComposeService.is_available()` is true only when *every* image of the rendered stack is present locally. One image is enough for nothing.

The **install** side, until now, was a property of one member of that set:

- `InferenceService.primary_installable()` returns `installs()[0]`.
- The Status page's action button (`utils/ui/_service_controls.py`) installed that one installable.
- The facade's install endpoint (`POST /v1/services/{name}/install`, ADR-038) installed that one installable.

On a clean node — no image of the stack present — the flow is deterministic and broken:

1. The page offers **Install** (the service is not available).
2. Clicking it pulls **one** image of the stack.
3. The service is still not available (the other images are missing), so the next render offers **Install** again — never **Start**.
4. The companion images are unreachable from that page. Only the Images page offers them, one section per installable, so the operator has to know they exist and hunt for them.

The outcome of that install was not even visible. The inline install flow rendered the terminal message (`Pulled …` / `Failed: …`) into a run that was immediately discarded: the polling fragment set the `drop_pending` flag *before* the message was rendered and reran the app; the app run rendered the message, saw the flag, popped the session and reran again — discarding its own output. The next run found no session and drew the plain Install button. So a failure looked exactly like a no-op, and so did a success. The same defect sat on the shared Images page and on the two plugin-owned pages (llama-swap Binaries, ComfyUI Images).

A third, latent variant of the same bug: `is_available()` reads the images of the **rendered template**, while the install axis is built from the **declared** `images:` block. A template line `image: redis:7` that nobody declared is required by the availability check and fetchable by no install button — the service can never become available from the UI, silently, forever.

The start path never had the problem: `ComposeService.start()` runs `docker compose pull` before `up`, which fetches every image in the file. That is why the API path *appeared* to work (the facade installed one image, then `start()` pulled the rest) while the UI path could not reach Start at all.

### Forces

- **The framework/plugin boundary (ADR-009).** The framework talks to `InferenceService` and nothing else. A new framework capability therefore becomes a method on the ABC, not a special case for compose services.
- **Blast radius.** llama-swap exposes two installables but one primary (the llama-swap proxy; the llama-server binary is a separate, deliberately un-pulled artifact), and ComfyUI's primary follows the active variant. "Install the service" must keep meaning "install the primary" for them, or the change silently redefines what their Install button does.
- **The Images page stays the version-picking surface.** A recorded version pick must survive a one-click install — the composite install must not re-record the default tag over the operator's choice.
- **Failures must be attributable.** "It didn't work" is not actionable. The operator needs to know *which* artifact failed and *why*, and the message has to survive long enough to be read.
- **The API contract must not move.** ADR-038 fixed the response shape of the install endpoint; the orchestrator depends on it.

## Decision

We will add a **service-level install axis** to the service contract, make the compose kind use it, and make every install outcome truthful and persistent.

### 1. Two new methods on `InferenceService`

```python
def service_install(self) -> AcquireSession | None:
    """A session that installs everything this service needs to run."""
    primary = self.primary_installable()
    return primary.install() if primary is not None else None

def missing_installables(self) -> list[ServiceInstall]:
    """The installables whose artifact is still absent."""
    primary = self.primary_installable()
    if primary is None:
        return []
    return [] if primary.is_present() else [primary]
```

The defaults are exactly today's behaviour: the primary installable, and the primary when it is not present. Every non-compose service keeps its current semantics — llama-swap's Install button still installs the proxy only.

### 2. Presence is about the artifact that will actually run

`ServiceInstall.is_present()` defaults to `state() == InstallState.INSTALLED`. `DockerImageInstall` overrides it: a docker image is present when the **effective ref** is present — the recorded version selection if there is one, else the default tag — not merely when the default tag happens to be on disk. `DockerImageInstall.effective_ref` exposes that ref. The distinction matters because the stack runs the effective ref.

### 3. A composite install session

`utils/acquire/multi_install.py` adds `MultiInstallSession`, an `AcquireSession` that installs a list of installables in order:

- an installable whose `is_present()` is true is skipped (the install is idempotent and re-runnable — a partial install resumes);
- the rest are installed at their **recorded version** when one exists, so a version pick is never clobbered;
- each sub-session is awaited; its progress is mirrored, with the title `Pulling postgres:18-alpine (2/3)`;
- a failed sub-session raises `RuntimeError("install of postgres:18-alpine failed: …")` — the artifact is named and the underlying error text is carried;
- a cancelled sub-session cancels the whole session.

### 4. Compose services install the whole stack

`ComposeService` overrides both methods: its service-level install is a `MultiInstallSession` over every declared image, and its missing installables are the images whose effective ref is absent. The list is derived from the same effective refs `is_available()` checks, so the missing list and the availability check never disagree.

### 5. The UI installs the service and keeps the outcome

- The Status page's action button drives `service_install()`, which fetches every image the service needs.
- **An install outcome stays on screen until it is dismissed.** The `drop_pending` auto-drop is gone: the session is popped only when the operator clicks *Dismiss*. This applies to every surface that renders an install outcome — the Status page, the shared Images page, the llama-swap Binaries page and the ComfyUI Images page.
- When the install completes and the service becomes available, the page offers **Start** next to the outcome.

### 6. The API installs the service too

`GenesisWorker.install_service` uses `service_install()` and short-circuits on an empty `missing_installables()`. The response shape is unchanged (`{"installed": true, "version": …}`); `version` is still the primary installable's recorded version. The per-service install lock and the `InstallInProgressError` behaviour are unchanged (ADR-038).

### 7. Load-time guard: every template image must be declared

The loader rejects a compose spec whose template contains an `image:` line that is not `$images.<name>` with `<name>` declared in the `images:` block. The error names the offending line and the rule. `build:` services are unaffected — they declare no image to pull. A spec that violates this could never become available from the UI, so it is a load-time error, not a runtime surprise.

## Consequences

**Positive**

- The normal flow on a clean node — *enable → Status page → Install → Start* — leaves a running service. The operator never has to visit the Images page to make a multi-image stack runnable.
- Install outcomes are visible: a failure names the artifact and the reason, and stays on screen until dismissed. A success is visible too.
- A partial install resumes: present images are skipped, the rest are retried.
- An operator's recorded version pick survives the one-click install.
- A compose spec whose template uses an undeclared image fails loudly at load time instead of being un-installable forever.
- The API and the UI now mean the same thing by "install".

**Negative**

- The Install button of a compose service now pulls the whole stack — more bytes, more time, on a first install.
- A failure message persists until dismissed; the page keeps a session in `session_state` for the duration. The Dismiss button restores the plain Install button.
- `InferenceService` grows two methods. The defaults keep every existing service's behaviour, but the ABC is the contract every plugin implements.

**Neutral**

- The Images page remains the place for per-image version picks; the Status page's Install button is the "make it runnable" action.
- Single-image compose services (traefik, genesis-productivity, genesis-agent-context) take a byte-for-byte identical path — the composite session over one installable is the primary installable's session.
- The load-time guard rejects a future compose spec that uses a literal image line. That is the documented house rule; the error message states the fix.

## Plan

Task `t-o51en9`, round 3 — the SE pipeline's approved plan for the paperless-ngx declarative service and the multi-image compose install fix. ADR-049 is written before the code it governs; ADR-038 is amended in the same change.
