# Threat model

Phase 4 treats tool arguments and tool code as untrusted. The gateway defends
against a compromised or prompt-injected agent attempting to read host files,
reach the network, escalate privileges, or exhaust host resources.

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

## Out of scope and residual risk

The gateway does not defend against a malicious Docker daemon, a host kernel
compromise, denial of service caused by unlimited concurrent calls, or secrets
already present in explicitly mounted files. gVisor and seccomp reduce attack
surface but are not a proof of perfect isolation. The tool image supply chain
and Docker daemon permissions remain deployment responsibilities.
