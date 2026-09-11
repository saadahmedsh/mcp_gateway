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

## ADR-0016: Use fresh Docker containers as the Phase 4 isolation boundary

- **Status:** Accepted in Phase 4
- **Decision:** Execute each tool call in a named, one-shot container with a
  read-only root filesystem, dropped capabilities, `no-new-privileges`, no
  network by default, seccomp, tmpfs workspace, cgroup limits, and a pids limit.
- **Reason:** This is available on native Docker under WSL2 and gives the
  runner explicit lifecycle and cleanup semantics.
- **Rejected:** Running tool code in the gateway process, because a compromised
  tool could access gateway memory and host resources. Long-lived pooled
  containers were deferred because they complicate cleanup and cross-call state.

## ADR-0017: Support gVisor ptrace and hardened Docker as selectable runtimes

- **Status:** Accepted in Phase 4
- **Decision:** Use `runsc` with `platform=ptrace` when configured, and retain a
  hardened-Docker profile as the default fallback.
- **Reason:** WSL2 commonly cannot provide reliable nested KVM; ptrace avoids
  that dependency while the fallback keeps the system usable where runsc is not
  installed.
- **Rejected:** Assuming KVM-backed gVisor everywhere, because it would make
  the documented WSL2 development path fail.

The Docker daemon must configure runsc with `platform=ptrace` in its runtime
arguments; the gateway selects `--runtime runsc` and does not pretend Docker's
CLI has a portable `--runtime-flag` option.

## ADR-0020: Use a local append-only JSONL audit sink

- **Status:** Accepted in Phase 6
- **Decision:** Write one canonical JSONL record per terminal tool call. Include
  the previous digest and a SHA-256 digest of the record payload; verify the
  chain before relying on it.
- **Reason:** JSONL is inspectable during development, supports append-only
  filesystem semantics, and keeps the audit contract independent from Redis
  expiration. Writes run in a worker thread and fsync before returning.
- **Rejected:** Storing audit records only in Redis, because lifecycle TTLs and
  mutable keys are not an immutable evidence trail. A remote event bus was
  deferred until deployment packaging defines its operational dependency.

## ADR-0021: Keep evaluation deterministic and machine-readable

- **Status:** Accepted in Phase 6
- **Decision:** Scenario YAML files run through the gateway dispatcher with an
  in-memory state backend; `eval/results.json` is the source for the generated
  benchmark table.
- **Reason:** The evaluation can run without a live Redis/OPA cluster while
  still exercising validation, policy boundary adapters, execution, and result
  classification. The JSON artifact makes every number reproducible.
- **Rejected:** Hard-coding benchmark numbers or requiring an external LLM for
  scenario repair, because either makes local verification nondeterministic.

## ADR-0022: Use an injected LLM advisor with policy re-evaluation

- **Status:** Accepted in Phase 6
- **Decision:** Repair proposals come from an OpenAI-compatible endpoint behind
  a typed advisor interface. The tool schema and exact failure diagnosis are
  sent to the model. Every repaired argument set is validated and evaluated by
  OPA again before execution.
- **Reason:** Production repair needs model-guided diagnosis, while the gateway
  must retain authority over the resulting call. Re-evaluation prevents a
  repair from changing a safe request into an unauthorized one.
- **Rejected:** Hard-coded argument substitutions and trusting the first policy
  decision across retries, because both are brittle and can create an
  authorization bypass.

## ADR-0023: Make idempotency explicit for retry safety

- **Status:** Accepted in Phase 6
- **Decision:** Tool definitions declare `idempotent`. Read-only tools may retry
  transient failures; mutating or destructive tools retry only when explicitly
  idempotent. Otherwise a possible partial execution returns `needs_review`.
- **Reason:** A timeout cannot prove that a side effect did not happen, so retry
  safety must be a tool contract rather than an inference from the error.
- **Rejected:** Retrying all failures or asking a human to approve every retry,
  because either risks duplicate side effects or turns routine recovery into
  an unbounded manual process.

## ADR-0024: Separate offline and live evaluation modes

- **Status:** Accepted in Phase 6
- **Decision:** Keep `make eval` deterministic and add `make eval-live` for the
  real MCP stdio, Redis, OPA, and sandbox path. Live results include measured
  p50/p95 latency and are written separately.
- **Reason:** Fast offline checks are suitable for CI, while live measurements
  prove integration behavior and expose infrastructure-dependent latency.
- **Rejected:** Making every evaluation depend on Docker and running services,
  because developer feedback and CI would become slow and fragile.

## ADR-0025: Package the stdio gateway with exec health probes

- **Status:** Accepted in Phase 7
- **Decision:** The production image remains a non-root stdio MCP process. The
  image healthcheck and Helm liveness/readiness probes validate configuration
  with a short Python command rather than exposing an unauthenticated HTTP
  endpoint.
