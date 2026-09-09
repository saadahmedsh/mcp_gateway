# Architecture decisions

This log captures non-obvious design choices and rejected alternatives. Decisions
from later phases will be added only when those phases begin.

## ADR-0001: Separate direct and fully resolved dependency manifests

- **Status:** Accepted in Phase 0
- **Decision:** `requirements.txt` and `requirements-dev.txt` pin direct
  dependencies. `requirements.lock` records the fully resolved environment.
- **Reason:** Reviewers can see intentional dependencies without sacrificing
  reproducible installation.
- **Rejected:** Unpinned direct requirements, because resolution could change
  between builds.

## ADR-0002: Bind development infrastructure to loopback

- **Status:** Accepted in Phase 0
- **Decision:** Compose-published ports bind to `127.0.0.1`.
- **Reason:** Redis, OPA, OTLP, and the Jaeger UI are local development services
  and should not be exposed to the LAN by default.
- **Rejected:** Binding to every interface, because convenience does not justify
  unnecessary exposure.

## ADR-0003: Keep phase-owned modules inert until their phase

- **Status:** Accepted in Phase 0
- **Decision:** Create importable package boundaries now, but implement behavior
  only in the phase that owns it.
- **Reason:** This preserves the authoritative repository layout without
  bypassing the plan's ordered acceptance gates.
- **Rejected:** Implementing speculative stubs that appear functional, because
  they would create misleading behavior and architecture ahead of its tests.

## ADR-0004: Use stdio for the initial MCP transport

- **Status:** Accepted in Phase 1
- **Decision:** The Phase 1 client launches the gateway as an MCP stdio
  subprocess. HTTP transport remains deferred.
- **Reason:** Stdio proves a real protocol round trip without exposing an
  unauthenticated network service. Tool logic remains transport-independent.
- **Rejected:** Streamable HTTP in Phase 1, because it adds service lifecycle and
  network exposure without strengthening this phase's acceptance criteria.

## ADR-0005: Keep the registry authoritative

- **Status:** Accepted in Phase 1
- **Decision:** Use the official MCP v2 low-level server callbacks as a thin
  adapter around a gateway-owned registry. Pydantic models in the registry are
  the source of input and output schemas.
- **Reason:** Later policy, approval, tracing, sandbox, and repair stages need one
  common dispatch boundary.
- **Rejected:** High-level per-tool decorators, because duplicated registry and
  decorator metadata could drift and would scatter dispatch behavior.

## ADR-0006: Seed SQLite at runtime and execute it off the event loop

- **Status:** Accepted in Phase 1
- **Decision:** Create deterministic synthetic orders idempotently at server
  startup. Run blocking `sqlite3` work using `asyncio.to_thread`.
- **Reason:** This avoids an opaque binary fixture while preserving a non-blocking
  async request path.
- **Rejected:** Committing a SQLite binary and adding `aiosqlite`; the first is
  difficult to review, while the second still delegates to a worker thread and
  becomes irrelevant when execution moves into the Phase 4 sandbox.

## ADR-0007: Enforce the Phase 1 database tool as physically read-only

- **Status:** Accepted in Phase 1
- **Decision:** Use a read-only SQLite URI, `query_only`, an authorizer that
  rejects mutations and attachment, single-statement execution, bound
  parameters, and a maximum result size of 500 rows.
- **Reason:** A tool declared read-only must not rely on its description for
  safety before the policy engine exists.
- **Rejected:** Parsing SQL as the primary enforcement mechanism, because parser
  classification does not constrain what SQLite ultimately executes.

## ADR-0008: Add database mutations only behind the Phase 3 policy gate

- **Status:** Accepted in Phase 1
- **Decision:** Keep `db_query` read-only now. Reconcile Phase 3's approved
  `DELETE` scenario by deliberately introducing mutation capability only when
  deterministic OPA enforcement and human approval are active.
- **Reason:** Enabling writes in Phase 1 would contradict its explicit read-only
  requirement and expose mutations before authorization exists.
- **Rejected:** Enabling early writes or inventing an unplanned `db_execute` tool.
