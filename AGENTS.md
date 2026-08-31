# AGENTS.md

`my-agent-backend` — the Genesis Worker. Runs on each machine in the fleet and manages local AI infrastructure (llama-swap today; ComfyUI / AIToolkit / vLLM later) behind a Streamlit UI reachable over Tailscale.

## Authority

When the user's instruction in conversation conflicts with a committed doc or ADR, **follow the user**. They wrote those documents; the latest stated opinion wins. Then update the doc in the same session so it stops lying.

## Working protocol

ADRs are for architectural decisions — changes that cross the framework/plugin boundary, change a contract, introduce a new pattern, or have multiple valid alternatives with meaningful tradeoffs. Routine implementation work, bug fixes, and UI updates that don't fit those criteria don't need one.

When an ADR is warranted, write it before code. Otherwise: code directly.

Flow when an ADR is warranted:

1. Understand context — read code, ask user.
2. Identify and present design alternatives to user.
3. Help user choose one of the alternatives. DO NOT choose for user.
4. (Optional, for non-trivial work) Propose and present to user a plan — a file-by-file step list. Refine with user's input. After user explicitly approves, write plan in `docs/arch/plans/`.
5. Write the ADR in `docs/arch/`, with link to the plan (if any).
6. Then code.

If implementation diverges from the ADR during coding, update the ADR first, then commit.

## Codebase rules

- New work happens on a branch off `main`. Branch name: `feature/<kebab>`, `fix/<kebab>`, or `chore/<kebab>` (housekeeping). No direct commits to `main`.
- The agent runs the auto-fixers before finalising code:
  ```
  uv run ruff check --fix genesis_worker
  uv run ruff format genesis_worker
  ```
  These resolve import ordering, unused imports, whitespace, and quote style. Re-run the gate afterwards; any remaining lint issues need a real fix.
- Verify with these four. All must pass:
  ```
  uv run pytest -q
  uv run pyright
  uv run ruff check genesis_worker
  uv run ruff format --check genesis_worker
  ```
  `make test` is a thin wrapper around them; the four invocations are the gate.
- Tests must pass from any working directory, not just the repo root. Anchor fixture paths with `repo_root()`, never `Path("config.yaml")`. A gate that `pytest.skip`s when it can't find a file is a gate that silently stops gating.
- Do not commit until user has verified and approved.
- Commit message: one line. No body.
- A remote `origin` is configured. Workflow: merge feature branch to `main` locally with `git merge --no-ff <branch>`, then push with `git push`.
- Tests: cover core logic and module interfaces only. Skip trivial coverage.

## Comments and docstrings

Write less. The code is the documentation.

- One short line at the top of a module saying what it is about. That is usually the only prose a file needs.
- A docstring that restates the signature is worse than no docstring. `"""Return the role tag for path."""` on `def classify(path: Path) -> str` adds nothing and rots when the code changes.
- Comment only genuinely non-obvious things: why a threshold has that value, why an ordering is load-bearing, why an apparent redundancy is deliberate, a link to the ADR that explains a constraint.
- Prefer making the code self-evident over explaining unclear code. Rename the variable instead of commenting it.
- No usage examples in docstrings, no ASCII diagrams of call flow, no restating the class's attributes as a bullet list. These go stale silently and nobody notices.
- If a function needs paragraphs to explain, that is a signal about the function, not about the docs.

## Architecture: framework / extension boundary

The package splits into **framework** and **plugins**. The framework is everything except `sources/` and `services/` — the `contracts/` boundary types, the framework modules at `genesis_worker/` root, and `utils/` (self-contained helpers). Plugins are the contents of `sources/` and `services/`: a subpackage under `sources/` implements one `ModelSource`; a subpackage under `services/` implements one `InferenceService`. ADR-009 governs the boundary. It is enforced by `test_plugin_boundary.py`, which AST-walks plugin modules and fails on illegal imports.

```
genesis_worker/
  contracts/     the only genesis_worker module an extension may import
  <framework>    settings.py  facade.py  catalog.py  registries.py
  utils/         self-contained helpers importable by both sides (paths, catalog_io, models, collectors)
  sources/       extension directory — one subpackage per source
  services/      extension directory — one subpackage per service
```

A few implications worth being explicit about:

