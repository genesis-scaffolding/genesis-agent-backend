# Plan 055: Provider-derived credential environment variable

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-2z2wtc-traefik-env/genesis-agent-backend`,
branch `se/t-2z2wtc-traefik-env`, from `origin/main` @ `4eee079`)
**Task:** `t-2z2wtc-traefik-env` — *the traefik spec renders the ACME credential as
`GODADDYV3_PERSONAL_ACCESS_TOKEN` regardless of `dns_provider`*.
**ADR:** `docs/arch/adr-055-provider-derived-credential-env.md`
**Status:** implemented — the plan below is the execution reference for ADR-055.

**Task.** The ingress proxy's DNS credential env var is hard-wired to
`GODADDYV3_PERSONAL_ACCESS_TOKEN` regardless of the selected `dns_provider`.
A non-GoDaddy provider never receives its credential.

**Repos.** Worker: `genesis-agent-backend` (this task). Orchestrator
(`genesis-infrastructure-toolkit`): follow-up only, see §9.

**Branch.** `se/t-2z2wtc-traefik-env` in
`/home/gentran/Documents/worktrees/t-2z2wtc-traefik-env/genesis-agent-backend`.

**Decision record.** `docs/arch/adr-055-provider-derived-credential-env.md`
(written in the worktree by this planning stage; status *Proposed*).

---

## 0. Blocker to clear first: the branch base

The worktree branch was cut from `main @ 703a24e`, which **predates the
traefik spec**. The spec and its tests exist only on `origin/main @ a00dbb4`
("Traefik ingress proxy service with orchestrator-supplied options (t-famzp6,
worker workstream) (#9)"):

```
$ git -C /home/gentran/Documents/genesis-agent-backend ls-tree -r --name-only origin/main -- genesis_worker/services/_declarative/ | grep traefik
genesis_worker/services/_declarative/traefik.yaml
$ git -C ... ls-tree -r --name-only 703a24e -- genesis_worker/services/_declarative/ | grep traefik
   (nothing)
