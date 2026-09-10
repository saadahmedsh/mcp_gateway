# Production readiness review

Review date: 2026-09-10

## Verification status

| Area | Result | Evidence |
|---|---|---|
| Formatting, lint, and typing | Pass | `make lint` |
| Automated tests | Partial | The suite reaches `tests/test_audit.py` but currently exceeds the local 180-second verification timeout; this is an open operational/test-runner issue, not a passing release gate |
| Pre-commit hooks | Pass | Ruff, Black, and mypy hooks passed |
| Rego policy tests | Pass | `opa test gateway/policy/policies` — 5/5 |
| Offline evaluation | Pass | `make eval` — 7/7 scenarios |
| Live MCP evaluation | Pass | `make eval-live` — 7/7 scenarios |
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
2. Kubernetes does not provide the Docker socket to the gateway. Sandbox calls
   therefore require a dedicated worker service or a Kubernetes-native runtime.
3. Redis, OPA, and OTLP defaults use plaintext local URLs. Production requires
   TLS, authentication, network policies, and dependency health checks.
4. The audit sink is a local JSONL file. Compliance deployments need replicated
   immutable/WORM storage and a fail-closed mode when audit persistence fails.
5. The chart's policy ConfigMap is not an OPA deployment. Operators must deploy
   OPA with the same bundle or add an OPA sidecar before relying on the chart's
   `policyBundles` values.
6. Identity, RBAC, tenant context, and PostgreSQL control-plane persistence are
   not yet part of the gateway runtime. The current bearer token and Redis
   lifecycle records are staging controls, not a multi-tenant authorization or
   durable control-plane design.

## High-priority hardening

- Add image vulnerability scanning, SBOM generation, signature verification, and
  digest-pinned base images.
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
- Add PostgreSQL migrations, transaction boundaries, backups, and restore tests
  for durable control-plane records while retaining Redis for coordination.
- Add a dedicated authenticated sandbox-worker service with idempotency,
  reconciliation, bounded concurrency, and crash recovery.
- Make policy bundles versioned, signed, loaded by OPA, and auditable.

## Decision

The repository is suitable for local demonstrations and controlled staging.
Phase 8 has started with the HTTP transport, health endpoints, and Kind
packaging, but it is not production-deployable until the release blockers above
and the post-Phase 8 roadmap in `PLAN.md` are closed with evidence.
