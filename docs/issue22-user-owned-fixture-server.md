# Issue #22 user-owned fixture server lifecycle

The issue #22 fixture HTTPS server can be managed by the ordinary `wasmhome`
account. This mode does not install a service, call `sudo`, alter systemd, or
change the ESP32 fixture catalog. The user-level lifecycle engine is adapted
from the private #20 manager candidate and has a closed `issue22-fixture`
server kind.

## Staging and routes

`RemoteFixtureServer.stage()` performs the existing staging flow and the same
`--check` validation without launching a process. It stages the server code,
the fixture tree, the signed OTA package, both fixture public keys, the OTA
public key, and the TLS identity into the existing campaign layout. The manager
accepts only those exact paths and the existing unprivileged campaign port.
It does not copy or rewrite staged inputs.

The fixture server's route table remains unchanged. Its nine fixture records
provide 18 routes, and the optional sequence-2 OTA package provides six more.
The manager's TLS health check requests the already-registered
`resilience/ENV-SIG/scenario-1/manifest.cose` route and checks its frozen digest.
It adds no health or index route. The existing allowlist test still asserts all
24 routes.

## Frozen local inputs

The orchestrator creates `server-manager-spec.json` and
`server-manager-state.json` inside the private campaign directory. The spec is
0600, bound to the exact SSH identity and literal Ed25519 host pin, the staged
campaign root, the existing health manifest digest, and the TLS CA digest. The
journal records its SHA-256 and fingerprints for the orchestrator, manager,
remote helper, hook, fixture server and signing module. Resume requires every
frozen value to match.

The CLI accepts an optional independently pinned SSH jump with
`--ct-jump-target`, `--ct-jump-identity`, `--ct-jump-known-hosts`,
`--ct-jump-host-key-alias`, and `--ct-jump-ssh-port`. Supplying a jump requires
all four identity fields. Staging SCP, manager SSH and direct HTTPS readiness
use the same frozen destination and jump pins.

Before staging, the orchestrator requires `--ct-root` to be a canonical path
below `/home/wasmhome`. A single persistent SSH helper performs the authoritative
read-only preflight, checking ownership and permissions of the account home and
every existing ancestor, the campaign root, manager and fixture PID receipts,
the manager lock, and TCP listeners on the configured port. If the root is
absent, it creates only validated user-owned 0700 components. It then takes the
exclusive manager lock, repeats the owner checks under that lock, and binds and
listens on the configured endpoint before granting the staging lease. Any
receipt, active manager operation, or listener stops staging without adopting
or killing a process. Every remote destination and parent is checked before
`mkdir -p`; a symlink or unsafe component blocks the write.

That helper remains open across all staging SSH/SCP operations and the
read-only hardware baseline verification. It holds both the manager lock and
the already-bound socket, so an unrelated listener cannot take the endpoint
during staging. SSH/SCP use private 0600 snapshots of the exact identity and
known-hosts bytes verified when the lease began. The first `before_child()`
sends one ensure command over the same lease. The helper validates the staged
inputs again, passes the listener as stable FD 3 to the sole manager launcher,
and the fixture adopts that listening socket without rebinding. The server
publishes `server.pid` only after adoption and TLS server construction succeed;
the manager receipt is published by its launcher before exec. The helper closes
its socket copy after handoff, and the exclusive lock remains inherited until
the manager receipt is durable and exec closes the lock descriptor.

If staging, baseline verification, or the lease fails before handoff, EOF or
lease timeout closes the reservation socket and lock without starting a
server or writing process receipts. The SSH session uses server-alive probes.
While staging remains active, the client renews the reservation every 5
seconds. A 30-second idle lease releases the helper's lock and socket after a
dead or blocked client, without imposing an absolute duration on a legitimate
long staging or baseline operation.
Once the ensure command is sent, that handoff is one-shot; it is never resent.
The health probe remains direct HTTPS from the coordinating workstation, using
a private snapshot of the exact CA bytes whose digest was verified. The
original SSH, SCP and CA input paths are not reopened by those clients after
verification.

Direct `RemoteFixtureServer.start()` and `.stop()` are disabled; the
user-owned manager is the only process lifecycle owner. After the read-only
baseline check writes a safe journal, `before_child()` is called immediately
before each row's one-shot claim. It permits only `ready`, `row_complete`,
`complete`, or `interrupted_recovered` with `restore_required` false and no
active row. Later readiness checks use manager status. If the exact owned
process has stopped, the manager reacquires the port under its exclusive lock,
adopts the new socket through FD 3, and completes the TLS health check before
the next one-shot claim. Any unowned listener or mismatched receipt still
blocks relaunch. Readiness retry remains before the claim, so it cannot repeat
a hardware row. The complete batch is wrapped in `batch_guard()` so
manager-owned cleanup runs on success or failure. Recovery uses the manager's
saved receipt directly and remains available while hardware restoration is
pending.

The manager reuses an existing process only when its private receipt, manager
ID, boot ID, PID, start ticks, UID, exact argv, and listening socket match. It
never adopts an unowned process or signals by a reusable PID. The fixture
server's own `server.pid` receipt is removed only when its contents match the
manager's bound PID and start time. A changed receipt or occupied endpoint
fails closed. Launch, status, cleanup, and HTTP event logs stay private.

## Transport limits

The optional Proxmox jump carries only pinned SSH/SCP traffic. The TLS
health probe still opens the configured HTTPS endpoint directly from the
coordinating workstation, using the frozen CA and server name. A working SSH
jump alone does not route HTTPS. If the workstation cannot reach that address,
readiness fails and the owned process is cleaned up; a TLS probe on the CT or a
TLS-preserving HTTPS transport needs a separate design and review.

The manager requires an ordinary local user, CT account `wasmhome`, Python 3,
`nohup`, a private staged campaign directory, and Linux pidfd support for
termination. Offline tests cover FD handoff and socket adoption on loopback;
they do not prove CT SSH, routing, firewall, or end-to-end TLS reachability. No
CT/Proxmox connection or live TLS probe was run.
