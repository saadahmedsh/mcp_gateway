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
