# Threat model

The gateway treats the agent, its tool arguments, and tool code as untrusted. In
the deployment-ready target it also treats tenant identity and control-plane
requests as security-sensitive. The gateway defends against a compromised or
prompt-injected agent attempting to read host files, reach the network,
escalate privileges, exhaust host resources, forge identity, or cross tenant
boundaries.

## Controls

- Every call gets a fresh Docker container and a read-only root filesystem.
- The default hardened-Docker profile drops all capabilities, enables
  `no-new-privileges`, disables networking, applies seccomp, and limits memory,
  CPU, and process count.
- The only writable location is a `tmpfs` workspace. Host mounts are explicit
  and read-only unless a profile opts in.
- Wall-clock and output limits are enforced by the runner, and named containers
  are explicitly reaped after success, failure, or timeout.
- gVisor is selectable through `runsc` with the ptrace platform for WSL2
  environments where nested KVM is unreliable.
- Network requests are authenticated with OIDC/JWT and authorization receives
  the verified subject, roles, tenant, agent, tool, and arguments.
- Tenant context is required for control-plane records and is checked by OPA;
  missing or mismatched context fails closed.
- PostgreSQL is the durable source of truth for principals, approvals,
  execution history, and idempotency records. Redis is limited to ephemeral
  coordination and waiting signals.
- Sandbox workers are separated from the gateway and run with a dedicated
  runtime boundary. The gateway never receives a host Docker socket.
- Audit records are replicated to immutable storage in compliance mode and are
  verified using the hash chain before they are accepted as evidence.

## Out of scope and residual risk

The gateway does not defend against a malicious container runtime, a host
kernel compromise, compromise of the configured identity provider, denial of
service caused by an intentionally oversized deployment, or secrets already
present in explicitly mounted files. gVisor and seccomp reduce attack surface
but are not a proof of perfect isolation. The PostgreSQL, Redis, OPA, object
storage, and worker supply chains remain deployment responsibilities. Local
Kind and Compose services do not provide production-grade HA, backup, or trust
separation.