- **Extensions own their UI.** Each `ModelSource` and `InferenceService` exposes a `ui_pages` property returning the Streamlit pages it contributes; the first entry is its landing page. The framework's main UI loads these pages and stitches them into a coherent interface.
- **Extensions may import from `utils/`.** Plugins import from `genesis_worker.utils` in addition to `genesis_worker.contracts`. `utils/` is a leaf package — it imports nothing from the rest of `genesis_worker` — so plugins transitively see only stdlib and third-party code. This is how services share common helpers without each one reimplementing them.

Rules, both directions:

- **The framework never reaches behind an extension's facade.** It talks to the ABC in `contracts/` and nothing else. No importing `services.llama_swap.generate_config`, no poking at attributes the interface doesn't declare. If the framework needs something new from an extension, that need becomes a method on the ABC.
- **An extension imports only from `genesis_worker.contracts` and `genesis_worker.utils`.** Not `settings`, not `facade`, not `registries`, not a sibling extension, not anything else under `genesis_worker`. If a plugin needs something the framework exposes, that need becomes a method on the ABC or a helper in `utils/`.
- **The framework initialises extensions and passes everything they need.** Extensions resolve nothing for themselves — no reading settings, no `xdg_path()`, no `repo_root()`, no fallback chains, no `Path.home()`. Construction receives a `SourceContext` / `ServiceContext` carrying already-resolved directories plus the extension's own option slice.
- **An extension owns its options schema.** `Settings.sources` / `Settings.services` are `dict[str, dict[str, Any]]`; the framework carries a slice without interpreting it. The extension defines its own pydantic options model and parses `ctx.options` at construction. Adding an extension must not require editing `settings.py`.
- **Extensions own their paths as attributes**, derived from the context once in `__init__`. Not methods with resolution logic in them.
- Capability-gated behaviour (`can_generate_config`, `can_export_for_agent`) is declared on the ABC as optional methods, so the framework stays capability-driven instead of branching on extension names.
- **Every new `InferenceService` subclass must override `category`** to declare its dashboard group (ADR-029). The default `OTHER` is a stopgap, not a destination — the dashboard renders `OTHER` services under a less prominent heading as a nudge to update. Also override `description` with one short sentence (~25–30 chars) for the Service Catalog row; longer copy belongs on the service's own landing page.

## Stack

- Packaging: uv (single project, `dependency-groups` for dev)
- Python: 3.11
- Config: pydantic-settings (`BaseSettings`)
- UI: Streamlit
- Tests: pytest (see `docs/test-architecture.md` for layers, hermeticity rules, and naming)
- Types: pyright (`standard` mode)
- Lint and format: ruff (`check` and `format`)

## Host / environment gotchas

These are recurring pain points on the host OS (Omarchy) or Tailscale layer. They are not bugs in this codebase, but they surface as if they were. When debugging a connection/service problem, check these first.

- **Docker containers unreachable from Tailscale peers.** A container works from `127.0.0.1`, from the LAN IP, and from the host via its own Tailscale IP, but times out silently (no log line in the container) from any other Tailscale device. This is `ufw-docker`'s anti-spoofing rules dropping Tailscale's CGNAT range (`100.64.0.0/10`) into the Docker bridge. Fix: `sudo ./scripts/tailscale-docker-fix.sh install`. Full writeup: `docs/tailscale-docker.md`. Diagnosis: a non-zero packet count on the `ufw-docker-logging-deny` rule for `172.16.0.0/12` in `sudo iptables -L DOCKER-USER -n -v`.

## ADR structure and naming

- Location: `docs/arch/adr-NNN-kebab-case-title.md`. NNN is sequential, zero-padded, never reused.
- Superseded: keep the file. Change status to `Superseded by ADR-NNN`. Do not delete. Partial supersession is annotated inline at the affected section, so a reader of the old text sees the correction where it applies.
- Sections (Nygard format):
  - **Title** — short noun phrase.
  - **Context** — forces at play. Neutral language.
  - **Decision** — "We will …". Full sentences.
  - **Status** — Proposed / Accepted / Deprecated / Superseded.
  - **Consequences** — positive, negative, neutral. All listed.
- Optional extended section:
  - **Plan** — link to `docs/arch/plans/...` (only when a plan was written during execution).
