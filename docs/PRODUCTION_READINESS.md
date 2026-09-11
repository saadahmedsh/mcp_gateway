# Production readiness review

Review date: 2026-09-10

## Verification status

| Area | Result | Evidence |
|---|---|---|
| Formatting, lint, and typing | Pass | `make lint` |
| Automated tests | Pass | `make test` — 51 passed, 3 skipped in 3.63s; PostgreSQL integration is opt-in |
| Pre-commit hooks | Pass | Ruff, Black, and mypy hooks passed |
| Rego policy tests | Pass | `opa test gateway/policy/policies` — 9/9 |
| Offline evaluation | Pass | `make eval` — 7/7 scenarios |
| Live MCP evaluation | Pass | `make eval-live` — 7/7 scenarios |
| Evaluation release gate | Pass | `make eval-gate` rejects failed/security-unsafe scenarios; CI also gates live p95 latency |
| Generated realistic benchmark | Pass | `eval.realistic` — seeded 10-scenario local run |
| Gateway image | Pass | Multi-stage build, non-root user, healthcheck |
| Helm chart | Pass | `helm lint` and `helm template` |
| HTTP entrypoint | Partial | Streamable HTTP app and bearer-token staging guard |
| Kind deployment | Staging path implemented | `make kind-deploy` provides the provider-neutral local cluster path; production HA and policy-bundle verification remain open |
| Demo path | Pass | `make demo` |

## Release blockers

These items should be resolved before production traffic is permitted:

1. The gateway now exposes an authenticated Streamable HTTP entrypoint for Kind
   and staging. Production still requires OIDC or mTLS authentication,
   ingress/TLS termination, and a load-tested network deployment.
2. The authenticated worker HTTP service and Helm Deployment/Service are now
   implemented. Production still requires dedicated gVisor/Kata nodes, a
   runtime-native sandbox adapter, network policy/TLS between gateway and
   worker, and an operationally managed shared secret. The gateway must not
   receive a Docker socket.
3. Redis, OPA, and OTLP defaults use plaintext local URLs. Production requires
   TLS, authentication, network policies, and dependency health checks.
4. The audit sink is a local JSONL file. Compliance deployments need replicated
   immutable/WORM storage and a fail-closed mode when audit persistence fails.
5. The chart's policy ConfigMap is not an OPA deployment. Operators must deploy
   OPA with the same bundle or add an OPA sidecar before relying on the chart's
   `policyBundles` values.
6. PostgreSQL control-plane persistence is implemented as an optional runtime
   boundary with Alembic migrations. Production still requires HA PostgreSQL,
   encrypted connections, backup/restore drills, and migration verification.

## High-priority hardening

- Add image vulnerability scanning, SBOM generation, signature verification, and
  digest-pinned base images.
- Keep the deterministic and live evaluation gates required in CI, publish
  signed scenario artifacts, and configure environment-specific latency/cost
  budgets.
- Pin GitHub Actions to commit SHAs and add dependency vulnerability checks.
- Add PodDisruptionBudget, autoscaling, NetworkPolicy, and external secret
  manager integration.
- Make readiness verify Redis and OPA connectivity, not only local settings.
- Add LLM repair rate limits, circuit breaking, model allow-lists, and egress
  restrictions.
- Add reconciliation and idempotency keys for each mutating tool.
- Replicate audit records and add key rotation for audit-chain verification.
- Replace single-node SQLite with a managed data service for real workloads.
- Replace the staging bearer token with OIDC or mTLS and configure ingress TLS.
- Add a Kind dependency overlay so HTTP readiness can be tested without host
  networking assumptions.
- Add OIDC/JWT validation with JWKS rotation, role and tenant claims, and
  identity-aware OPA inputs.
- Run PostgreSQL migrations, transaction tests, backup/restore drills, and
  disaster-recovery verification for durable control-plane records while
  retaining Redis for coordination.
- Validate the separately deployed authenticated sandbox-worker service on
  dedicated gVisor/Kata nodes with idempotency, reconciliation, bounded
  concurrency, crash recovery, TLS, and network policy.
- Make policy bundles versioned, signed, loaded by OPA, and auditable.

## Decision

The repository is suitable for local demonstrations and controlled staging.
Phase 8 has started with the HTTP transport, health endpoints, and Kind
packaging, but it is not production-deployable until the release blockers above
and the post-Phase 8 roadmap in `PLAN.md` are closed with evidence.
