#!/usr/bin/env python3
"""Ignored Windows supervisor proposal; remote mutations require --execute --authorized-ct110.

Never creates/starts CT110 or changes another guest. Normal shutdown110 only.
"""
import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import time

import proxmox_donor_guard as guard

HOST = "root@192.168.0.99"
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

REMOTE = r'''
import json, subprocess, sys
p=json.load(sys.stdin)
namespace={'__name__':'host_guard'}
exec(compile(p['guard_source'],'reviewed-host-guard.py','exec'),namespace)
phase=p['phase']
result=namespace['check'](phase,baseline=p['baseline'])
if p['operation']=='shutdown':
    # Identity/state guard, no resource requirement that prevents safe shutdown.
    if phase!='identity': raise ValueError('shutdown requires identity guard')
    subprocess.run(['pct','shutdown','110','--timeout','60'],check=True,timeout=75)
    result=namespace['check']('final',baseline=p['baseline'])
print(json.dumps(result))
'''

ENDPOINT = r'''
import json, subprocess, sys
p=json.load(sys.stdin)
namespace={'__name__':'host_guard'}
exec(compile(p['guard_source'],'reviewed-host-guard.py','exec'),namespace)
namespace['check']('identity',baseline=p['baseline'])
result=subprocess.run(['pct','exec','110','--','runuser','-u','wasmhome','--','python3','-c',p['endpoint_source']],input=json.dumps(p['endpoint']).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
'''


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_baseline(base):
    require(base.get("schema") == "wasmhome-proxmox-donor-lease-v1" and base.get("ctid") == 110
            and base.get("hostname") == guard.NAME and base.get("phase") == "before-start"
            and base.get("ct_state") == "stopped", "supervisor requires exact before-start CT110 baseline")
    require(base.get("host_start_gate_memavailable_bytes", 0) >= 10 * guard.GIB
            and base.get("configured_memory_bytes") == 8 * guard.GIB and base.get("configured_swap_bytes") == 0,
            "startup resource contract differs")
    require(base.get("template", {}).get("sha256") == guard.TEMPLATE_SHA256
            and base.get("other_guest_states", {}).get("ct109") == "stopped" and base.get("config_sha256"),
            "template/config/CT109 baseline differs")


