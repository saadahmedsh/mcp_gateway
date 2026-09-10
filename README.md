# MCP Enterprise Agent Gateway

A security and reliability control plane between LLM agents and the tools they
invoke through the Model Context Protocol (MCP).

> **Project status:** Phase 5 bounded repair and safe retry handling is
> implemented on top of the Phase 4 sandbox path. gVisor host validation is
> still pending. A real MCP client can discover
> tools and execute calls while the gateway persists lifecycle state, emits
> traces, and evaluates every live call through fail-closed OPA policy. Mutating
> and destructive calls pause for explicit CLI approval. Repair and the audit
> chain remain planned capabilities. See [PLAN.md](PLAN.md).

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
│  Registry/schema validation ──► OPA policy decision       │
│                                      │                    │
│                           requires approval               │
│                                      ▼                    │
│                              Redis HITL queue              │
│                                                           │
│  Repair loop ──► sandbox runner ──► tool execution        │
│                                                           │
│  Redis state │ OpenTelemetry traces │ hash-chained audit  │
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
| 4 | gVisor and hardened-Docker execution | In progress |
| 5 | Bounded repair and retry orchestration | Implemented |
| 6 | Hash-chained audit and evaluation harness | Planned |
| 7 | Container packaging, Helm, and CI | Planned |

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
```

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
| `GATEWAY_SANDBOX_RUNTIME` | `hardened-docker` | `hardened-docker` or `gvisor` |
| `GATEWAY_SANDBOX_IMAGE` | `mcp-gateway-tool:local` | Tool image used for isolated calls |
| `GATEWAY_SANDBOX_OUTPUT_LIMIT_BYTES` | `1048576` | Maximum combined stream size per stream |
| `GATEWAY_AUDIT_LOG_PATH` | `data/audit.jsonl` | Future audit destination |

Never commit `.env`, credentials, tokens, private keys, or production data.

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
SQLite queries, shell execution, and Git workspaces. A web approval UI,
multi-tenancy, RBAC, a custom policy language, and remote MCP proxying are outside
the current scope.

## Documentation

- [PLAN.md](PLAN.md) — authoritative phase plan and acceptance criteria
- [Architecture decisions](docs/DECISIONS.md) — accepted and rejected trade-offs
- [Threat model](docs/THREAT_MODEL.md) — Phase 4 isolation assumptions and residual risks
- [Benchmarks](docs/BENCHMARKS.md) — generated from the Phase 6 evaluation suite

## License

No license has been selected. Treat the source as all rights reserved until a
license file is added.
