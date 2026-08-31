# Test architecture

Conventions and rules for the pytest suite in `genesis_worker/tests/`.
The goal is a suite that's hermetic by default — running it must never
touch the developer's real install, real services, or real state.

## Layers

Every test is one of three kinds. Pick the smallest layer that
covers what you're testing.

| Layer | Touches | When to use |
|---|---|---|
| **Pure unit** | Stdlib + the module under test, everything else mocked | Logic, parsing, dataclass behavior, schema validation |
| **Hermetic** | tmp_path + monkeypatch; no real subprocesses, no real network | Plugin wiring, facade wiring, lifecycle calls on the plugin instance |
| **Integration** | Real subprocess / real network / real docker, opt-in via `@pytest.mark.integration` | End-to-end flows that can't be meaningfully mocked |

**Default: hermetic.** Pure unit is finer-grained and worth the extra
mocking for things like parsing; integration is opt-in because it has
real side effects on the dev box.

## Hermeticity rules

These exist because a test suite that touches real services can silently
break a developer's running stack. The most expensive bug we hit:
`test_facade_stop_service_returns_stop_result` called
`w.stop_service("llama_swap")` against the live worker, killing the
dev-loop llama-server on every `pytest` run.

### Never write to a real path

Every test that constructs `GenesisWorker()` or `Settings()` must pass
explicit `PathsSettings` rooted in `tmp_path`:

```python
from genesis_worker.settings import PathsSettings, Settings


def test_xxx(tmp_path: Path) -> None:
    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
```

Or set `XDG_*_HOME` env vars to `tmp_path`-rooted dirs:

```python
monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
```

The bootstrap (`enabled_services.yaml` first-run auto-enable) is
otherwise driven by what's installed on the dev box, which makes tests
non-deterministic across environments.

### Never call a real subprocess / process for the unit under test

If a test verifies `worker.start_service("llama_swap")`, monkeypatch
the service's `start` method to return a synthetic `StartResult`:

```python
monkeypatch.setattr(
    LlamaSwapService,
    "start",
    lambda self: StartResult(ok=True, message="mock-start"),
)
```

This is **the no-mock-the-target rule**, inverted: mock the *callee*,
not the function you're testing. Tests that mock the function under
test are worse than no test — they verify the mock works, not the
code.

### Don't make timeouts non-injectable

If a lifecycle function has a hardcoded `time.sleep(...)` loop with a
production timeout, expose the timeout as a parameter with a default.
Tests that exercise the timeout path should pass a short value
(e.g. `graceful_timeout_s=0.1`) instead of waiting the full
production window.

The `cptr.stop_cptr(graceful_timeout_s=...)` parameter exists
because of this rule.

### Real-subprocess calls in tests must use `capture_output=True` and `check=False`

The teardown path in `test_lifecycle.py` calls real `tmux kill-session`
to clean up. That's tolerable — it doesn't read or write project
state — but it must be wrapped so a non-zero return code doesn't fail
the test:

```python
subprocess.run(["tmux", "kill-session", "-t", session], check=False, capture_output=True)
```

Don't add new tests that invoke real subprocesses without this wrap.

## Fixtures and helpers

### `tmp_path` is the default isolation boundary

Every test should either take `tmp_path: Path` as a parameter and use
it for state, or monkeypatch `XDG_*_HOME`. If neither is feasible, the
test belongs in the integration layer (opt-in marker).

### `genesis_worker.tests._factories` — placeholder for shared builders

The repo has a `_factories.py` stub; future shared test builders (e.g.
a `hermetic_worker(tmp_path)` factory that returns a `GenesisWorker`
with isolated state) should land there. Inline construction is
acceptable in the meantime, but if the same `Settings(paths=...)`
pattern appears in three or more tests, extract a factory.

### CLI smoke tests

`test_cli_smoke.py` invokes CLIs as real subprocesses by design
(`python -m genesis_worker.cli.up --help`). This is the right pattern
for a smoke test — there's no value in mocking the CLI to test the
CLI. Keep these tests but never extend them beyond `--help` checks.

The four `test_cli_help_exits_zero[*]` tests are marked
`@pytest.mark.integration` for this reason. Skip them in fast unit
runs with `pytest -m "not integration"`.

### Running integration tests

By default, `pytest` runs every test including integration ones. To
run only fast unit + hermetic tests:

```bash
pytest -m "not integration"
```

To run only integration tests (e.g. in a CI job):

```bash
pytest -m integration
```

The default stays unchanged so existing workflows don't break — but
the marker gives everyone a knob when they want it.

## The "ask first" rule

Before adding a new test that needs real network, real docker, real
subprocesses on the dev box, or any other system resource that lives
outside `tmp_path`, ask the user. The suite cannot grow silent
dependencies on the developer's environment.

A test that "works on my machine" but depends on a running llama-swap
is a test that breaks someone else's machine. The default answer is
"make it hermetic." Integration tests are an exception, not a
default.

## Naming

`test_<unit>_<scenario>_<expected>`:

- `test_facade_stop_service_returns_stop_result` — facade wiring, the
  stop-service call returns a `StopResult`
- `test_cptr_lifecycle_force_kill_when_graceful_stalls` — cptr
  lifecycle, force-kill when graceful stall

A test name is a contract. If you can't read it and predict the
assertions, the name is wrong.

## Coverage

We target ~80% overall. The remaining gaps cluster in three places,
all by design:

- **Streamlit page modules** (`genesis_worker/ui/*.py`,
  `services/*/ui/*.py`) — Streamlit executes these at runtime; pytest
  never imports them. Coverage tools report 0%; this is not a real
  gap. Use Streamlit's `AppTest` framework if you need to assert on
  rendered output.
- **Shared UI helpers** (`utils/ui/_service_controls.py`,
  `_install_flow.py`) — same reason: only invoked from inside
  Streamlit pages. Adding tests that mock `st.button`/`st.badge`/
  `st.fragment` is heavy and low-value; prefer `AppTest` over
  hand-rolled mocks.
- **Branch conditionals** that depend on real I/O results — covered
  by the integration tests when those exist.

Coverage is a hygiene metric, not a goal. Don't add tests that
exist only to bump a number; add tests that verify behavior the
suite would otherwise miss.

## See also

- `AGENTS.md` — pointer to this document
- `genesis_worker/tests/_factories.py` — shared test builders
- `genesis_worker/tests/test_plugin_boundary.py` — the AST-walking
  guard that enforces the framework/plugin import boundary at test
  time