```

So the file this task is about does not exist in the worktree yet. **Step 0 of
the implementation:** fast-forward the branch onto `origin/main`
(`a00dbb4` is a direct child of `703a24e`, so this is a clean fast-forward):

```bash
cd /home/gentran/Documents/worktrees/t-2z2wtc-traefik-env/genesis-agent-backend
git merge --ff-only origin/main      # or: git rebase origin/main
```

Everything below assumes the branch is on `a00dbb4` or later. The remote's
`main` is `a00dbb4` (`git ls-remote origin` confirmed); the local `main`
checkout is one commit behind and is **read-only reference** — do not write
there.

**ADR number.** At planning time the highest ADR on `origin/main` was
`adr-044` and the sibling branch `se/t-abx64j-adopt-ports` claimed `ADR-045`.
Before this plan was gated, both had merged to `origin/main` (`ADR-045`, and
`t-famzp6`'s ADR renumbered to `ADR-046`), so the plan-stage ADR was renumbered
046 -> 047 by the coordinator on 2026-11-08. `main` moved on again: it now
carries `adr-047-vault-backed-model-stores.md`, and 052–054 are claimed by live
branches (`t-au025m`, `t-hog4ql` / `t-5wcd53`, `t-jmu05l`). ADR numbers are
sequential and never reused, so the implementation renamed this task's ADR
**047 -> 055**. (Note: `origin/main` also carries two `adr-042-*` files — a
pre-existing numbering collision, not this task's problem.)

---

## 1. Goal

The proxy's DNS-01 credential must reach the container under the environment
variable that Lego — the ACME client embedded in the Traefik image — reads for
the **selected** provider. Changing the `dns_provider` option must be enough:
no second repository's release, no second option to keep in sync.

Non-goals (see §9): serving providers that need more than one credential
(`route53`, `ovh`, `azuredns`); changing the orchestrator's Ingress page;
pinning the Traefik image to a patch tag.

---

## 2. What the investigation found

### 2.1 The defect (worker)

`genesis_worker/services/_declarative/traefik.yaml` (on `origin/main`):

```
144   dns_provider:
145     type: string
146     default: "godaddyv3"
148     ui_help: "Traefik DNS-01 provider (lego): godaddyv3 … cloudflare, route53, ovh, azure, digitalocean, gandi, linode, vultr. …"
150   dns_api_token:
151     type: string
152     default: ""
…
185       environment:
186         # Lego (embedded in the official image) reads the GoDaddy
187         # Personal Access Token from the environment.
188         GODADDYV3_PERSONAL_ACCESS_TOKEN: "$options.dns_api_token"
…
204         - --certificatesResolvers.letsencrypt.acme.dnschallenge.provider=$options.dns_provider
```

The provider is a variable (line 204); the credential's variable name is a
literal (line 188). Selecting `cloudflare` produces a container that asks
Cloudflare for a TXT record while handing it a GoDaddy PAT under a name
Cloudflare never reads.

Existing test that pins the current (broken for other providers) behaviour:
`genesis_worker/tests/test_traefik_service.py:145`
(`test_rendered_compose_carries_the_godaddy_token`).

### 2.2 How the spec's env vars are rendered today

* The declarative loader is `genesis_worker/utils/services/loader.py`.
  `resolve_placeholders` (line 252) walks the spec; **dict keys are not
  substituted, only values** (lines 280-287). So a *top-level* `env:` map
  cannot have a templated key.
* **The compose template is one string** (`compose_template: str` in
  `spec.py:294`), substituted as text — so a placeholder inside it is replaced
  *wherever* it appears, including where it is a key of the YAML the template
  carries.
* `_resolve_placeholder` (line 199) ends with `return f"${key}"` for a key it
  does not know — that is how `$images.<name>` and `$auto_port.<name>`
  survive the load-time rounds and are materialised later
  (`compose_service.py:189` for the images, `loader.py:604` round C).
* `resolve_placeholders` already carries **two optional resolution sources**
  (`images=`, `ports=`). A third (`credential_env=`) is the same pattern
  (ADR-043's rule: one mechanism, one `$` namespace, one more resolution
  source).

**Probe (run in this worktree, `uv run python /tmp/probe_env_name.py`):** a
synthetic compose spec with the option `dns_credential_env` and the template
line `"$options.dns_credential_env": "$options.dns_api_token"` renders:

```
      environment:
        "GODADDYV3_PERSONAL_ACCESS_TOKEN": ""
```
and with `dns_credential_env=CLOUDFLARE_DNS_API_TOKEN`:
```
      environment:
        "CLOUDFLARE_DNS_API_TOKEN": "cf-token"
