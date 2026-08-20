# Plan-017: Replace the repo Makefile

## Step 1 — Write the new Makefile

**File:** `Makefile` (overwrite)

Write the target table from `spec-017`. Single file, no other repo changes in this step.

## Step 2 — Verify

```sh
make help
make install
make test-fast
make lint
make typecheck
make env-init      # second time — should print "already exists"
make clean
```

Each target must match its `spec-017` description. `make test` (the combined gate) is the final check.

## Step 3 — Delete the old Makefile's downstream assumptions

After the rewrite, the legacy `bin/` scripts are already gone (commit `f4e039d`). No source files reference the old Makefile targets. `grep -rE 'make (catalog|config|all|up|install-model|pi-)' --exclude-dir=.git --exclude-dir=.venv .` should return no matches.

## Step 4 — Update docs

- Mark ADR-008 migration as complete (see ADR diff below).
- Add ADR-017 anchoring this rewrite (the spec/plan are already written).
- Update AGENTS.md: the "Do not break the running service" section currently claims the running llama-swap is against `config.yaml` in the repo root, and the "Codebase rules" section says the Makefile is frozen. Both are no longer true (the running llama-swap reads `~/.local/share/genesis-worker/llama-swap/config.yaml`; the Makefile is being replaced here).
