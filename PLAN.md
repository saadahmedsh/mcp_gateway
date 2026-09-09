# PLAN.md — MCP Enterprise Agent Gateway

## 0. How to use this file

This is the authoritative build plan for coding agents (Claude Code, Codex, or similar) working on this repository.

**Rules for the agent:**

1. Work on exactly **one phase at a time**. Do not start Phase N+1 until every acceptance criterion in Phase N passes.
2. Before starting a phase, restate the phase goal and the acceptance criteria you are about to satisfy.
3. After finishing a phase, run the phase's verification commands and paste the real output. Do not claim a criterion passes without showing evidence.
4. If a design decision is not covered here, stop and ask rather than inventing architecture. Record the answer in `docs/DECISIONS.md`.
5. Never weaken a security boundary to make a test pass. If a sandbox restriction breaks a tool, the tool changes, not the sandbox.
6. Keep `docs/DECISIONS.md` updated with every non-obvious trade-off, including ones you rejected.

---

## 1. What this system is

A production-grade gateway that sits between an LLM agent and the tools it wants to execute. The agent speaks Model Context Protocol (MCP) to the gateway. The gateway decides whether a tool call is allowed, executes it inside an isolated sandbox, traces it, repairs it if the arguments were malformed, and writes an immutable audit record.

**The thing that makes this project interesting is not MCP integration.** It is the three layers wrapped around it:

- a deterministic policy gate with human-in-the-loop approval for destructive operations
- syscall-level sandboxing of tool execution
- a self-healing re-execution loop that repairs bad tool arguments instead of failing

Every phase below should be judged by whether it strengthens one of those three.

---

## 2. Non-negotiable conventions

- **Python 3.11+**, `uv` or `pip` with a pinned `requirements.txt`. No unpinned versions.
- **Async by default.** The gateway is `asyncio` end to end. No blocking calls in request paths; blocking work goes to a thread pool or a worker.
- **Typed.** Every public function has type hints. Every tool input/output is a Pydantic model. `mypy` runs clean.
- **Structured logging only.** `structlog` with JSON output. No bare `print`. Every log line carries `trace_id`, `session_id`, `tool_name`.
- **No secrets in code, config, or test data.** Use environment variables read through a single `config.py`. Use only synthetic sample data (`user@example.com`, `Max Mustermann`, fake order IDs).
- **Tests before phase completion.** Each phase adds `pytest` tests. Coverage on the policy engine, repair loop, and sandbox runner is mandatory, not optional.
- **Every phase ends with a working system.** No phase leaves the repo in a state where `make up && make demo` fails.

---

## 3. Target architecture

```
Agent (MCP client)
      |
      v
+---------------------------------------------------+
|  Gateway (FastAPI + MCP server)                   |
|                                                   |
|  1. Tool registry & schema validation             |
|  2. Policy engine  -> OPA (Rego)                  |
|         |                                         |
|         +-- requires_approval -> HITL queue       |
|                                                   |
|  3. Sandbox runner -> gVisor container per call   |
|  4. Repair loop    -> validate, retry, backoff    |
|  5. State          -> Redis                       |
|  6. Tracing        -> OpenTelemetry -> Jaeger     |
|  7. Audit          -> append-only log             |
+---------------------------------------------------+
      |
      v
Tool backends: SQLite query runner, shell executor, Git workspace
```

---

## 4. Repo layout

Create this structure in Phase 0 and do not reorganize it later without recording why.

```
mcp-gateway/
  gateway/
    __init__.py
    config.py              # single source of env-driven settings
    server.py              # MCP server entrypoint
    registry.py            # tool registration + schema export
    models.py              # Pydantic request/response/decision models
    errors.py              # typed exception hierarchy
    policy/
      client.py            # OPA HTTP client
      decisions.py         # allow / deny / requires_approval
      policies/
        tools.rego
        destructive.rego
    hitl/
      queue.py             # pending-approval store (Redis-backed)
      cli.py               # approver CLI
    sandbox/
      runner.py            # container lifecycle
      profiles.py          # per-tool resource + capability limits
      runtimes.py          # gvisor | hardened-docker selection
    repair/
      loop.py              # retry orchestration
      backoff.py
      diagnose.py          # classify failure -> repair strategy
    tools/
      db_query.py
      shell_exec.py
      git_workspace.py
    state/
      redis_store.py
    tracing/
      otel.py
    audit/
      log.py               # append-only writer
      verify.py            # hash-chain integrity check
  eval/
    harness.py
    scenarios/
      valid.yaml
      malformed.yaml
      destructive.yaml
      slow.yaml
      adversarial.yaml
    report.py              # emits metrics table + JSON
  tests/
  docker/
    Dockerfile.gateway
    Dockerfile.tool-base
    seccomp/tool.json
  docs/
    DECISIONS.md
    THREAT_MODEL.md
    BENCHMARKS.md
  docker-compose.yml
  Makefile
  requirements.txt
  README.md
  PLAN.md
```