```
`yaml.safe_load` of the rendered template gives
`{'CLOUDFLARE_DNS_API_TOKEN': 'cf-token'}` — the key is a real mapping key.

**Conclusion:** the env var *name* **can** be templated from an option today,
with no loader change. What the loader cannot do today is *choose* that name
from the provider's value — there is no map and no conditional in the spec
DSL (`spec.py:438` `_OPTION_TYPE_BUILDERS` is the closed option vocabulary:
`string, int, float, bool, path, port, env_map, mount_map, list[*]`).

### 2.3 Why the choice must be made in the worker

* The orchestrator's Ingress page (`genesis_orchestrator/ui/pages/5_Ingress.py`)
  has a free-text "DNS provider" field and pushes `dns_provider`,
  `dns_api_token`, `acme_email` in the config body's `options` section
  (`shared_utilities/ingress/shared_utility.py:125-140`). The worker applies
  them through ADR-042's sidecar + rebuild path
  (`genesis_worker/facade.py:479`).
* If the mapping lived in the Ingress page, the admin would have to supply a
  software-internal name (`CLOUDFLARE_DNS_API_TOKEN`) next to the provider, and
  the fix would depend on a second repository's release. The mapping is a
  property of Traefik + Lego, not an admin-facing setting, and the proxy's
  spec already owns the provider option, the image pin and the credential.
* A framework-level table of Lego providers would put service knowledge in
  `genesis_worker/utils/` — against the framework/plugin boundary (ADR-009).

### 2.4 The provider → credential env var map (Lego)

`traefik:v3.7` embeds **Lego v5.6.0** (Traefik `v3.7.17`'s `go.mod`;
`v3.7.0`–`v3.7.16` embed Lego v4.35.2 / v5.2.2–v5.5.2). The provider name
Traefik passes to Lego is resolved by `dns.NewDNSChallengeProviderByName`
(`traefik/pkg/provider/acme/provider.go:352`, v3.7) → the names are Lego's own
(`lego/providers/dns/zz_gen_dns_providers.go`).

| Provider (Traefik/Lego name) | Lego reads | Single credential? | Source |
|---|---|---|---|
| `godaddyv3` | `GODADDYV3_PERSONAL_ACCESS_TOKEN` | yes | <https://go-acme.github.io/lego/dns/godaddyv3/> · `lego/providers/dns/godaddyv3/godaddyv3.go` (`envNamespace = "GODADDYV3_"`) |
| `cloudflare` | `CLOUDFLARE_DNS_API_TOKEN` (scoped token; `CLOUDFLARE_ZONE_API_TOKEN` for a second, zone-read token) | yes (scoped token) | <https://go-acme.github.io/lego/dns/cloudflare/> · `cloudflare.go` (`EnvDNSAPIToken = envNamespace + "DNS_API_TOKEN"`) — the legacy `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY` pair is the alternative form |
| `digitalocean` | `DO_AUTH_TOKEN` | yes | <https://go-acme.github.io/lego/dns/digitalocean/> · `digitalocean.go` (`envNamespace = "DO_"`, `EnvAuthToken = envNamespace + "AUTH_TOKEN"`) |
| `gandi` (Gandi Classic) | `GANDI_API_KEY` | yes | <https://go-acme.github.io/lego/dns/gandi/> · `gandi.go` |
| `gandiv5` (Gandi Live DNS v5) | `GANDIV5_PERSONAL_ACCESS_TOKEN` (`GANDIV5_API_KEY` is the deprecated form) | yes | <https://go-acme.github.io/lego/dns/gandiv5/> · `gandiv5.go` |
| `linode` (Linode v4; alias `linodev4`) | `LINODE_TOKEN` | yes | <https://go-acme.github.io/lego/dns/linode/> · `linode.go` |
| `vultr` | `VULTR_API_KEY` | yes | <https://go-acme.github.io/lego/dns/vultr/> · `vultr.go` |
| `route53` | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ `AWS_REGION`, `AWS_HOSTED_ZONE_ID`) | **no** — two secrets | <https://go-acme.github.io/lego/dns/route53/> |
| `ovh` | `OVH_APPLICATION_KEY` + `OVH_APPLICATION_SECRET` + `OVH_CONSUMER_KEY` (+ `OVH_ENDPOINT`), or `OVH_ACCESS_TOKEN` | **no** — three secrets | <https://go-acme.github.io/lego/dns/ovh/> |
| `azuredns` | `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (+ `AZURE_ENVIRONMENT`) | **no** — five values | <https://go-acme.github.io/lego/dns/azuredns/> |

Two corrections to the task brief's assumptions, confirmed against Lego's
source:

* Cloudflare does **not** read `CLOUDFLARE_API_TOKEN`; it reads
  `CLOUDFLARE_DNS_API_TOKEN` / `CLOUDFLARE_ZONE_API_TOKEN` (or the legacy
  e-mail + global key pair).
