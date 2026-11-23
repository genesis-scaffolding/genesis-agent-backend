# Plan 052: Bifrost setup token — an operator secret in the declarative `auth:` block

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-au025m-bifrost-setup-token/genesis-agent-backend`,
branch `se/t-au025m-bifrost-setup-token`, from `origin/main` @ `4eee079`)
**Task:** `t-au025m-bifrost-setup-token` — *add support for setting setup tokens to Bifrost*
**ADR:** `docs/arch/adr-052-operator-secret-in-the-auth-block.md` (Status: Proposed)
**Status:** planned — nothing implemented yet.

---

## 1. Goal

A fresh Bifrost container must start with a setup token the operator can read
and paste into the dashboard. Concretely:

1. The worker generates a setup token **once**, before the container starts,
   and persists it (mode `0600`) under the service's state dir, so it survives
   worker restarts and image upgrades.
2. The worker injects it into the container as `BIFROST_SETUP_TOKEN`.
3. The worker shows it on the Bifrost status page with a copy-to-clipboard
   control, so the operator can paste it into *Workspace → Config → Security →
   Setup token*.
4. An operator who wants their own value can set one instead (a user-typed
   option beats the generated file), exactly as crawl4ai's `api_token` and
   vaultwarden's `admin_token` do.

The mechanism is crawl4ai's, verbatim: an `ensure_persistent_token` pre-start
hook + the declarative `auth:` block + the `auth_token` status panel.

## 2. What the repo already gives us

The crawl4ai reference model, mapped onto Bifrost:

| crawl4ai (today) | mechanism in the repo | bifrost (wanted) |
|---|---|---|
| `CRAWL4AI_API_TOKEN` in `env:` | `DockerService.start()` overwrites it from the auth block | `BIFROST_SETUP_TOKEN` |
| `ensure_persistent_token` hook → `$state_dir/api_token` | `utils/services/hooks.py` + `ensure_persistent_file.py` | → `$state_dir/setup_token` |
| `auth:` block (`token_file`, `token_env_var`, `fallback_option`) | `utils/services/spec.py` (`AuthSpec`), `loader.py` (`_build_docker_config`), `docker_service.py` (`AuthConfig`) | same block, `BIFROST_SETUP_TOKEN` |
| `auth_token` status panel with a copy button | `utils/services/panels.py` | same panel, worded for a setup token |
| `api_token` option (user override) | `options:` block + `user-overrides.env` | `setup_token` option |

The pieces are all there. What is missing is only the **wording**: the panel
hardcodes the caption "Authorization header: `Bearer <token>`" and the heading
"API token",
and there is no way for a spec to say otherwise. A Bifrost setup token is
pasted into a dashboard form — it is not a bearer header.

## 3. Upstream facts (Bifrost v2)

From <https://docs.getbifrost.ai/quickstart/gateway/setting-up> and
<https://docs.getbifrost.ai/quickstart/gateway/setting-up-auth> (fetched while
planning):

- Bifrost v2.0.0-prerelease3+ refuses to create the **first admin account**
  unless a setup token is configured on the server:
  `BIFROST_SETUP_TOKEN` in the environment, or `setup_token` at the top level
  of `config.json` (literal, `env.VAR` reference, or `vault.path` reference).
- The dashboard flow: *Workspace → Config → Security* → enable "Password
  protect the dashboard" → set admin username + password → paste the same
  value into the **Setup token** field → Save.
- The token is never persisted by Bifrost and never written to logs. It is
  checked only while the first admin account is created; it stops mattering
  once the account exists.
- The gateway itself keeps working without a password until the operator
  enables dashboard protection — the token is a *setup* secret, not an API
  credential.

## 4. Decision (ADR-052)

Widen the `auth:` block from "API credential" to **operator secret**, and give
it the two strings the panel needs to be honest about what the secret is:

```yaml
auth:
  token_env_var: BIFROST_SETUP_TOKEN
  token_file: "$state_dir/setup_token"
  token_file_mode: "0o600"
  token_generator: random_hex_32
  fallback_option: "$options.setup_token"
  label: "Setup token"                     # new — the panel's heading
  usage: "Paste into Bifrost's ..."        # new — the panel's usage line
```

`label` and `usage` are optional and default to empty, which means "keep
today's wording". Crawl4ai, vaultwarden and every other spec render exactly
as they do now.

Alternatives considered (full reasoning in the ADR):

| alternative | verdict |
|---|---|
| New panel kind `setup_token` beside `auth_token` | rejected — duplicates the copy control and needs its own wording source |
| General `ui.panel_config` plumbing (YAML → loader → `render_default_status`) | deferred — a general mechanism with one user; the wording belongs to the secret, not to the panel list |
| Python service subclass for bifrost | rejected — the YAML template expresses this perfectly well |
| Reuse the panel unchanged ("Bearer <token>") | rejected — wrong copy; the operator would send a bearer header to a dashboard form |
| Put the token in bifrost's `config.json` instead of the env var | rejected — that file is owned by the orchestrator's materialisation hook (ADR-038); a second writer is a lost update |

## 5. File-by-file

### 5.1 Framework (4 files, all additive)

**`genesis_worker/utils/services/spec.py`** — `AuthSpec` gains two optional
strings (the schema is `extra="forbid"`, so the YAML cannot carry them
before this change):

```python
class AuthSpec(BaseModel):
    ...
    label: str = ""   # heading the status page shows above the copy control
    usage: str = ""   # what the operator does with the secret
