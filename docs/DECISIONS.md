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

## ADR-0009: Use Redis as the production lifecycle state store

- **Status:** Accepted in Phase 2
- **Decision:** Store each tool-call record as JSON in Redis, maintain a
  session index, and expire records after the configured TTL. State updates use
  optimistic `WATCH`/`MULTI` transactions.
- **Reason:** Redis provides the required shared state for multiple gateway
  processes, bounded async operations, and the future approval queue without
  introducing a second persistence abstraction.
- **Rejected:** Process-local dictionaries for live operation, because they
  lose state on restart and cannot coordinate workers. An SQL database was
  deferred because it adds operational weight for short-lived coordination
  records.

## ADR-0010: Keep an in-memory state backend only for isolated tests

- **Status:** Accepted in Phase 2
- **Decision:** Support an explicit `memory` backend for unit and integration
  tests; default deployments use Redis.
- **Reason:** Tests remain deterministic and do not require infrastructure,
  while the default still exercises the real production path.
- **Rejected:** Silently falling back to memory when Redis is unavailable,
  because losing lifecycle state would hide an operational failure.

## ADR-0011: Trace the complete call lifecycle with one root and five spans

- **Status:** Accepted in Phase 2
- **Decision:** Create one root `tool_call` span and child spans named
  `validate`, `policy`, `sandbox`, and `execute`. Export live traces through
  OTLP/gRPC and use an in-memory exporter in tests.
- **Reason:** A stable span shape makes later policy, sandbox, and repair work
  visible without changing the MCP interface. The test exporter proves span
  relationships without requiring Jaeger.
- **Rejected:** One span per request, because it cannot show where a call
  spent time or failed. Exporting directly to Jaeger was rejected because OTLP
  keeps the gateway independent of the tracing backend.

## ADR-0012: Keep structured logs off MCP stdout

- **Status:** Accepted in Phase 2
- **Decision:** Emit structlog JSON to stderr and bind `trace_id`, `session_id`,
  and `tool_name` for each call.
- **Reason:** MCP stdio reserves stdout for protocol messages; stderr is safe
  for diagnostics and remains easy to collect in containers.
- **Rejected:** Human-readable logs or stdout logging, because either breaks
  machine parsing or corrupts the stdio protocol.

## ADR-0013: Make OPA the only policy authority

- **Status:** Accepted in Phase 3
- **Decision:** Send the declared risk class and validated arguments to OPA for
  every live call. Any transport, HTTP, or malformed-response failure is a
  typed denial.
- **Reason:** Tool metadata is only a hint; pattern rules must detect a
  destructive request disguised as read-only input.
- **Rejected:** Local Python heuristics as a second authority, because two
  implementations could disagree and create an unsafe fail-open path.

## ADR-0014: Approval is an explicit Redis state transition

- **Status:** Accepted in Phase 3
- **Decision:** Persist the full policy input and matched rule before waiting in
  `awaiting_approval`. The CLI records approver identity and a mandatory reason;
  timeout and rejection become `denied`.
- **Reason:** Operators need a reviewable command and a durable decision that
  survives process boundaries.
- **Rejected:** In-process callbacks, because they cannot support a separate
  approver process or gateway restart.

## ADR-0015: Keep the Phase 1 database physically read-only

- **Status:** Reaffirmed in Phase 3
- **Decision:** Policy detects and gates destructive SQL, but the existing
  `db_query` executor still rejects writes. A write-capable tool will only be
  introduced together with sandboxing and the complete policy path.
- **Reason:** Approval authorizes intent; it must not weaken the independent
  executor boundary.
- **Rejected:** Removing SQLite authorizer protections to make `DELETE` execute,
  because that would violate the Phase 1 security boundary.
