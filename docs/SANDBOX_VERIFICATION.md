# Sandbox verification — gate H2

Observed behaviour of the **measure** profile, recorded from an actual run. This
file exists because §9.2 of the design specification is a list of *requests*: a
flag in an argv asks the kernel for something, and only running it shows whether
the request was granted.

Until this record existed, every document had to describe those properties as
design requirements. It now exists, and the tests behind it run under
`pytest -m docker tests/sandbox/test_observed_isolation.py`.

## Environment

| | |
|---|---|
| Recorded | 2026-08-05 |
| Docker server | 29.6.1 |
| Docker API | 1.55 |
| Platform | linux/amd64 |
| Kernel | 6.6.114.1-microsoft-standard-WSL2 |
| Image tag | `cae/fx-taskq-py:1.0.2` |
| Image digest | `sha256:7bed2197ce334c2ee23467f35513c8e287565fedfd7af9076e6be55972c3ad8c` |
| Profile | `measure` |

The image is referenced by **digest**, never by tag: a tag can be repointed, and
a result taken against a moving base is not reproducible.

### Gate A recheck — 2026-08-10

The complete Docker-marked suite was re-run under Docker Desktop 4.80.0, engine
29.6.1/API 1.55, linux/amd64, kernel 6.6.114.1-microsoft-standard-WSL2: **67 passed and 3
skipped**. Two skips explicitly report that the original local-only prepared images are no
longer present; best-effort rebuilds have new image digests but match every recorded runtime
component. The third skip is the Windows account's lack of symlink privilege. Both fixtures'
full clean suites and all eight clean→mutated→reverted witness cycles passed against the
rebuilt images.

This recheck confirms sandbox and witness behaviour. It does **not** replace the bit-level
identity record above or make the rebuilt images comparable to results naming the lost
digests.

## Observed

| Property | Observed | Result |
|---|---|---|
| Outbound TCP is refused | `REFUSED OSError` | pass |
| DNS does not resolve | `NO_RESOLVER` | pass |
| Root filesystem rejects writes | `REFUSED` | pass |
| Measured tree rejects writes | `REFUSED` | pass |
| /tmp accepts writes | `x` | pass |
| /workspace/scratch accepts writes | `x` | pass |
| Process runs as uid 1000 | `1000` | pass |
| CAP_CHOWN is unavailable | `DENIED` | pass |
| PID limit contains runaway creation | `BLOCKED 254` | pass |
| Over-timeout command is killed | `killed, exit 137` | pass |

The agent's **tool container** is created by a second path — `docker create` plus
`docker start`, so a tree can be delivered before any tool runs — and therefore
gets its own observations rather than inheriting these:

| Property | Observed | Result |
|---|---|---|
| Only loopback exists in the namespace | `{lo}` from `/proc/net/dev` | pass |
| No route leaves the container | `/proc/net/route` empty | pass |
| A host path is unreadable from inside | non-zero exit, no content | pass |
| The measured tree rejects writes | `REFUSED` | pass |
| No symlink survives delivery | `0` | pass |
| The container is removed when its block raises | not running | pass |
| Tool output matches the host backend byte for byte | equal, both fixtures | pass |

## What this record does not establish

- **It is one platform.** These results are from Docker 29.6.1
  on linux/amd64, kernel 6.6.114.1-microsoft-standard-WSL2. A different kernel, runtime, or
  rootless configuration may grant different things, and this file says nothing
  about those.
- **It is not a security audit.** It observes that the specific properties §9.2
  asks for were granted. It does not establish that the container cannot be
  escaped, and a kernel escape puts everything inside it on the host.
- **The witness profile is not covered.** Witness contracts run with a writable
  root filesystem, because `docker cp` into a read-only container is refused by
  the daemon and the only alternative is a bind mount. That profile is weaker on
  purpose and is not what agent output runs under.

## What writing this found

The gate did its job on first contact. `/workspace/scratch` — the one directory a
run is supposed to be able to write — **was not writable at all**. The tmpfs
mounted over the image's `chown` and came up owned by root while the container
runs as uid 1000. The flag had been correct since H1; the behaviour never was,
and no amount of reading the argument builder would have shown it.

Extending it to the tool container found a defect in the *test* rather than the
sandbox, which is the same lesson from the other side. The network probe used
`cat < /dev/tcp/1.1.1.1/53`, a bash construct — and the shell in both prepared
images is dash, where it fails whether or not a network exists. The assertion
passed for a reason unrelated to what it claimed to check. Reading the interface
list out of `/proc/net/dev` replaced it. **A probe that cannot succeed proves
nothing by failing**, and one that runs only where the property already holds
proves nothing by passing.

Fixed by giving both tmpfs mounts `mode=1777`.

A second probe was discarded rather than recorded: binding a privileged port was
going to be the capability check, and it *succeeded* despite `--cap-drop ALL`,
because this kernel sets `ip_unprivileged_port_start=0` inside the namespace. It
proved nothing, so the capability check is `chown` to another uid instead.
