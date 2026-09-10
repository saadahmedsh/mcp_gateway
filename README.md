# MCP Enterprise Agent Gateway

A security and reliability control plane between LLM agents and the tools they
invoke through the Model Context Protocol (MCP).

> **Project status:** Phases 0–8 provide the secure gateway, authenticated
> Streamable HTTP staging entrypoint, sandbox controls, repair loop, audit
> chain, evaluation harness, Helm chart, and Kind verification. The next
> deployment-ready stages add OIDC/RBAC and tenant context, PostgreSQL control-
> plane persistence, dedicated sandbox workers, immutable remote audit storage,
> and hardened service integrations. Local Compose and Kind environments are
> production-shaped test environments, not a substitute for HA cloud
> operations. See [PLAN.md](PLAN.md) and
> [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).

## Why this project exists

Giving an AI agent direct access to a database, shell, or Git workspace creates a
large trust gap. A model can generate malformed arguments, misunderstand intent,
or be manipulated into attempting destructive operations. This gateway is
designed to close that gap with three independent controls:

1. **Deterministic policy enforcement** with OPA/Rego and explicit human approval
   for sensitive operations.
2. **Syscall-level execution isolation** through a gVisor or hardened-Docker
   sandbox created for each tool call.
3. **Bounded self-healing** that repairs malformed calls without retrying policy
   denials or unsafe, potentially partial mutations.

The policy layer—not the agent—is the authority. Every call is intended to be
validated, traced, persisted, and written to a tamper-evident audit trail.

## Target architecture

```text
Agent (MCP client)
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Gateway (FastAPI + MCP server)                            │
│                                                           │
│  OIDC/JWT ──► tenant/RBAC ──► schema validation            │
│                                  │                        │
│  Registry/schema validation ──► OPA policy decision       │
│                                      │                    │
│                           requires approval               │
│                                      ▼                    │
│                              Redis HITL queue              │
│                                                           │
│  Repair loop ──► job queue ──► dedicated sandbox worker   │
│                                                           │
│  PostgreSQL │ Redis │ OTel │ immutable audit sink         │
└───────────────────────────────────────────────────────────┘
        │
        ▼
SQLite query runner │ shell executor │ Git workspace
```

## Delivery status

| Phase | Capability | Status |
|---:|---|---|
| 0 | Environment and repository skeleton | Complete |
| 1 | MCP server, registry, and database query tool | Complete |
| 2 | Redis lifecycle state and OpenTelemetry tracing | Complete |
| 3 | OPA policy enforcement and human approval | Complete |
| 4 | gVisor and hardened-Docker execution | Implemented with host validation pending |
| 5 | Bounded repair and retry orchestration | Implemented |
| 6 | Hash-chained audit and evaluation harness | Implemented |
| 7 | Container packaging, Helm, and CI | Implemented |
| 8 | Production hardening and Kind deployment | Implemented with local limits |
| A | Documentation and scope expansion | In progress |
| B | JWT/OIDC foundation, RBAC vocabulary, tenant-aware policy context | Implemented foundation |
| C–F | PostgreSQL, workers, evaluation gates, production integrations | Planned |

Phase 2 records each call as `received`, `validated`, `policy_checked`,
`executing`, and a terminal state. It creates a root `tool_call` span plus
`validate`, `policy`, `sandbox`, and `execute` child spans. The policy and
sandbox spans are observability boundaries for the phases that implement those
controls.

Phase 3 evaluates the declared risk class and the actual arguments. A policy
denial never reaches tool execution. A `requires_approval` result is stored in
Redis and waits for an operator decision from the CLI; rejection or timeout
transitions the call to `denied`.

Phase 4 runs live tool calls in a fresh hardened Docker container. The default
profile has no network, no capabilities, a read-only root filesystem, a tmpfs
workspace, seccomp, memory/CPU/pids limits, output limits, and explicit cleanup.
The gVisor runtime is selectable when `runsc` is installed.

Phase 5 diagnoses failures into schema, timeout, sandbox, policy, and tool
categories. Read-only transient failures are retried with bounded exponential
backoff. Policy denials are terminal, and non-idempotent mutations surface as
`needs_review` instead of being retried blindly.

Phase 6 appends one JSONL audit record for every completed call. Each record
contains the policy result, approval identity, attempts, runtime, outcome, and
duration, and links to the previous record with SHA-256. Run `make eval` to
execute all checked-in scenarios, write `eval/results.json`, and regenerate the
metrics section in [docs/BENCHMARKS.md](docs/BENCHMARKS.md). The harness uses the
gateway dispatcher with an in-memory state backend for deterministic local
measurements; production transport and sandbox measurements remain separate
smoke checks.