* DigitalOcean does **not** read `DIGITALOCEAN_API_TOKEN`; it reads
  `DO_AUTH_TOKEN`. Vultr reads `VULTR_API_KEY`, not `VULTR_API_TOKEN`.

The names are identical in Lego v4.35.2 and v5.6.0 for every provider in the
table (checked in both source trees).

### 2.5 The provider list the Ingress page offers (and two errors in it)

`genesis_orchestrator/ui/pages/5_Ingress.py:121-132` (toolkit, branch
`se/t-famzp6-reverse-proxy`) offers a free-text field whose help text says:
"godaddyv3 …, cloudflare, route53, ovh, azure, digitalocean, gandi, linode,
vultr". The worker's `dns_provider` ui_help (`traefik.yaml:148`) repeats it.

* `azure` is **not** a Lego provider name — it is `azuredns`
  (`lego/providers/dns/zz_gen_dns_providers.go`: `case "azuredns":`).
  Typing `azure` makes Traefik reject the resolver.
* `route53`, `ovh` and `azuredns` need more than one credential value, so the
  proxy's single `dns_api_token` cannot serve them.

Both facts are in the same help strings this task touches; fixing the worker's
copy is in scope (§4.4), fixing the toolkit's copy is a follow-up (§9).

### 2.6 Where the failure must land

`ServiceRegistry.__init__` loads **every** declarative spec
(`genesis_worker/registries.py:182-191`) and does not catch loader errors — a
load-time failure takes the whole worker's UI and API down. The provider is a
*runtime* value (orchestrator push or Configure panel), so an unmapped provider
must fail the proxy's **start**, not the worker's startup.

`compose_service.py` already has the right shape for this: `_render_compose_file`
(233) renders, runs `assert_no_markers` (247) and writes the file `0600` (251).
The compose kind's `extra_env` is *not* a way out: for the compose kind it is
the environment of the `docker compose up` **process**
(`compose_stack.py:74-96`), not the container's — only the template's
`environment:` block reaches the container.

---

## 3. The decision

Recorded in `docs/arch/adr-055-provider-derived-credential-env.md`
(**Proposed**).

A declarative spec may declare which of its options selects a credential's
environment variable name, and which value of that option maps to which
variable:

```yaml
credential_env:
  provider_option: dns_provider
  by_provider:
    godaddyv3: GODADDYV3_PERSONAL_ACCESS_TOKEN
    cloudflare: CLOUDFLARE_DNS_API_TOKEN
    digitalocean: DO_AUTH_TOKEN
    gandi: GANDI_API_KEY
    gandiv5: GANDIV5_PERSONAL_ACCESS_TOKEN
    linode: LINODE_TOKEN
    vultr: VULTR_API_KEY
```

The template writes the credential under that name:

```yaml
      environment:
        "$credential_env": "$options.dns_api_token"
```

`$credential_env` is a new key in the existing `$` namespace, resolved by the
existing `resolve_placeholders`, at the existing rounds: it survives the
load-time rounds (unknown-key fallback, like `$images.X`) and is materialised
at render time, when the service knows the current value of the provider
option. An unmapped provider leaves the marker in the rendered command and the
render refuses to write the file — the proxy's start fails with a message that
names the provider and the known ones; the worker stays up.

Rejected alternatives (and why) are in the ADR: an explicit
`dns_credential_env` option (two options that can drift; pushes a
software-internal name into the admin UI), emitting every provider's variable
(the credential is published under names that do not apply), and a new option
type (the option DSL is deliberately closed, ADR-035).

---

## 4. Files and areas touched

All paths are relative to the repo root. Line numbers refer to `origin/main
@a00dbb4`.

### 4.1 `genesis_worker/utils/services/spec.py`

* New `CredentialEnvSpec(BaseModel)`, `extra="forbid"`:

  ```python
  class CredentialEnvSpec(BaseModel):
      provider_option: str
      by_provider: dict[str, str]
  ```

