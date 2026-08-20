# AGENTS.md

`my-agent-backend` — the Genesis Worker. Runs on each machine in the fleet and manages local AI infrastructure (llama-swap today; ComfyUI / AIToolkit / vLLM later) behind a Streamlit UI reachable over Tailscale.

## Authority

When the user's instruction in conversation conflicts with a committed doc, spec, or ADR, **follow the user**. They wrote those documents; the latest stated opinion wins. Then update the doc in the same session so it stops lying.

## Working protocol

Every feature or bugfix starts with an ADR. Do not touch code first.

Flow:

1. Understand context — read code, ask user.
2. Identify and present design alternatives to user.
3. Help user choose one of the alternatives. DO NOT choose for user.
4. Propose and present to user a spec (implementation details + verification conditions). Refine with user's input. After user explicitly approve, write spec in `docs/arch/specs/`
5. Propose and present to user a plan (file-by-file step list to implement the spec). Refine with user's input. After user explicitly approve, write plan in `docs/arch/plans/`
6. Complete the ADR with links to spec and plan.
7. Then code.

If implementation diverges from spec during coding, update the spec first, then commit.

## Do not break the running service

This machine runs a live `llama-swap` against `~/.local/share/genesis-worker/llama-swap/config.yaml`. The package owns that file and regenerates it on demand; the service is started by the package (or `genesis_worker.cli.up`) and runs with `-watch-config`, so any write to that config triggers a reload.

- New code writes to XDG paths only. It never reads or writes repo-root state.
- Treat the running llama-swap as production. Writing to its config, killing the tmux session, or otherwise disrupting the live service requires user approval, not autonomy.

## Codebase rules

- New work happens on a branch off `main`. Branch name: `feature/<kebab>` or `fix/<kebab>`. No direct commits to `main`.
- Verify with these three. All must pass:
  ```
  uv run pytest -q
  uv run pyright
  uv run ruff check genesis_worker
  ```
  `make test` is a thin wrapper around them; the three invocations are the gate.
- Tests must pass from any working directory, not just the repo root. Anchor fixture paths with `repo_root()`, never `Path("config.yaml")`. A gate that `pytest.skip`s when it can't find a file is a gate that silently stops gating.
- Do not commit until user has verified and approved.
- Commit message: one line. No body.
- No remote is configured. Merge locally: on `main`, `git merge --no-ff <branch>`.
- Tests: cover core logic and module interfaces only. Skip trivial coverage.
- `uv run ruff format` is **not** used on this repo — the tree does not conform to it and reformatting creates noise diffs. `ruff check` only.

## Comments and docstrings

Write less. The code is the documentation.

- One short line at the top of a module saying what it is about. That is usually the only prose a file needs.
- A docstring that restates the signature is worse than no docstring. `"""Return the role tag for path."""` on `def classify(path: Path) -> str` adds nothing and rots when the code changes.
- Comment only genuinely non-obvious things: why a threshold has that value, why an ordering is load-bearing, why an apparent redundancy is deliberate, a link to the ADR that explains a constraint.
- Prefer making the code self-evident over explaining unclear code. Rename the variable instead of commenting it.
- No usage examples in docstrings, no ASCII diagrams of call flow, no restating the class's attributes as a bullet list. These go stale silently and nobody notices.
- If a function needs paragraphs to explain, that is a signal about the function, not about the docs.

## Architecture: framework / extension boundary

The package has two extension axes — **sources** (where models come from) and **services** (what runs them). ADR-009 governs the boundary. It is enforced by `test_plugin_boundary.py`, which AST-walks plugin modules and fails on illegal imports.

```
genesis_worker/
  contracts/     the only genesis_worker module an extension may import
  <framework>    settings.py  paths.py  facade.py  catalog_build.py  registries.py
  sources/       extension directory — one subpackage per source
  services/      extension directory — one subpackage per service
```

Rules, both directions:

- **The framework never reaches behind an extension's facade.** It talks to the ABC in `contracts/` and nothing else. No importing `services.llama_swap.generate_config`, no poking at attributes the interface doesn't declare. If the framework needs something new from an extension, that need becomes a method on the ABC.
- **An extension never touches framework internals.** It imports `genesis_worker.contracts` and its own package. Not `settings`, not `paths`, not `models`, not a sibling extension.
- **The framework initialises extensions and passes everything they need.** Extensions resolve nothing for themselves — no reading settings, no `xdg_path()`, no `repo_root()`, no fallback chains, no `Path.home()`. Construction receives a `SourceContext` / `ServiceContext` carrying already-resolved directories plus the extension's own option slice.
- **An extension owns its options schema.** `Settings.sources` / `Settings.services` are `dict[str, dict[str, Any]]`; the framework carries a slice without interpreting it. The extension defines its own pydantic options model and parses `ctx.options` at construction. Adding an extension must not require editing `settings.py`.
- **Extensions own their paths as attributes**, derived from the context once in `__init__`. Not methods with resolution logic in them.
- Capability-gated behaviour (`can_generate_config`, `can_export_for_agent`) is declared on the ABC as optional methods, so the framework stays capability-driven instead of branching on extension names.

Consequences to respect:

- A value a plugin cannot legitimately know gets **no default**. `BuildOptions.repo_root` is required because a plugin cannot know where the checkout lives; defaulting it to `Path(".")` would silently resolve against CWD.
- Module-level constants must not compute environment state at import time. `Path.home()`, `repo_root()`, and `os.environ` reads belong inside the function that needs them.
- The framework may later expose hooks for extensions to register into. Not today — the arrow points one way.

## Stack

- Packaging: uv (single project, `dependency-groups` for dev)
- Python: 3.11
- Config: pydantic-settings (`BaseSettings`)
- UI: Streamlit (spec-003)
- Tests: pytest
- Types: pyright (`standard` mode)
- Lint: ruff (`check` only)

## ADR structure and naming

- Location: `docs/arch/adr-NNN-kebab-case-title.md`. NNN is sequential, zero-padded, never reused.
- Superseded: keep the file. Change status to `Superseded by ADR-NNN`. Do not delete. Partial supersession is annotated inline at the affected section, so a reader of the old text sees the correction where it applies.
- Sections (Nygard format):
  - **Title** — short noun phrase.
  - **Context** — forces at play. Neutral language.
  - **Decision** — "We will …". Full sentences.
  - **Status** — Proposed / Accepted / Deprecated / Superseded.
  - **Consequences** — positive, negative, neutral. All listed.
- Extended sections:
  - **Spec** — link to `docs/arch/specs/...`.
  - **Plan** — link to `docs/arch/plans/...`.