---

## 5. Phases

### Phase 0 — Environment and skeleton

**Goal:** a runnable, empty-but-wired repo.

Tasks:
- Verify Docker Engine runs natively inside WSL2 Ubuntu (not via Docker Desktop): `docker run hello-world`.
- Create the repo layout above with real (not placeholder) `__init__.py` files.
- `docker-compose.yml` bringing up: `redis`, `opa`, `jaeger`.
- `Makefile` with `up`, `down`, `test`, `lint`, `demo`, `eval`.
- `config.py` reading every setting from env with typed defaults.
- Pre-commit with `ruff`, `black`, `mypy`.

Acceptance criteria:
- [x] `make up` starts all three services; `docker compose ps` shows them healthy.
- [x] `make lint` and `make test` both exit 0 (with a trivial test).
- [x] `curl localhost:8181/health` returns OPA health.

---

### Phase 1 — MCP gateway skeleton, no security

**Goal:** an agent makes one real tool call through the gateway, end to end.

Tasks:
- Implement `server.py` as an MCP server using the official Python `mcp` SDK.
- Implement `registry.py`: tools declare a name, a Pydantic input model, a description, and a risk class (`read_only` | `mutating` | `destructive`). Risk class is declared now even though nothing enforces it yet.
- Implement `tools/db_query.py`: read-only SQL against a seeded SQLite file with synthetic data. Execute in-process for now.
- Implement `tools/shell_exec.py` as a stub that refuses to run until Phase 4 lands. It exists so the registry has a dangerous tool to reason about.
- Write a minimal MCP client script in `eval/` that connects, lists tools, and calls `db_query`.

Acceptance criteria:
- [x] Client lists both tools with correct JSON schemas derived from the Pydantic models.
- [x] `db_query` returns real rows from the seeded database.
- [x] Invalid arguments are rejected with a typed schema error, not a stack trace.
- [x] `shell_exec` returns a clear "not yet enabled" error.

Anti-goals for this phase: no auth, no policy, no containers. Resist adding them early.

---

### Phase 2 — State and observability

**Goal:** every tool call is traceable and its lifecycle is persisted. Do this *before* security, because Phases 3-5 are painful to debug blind.

Tasks:
- `state/redis_store.py`: persist a `ToolCall` record with states `received -> validated -> policy_checked -> awaiting_approval -> executing -> succeeded | failed | denied`. Store attempt history.
- `tracing/otel.py`: initialize the OTel SDK, export to Jaeger. Create a span per tool call with child spans for `validate`, `policy`, `sandbox`, `execute`. Attach `tool_name`, `risk_class`, `attempt`, `decision` as span attributes.
- Propagate a single `trace_id` into every log line and every Redis record.
- Add session-scoped state so repeat calls in one agent session share context.

Acceptance criteria:
- [ ] A single `db_query` call produces one Jaeger trace with the four child spans, screenshot saved to `docs/`.
- [ ] The Redis record for that call shows the full state transition history with timestamps.
- [ ] Killing Redis produces a clean degraded error, not a hang.

---

### Phase 3 — Policy engine and human-in-the-loop gate

**Goal:** destructive operations cannot execute without explicit approval, and the decision is deterministic.

Tasks:
- `policy/client.py`: async OPA client, evaluated on every call, with a hard fail-closed default. If OPA is unreachable, **deny**.
- Write `tools.rego`: allow `read_only` unconditionally; require approval for `mutating`; require approval plus a reason string for `destructive`.
- Write `destructive.rego`: pattern-level rules independent of the declared risk class, e.g. SQL containing `DROP`, `TRUNCATE`, or an `UPDATE`/`DELETE` with no `WHERE`; shell commands touching paths outside the workspace. The declared risk class is a hint; the policy is the authority.
- `hitl/queue.py`: on `requires_approval`, persist the pending call, suspend execution, and wait on a Redis-backed signal with a configurable timeout. Timeout means deny.
- `hitl/cli.py`: an approver CLI listing pending calls with the full rendered command and letting the operator approve or reject with a reason.
- Every decision written to the audit log with the policy input, the matched rule, and the outcome.