- **Reason:** The current MCP transport is stdio, so an HTTP probe would imply
  a listener that does not exist and would expand the security surface.
- **Rejected:** Adding an unauthenticated health HTTP server solely for probes;
  a network transport will be introduced as a separate, authenticated design.

## ADR-0026: Do not mount the host Docker socket in the Helm chart

- **Status:** Accepted in Phase 7
- **Decision:** The chart does not expose `/var/run/docker.sock` to the gateway.
  Sandbox execution in Kubernetes requires a separately deployed worker or a
  Kubernetes-native runtime integration.
- **Reason:** The Docker socket is effectively host-root access and would
  undermine the gateway's own isolation boundary.
- **Rejected:** Mounting the socket for immediate feature parity, because it
  creates a privilege-escalation path larger than the tool sandbox protects.

## ADR-0018: Keep repair policy separate from authorization policy

- **Status:** Accepted in Phase 5
- **Decision:** The repair loop may retry schema/tool/transient sandbox failures
  within explicit attempt and wall-clock limits, but it classifies every policy
  denial as terminal. Repaired arguments are never rewritten to evade OPA.
- **Reason:** Reliability must not become an authorization bypass.
- **Rejected:** Retrying every exception uniformly, because a denial or partial
  mutation could be repeated indefinitely.

## ADR-0019: Retry only safe or explicitly idempotent operations

- **Status:** Accepted in Phase 5
- **Decision:** Read-only operations may retry timeout and sandbox failures.
  Mutating and destructive operations require an explicit idempotent declaration;
  otherwise a possible partial execution returns `needs_review`.
- **Reason:** A timeout does not prove that the remote side did nothing.
- **Rejected:** Blind retries based only on the exception type, because they can
  duplicate side effects.

## ADR-0027: Use Streamable HTTP for network MCP and retain stdio locally

- **Status:** Accepted for production hardening
- **Decision:** Add an authenticated Streamable HTTP entrypoint for Kubernetes
  deployments while retaining stdio for local development and compatibility.
- **Reason:** Kubernetes clients require a network listener, while stdio remains
  the smallest and safest local transport for demonstrations and subprocess
  integration tests. The MCP SDK provides both transport paths.
- **Rejected:** Replacing stdio outright, because it would break the existing
  local test workflow and remove a useful isolated deployment mode.

## ADR-0028: Use Kind and provider-neutral integration manifests

- **Status:** Accepted for production hardening
- **Decision:** Validate the first Kubernetes deployment with Kind and avoid
  cloud-specific resources in the base Helm chart.
- **Reason:** Kind is reproducible in developer machines and CI. Cloud-specific
  Redis, identity, object storage, and runtime integrations remain optional
  overlays rather than hidden assumptions in the core chart.
- **Rejected:** Targeting one cloud immediately, because it would make local
  verification and portability unnecessarily difficult.

## ADR-0029: Keep the gateway unprivileged and isolate sandbox workers

- **Status:** Accepted for production hardening
- **Decision:** The gateway never receives a Docker socket or privileged runtime
  access. Kubernetes sandbox execution is delegated to a separately deployed
  worker using a Kubernetes RuntimeClass such as gVisor or Kata.
- **Reason:** Docker socket access is effectively host-root access and would
  defeat the gateway's security boundary. A worker creates an explicit trust
  boundary and permits dedicated node policies.
- **Rejected:** Mounting `/var/run/docker.sock` into the gateway for convenience.

## ADR-0030: Expand the target from a single-tenant gateway to a deployment-ready control plane

- **Status:** Accepted for the post-Phase 8 roadmap
- **Decision:** Add OIDC/JWT identity, RBAC, tenant context, PostgreSQL control-
  plane persistence, dedicated sandbox workers, deployment-gated evaluation,
  and production service integrations. Retain stdio, SQLite, local JSONL, and
  in-memory adapters as explicitly scoped development/test paths.
- **Reason:** A production deployment needs authenticated principals, durable
  control-plane state, isolation from the gateway process, and release evidence.
  Local adapters remain valuable for fast deterministic tests, but they must not
  be mistaken for production dependencies.
- **Rejected:** Keeping RBAC, tenants, and durable persistence permanently out
  of scope, because that would leave authorization and recovery dependent on
  process-local or single-tenant assumptions.
- **Trade-off:** The local stack becomes heavier and requires more integration
  tests. The benefit is that Kind can exercise the same service boundaries and
  failure behavior expected in a real deployment.

## ADR-0031: Use an explicit bounded executor bridge for synchronous I/O