* `ComposeServiceSpec` gains `credential_env: CredentialEnvSpec | None = None`.
  Compose kind only: the docker kind substitutes environment *values*, not
  environment *keys*, so a templated variable name cannot be expressed there;
  the uv kind has no environment block.
* Export `CredentialEnvSpec` in `__all__`.

### 4.2 `genesis_worker/utils/services/loader.py`

* `resolve_placeholders`, `_resolve_string`, `_resolve_placeholder` gain one
  keyword-only parameter `credential_env: str | None = None` (alongside
  `images` and `ports`).
* `_resolve_placeholder` gains one branch, mirroring the `images.` branch:

  ```python
  if key == CREDENTIAL_ENV_KEY:          # "credential_env"
      return credential_env if credential_env is not None else f"${key}"
  ```

  With no resolution source the marker survives the load-time rounds — the
  same fallback that keeps `$images.X` intact.
* `load_service_spec` validates the block at load time (a spec error belongs
  there): `credential_env.provider_option` must name one of the spec's own
  options, otherwise `ValueError` naming the spec and the option. A block
  whose `by_provider` is empty is rejected for the same reason.
* No new substitution round, no second templating language.

### 4.3 `genesis_worker/utils/services/compose_service.py`

* `ComposeServiceConfig` gains `credential_env: CredentialEnv | None = None`,
  where `CredentialEnv` is a small frozen dataclass in the same module
  (`provider_option: str`, `by_provider: Mapping[str, str]`) — the same
  pattern as `ComposeImage` (built from the resolved dict in
  `_build_compose_config`).
* `ComposeService._credential_env_name() -> str | None` — reads the current
  value of the named option off `self.options` (the service's validated
  options model, `utils/services/base.py:103`) and looks it up in
  `by_provider`. **Returns `None`, never raises** — `is_available()` renders
  the template too, and the catalogue page must not break on a bad provider.
* `ComposeService._rendered_template()` passes `credential_env=self._credential_env_name()`
  to `resolve_placeholders`.
* `ComposeService._render_compose_file()` refuses to write the file when the
  marker survived, with a message that names the cause:
  * provider selected but not in the map →
    `traefik: the provider 'azure' (option 'dns_provider') has no credential environment variable — add it to the spec's credential_env.by_provider map (known: cloudflare, digitalocean, gandi, gandiv5, godaddyv3, linode, vultr)`
  * template uses `$credential_env` but the spec declares no block →
    `traefik: the compose template uses $credential_env but the spec declares no credential_env block`.

### 4.4 `genesis_worker/services/_declarative/traefik.yaml`

* Add the `credential_env:` block (§3) with the seven single-credential
  providers of §2.4, and a comment naming the source
  (`https://go-acme.github.io/lego/dns/<provider>/`, Lego v5.6.0 as embedded in
  `traefik:v3.7`).
* Replace line 188 with `"$credential_env": "$options.dns_api_token"`, and
  rewrite the comment above it (the current one says the credential *is* the
  GoDaddy PAT).
* Update the header block (the "TLS — DNS-01, not HTTP-01" section, lines
  34-52): the credential's variable name now follows the provider; the
  GoDaddy PAT paragraph stays as the default case; the sentence "Another
  provider needs its own credential variable, so switching providers is not only
  this field" becomes "the variable follows the provider (see `credential_env`)".
* Update the `dns_provider` ui_help (line 148): drop `azure` (the name is
  `azuredns`) and say which providers the single `dns_api_token` can serve.
  Keep the change to the words only — no new option, no new field.
* `dns_api_token`'s ui_help (line 154): mention that the token is written
  under the provider's own variable.

### 4.5 `genesis_worker/tests/test_traefik_service.py`

* Keep `test_rendered_compose_carries_the_godaddy_token` (line 145) — it must
  still pass unchanged: the default provider renders
  `GODADDYV3_PERSONAL_ACCESS_TOKEN: "the-token"`.