When `GATEWAY_REPAIR_ENABLED=true` and an API key is configured, malformed
arguments are repaired by the configured OpenAI-compatible or native Anthropic
Messages API model. The model
receives the exact Pydantic schema and validation diagnosis and must return a
JSON argument object. The gateway validates and rechecks policy before every
repaired attempt. Policy denials are never sent for repair; non-idempotent
mutations that may have partially executed return `needs_review`.

## Local environments and production target

The repository has two local execution profiles:

| Profile | What it exercises | Intended use |
|---|---|---|
| Compose | stdio MCP, Redis, OPA, Jaeger, and hardened-Docker tools | Fast development and deterministic integration checks |
| Kind | HTTP MCP service, Kubernetes probes, in-cluster Redis/OPA, Helm security context | Production-shaped local staging |

The deployment-ready target extends the Kind profile with an OIDC provider,
PostgreSQL, a dedicated sandbox-worker deployment, TLS and external secrets,
Prometheus/Grafana, and an S3-compatible immutable audit sink. The local
versions of those services prove interfaces and failure behavior; HA,
disaster recovery, managed encryption, and independent trust domains still
require a real production platform.

## Request walkthrough

1. The MCP client sends a tool request over stdio or authenticated Streamable
   HTTP.
2. The gateway verifies identity, tenant, and roles, then validates arguments
   against the registry's Pydantic schema.
3. OPA evaluates the verified principal, tenant, tool, risk class, and actual
   arguments. A denial is terminal; a destructive request enters the approval
   queue.
4. The repair advisor may propose corrected arguments only for repairable
   failures. The gateway validates and re-evaluates policy before every retry.
5. An authorized request is submitted to a dedicated sandbox worker with an
   idempotency key and bounded resources.
6. State transitions, traces, approval evidence, attempts, and the outcome are
   persisted. The audit record is hash-chained and replicated in compliance
   mode.

The complete deployment roadmap is in [PLAN.md](PLAN.md), beginning with
Stage A (scope and documentation), followed by identity/RBAC, PostgreSQL,
sandbox workers, evaluation gates, and production service integrations.

## Quick start

### Prerequisites

- Linux or WSL2 with native Docker Engine and the Compose plugin
- Python 3.11 or newer
- GNU Make and `curl`

Docker Desktop is not required. Confirm that the current user can access the
native Docker daemon:

```bash
docker run --rm hello-world
```

### Install the development environment

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --requirement requirements.lock
cp .env.example .env
```

All direct dependencies are version-pinned. `requirements.lock` captures the
fully resolved environment used by the project.

### Start and verify infrastructure

```bash
make up
docker compose ps
curl --fail http://localhost:8181/health
make demo
```

The infrastructure command starts the following local-only services:

| Service | Purpose | Local endpoint |
|---|---|---|
| Redis | State and approval-queue foundation | `redis://localhost:6379/0` |
| OPA | Deterministic policy engine | `http://localhost:8181` |
| Jaeger | Trace collection and inspection | `http://localhost:16686` |
| OTLP/gRPC | Trace ingestion | `http://localhost:4317` |

Published ports bind to `127.0.0.1` and are not exposed to the local network by
default.

`make demo` also starts a temporary stdio MCP server, discovers both registered
tools, runs a parameterized `db_query`, and confirms that `shell_exec` is denied
when no approval arrives. The demo sets a one-second approval timeout so it
never blocks. The client emits one machine-readable JSON report.

To approve a real pending call, use a second terminal while the gateway is
waiting:

```bash
.venv/bin/python -m gateway.hitl.cli list
.venv/bin/python -m gateway.hitl.cli <approval-id> approve \
  --approver operator --reason "Verified maintenance request"
```

The CLI first lists pending requests. Rejection uses `reject` and also requires
an explicit reason.

To run only the MCP demonstration:

```bash
.venv/bin/python -m eval.client
```

Stop the services without deleting Redis's named volume:

```bash
make down
```

## Development workflow

```bash
make lint       # Ruff, Black, and strict mypy
make test       # pytest suite
make eval       # phase-appropriate evaluation target
make eval-live  # real MCP + Redis + OPA + sandbox evaluation
make eval-realistic # seeded varied scenarios through one live MCP session
make kind-deploy # build and deploy the HTTP chart to a local Kind cluster
make kind-down   # delete the local Kind cluster
```

For a reproducible benchmark with varied generated requests, start the local
services and run:

```bash
make up
.venv/bin/python -m eval.realistic --seed 20260910 --repetitions 20
jq '.metrics' eval/realistic-results.json
```

To measure model-guided repair, set `GATEWAY_REPAIR_ENABLED=true` and provide
the OpenAI-compatible endpoint, model, and API key through the ignored `.env`
file or the process environment. The report records whether malformed cases
were repaired and never stores the API key.