class Client:
    def __init__(self, args, log, baseline):
        self.log, self.baseline, self.number = log, baseline, 0
        self.guard_source = (SCRIPT_DIR / "proxmox_donor_guard.py").read_text(encoding="utf-8")
        self.endpoint_source = (SCRIPT_DIR / "ct110_supervisor_receiver.py").read_text(encoding="utf-8")
        self.options = [args.ssh, "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=5",
                        "-oServerAliveInterval=5", "-oServerAliveCountMax=2"]
        if args.known_hosts:
            require(args.known_hosts.is_file(), "known-hosts file missing")
            self.options += ["-oUserKnownHostsFile=" + str(args.known_hosts.resolve())]

    def command(self, code):
        # One shell-quoted remote command, JSON stdin bytes. No PowerShell string interpolation.
        return self.options + [HOST, "python3 -c " + shlex.quote(code)]

    def payload(self, phase, operation="guard", endpoint=None):
        data = dict(guard_source=self.guard_source, phase=phase, operation=operation, baseline=self.baseline)
        if endpoint is not None:
            data.update(endpoint_source=self.endpoint_source, endpoint=endpoint)
        return json.dumps(data).encode("utf-8") + b"\n"

    def call(self, phase, operation="guard", endpoint=None):
        self.number += 1
        tag = f"{self.number:04}-{operation}-{phase}"
        payload = self.payload(phase, operation, endpoint)
        (self.log / (tag + ".input.json")).write_bytes(payload)
        try:
            result = subprocess.run(self.command(ENDPOINT if endpoint is not None else REMOTE), input=payload,
                                    capture_output=True, timeout=90 if operation == "shutdown" else 25)
        except subprocess.TimeoutExpired as error:
            (self.log / (tag + ".timeout.json")).write_text(json.dumps(dict(timeout=error.timeout)) + "\n")
            raise
        (self.log / (tag + ".stdout.log")).write_bytes(result.stdout)
        (self.log / (tag + ".stderr.log")).write_bytes(result.stderr)
        (self.log / (tag + ".status.json")).write_text(json.dumps(dict(returncode=result.returncode)) + "\n")
        require(result.returncode == 0, tag + " failed; preserved logs, no automatic guest correction")
        return json.loads(result.stdout)

    def launch(self, endpoint):
        stdout = (self.log / "build-ssh.stdout.log").open("xb")
        stderr = (self.log / "build-ssh.stderr.log").open("xb")
        child = subprocess.Popen(self.command(ENDPOINT), stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        try:
            child.stdin.write(self.payload("identity", "endpoint", endpoint))
            child.stdin.close()
        finally:
            stdout.close()
            stderr.close()
        return child


def supervise(args):
    require(args.execute and args.authorized_ct110, "execution requires explicit authorization of the new CT110 lifecycle")
    baseline = read(args.baseline)
    validate_baseline(baseline)
    require(not args.log.exists() and not args.log.is_symlink(), "exclusive local log directory exists/redirected; preserve it")
    require(args.builder_sha256 == sha(SCRIPT_DIR / "donor_wamrc.py"), "proposed/CT builder digest must match reviewed donor script")
    args.log.mkdir()
    (args.log / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
    (args.log / "supervisor-inputs.json").write_text(json.dumps(dict(
        supervisor_sha256=sha(pathlib.Path(__file__)), host_guard_sha256=sha(SCRIPT_DIR / "proxmox_donor_guard.py"),
        endpoint_sha256=sha(SCRIPT_DIR / "ct110_supervisor_receiver.py"), builder_sha256=args.builder_sha256,
        authorization_scope="supervise already started newCT110; shutdown110 normally; no other guests",
        control=args.control, run=args.run), indent=2) + "\n")
    client = Client(args, args.log, baseline)
    control = dict(control=args.control)
    build = None
    identity_confirmed = False
    failure = None
    final = None
    try:
        client.call("identity")
        identity_confirmed = True
        lease = client.call("running")
        client.call("identity", "endpoint", dict(action="prepare", **control))
        client.call("identity", "endpoint", dict(action="lease", lease=lease, **control))
        argv = ["/usr/bin/python3", args.builder, "--execute", "--profile", "lxc110", "--host-lease", args.control + "/host-lease.json",
                "--run", args.run, "--source-bundle", args.source_bundle, "--review-approval", args.review_approval, "--review-receipt", args.review_receipt]
        build = client.launch(dict(action="build", argv=argv, builder_sha256=args.builder_sha256, **control))
        while build.poll() is None:
            iteration_start = time.monotonic()
            lease = client.call("running")
            client.call("identity", "endpoint", dict(action="lease", lease=lease, **control))
            try:
                build.wait(timeout=max(0.01, 10 - (time.monotonic() - iteration_start)))
            except subprocess.TimeoutExpired:
                pass
        status = client.call("identity", "endpoint", dict(action="status", **control))
        require(status.get("status") == "finished" and status.get("returncode") == 0 and build.returncode == 0, "build failed/missing finish receipt; no retry")
    except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        failure = str(error) or type(error).__name__
        (args.log / "failure.json").write_text(json.dumps(dict(error=failure)) + "\n")
        if build is not None and build.poll() is None:
            try:
                # SIGINT only to PID/starttime/argv/UID/PGID-identified donor Python.
                # Its Runner handles its own nested command group. Never kill SSH
                # as a substitute for terminating the remote compiler.
                client.call("identity", "endpoint", dict(action="interrupt", **control))
                build.wait(timeout=45)
            except (ValueError, OSError, subprocess.SubprocessError) as stop_error:
                (args.log / "interrupt-unconfirmed.json").write_text(json.dumps(dict(error=str(stop_error))) + "\n")
    finally:
        if identity_confirmed:
            try:
                # Rechecks CT110 configuration + all other guest states, even
                # when memory is below the runtime resource gate. No force/stop.
                final = client.call("identity", "shutdown")
            except (ValueError, OSError, subprocess.SubprocessError) as shutdown_error:
                (args.log / "shutdown-unconfirmed.json").write_text(json.dumps(dict(error=str(shutdown_error))) + "\n")
                failure = failure or "normal shutdown/final-state guard failed; human intervention required"
        if build is not None and build.poll() is None:
            try:
                build.wait(timeout=15)
            except subprocess.TimeoutExpired:
                failure = failure or "SSH build channel still open after shutdown attempt; preserved, no process-wide termination"
        result = dict(status="failed_preserved" if failure else "build_finished_newCT110_stopped", failure=failure,
                      final_guard=final, log=str(args.log.resolve()))
        (args.log / "supervisor-result.json").write_text(json.dumps(result, indent=2) + "\n")
    require(not failure and final is not None, "supervisor did not prove build success plus normal CT110 shutdown; consult exclusive logs")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorized-ct110", action="store_true")
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--known-hosts", type=pathlib.Path)
    parser.add_argument("--baseline", type=pathlib.Path)
    parser.add_argument("--log", type=pathlib.Path)
    for name in ("control", "builder", "builder-sha256", "run", "source-bundle", "review-approval", "review-receipt"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        if not args.execute:
            print(json.dumps(dict(status="proposal_only_no_remote_calls", host=HOST, ctid=110, lease_interval_seconds=10,
                                  stale_limit_seconds=30, final_state="normal shutdown newCT110 only; no force; preserve outputs"), indent=2))
        else:
            require(all(getattr(args, key) for key in ("baseline", "log", "control", "builder", "builder_sha256", "run", "source_bundle", "review_approval", "review_receipt")), "all concrete existing-CT inputs are required")
            print(json.dumps(supervise(args)))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, "CT110 supervisor rejected: " + str(error) + "\n")
