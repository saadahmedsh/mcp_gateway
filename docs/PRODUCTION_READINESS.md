# Production readiness review

Review date: 2026-09-10

## Verification status

| Area | Result | Evidence |
|---|---|---|
| Formatting, lint, and typing | Pass | `make lint` |
| Automated tests | Pass | `make test` — 28 passed |
| Pre-commit hooks | Pass | Ruff, Black, and mypy hooks passed |
| Rego policy tests | Pass | `opa test gateway/policy/policies` — 5/5 |
| Offline evaluation | Pass | `make eval` — 7/7 scenarios |
| Live MCP evaluation | Pass | `make eval-live` — 7/7 scenarios |
| Gateway image | Pass | Multi-stage build, non-root user, healthcheck |
| Helm chart | Pass | `helm lint` and `helm template` |
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

## Decision

The repository is suitable for review, local demonstrations, CI, and controlled
staging. It is not production-deployable until the release blockers above are
closed.