Verify a generated audit chain with:

```bash
.venv/bin/python -m gateway.audit.verify data/audit.jsonl
```

Build the production image with:

```bash
docker build --file docker/Dockerfile.gateway --tag mcp-gateway:local .
```

Render and validate the Kubernetes chart with Helm:

```bash
helm lint deploy/helm/mcp-gateway
helm template gateway deploy/helm/mcp-gateway
```

### Local Kind HTTP smoke test

The Kind target exercises the network entrypoint and Kubernetes probes. Create
the cluster and load the gateway image with:

```bash
make kind-deploy
kubectl get pods,service
curl --fail http://127.0.0.1:8080/livez
curl http://127.0.0.1:8080/readyz
make kind-down
```

The base chart expects Redis, OPA, and OTLP endpoints to be supplied by the
cluster or an explicitly configured staging environment. Until those
dependencies exist, `/readyz` is expected to report `503 unready` while
`/livez` remains successful. The chart does not mount the Docker socket;
Kubernetes sandbox execution belongs in the separate worker deployment
described in the production-readiness report.

Install the Git hooks after creating the environment:

```bash
.venv/bin/pre-commit install
.venv/bin/pre-commit run --all-files
```

Work proceeds one phase at a time. A phase cannot begin until the previous
phase's acceptance criteria pass with real command output. Architectural choices
that are not settled by the plan are recorded in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Configuration

Configuration is loaded by `gateway.config.Settings` from `GATEWAY_*`
environment variables. `.env` is supported for local development and is ignored
by Git; `.env.example` contains safe defaults.

| Variable | Default | Purpose |
|---|---|---|
| `GATEWAY_ENVIRONMENT` | `development` | Runtime environment |
| `GATEWAY_LOG_LEVEL` | `INFO` | Structured log threshold |
| `GATEWAY_REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `GATEWAY_STATE_STORE_BACKEND` | `redis` | `redis` for live state or `memory` for isolated tests |
| `GATEWAY_REDIS_OPERATION_TIMEOUT_SECONDS` | `2.0` | Maximum Redis operation time |
| `GATEWAY_STATE_TTL_SECONDS` | `86400` | State record retention |
| `GATEWAY_OPA_URL` | `http://localhost:8181` | OPA API |
| `GATEWAY_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP exporter target |
| `GATEWAY_JAEGER_UI_URL` | `http://localhost:16686` | Jaeger UI |
| `GATEWAY_DATABASE_PATH` | `data/gateway.sqlite` | Synthetic SQLite database |
| `GATEWAY_APPROVAL_TIMEOUT_SECONDS` | `300` | Human approval timeout |
| `GATEWAY_REPAIR_ENABLED` | `false` | Enable model-guided repair |
| `GATEWAY_REPAIR_LLM_PROVIDER` | `openai_compatible` | Repair provider |
| `GATEWAY_REPAIR_LLM_URL` | `http://localhost:4000/v1/chat/completions` | Repair API endpoint |
| `GATEWAY_REPAIR_LLM_MODEL` | `repair-model` | Provider-specific model identifier |
| `GATEWAY_REPAIR_LLM_ANTHROPIC_VERSION` | `2023-06-01` | Anthropic API version header |
| `GATEWAY_REPAIR_LLM_MAX_TOKENS` | `1024` | Maximum repair response tokens |
| `GATEWAY_HTTP_AUTH_MODE` | `none` | `none`, `static_token`, or `oidc` HTTP authentication |
| `GATEWAY_HTTP_AUTH_TOKEN` | unset | Local-only static bearer token |
| `GATEWAY_OIDC_ISSUER_URL` | unset | OIDC issuer URL |
| `GATEWAY_OIDC_AUDIENCE` | unset | Expected JWT audience |
| `GATEWAY_OIDC_JWKS_URL` | unset | OIDC JSON Web Key Set URL |
| `GATEWAY_OIDC_TIMEOUT_SECONDS` | `2.0` | OIDC key-fetch timeout |
| `GATEWAY_SANDBOX_RUNTIME` | `hardened-docker` | `hardened-docker` or `gvisor` |
| `GATEWAY_SANDBOX_IMAGE` | `mcp-gateway-tool:local` | Tool image used for isolated calls |
| `GATEWAY_SANDBOX_OUTPUT_LIMIT_BYTES` | `1048576` | Maximum combined stream size per stream |
| `GATEWAY_AUDIT_LOG_PATH` | `data/audit.jsonl` | Hash-chained audit destination |

Never commit `.env`, credentials, tokens, private keys, or production data.

### OIDC configuration

For a network deployment, set `GATEWAY_HTTP_AUTH_MODE=oidc` and configure the
issuer, audience, and JWKS URL. Tokens must use RS256 and contain `sub`,
`tenant_id`, `roles`, `iss`, `aud`, and `exp` claims. Supported roles are
`user`, `operator`, and `admin`.