```

**`genesis_worker/utils/services/docker_service.py`**

- `AuthConfig` gains `label: str = ""` and `usage: str = ""` (appended last, so
  the existing positional/keyword construction in tests keeps working).
- `DockerService` gains one accessor the panel can call:

```python
    def auth_config(self) -> AuthConfig | None:
        """The declared secret block, or ``None`` when the service has none."""
        return self.config.auth
```

**`genesis_worker/utils/services/loader.py`** — `_build_docker_config` maps the
two new fields into `AuthConfig`:

```python
        auth_cfg = AuthConfig(
            ...,
            label=auth.get("label") or "",
            usage=auth.get("usage") or "",
        )
```

**`genesis_worker/utils/services/panels.py`** — `_auth_token` reads the
heading and the usage line from the block; everything else is unchanged:

```python
    auth_cfg = getattr(svc, "auth_config", lambda: None)()
    source_label = (
        panel_config.get("source_label")
        or getattr(auth_cfg, "label", "")
        or "API token"
    )
    usage = getattr(auth_cfg, "usage", "") or "Authorization header: `Bearer <token>`"
    ...
    st.caption(f"{usage}  ·  source: {source_note}")
```

The JWT-mode branch and the "no token yet" warning stay as they are. The
`env_var` string in the JWT message is deliberately **not** re-sourced from
the block (crawl4ai's real var is `CRAWL4AI_JWT_ENABLED`, not
`CRAWL4AI_API_TOKEN_JWT_ENABLED` — re-sourcing it would replace one wrong
name with another; out of scope).

### 5.2 The service spec (1 file)

**`genesis_worker/services/_declarative/bifrost.yaml`**

- header comment: a `SETUP TOKEN` section explaining what upstream requires,
  where the token comes from, and that it only matters until the first admin
  account exists;
- `pre_start_hooks`: add `ensure_persistent_token` → `$state_dir/setup_token`
  (mode `0o600`, `random_hex_32`) **before** the existing
  `materialize_orchestrator_config` hook (order is irrelevant — the two touch
  different files — but the token first reads better);
- `auth:` block as in §4;
- `ui.status_panels: [auth_token]` — the panel Bifrost needs;
- `options.setup_token` — `type: string`, `optional: true`, `default: null`,
  `ui_group: Security`, with a `ui_help` saying "leave blank to use the
  generated token in the state file";
- `env:` is **not** extended with `BIFROST_SETUP_TOKEN`: `start()` injects it
  from the auth block, and a literal `$auth.token` in `env:` would only be a
  dead marker (the crawl4ai spec has one; the tutorial warns against it).

### 5.3 Tests

| file | change |
|---|---|
| `genesis_worker/tests/test_declarative_specs.py` | `test_bifrost_pre_start_hooks_includes_materialize_orchestrator_config` now sees **two** hooks — select the materialisation hook by `kind` instead of `hooks[0]` (same fix the traefik test already uses). New: `test_bifrost_setup_token_block` (env var name, file path, mode, fallback), `test_bifrost_setup_token_is_persistent_and_private` (hook generates 64 hex chars at 0600, `auth_token()` returns it, a second run reuses it), `test_bifrost_user_supplied_setup_token_wins` (the option beats the file), `test_bifrost_ui_panels_include_auth_token`. |
| `genesis_worker/tests/test_bifrost_config_materialisation.py` | `test_hook_unknown_format_raises` mutates `hooks[0]` — with the token hook first that is the wrong entry; select the materialisation hook by `kind`. The remaining tests run all hooks and stay valid (the token hook writes a different file). |
| `genesis_worker/tests/test_panels.py` | the `_MockService` stub gains `auth_config()`; the `_stub_streamlit` fixture gains `caption` / `info` / `warning` recorders (the auth panel uses all three and no test exercises it today). New: the panel uses the block's `label` + `usage`; without them it keeps today's wording. |
| `genesis_worker/tests/test_yaml_loader.py` | the `auth:` block's `label` / `usage` resolve into `AuthConfig`. |
| `genesis_worker/tests/test_docker_service_base.py` | `auth_config()` returns the block; `None` when the service has none. |
| `genesis_worker/tests/test_bifrost_setup_token.py` (new) | end-to-end of the Bifrost half: the hook generates the token, `start()` injects `BIFROST_SETUP_TOKEN` into the container env (same shape as `test_start_injects_token_env_var`), the panel is opted into. |

### 5.4 Docs

- `docs/tutorials/declarative-services.md` — the "Auth (API tokens)" section
  becomes "Auth (operator secrets)": the two new fields, the Bifrost example,
  and the note that the block owns the env var (don't declare it in `env:`).
- `docs/arch/adr-052-operator-secret-in-the-auth-block.md` — the ADR (written
  with this plan; Status: Proposed until the user ratifies the choice).
- `docs/arch/plans/plan-052-bifrost-setup-token.md` — this file.

## 6. QA plan

The in-repo gate (AGENTS.md: all four must pass):

```
uv run ruff check --fix genesis_worker     # auto-fixers first
uv run ruff format genesis_worker
uv run pytest -q                            # baseline today: 1610 passed, 37 deselected
uv run pyright                              # baseline today: 0 errors, 0 warnings
uv run ruff check genesis_worker            # baseline today: All checks passed!
uv run ruff format --check genesis_worker   # baseline today: 233 files already formatted
```

`make test` = pytest + pyright + ruff check; `make lint` = ruff check;
`make typecheck` = pyright. All green on `origin/main` in this worktree
(verified while planning).

Manual / docker smoke (not automatable in this repo):

1. Start Bifrost from the worker UI on a fresh state dir → the container env
   carries `BIFROST_SETUP_TOKEN`, and `$state_dir/setup_token` exists at 0600.
2. Open the dashboard → *Workspace → Config → Security* → enable "Password
   protect the dashboard" → paste the copied token → Save → the admin account
   is created.
3. Restart the service → the same token is still in place (the file is
   read, never rewritten).

## 7. Risks

1. **config.json vs env var precedence is undocumented upstream.** Bifrost
   accepts `setup_token` in `config.json` *and* `BIFROST_SETUP_TOKEN`. If the
   orchestrator's config body ever carries a `setup_token`, the dashboard may
   ask for a different value than the one the worker UI shows. Mitigation:
   the worker owns the env var only; the orchestrator must not put
   `setup_token` in the body it pushes. Flag it in the bifrost.yaml header.
2. **The panel's default wording stays bearer-shaped.** A spec that declares
   a non-bearer secret and forgets `label` / `usage` shows the wrong copy —
   silently. The ADR records it as a known negative.
3. **Token exposure in the UI.** The setup token is rendered in the worker's
   Streamlit UI, reachable over Tailscale — the same exposure crawl4ai's API
   token already has. The file stays 0600. The token is inert once the admin
   account exists.
4. **Upstream may rename the env var.** The spec tracks `maximhq/bifrost:latest`
   (no pin). A future release that renames `BIFROST_SETUP_TOKEN` would leave
   the dashboard saying "no setup token is configured". Mitigation: the panel
   shows the env var name in the usage text, so the mismatch is visible.
5. **Hook-order coupling in the existing tests.** `hooks[0]` is assumed by two
   bifrost tests; adding a hook breaks them unless they select by `kind`.
   Called out in §5.3.
6. **The token is only needed once.** If the operator never creates an admin
   account, the token is unused and the panel is decoration. Acceptable — it
   is the same shape as crawl4ai's.

## 8. Out of scope

- Creating the admin account / enabling the dashboard password — that stays a
  human action inside the Bifrost dashboard.
- The `env.BIFROST_SETUP_TOKEN` reference form inside `config.json` — the
  worker uses the env var directly.
- Any orchestrator-side change (the orchestrator must simply not set
  `setup_token` in the config body it pushes).
- Vaultwarden's admin token still has no panel (it is a password, not a
  bearer credential). The new `label` / `usage` fields now make a panel
  possible for it — a separate decision.
- Fixing the "source note" caption when the value comes from the user-typed
  option rather than the file (it always says "persisted on disk"). Cheap to
  fix (`auth_token_source()`), but not needed for Bifrost.

## 9. Open questions for the user

1. **Ratify the mechanism** (ADR-052, Status: Proposed): widen the `auth:`
   block to "operator secret" with the two display fields, or take the
   "new panel kind" / "general `panel_config` plumbing" route instead.
2. **Wording**: the label "Setup token" and a usage line naming Bifrost's
   *Workspace → Config → Security → Setup token* field — confirm the copy you
   want, or supply your own.
3. **Operator override**: keep the `setup_token` option (a user-typed value
   beats the generated file), as crawl4ai and vaultwarden do?
4. **Should the token also be written into bifrost's `config.json`** (as
   `{"setup_token": "env.BIFROST_SETUP_TOKEN"}`)? Not needed today — the env
   var is enough — but it would make the value visible in the file the
   orchestrator writes. Default: no (risk 1).