- **Status:** Accepted during Stage B verification
- **Decision:** SQLite, audit-file, and evaluation-file operations use a shared
  bounded `ThreadPoolExecutor`. The async wrapper polls the concurrent future
  from the event loop instead of relying on `asyncio.to_thread()`'s default
  executor callback.
- **Reason:** On the supported local Python environment, default executor
  callbacks did not wake the event loop reliably after filesystem or SQLite
  operations, causing tests and local commands to hang. The explicit executor
  preserves the non-blocking request boundary and makes worker capacity
  visible and bounded.
- **Rejected:** Performing filesystem or SQLite work directly in the event
  loop, because it would violate the async request-path boundary. Increasing
  test timeouts was also rejected because it would hide the stuck operation.

## ADR-0032: Use asyncio pipes for the local MCP stdio server

- **Status:** Accepted during Stage B verification
- **Decision:** The gateway's stdio server uses asyncio's native pipe
  transports and MCP memory streams rather than the SDK helper that wraps
  standard files through AnyIO worker threads.
- **Reason:** The SDK stdio helper did not deliver newline-delimited messages in
  this Python/AnyIO environment. A minimal unmodified MCP server reproduced the
  same behavior, while native asyncio pipe transports completed initialization
  and clean shutdown reliably.
- **Rejected:** Increasing MCP request timeouts or suppressing the integration
  test, because that would hide a broken protocol boundary. Replacing stdio
  with HTTP was also rejected because stdio remains a supported local path.

## ADR-0033: Use PostgreSQL for durable control-plane state

- **Status:** Accepted during Stage C
- **Decision:** Store tenants, principals, approvals, tool-call summaries,
  attempts, idempotency keys, policy versions, and configuration in PostgreSQL
  through SQLAlchemy's async engine. Manage schema changes with Alembic. Keep
  Redis for short-lived state, locks, and approval waiting signals.
- **Reason:** Control-plane records need transactions, constraints, durable
  backups, and independent scaling. PostgreSQL provides those properties while
  asyncpg preserves the gateway's asynchronous request boundary.
- **Rejected:** Redis-only persistence, because TTL-oriented coordination data
  is not an adequate system of record; a synchronous ORM, because it would add
  blocking database work to async request paths; moving the example SQLite tool
  into PostgreSQL, because its local read-only fixture is intentionally scoped
  as a sandboxed tool demonstration.
- **Trade-off:** A local deployment now has one additional service and a
  migration release step. The benefit is explicit transactional ownership and
  a path to managed PostgreSQL, HA, backups, and restore verification.

## ADR-0034: Use an authenticated bounded worker queue before externalizing workers

- **Status:** Accepted during Stage D
- **Decision:** Introduce a typed HMAC-authenticated job protocol and bounded
  worker queue. Worker-owned registries execute tools, while the gateway submits
  jobs through a client boundary. Add circuit breaking, idempotency
  deduplication, and reconciliation records for uncertain mutations. The local
  implementation runs the worker service as a separate asyncio component;
  production moves it to a separately deployed service.
- **Reason:** This isolates scheduling and execution responsibilities while
  keeping local development reproducible. HMAC prevents an untrusted caller
  from forging worker jobs, and explicit reconciliation avoids treating a
  partially applied mutation as safely retryable.
- **Rejected:** Giving the gateway a Docker socket, because that is effectively
  host-root access; unbounded in-process retries, because they can duplicate
  mutations; and immediately requiring a cloud queue, because it would make
  local verification depend on paid infrastructure.
- **Trade-off:** The local mode is not yet a kernel-level process boundary.
  Dedicated worker deployment and runtime isolation remain required before
  production traffic.

## ADR-0035: Externalize the worker protocol as a separate HTTP service

- **Status:** Accepted during Stage D hardening
- **Decision:** Keep the typed HMAC job envelope and expose it through a
  standalone `gateway.workers.http_server` service. The gateway's `remote`
  mode uses an HTTP client and never constructs a sandbox runner. The Helm
  chart defines a separate worker Deployment and ClusterIP Service with an
  optional gVisor/Kata `RuntimeClass`.
- **Reason:** A process and network boundary is required before Kubernetes can
  place execution on dedicated sandbox nodes. Keeping one protocol avoids
  divergent local and production behavior and preserves idempotency and
  reconciliation semantics.
- **Rejected:** Mounting `/var/run/docker.sock` into the gateway, because it
  grants effective host-root control. Running the worker as a sidecar, because
  it does not provide independent scheduling or node isolation.
- **Trade-off:** The HTTP service is deployable now, but the actual production
  sandbox must be supplied by a runtime-native gVisor/Kata adapter or an
  explicitly isolated transitional worker. The chart therefore disables the
  worker by default and requires an explicit shared secret when enabled.