```dotenv
GATEWAY_HTTP_AUTH_MODE=oidc
GATEWAY_OIDC_ISSUER_URL=https://identity.example/realms/gateway
GATEWAY_OIDC_AUDIENCE=mcp-gateway
GATEWAY_OIDC_JWKS_URL=https://identity.example/realms/gateway/protocol/openid-connect/certs
```

`static_token` is retained only for local staging compatibility. `none` is for
local development and must not be used for production traffic. OPA receives the
verified principal and tenant context on every tool call; the client cannot
override those values through tool arguments.

## Failure modes and planned improvements

| Failure mode | Current behavior | Improvement |
|---|---|---|
| Redis unavailable | Bounded typed error; calls fail closed | Add replicated Redis and alerting |
| OPA unavailable | Every live call is denied | Run HA OPA with versioned bundles |
| Approval timeout | Call is denied and recorded | Add operator notifications and escalation |
| Sandbox startup failure | Tool call fails; container cleanup is attempted | Add capacity checks and a quarantined worker pool |
| Kubernetes sandbox runtime unavailable | Chart does not mount a host Docker socket | Deploy a dedicated sandbox worker service or Kubernetes-native runtime |
| gVisor unsupported by host | Hardened Docker remains the fallback | Move gVisor to a Linux/Kubernetes worker pool |
| LLM repair endpoint unavailable | Original failure is returned; no unsafe retry | Add a highly available repair service and circuit breaker |
| LLM proposes unsafe arguments | Arguments are schema-validated and rechecked by OPA | Add signed model policies and an independent semantic validator |
| Non-idempotent timeout | `needs_review`; no blind retry | Add tool-specific reconciliation and idempotency keys |
| Audit disk full or unwritable | Audit write is logged as an operational error | Fail closed for compliance deployments and ship to immutable storage |
| Stdio process crash | MCP connection terminates | Run supervised workers with restart and drain semantics |
| Single-node audit file loss | Local evidence is unavailable | Replicate hash-chained records to WORM/object storage |

The local deployment supports stdio and the authenticated Streamable HTTP
entrypoint. The current chart is suitable for Kind and staging experiments.
OIDC/RBAC, tenant-aware authorization, PostgreSQL control-plane persistence,
dedicated sandbox workers, HA Redis/OPA, external secret management, and a
durable remote audit sink are the next deployment-ready stages.

See the full release gate and evidence in
[docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).

### Inspect Phase 2 state and traces

After `make demo`, list persisted calls and inspect one record:

```bash
docker compose exec redis redis-cli --scan --pattern 'gateway:tool_call:*'
docker compose exec redis redis-cli GET gateway:tool_call:<call-id>
```

Open `http://localhost:16686`, select the
`mcp-enterprise-agent-gateway` service, and inspect a trace. The Jaeger API
provides a scriptable alternative:

```bash
curl --fail 'http://localhost:16686/api/traces?service=mcp-enterprise-agent-gateway&limit=5'
```

If Redis is unavailable, calls return a typed `state_store_unavailable` error
within the configured timeout rather than hanging.

## Repository structure

```text
gateway/       application packages and phase-owned boundaries
eval/          evaluation harness and adversarial scenarios
tests/         automated test suite
docker/        gateway/tool images and seccomp policy boundaries
docs/          decisions, threat model, and reproducible benchmarks
```

## Security model

The final design assumes that agent input and tool arguments are untrusted. It
will fail closed when policy evaluation is unavailable and will never rewrite a
denied request to evade policy. Tool execution will receive restrictive resource,
filesystem, capability, process, and network limits.

Those are **target guarantees, not unrestricted host guarantees**. Phase 4 adds
the Docker isolation boundary, while the database tool remains physically
read-only. Read [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for residual risks;
the Docker daemon and host kernel remain trusted components. Do not
connect this repository to untrusted agents or grant it access to production
systems.

## Scope

The project intentionally supports only three deeply tested tool families:
SQLite queries, shell execution, and Git workspaces. The deployment-ready
roadmap includes multi-tenancy and RBAC, while a web approval UI, a custom
policy language, and remote MCP proxying remain outside the current scope.

## Documentation

- [PLAN.md](PLAN.md) — authoritative phase plan and acceptance criteria
- [Architecture decisions](docs/DECISIONS.md) — accepted and rejected trade-offs
- [Threat model](docs/THREAT_MODEL.md) — Phase 4 isolation assumptions and residual risks
- [Benchmarks](docs/BENCHMARKS.md) — generated from the Phase 6 evaluation suite
- [Production readiness](docs/PRODUCTION_READINESS.md) — release blockers and hardening evidence