* New: provider `cloudflare` → `CLOUDFLARE_DNS_API_TOKEN: "the-token"` and
  **no** `GODADDYV3_…` in the rendered file.
* New: provider `digitalocean` → `DO_AUTH_TOKEN`.
* New: provider `gandiv5` → `GANDIV5_PERSONAL_ACCESS_TOKEN` (the PAT form, not
  the deprecated API-key form).
* New: an unmapped provider (`azure`) → the **service still loads** (the
  worker is not broken) and `_render_compose_file` raises with the provider
  name and the known list.
* New: the orchestrator-pushed path (ADR-042): `_apply_orchestrator_options`
  with `{"options": {"dns_provider": "cloudflare", "dns_api_token": "t"}}` →
  the rendered file carries `CLOUDFLARE_DNS_API_TOKEN: "t"`.
* New: the rendered file is still `0600` (the existing test at line 150 keeps
  passing — the credential is still in the file).

### 4.6 `genesis_worker/tests/test_yaml_loader.py`

* The placeholder survives when no resolution source is supplied
  (`resolve_placeholders(..., credential_env=None)` → `"$credential_env"`).
* The placeholder resolves when a name is supplied.
* A spec whose `credential_env.provider_option` names no option is rejected at
  load time.

### 4.7 `docs/tutorials/declarative-services.md`

