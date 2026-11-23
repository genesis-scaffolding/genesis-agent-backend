# Plan 049: ROMM permissions and provider environment

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-hog4ql-romm-config/genesis-agent-backend`,
branch `se/t-hog4ql-romm-config`, from `origin/main` @ `4eee079`)
**Task:** `t-hog4ql` — *Issues with ROMM* (the library directory is locked; the metadata-provider
env vars cannot be set).
**ADR:** `docs/arch/adr-053-compose-service-identity-and-env.md`
**Status:** implemented — the plan below is the execution reference for ADR-053.

---

## 1. Goal

Two user-reported defects in the ROMM declarative service
(`genesis_worker/services/_declarative/romm.yaml`, `kind: compose`):

- **Issue 1 — the library directory is "locked".** The media-vault ROM directory is
  bind-mounted into the ROMM container, but everything ROMM writes there is owned by
  **root**, so the desktop user cannot add, rename or delete ROMs.
- **Issue 2 — the provider configuration is unreachable.**
  `SCREENSCRAPER_USER` / `SCREENSCRAPER_PASSWORD` (and every other metadata-provider env
  var ROMM reads) cannot be set from the worker; the compose template hard-codes its
  `environment:` block.

The fix is a framework capability (the compose kind gains a user-supplied environment) plus a
spec change (the ROMM service uses it, and declares the uid/gid it runs as).

## 2. What the investigation found

- The upstream image has **no `USER` directive** → the container runs as uid 0. The image
  creates a `romm` user (uid/gid 1000) and chowns `/romm` + `/redis-data` to it, and the
  upstream init script supports a non-root container
  (`if [[ ${EUID} -ne 0 ]]; then nginx; else nginx -g 'user romm;'; fi`).
- `ComposeService._ensure_volume_dirs()` walks `config.extra_volumes`, which is **empty**
  for a compose spec — a compose spec declares its mounts inside the template, so the
  template's host paths are never pre-created and Docker creates the missing ones as root.
- The upstream entrypoint implements the `_FILE` convention for any `*_FILE` variable
  (`/docker-entrypoint.sh`), and the init script generates a **random** `ROMM_AUTH_SECRET_KEY`
  on every start when it is unset — so every restart invalidates the sessions.
- The compose `environment:` block wins over `env_file:`, and compose's env-file parser
  (compose-go `dotenv`) reads a double-quoted value like a shell (`\\`, `\"`, `\$`, `\n`,
  `\r`, `\t`); a single-quoted value keeps its backslashes verbatim.

## 3. Design

See ADR-053. In short:

1. The service declares the uid/gid it runs as (`user:` in the template); the framework's
   `ensure_writable_dir` hook makes the host side match.
2. The compose kind gains an `env_file` field: the framework writes the user's `extra_env`
   map there (0600, atomic) and the template references it with compose's `env_file:`.
3. `extra_env` means the container's environment in both kinds; the compose **process**
   environment is a separate field (`compose_env`).
4. The loader rejects a spec with an `extra_env` option and no `env_file`, and a spec whose
   template does not reference the declared env file.

## 4. File-by-file

| File | Change |
|---|---|
| `genesis_worker/utils/services/env_file.py` (new) | `write_env_file(path, values)` — compose-compatible quoting (double-quoted values), mode 0600, atomic write. |
| `genesis_worker/utils/services/spec.py` | `ComposeServiceSpec.env_file: str \| None` — the host path of the framework-managed env file. |
| `genesis_worker/utils/services/compose_service.py` | `ComposeServiceConfig` gains `env_file`, `user_env`, `compose_env`; `extra_env` is now the container's environment. `start()` writes the env file before the stack comes up. |
| `genesis_worker/utils/services/loader.py` | the user's map reaches the config as `user_env`; the env-file validations. |
| `genesis_worker/services/_declarative/romm.yaml` | `user:` on the app container; the `puid` / `pgid` options; the `ensure_writable_dir` + `ensure_persistent_token` hooks; the `env_file` field, the template's `env_file:` and the `extra_env` option; `ROMM_AUTH_SECRET_KEY_FILE` + its mount; a header that explains the permission model. |
| `genesis_worker/tests/test_compose_env_file.py` (new) | the writer (quoting, mode, atomicity, rewrite) and the compose service (the file exists before `up`). |
| `genesis_worker/tests/test_yaml_loader.py` | the two env-file validations and the happy path. |
| `genesis_worker/tests/test_declarative_specs.py` | the ROMM section: the `user:` line, the hooks, the env option, the env-file reference, the persisted secret. |
| `genesis_worker/tests/test_hooks.py` | `ensure_writable_dir` with a placeholder-resolved int uid/gid. |
| `docs/arch/adr-053-compose-service-identity-and-env.md` (new) | the ADR. |
| `docs/arch/plans/plan-049-romm-permissions-and-provider-env.md` (new) | this plan. |
| `docs/tutorials/declarative-compose-services.md` | the compose kind's env story, the `user:` / permission pattern, the worked ROMM example. |
| `docs/tutorials/declarative-services.md` | the collision rule (the docker kind's map wins; the compose kind's typed knobs win). |

## 5. Tests

| File | Covers |
|---|---|
| `test_compose_env_file.py` | `write_env_file` quotes the special characters compose unescapes, keeps a `#` and spaces inside the value, writes 0600, rewrites whole, rejects an invalid name; the compose service writes the file before `up` and the map stays in the process environment; a spec without an `env_file` writes none. |
| `test_yaml_loader.py` | an `extra_env` option without an `env_file` is rejected; an `env_file` the template does not reference is rejected; the matching pair loads and carries the user's map. |
| `test_declarative_specs.py` | ROMM runs as `1000:1000` (and follows `puid` / `pgid`); the two `ensure_writable_dir` hooks carry the resolved int uid/gid; `extra_env` is an `env_map` that reaches the config; the template references the env file; the auth secret is persisted and mounted. |
| `test_hooks.py` | a `$options.X` uid stays an int through the loader, so the hook chowns instead of taking the world-writable fallback. |

## 6. QA gate

```bash
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

## 7. Rollout

The files ROMM already created as root stay root-owned — the framework cannot chown what it
does not own. One-time remediation on the host:

```sh
sudo chown -R "$(id -u):$(id -g)" ~/media/roms ~/.local/state/genesis-worker/romm
```

Verified out of band on the host: the stack comes up as `1000:1000`, the health check is
healthy, the files ROMM writes into the library are owned by the host user, the
`SCREENSCRAPER_*` variables arrive with their exact value, and the persisted
`ROMM_AUTH_SECRET_KEY` is read from the mounted file.
