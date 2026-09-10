# Benchmarks

Reproducible evaluation metrics are scheduled for Phase 6. Phase 4 measures
sandbox startup with the following command after building the tool image:

```bash
hyperfine --runs 10 --warmup 2 \
  'docker run --rm --network=none --read-only --cap-drop=ALL \
   --security-opt=no-new-privileges mcp-gateway-tool:local true'
```

Record separate gVisor and hardened-Docker results here only after running the
command on the target WSL2 host. No numbers are estimated in source control.

## Phase 4 local smoke measurement

On the development Docker host, five cold `docker run` invocations of the
hardened profile measured, in seconds:

```text
0.38, 0.37, 0.40, 0.38, 0.37
```

The arithmetic mean was `0.378s`. The configured `runsc` runtime was present,
but a ptrace smoke call failed with `cannot read client sync file`; gVisor is
therefore recorded as unavailable on this host rather than assigned an
invented latency number.