* New subsection under "The `$variable` substitution" (after "Auto-port
  placeholders", line 229): "Credential env placeholders (`$credential_env`)" —
  the block, the placeholder, the render-time resolution, the refusal, and the
  note that it is compose-only.
* The "Limitations (when to fall back to Python)" section (line 799) is
  checked for a bullet that this mechanism supersedes.

### 4.8 `docs/arch/adr-055-provider-derived-credential-env.md`

* **Already written by the planning stage** (in the worktree, untracked).
  The implementer re-reads it and updates it if the implementation diverges
  (AGENTS.md: "If implementation diverges from the ADR during coding, update
  the ADR first").

### 4.9 Not touched

* `genesis_worker/facade.py` (the ADR-042 options path already carries
  `dns_provider` and `dns_api_token` — no change).
* The orchestrator repo (follow-up, §9).
* `genesis_worker/utils/ports/markers.py` (`assert_no_markers` stays
  `$auto_port.`-only; the credential marker has its own check in the compose
  render).

---

## 5. Implementation order

1. **Fast-forward the branch** onto `origin/main` (§0). Re-run the gate —
   the traefik spec and its tests now exist.
2. `spec.py`: `CredentialEnvSpec` + the `ComposeServiceSpec` field.
3. `loader.py`: the `credential_env` resolution source, the
   `_resolve_placeholder` branch, the load-time validation of the block.
4. `compose_service.py`: `CredentialEnv` dataclass, the config field, the
   lookup, the render-time refusal.
5. `traefik.yaml`: the `credential_env` block, the template line, the header
   and ui_help updates.
6. Tests: `test_yaml_loader.py`, then `test_traefik_service.py`.
7. Tutorial section.
8. The gate (§6).
9. Re-read ADR-055 against the diff; amend the ADR if anything moved.

---

## 6. QA plan

The repo's four checks are the gate (AGENTS.md). Run from the repo root:

```bash
cd /home/gentran/Documents/worktrees/t-2z2wtc-traefik-env/genesis-agent-backend
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

Targeted runs while iterating:

```bash
uv run pytest -q genesis_worker/tests/test_traefik_service.py genesis_worker/tests/test_yaml_loader.py
uv run pytest -q genesis_worker/tests/test_declarative_specs.py genesis_worker/tests/test_services_registry.py
```

Manual check of the rendered file (no docker needed — the render is pure text):

```bash
uv run python - <<'PY'
from pathlib import Path
import tempfile
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import ComposeService, load_service_spec
spec = Path("genesis_worker/services/_declarative/traefik.yaml")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    svc = load_service_spec(spec, ctx=service_ctx(root, name="traefik", options={
        "dns_provider": "cloudflare", "dns_api_token": "tok"}))
    assert isinstance(svc, ComposeService)
    print(svc._render_compose_file(ports=svc.port_binder().resolver()).read_text())
PY
```

Expect `CLOUDFLARE_DNS_API_TOKEN: "tok"` in the `environment:` block and no
`GODADDYV3_…` line.

**Baseline recorded in this worktree before any change** (branch at `703a24e`):

```
$ uv run pytest -q
1436 passed, 37 deselected, 1 warning in 40.31s
```

(After the fast-forward to `a00dbb4` the suite grows by the traefik tests; the
implementer records the new baseline and must not regress it.)

---

## 7. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The branch does not contain the traefik spec (it was cut before `a00dbb4`) | **blocker** | §0 — fast-forward before touching anything; the spec's real line numbers are the `origin/main` ones quoted in §2.1 |
| An unmapped provider raises at load time and takes the whole worker down | high | The refusal is on the render/start path only (§2.6); the loader's load-time errors are limited to *static* spec errors (an unknown `provider_option`, an empty map) |
| The credential lands in the wrong variable if the map is stale | medium | The map is pinned to Lego v5.6.0 and every name is cited (§2.4); the spec comment names the source and the version |
| The Traefik image tag `v3.7` floats: `godaddyv3` only exists from **traefik v3.7.17** (Lego v5.6.0) onwards — an older v3.7.x image on a node has no `godaddyv3` provider at all and would fail with "unrecognized DNS provider" | medium | Noted in ADR-055's consequences. Pinning the image to a patch tag is a separate decision (§9). The map itself is unaffected (the names are the same in Lego v4 and v5 for these providers) |
| The admin picks a provider needing several credentials (`route53`, `ovh`, `azuredns`) | medium | They are absent from the map → the start fails with the message of §4.3 (honest failure, not a silent mis-wire); the worker's `dns_provider` ui_help is corrected to list the supported ones |
| The Ingress page's provider list still says `azure` and lists the multi-credential providers | low (this repo) | Worker copy corrected (§4.4); toolkit copy is a follow-up (§9) |
| A token containing `"` or `\` breaks the rendered YAML (the value is substituted into a quoted scalar) | pre-existing | Not introduced here; out of scope (the token is written into the template the same way today) |
| The `$credential_env` marker survives into a written compose file | low | The render refuses to write the file when the marker is present (§4.3) — the same shape as `assert_no_markers` for `$auto_port.` |
| Scope creep into the orchestrator repo | low | §9 keeps it out; the worker's fix is self-sufficient |

---

## 8. What the plan deliberately does not do

* No change to the orchestrator's Ingress page, the shared utility, or the
  ADR-042 options channel. The worker derives the variable name from the
  provider it is already given.
* No new option type, no new substitution language, no second templating path.
* No support for `route53` / `ovh` / `azuredns` (multi-credential providers).
  Serving them needs a credential *map* option — the `env_map` type the option
  DSL already has — and a separate decision.
* No change to `assert_no_markers` (`$auto_port.` only).

## 9. Follow-ups (separate tasks)

1. **Toolkit (orchestrator)**: the Ingress page's provider help text lists
   `azure` (not a Lego name — it is `azuredns`) and lists the
   multi-credential providers. It should offer the providers the worker
   actually serves, ideally as a select rather than free text.
2. **Image pinning**: `traefik:v3.7` floats; `godaddyv3` needs Traefik
   ≥ v3.7.17 (Lego ≥ v5.6.0). Pin a patch tag, or record the floor in the
   spec.
3. **Multi-credential providers**: an `env_map`-shaped credential option, if
   the fleet ever needs `route53` / `ovh` / `azuredns`.


**task:** t-2z2wtc