Acceptance criteria:
- [ ] `SELECT` runs without prompting.
- [ ] `DELETE FROM orders` (no `WHERE`) suspends, appears in the approver CLI, and executes only after approval.
- [ ] Rejecting it returns a denial to the agent, and nothing was executed.
- [ ] Approval timeout results in denial.
- [ ] With OPA stopped, every call is denied (fail-closed), proven by test.
- [ ] Rego unit tests via `opa test` cover each rule, including the "declared read_only but actually destructive SQL" case.

---

### Phase 4 — Sandboxed execution

**Goal:** tool code runs with syscall-level isolation, not in the gateway process.

Read `docs/THREAT_MODEL.md` (write it in this phase) before implementing. State explicitly what you are defending against: a compromised or prompt-injected agent trying to read host files, reach the network, escalate privileges, or exhaust resources.

Tasks:
- `sandbox/runtimes.py`: two selectable runtimes, chosen by config.
  - `gvisor`: containers via the `runsc` runtime. **Under WSL2, KVM-backed gVisor generally will not work** because nested virtualization is unreliable there; configure `runsc` with `--platform=ptrace` in `/etc/docker/daemon.json`. Slower per syscall, no nested virt needed.
  - `hardened-docker`: fallback with a custom seccomp profile (`docker/seccomp/tool.json`), `--read-only` rootfs, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--network=none`, tmpfs workspace, explicit memory and CPU limits, and a pids limit.
- `sandbox/profiles.py`: per-tool limits (timeout, memory, CPU, network on/off, mounted paths). Defaults are the most restrictive; tools opt in.
- `sandbox/runner.py`: spawn per call, stream stdout/stderr with size caps, enforce wall-clock timeout, always clean up the container even on crash.
- Move `db_query` and `shell_exec` execution into the sandbox. Enable `shell_exec` for real now, restricted to a tmpfs workspace.
- Document the isolation trade-off between the two runtimes in `docs/DECISIONS.md`. This reasoning is itself a deliverable.

Acceptance criteria:
- [ ] A tool attempting to read a host path outside its mounts fails, and the failure is logged as a security event.
- [ ] A tool attempting an outbound network call fails when its profile has network disabled.
- [ ] An infinite loop is killed at the timeout and the container is reaped (`docker ps -a` shows no orphans).
- [ ] A memory bomb hits the cgroup limit rather than the host.
- [ ] A `fork` bomb hits the pids limit.
- [ ] Sandbox startup overhead measured and recorded in `docs/BENCHMARKS.md` for both runtimes.

---

### Phase 5 — Fault-tolerant re-execution and self-healing

**Goal:** the gateway recovers from bad tool calls instead of returning failures to the agent.

Tasks:
- `repair/diagnose.py`: classify failures into `schema_mismatch`, `malformed_json`, `missing_required_field`, `type_coercion`, `timeout`, `sandbox_crash`, `policy_denied`, `tool_error`.
- Map each class to a strategy:
  - schema and argument errors: re-prompt the model with the exact validation error and the tool schema, then retry the repaired arguments.
  - timeout and sandbox crash: retry with exponential backoff plus jitter, capped attempts.
  - policy denied: **never retry**, never rewrite the call to sidestep the policy. Return the denial. Make this an explicit test.
  - tool error (genuine, e.g. bad SQL against a real schema): one repair attempt with the error message, then surrender.
- `repair/backoff.py`: exponential with jitter, max attempts, max total wall clock.
- Idempotency: never retry a `mutating` or `destructive` call that may have partially applied unless the tool declares itself idempotent.
- Every attempt is its own Redis record and its own OTel span, so the trace shows the repair sequence.

Acceptance criteria:
- [ ] Malformed JSON arguments are repaired and succeed on retry, visible as multiple attempt spans in one trace.
- [ ] A missing required field is repaired without human intervention.
- [ ] A denied call is never retried (test asserts zero retry attempts).
- [ ] A non-idempotent mutating call that times out mid-execution is **not** blindly retried; it surfaces as needing review.
- [ ] Backoff timing behaves as configured under test with a fake clock.
- [ ] Repair attempts are capped; runaway loops are impossible.

---

### Phase 6 — Audit trail and evaluation harness

**Goal:** produce the numbers that make this defensible in an interview.

Tasks:
- `audit/log.py`: append-only records, each containing timestamp, session, trace_id, tool, arguments, policy decision, matched rule, approver identity if any, sandbox runtime, attempts, outcome, duration. Hash-chain each record to the previous one.
- `audit/verify.py`: detect tampering by re-walking the chain. Add a test that mutates a record and proves detection.
- `eval/harness.py`: run scenario files end to end against the running gateway and emit metrics.
- Scenarios to cover:
  - `valid.yaml` — normal calls across all tools
  - `malformed.yaml` — bad JSON, wrong types, missing fields
  - `destructive.yaml` — calls that must be gated, approved and rejected paths
  - `slow.yaml` — timeouts, hangs, partial output
  - `adversarial.yaml` — prompt-injection-style attempts to escape the sandbox, read host files, disable policy, or reach the network
- `eval/report.py` emits into `docs/BENCHMARKS.md`:
  - % of malformed calls auto-repaired without human input
  - mean attempts to success
  - policy gate accuracy: destructive calls correctly gated, false positives on safe calls
  - p50/p95 latency overhead added by the sandbox layer, per runtime
  - throughput under N concurrent sessions
  - sandbox escape attempts blocked (target: all of them)

Acceptance criteria:
- [ ] `make eval` runs every scenario and writes a metrics table plus machine-readable JSON.
- [ ] Audit chain verification passes on a clean run and fails on a tampered record.
- [ ] Every number in `docs/BENCHMARKS.md` is reproducible by re-running `make eval`.
- [ ] Zero adversarial scenarios succeed.

---

### Phase 7 — Deployment and packaging

**Goal:** "production-ready" means something a reviewer can verify.

Tasks:
- Multi-stage `Dockerfile.gateway`, non-root user, healthcheck, no build tools in the final image.
- Helm chart under `deploy/helm/` with liveness and readiness probes, resource requests and limits, a `PodSecurityContext` that drops privileges, and configurable policy bundles. This reuses the Kubernetes and Argo CD tooling already in your stack rather than stopping at compose.
- GitHub Actions: lint, mypy, pytest, `opa test`, build image, run the eval harness on a smoke subset.
- `README.md` with the architecture diagram, the threat model summary, the benchmark table, a 60-second quickstart, and an explicit "known limitations" section.

Acceptance criteria:
- [ ] `helm template` renders valid manifests; `helm lint` passes.
- [ ] CI is green on a clean clone.
- [ ] A fresh clone reaches a working demo in under five minutes following only the README.

---

## 6. Definition of done for the whole project

- [ ] All seven phases' criteria pass.
- [ ] `docs/THREAT_MODEL.md` states what is defended, what is explicitly out of scope, and the residual risks.
- [ ] `docs/DECISIONS.md` records each significant trade-off with the rejected alternative.
- [ ] `docs/BENCHMARKS.md` contains reproducible numbers, not estimates.
- [ ] The README's known-limitations section is honest. An interviewer finding a limitation you already documented is a good outcome; finding one you hid is not.

---

## 7. Explicit anti-goals

Do not build these. They expand scope without strengthening the three core differentiators:

- A web UI for approvals. The CLI is sufficient; a UI is a portfolio distraction.
- Multi-tenancy, RBAC, or user management.
- MCP-to-MCP proxying to remote tool servers, until every phase above is done.
- A custom policy DSL. OPA and Rego are the choice; do not abstract over Cedar as well.
- Support for more than three tools. Depth on three beats breadth on eight.
- LLM-based policy decisions. The gate is deterministic on purpose, and that is the point.

---

## 8. Known risks

| Risk | Mitigation |
|---|---|
| gVisor unusable under WSL2 nested virt | `--platform=ptrace`; fall back to hardened Docker and document the trade-off |
| Sandbox overhead makes latency look bad | Measure it, publish it, discuss pooling warm sandboxes as future work |
| Repair loop masks real bugs | Cap attempts, log every attempt, never retry policy denials |
| Scope creep across seven phases | Anti-goals list above; one phase at a time |
| Benchmarks that cannot be reproduced | Everything runs from `make eval` against scenario files in the repo |
